import logging
import sys
from logging.config import fileConfig
from os.path import isfile

if isfile("etc/zoom-ingest/logger.ini"):
    fileConfig('etc/zoom-ingest/logger.ini')
if isfile("/etc/zoom-ingest/logger.ini"):
    fileConfig('/etc/zoom-ingest/logger.ini')
else:
    out = logging.StreamHandler(sys.stderr)
    logging.basicConfig(level=logging.ERROR,
                        handlers=[out],
                        format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                        datefmt="%Y-%m-%d %H:%M:%S %Z")