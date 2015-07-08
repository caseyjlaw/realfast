#! /usr/bin/env python

# Main controller. Sarah Burke Spolaor June 2015
#
# Based on frb_trigger_controller.py by P. Demorest, 2015/02
#
# Listen for OBS packets having a certain 'triggered archiving'
# intent, and perform some as-yet-unspecified action when these
# are recieved.

import datetime
import os
import logging
import asyncore
import subprocess
import mcaf_library as mcaf
from optparse import OptionParser

cmdline = OptionParser()
cmdline.add_option('-m', '--mode', dest="trigger_mode",
        action="store", default="intent",
        help="Trigger on what field? (modes currently accpeted: intent, project). [DEFAULT: intent]")
cmdline.add_option('-t', '--value', dest="trigger_value",
        action="store", default="realfast",
        help="Triggers if trigger field contains this string. [DEFAULT: realfast]")
cmdline.add_option('-l', '--listen', dest="listen",
        action="store_true", default=False,
        help="Only listen to multicast, don't launch anything") 
cmdline.add_option('-v', '--verbose', dest="verbose",
        action="store_true", default=False,
        help="More verbose output")
(opt,args) = cmdline.parse_args()

progname = 'main_controller'

# Set up verbosity level for log
loglevel = logging.INFO
if opt.verbose:
    loglevel = logging.DEBUG

logging.basicConfig(format="%(asctime)-15s %(levelname)8s %(message)s",
        level=loglevel)

logging.info('%s started' % progname)

logging.info("Trigger mode %s; will trigger on value \"%s\"" % (opt.trigger_mode,opt.trigger_value))

if opt.listen:
    logging.info('Running in listen-only mode')

node = os.uname()[1]


class FRBController(object):
    """Listens for OBS packets and tells FRB processing about any
    notable scans."""

    def __init__(self,mode="project",):
        # Mode can be project, intent
        self.trigger_mode = opt.trigger_mode
        self.trigger_value = opt.trigger_value
        self.dotrigger = False

    def add_sdminfo(self,sdminfo):
        config = mcaf.EVLAConfig(sdminfo=sdminfo)

        if self.trigger_mode == 'project':
            compString = config.projectID
        elif self.trigger_mode == 'intent':
            compString = config.intentString
        else:
            print ("FRBController didn't understand your trigger mode, %s.\nPlease double-check for valid value." % self.trigger_mode)

        # !!! Wrapper here to deal with potential subscans?

        if self.trigger_value in compString:
            logging.info("Received saught %s: %s" % (self.trigger_mode,compString))
            #logging.info("Received trigger intent")
            # If we're not in listening mode, submit the pipeline for this scan as a queue submission.
            job = ['queue_rtpipe.py', os.path.basename(config.sdmLocation), '--scans', str(config.scan), '--paramfile', '~claw/code/vlart/rtparams.py']
            logging.info("Ready to submit scan %d as job %s" % (config.scan, ' '.join(job)))
            if not opt.listen:
                logging.info("Submitting scan %d as job %s" % (config.scan, ' '.join(job)))
                subprocess.call(job)
        else:
            logging.info("Received %s: %s" % (self.trigger_mode,compString)) 
            logging.info("Its BDF is in %s\n" % (config.bdfLocation)) 

if __name__ == '__main__':
    # This starts the receiving/handling loop
    controller = FRBController()
    sdminfo_client = mcaf.SdminfoClient(controller)
    try:
        asyncore.loop()
    except KeyboardInterrupt:
        # Just exit without the trace barf
        logging.info('%s got SIGINT, exiting' % progname)