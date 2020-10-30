# -*- coding: utf-8 -*-
import logging
import sys
from os.path import isfile


def init_logger():
    """
    Initialize logger, load logger configuration.
    """
    if isfile("etc/zoom-ingest/logger.ini"):
        logging.config.fileConfig('etc/zoom-ingest/logger.ini')
    if isfile("/etc/zoom-ingest/logger.ini"):
        logging.config.fileConfig('/etc/zoom-ingest/logger.ini')
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        logging.basicConfig(level=logging.INFO,
                            handlers=[console_handler],
                            format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                            datefmt="%Y-%m-%d %H:%M:%S")

