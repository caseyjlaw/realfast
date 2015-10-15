""" Functions used in realfast system.
Originally a helper script, so strucutre is odd and needs reworking.
"""

import os, glob, time, shutil, subprocess, logging
import sdmreader
import rtpipe.RT as rt
import rtpipe.calpipe as cp
import rtpipe.parsesdm as ps
import rtpipe.parsecands as pc
import cPickle as pickle

logger = logging.getLogger(__name__)

def read(filename, paramfile='', fileroot='', bdfdir='/lustre/evla/wcbe/data/realfast'):
    """ Simple parse and return metadata for pipeline for first scan
    """

    sc, sr = sdmreader.read_metadata(filename, bdfdir=bdfdir)
    logger.info('Scans, Target names:')
    logger.info('%s' % str([(ss, sc[ss]['source']) for ss in sc]))
    logger.info('Example pipeline:')
    state = rt.set_pipeline(filename, sc.popitem()[0], paramfile=paramfile, fileroot=fileroot, nologfile=True)

def search(qname, filename, paramfile, fileroot, scans=[], telcalfile='', redishost='localhost', depends_on=None, bdfdir='/lustre/evla/wcbe/data/bunker'):
    """ Search for transients in all target scans and segments
    """

    from rq import Queue, Connection
    from redis import Redis

    # enqueue jobs
    stateseg = []
    logger.info('Setting up pipelines for %s, scans %s...' % (filename, scans))

    for scan in scans:
        assert isinstance(scan, int), 'Scan should be an integer'
        scanind = scans.index(scan)
        state = rt.set_pipeline(filename, scan, paramfile=paramfile, fileroot=fileroot, gainfile=telcalfile, writebdfpkl=True, nologfile=True, bdfdir=bdfdir)
        for segment in grouprange(0, state['nsegments'], 3):   # submit three segments at a time to reduce read/prep overhead
            stateseg.append( (state, segment) )
    njobs = len(stateseg)

    if njobs:
        logger.info('Enqueuing %d job%s...' % (njobs, 's'[:njobs-1]))

        # submit to queue
        q = Queue(qname, connection=Redis(redishost))

        # enqueue all but one
        if njobs > 1:
            for i in range(njobs-1):
                state, segment = stateseg[i]
                job = q.enqueue_call(func=rt.pipeline, args=(state, segment), depends_on=depends_on, timeout=24*3600, result_ttl=24*3600)
        else:
            job = depends_on

        # use second to last job as dependency for last job
        state, segment = stateseg[-1]
        lastjob = q.enqueue_call(func=rt.pipeline, args=(state, segment), depends_on=job, at_front=True, timeout=24*3600, result_ttl=24*3600)  # queued after others, but moved to front of queue

        logger.info('Jobs enqueued. Returning last job with id %s.' % lastjob.id)
        return lastjob
    else:
        logger.info('No jobs to enqueue')
        return

def integrate(filename, scanstr, inttime, redishost='localhost'):
    """ Creates MS from SDM and integrates down.
    filename is sdm, scanstr is comma-delimited string of scans, inttime is time in s (no label).
    """

    from rq import Queue, Connection
    from redis import Redis

    with Connection(Redis(redishost)):
        q = Queue('slow')
        q.enqueue_call(func=ps.sdm2ms, args=(filename, filename.rstrip('/')+'.ms', scanstr, inttime), timeout=24*3600, result_ttl=24*3600)

def calibrate(filename, fileroot):
    """ Run calibration pipeline
    """

    pipe = cp.pipe(filename, fileroot)
    pipe.run()

def cleanup(workdir, fileroot, scans=[]):
    """ Cleanup up noise and cands files.
    Finds all segments in each scan and merges them into single cand/noise file per scan.
    """

    os.chdir(workdir)

    # merge cands/noise files per scan
    for scan in scans:
#try:
        pc.merge_segments(fileroot, scan, cleanup=True)
#        except:
#            logger.exception('')

def addjob(jobid):
    """ Adds jobid as key in db. Value = 0.
    """

    from redis import Redis
    conn = Redis(db=1)   # db for tracking ids of tail jobs

    conn.set(jobid, 0)

def removejob(jobid):
    """ Removes jobid from db.
    """

    from redis import Redis
    conn = Redis(db=1)   # db for tracking ids of tail jobs

    status = conn.delete(jobid)
    if status:
        logger.info('jobid %s removed from tracking queue' % jobid)
    else:
        logger.info('jobid %s not removed from tracking queue' % jobid)

def plot_summary(workdir, fileroot, scans, remove=[], snrmin=0, snrmax=999):
    """ Make summary plots for cands/noise files with fileroot
    Uses only given scans.
    """

    os.chdir(workdir)

    try:
        pc.plot_summary(fileroot, scans, remove=remove, snrmin=snrmin, snrmax=snrmax)
        pc.plot_noise(fileroot, scans, remove=remove)
    except:
        logger.exception('')

    logger.info('Completed plotting for fileroot %s with all scans available (from %s).' % (fileroot, str(scans)))

def plot_cand(workdir, fileroot, scans=[], candnum=-1):
    """ Visualize a candidate
    """

    pkllist = []
    for scan in scans:
        pklfile = os.path.join(workdir, 'cands_' + fileroot + '_sc' + str(scan) + '.pkl')
        if os.path.exists(pklfile):
            pkllist.append(pklfile)

    pc.plot_cand(pkllist, candnum=candnum)

def plot_pulsar(workdir, fileroot, scans=[]):
    """
    Assumes 3 or 4 input pulsar scans (centered then offset pointings).
    """

    pkllist = []
    for scan in scans:
        pkllist.append(os.path.join(workdir, 'cands_' + fileroot + '_sc' + str(scan) + '.pkl'))

    logger.info('Pulsar plotting for pkllist:', pkllist)
    pc.plot_psrrates(pkllist, outname=os.path.join(workdir, 'plot_' + fileroot + '_psrrates.png'))

def getscans(filename, scans='', sources='', intent='', bdfdir='/lustre/evla/wcbe/data/realfast'):
    """ Get scan list as ints.
    First tries to parse scans, then sources, then intent.
    """

    # if no scans defined, set by mode context
    if scans:
        scans = [int(i) for i in scans.split(',')]
    elif sources:
        meta = sdmreader.read_metadata(filename, bdfdir=bdfdir)

        # if source list provided, parse it then append all scans to single list
        sources = [i for i in sources.split(',')]
        scans = []
        for source in sources:
            sclist = filter(lambda sc: source in meta[0][sc]['source'], meta[0].keys())
#            sclist = [sc for sc in meta[0].keys() if source in meta[0][sc]['source']]
            if len(sclist):
                scans += sclist
            else:
                logger.info('No scans found for source %s' % source)
    elif intent:
        meta = sdmreader.read_metadata(filename, bdfdir=bdfdir)
        scans = filter(lambda sc: intent in meta[0][sc]['intent'], meta[0].keys())
#        scans = [sc for sc in meta[0].keys() if intent in meta[0][sc]['intent']]   # get all target fields
    else:
        logger.error('Must provide scans, sources, or intent.')
        raise BaseException

    return scans

def grouprange(start, size, step):
    arr = range(start,start+size)
    return [arr[ss:ss+step] for ss in range(0, len(arr), step)]

def rsync(original, new):
    """ Uses subprocess.call to rsync from 'filename' to 'new'
    If new is directory, copies original in.
    If new is new file, copies original to that name.
    """

    assert os.path.exists(original), 'Need original file!'

    # need to dynamically define whether rsync from has a slash at the end
    if os.path.exists(new):   # new is a directory
        assert os.path.isdir(new), 'New should be a directory.'
        trailing = ''
    else:   # new is a new file. fill it with contents of original
        trailing = '/'

    subprocess.call(["rsync", "-ar", original.rstrip('/') + trailing, new.rstrip('/')])

def copysdm(filename, workdir):
    """ Copies sdm from filename (full path) to workdir
    """

    # first copy data to working area
    fname = os.path.basename(filename)
    newfileloc = os.path.join(workdir, fname)
    if not os.path.exists(newfileloc):
        logger.info('Copying %s into %s' % (fname, workdir))
        shutil.copytree(filename, newfileloc)  # copy file in
    else:
        logger.info('File %s already in %s. Using that one...' % (fname, workdir))
    filename = newfileloc

def copyDirectory(src, dest):
    try:
        shutil.copytree(src, dest)
    # Directories are the same
    except shutil.Error as e:
        print('Directory not copied. Error: %s' % e)
    # Any error saying that the directory doesn't exist
    except OSError as e:
        print('Directory not copied. Error: %s' % e)

def check_spw(sdmfile, scan):
    """ Looks at relative freq of spw and duplicate spw_reffreq. 
    Returns 1 for permutable order with no duplicates and 0 otherwise (i.e., funny data)
    """

    d = rt.set_pipeline(sdmfile, scan, silent=True)

    dfreq = [d['spw_reffreq'][i+1] - d['spw_reffreq'][i] for i in range(len(d['spw_reffreq'])-1)]
    dfreqneg = [df for df in dfreq if df < 0]

    duplicates = list(set(d['spw_reffreq'])).sort() != d['spw_reffreq'].sort()

    return len(dfreqneg) <= 1 and not duplicates

def find_archivescans(mergefile, threshold=0):
    """ Parses merged cands file and returns list of scans with detections.
    All scans with cand SNR>threshold are returned as a list.
    Goal for this function is to apply RFI rejection, dm-t island detection, and whatever else we can think of.
    """

    # read metadata and define columns of interest
    d = pickle.load(open(mergefile, 'r'))
    scancol = d['featureind'].index('scan')
    if 'snr2' in d['features']:
        snrcol = d['features'].index('snr2')
    elif 'snr1' in d['features']:
        snrcol = d['features'].index('snr1')

    # read data and define snrs
    loc, prop = pc.read_candidates(mergefile)
    snrs = [prop[i][snrcol] for i in range(len(prop))]

    # calculate unique list of scans of interest
    sigscans = [loc[i,scancol] for i in range(len(loc)) if snrs[i] > threshold]
    sigsnrs = [snrs[i] for i in range(len(loc)) if snrs[i] > threshold]
    siglocs = [list(loc[i]) for i in range(len(loc)) if snrs[i] > threshold]
    if len(sigscans):
        logger.info('cands above threshold %.1f for %s: %s' % (threshold, mergefile, str(zip(siglocs, sigsnrs))))
    else:
        logger.info('no cands above threshold %.1f for %s' % (threshold, mergefile))

    return set(sigscans)

def tell_candidates(mergefile, filename):
    """
    Parses merged cands file and prints out candidate information to outfile.
    """
    with open(mergefile, 'rb') as pkl:
        d = pickle.load(pkl) # dmlist = d['dmarr'] ; same for dtarr.
        cands = pickle.load(pkl) # can also read as (loc, prop) to get arrays of each thing.
    with open(filename, 'w') as outfile:
        k = list(cands.keys())
        v = list(cands.values())
        for i in range(0,len(k)):            
            outfile.write('\t'.join(map(str,k[i]))+'\t'.join(map(str,v[i]))+"\n")
    return

def gettelcalfile(telcaldir, filename, timeout=0):
    """ Looks for telcal file with name filename.GN in typical telcal directories
    Searches recent directory first, then tries tree search.
    If none found and timeout=0, returns empty string. Else will block for timeout seconds.
    """

    fname = os.path.basename(filename)
    time_filestart = time.time()

    # search for associated telcal file
    year = str(time.localtime()[0])
    month = '%02d' % time.localtime()[1]
    telcaldir2 = os.path.join(telcaldir, year, month)

    while 1:
        logger.info('Looking for telcalfile in %s' % telcaldir2)
        telcalfile = [os.path.join(telcaldir2, ff) for ff in os.listdir(telcaldir2) if fname+'.GN' in ff]
        
        # if not in latest directory, walk through whole structure
        if not len(telcalfile):
            logger.info('No telcal in newest directory. Searching whole telcalfile tree.')
            telcalfile = [os.path.join(root, fname+'.GN') for root, dirs, files in os.walk(telcaldir) if fname+'.GN' in files]

        assert isinstance(telcalfile, list)

        # make into string (emtpy or otherwise)
        if len(telcalfile) == 1:
            telcalfile = telcalfile[0]
            logger.info('Found telcal file at %s' % telcalfile)
            break
        elif len(telcalfile) > 1:
            telcalfile = ''
            logger.info('Found multiple telcalfiles %s' % telcalfile)
        else:
            telcalfile = ''
            logger.info('No telcal file found in %s' % telcaldir)

        assert isinstance(telcalfile, str)

        # if waiting, but no file found, check timeout
        if timeout:
            if time.time() - time_filestart < timeout:  # don't break yet
                logger.info('Waiting for telcalfile...')
                time.sleep(2)
                continue
            else:   # reached timeout
                logger.info('Timeout waiting for telcalfile')
                break
        else:  # not waiting
            logger.info('Not waiting for telcalfile')
            break

    return telcalfile

def lookforfile(lookdir, subname, changesonly=False):
    """ Look for and return a file with subname in lookdir.
    changesonly means it will wait for changes. default looks only once.
    """

    logger.info('Looking for %s in %s.' % (subname, lookdir))

    filelist0 = os.listdir(os.path.abspath(lookdir))
    if changesonly:
        while 1:
            filelist = os.listdir(os.path.abspath(lookdir))
            newfiles = filter(lambda ff: ff not in filelist0, filelist)
            matchfiles = filter(lambda ff: subname in ff, newfiles)
            if len(matchfiles):
                break
            else:
                logger.info('.')
                filelist0 = filelist
                time.sleep(1)
    else:
        matchfiles = filter(lambda ff: subname in ff, filelist0)

    if len(matchfiles) == 0:
        logger.info('No file found.')
        fullname = ''
    elif len(matchfiles) == 1:
        fullname = os.path.join(lookdir, matchfiles[0])
    elif len(matchfiles) > 1:
        logger.info('More than one match!', matchfiles)
        fullname = os.path.join(lookdir, matchfiles[0])

    logger.info('Returning %s.' % fullname)
    return fullname

def waitforsdm(filename, timeout=300):
    """ Monitors filename (an SDM) to see when it is finished writing.
    timeout is time in seconds to wait from first detection of file.
    Intended for use on CBE.
    """

    time_filestart = 0
    while 1:
        try:
            sc,sr = sdmreader.read_metadata(filename)
        except RuntimeError:
            logger.info('File %s not found.' % filename)
        except IOError:
            logger.info('File %s does not have Antenna.xml yet...' % filename)
            time.sleep(2)
            continue
        else:
            bdflocs = [sc[ss]['bdfstr'] for ss in sc]
            if not time_filestart:
                logger.info('File %s exists. Waiting for it to complete writing.' % filename)
                time_filestart = time.time()

        # if any bdfstr are not set, then file not finished
        if None in bdflocs:
            if time.time() - time_filestart < timeout:
                logger.info('bdfs not all written yet. Waiting...')
                time.sleep(2)
                continue
            else:
                logger.info('Timeout exceeded. Exiting...')
                break
        else:
            logger.info('All bdfs written. Continuing.')
            break

def sdmascal(filename, calscans='', bdfdir='/lustre/evla/wcbe/data/realfast'):
    """ Takes incomplete SDM (on CBE) and creates one corrected for use in calibration.
    optional calscans is casa-like string to select scans
    """

    if not calscans:
        calscanlist = getscans(filename, intent='CALI')   # get calibration scans
        calscans = ','.join([str(sc) for sc in calscanlist])   # put into CASA-like selection string

    # make new Main.xml for relevant scans
    if ( os.path.exists(os.path.join(filename, 'Main_cal.xml')) and os.path.exists(os.path.join(filename, 'ASDMBinary_cal')) ):
        logger.info('Found existing cal xml and data. Moving in...')
        shutil.copyfile(os.path.join(filename, 'Main.xml'), os.path.join(filename, 'Main_orig.xml'))   # put new Main.xml in
        shutil.move(os.path.join(filename, 'Main_cal.xml'), os.path.join(filename, 'Main.xml'))   # put new Main.xml in
        shutil.move(os.path.join(filename, 'ASDMBinary_cal'), os.path.join(filename, 'ASDMBinary'))
    else:
        shutil.copyfile(os.path.join(filename, 'Main.xml'), os.path.join(filename, 'Main_orig.xml'))   # put new Main.xml in
        subprocess.call(['choose_SDM_scans.pl', filename, os.path.join(filename, 'Main_cal.xml'), calscans])  #  modify Main.xml
        shutil.move(os.path.join(filename, 'Main_cal.xml'), os.path.join(filename, 'Main.xml'))   # put new Main.xml in

        if not os.path.exists(os.path.join(filename, 'ASDMBinary')):
            os.makedirs(os.path.join(filename, 'ASDMBinary'))

        sc,sr = sdmreader.read_metadata(filename, bdfdir=bdfdir)
        for calscan in calscanlist:
            bdfstr = sc[calscan]['bdfstr'].split('/')[-1]
            bdffile = glob.glob(os.path.join(bdfdir, bdfstr))[0]
            bdffiledest = os.path.join(filename, 'ASDMBinary', os.path.split(bdffile)[1])
            if not os.path.exists(bdffiledest):
                logger.info('Copying bdf in for calscan %d.' % calscan)
                shutil.copyfile(bdffile, bdffiledest)
            else:
                logger.info('bdf in for calscan %d already in place.' % calscan)

def sdmasorig(filename):
    """ Take sdm for calibration and restore it.
    keeps ASDMBinary around, just in case
    """

    shutil.move(os.path.join(filename, 'Main.xml'), os.path.join(filename, 'Main_cal.xml'))
    shutil.move(os.path.join(filename, 'Main_orig.xml'), os.path.join(filename, 'Main.xml'))
    shutil.move(os.path.join(filename, 'ASDMBinary'), os.path.join(filename, 'ASDMBinary_cal'))
