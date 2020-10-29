import configparser
import logging
import os.path
import sys
import threading

from flask import Flask

import zingest.db
from logger import init_logger
from zingest.opencast import Opencast
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom

init_logger()
logger = logging.getLogger(__name__)
logger.info("Startup")

try:
    config = configparser.ConfigParser()
    if os.path.isfile("etc/zoom-ingest/settings.ini"):
        config.read("etc/zoom-ingest/settings.ini")
        logger.debug("Configuration read from etc/zoom-ingest/settings.ini")
    else:
        config.read("/etc/zoom-ingest/settings.ini")
        logger.debug("Configuration read from /etc/zoom-ingest/settings.ini")
except FileNotFoundError:
    sys.exit("No settings found")

zingest.db.init(config)
z = Zoom(config)
r = Rabbit(config, z)
o = Opencast(config, r, z)

def uploader():
    o.run()

def reingester():
    o.process_backlog()

thread = threading.Thread(target=uploader, daemon=True)
thread.start()

thread = threading.Thread(target=reingester, daemon=True)
thread.start()


app = Flask(__name__)

@app.route('/', methods=['GET'])
def get_index():
    return "Doc links, links to /count"

@app.route('/count', methods=['GET'])
def get_count():
    return "Count of currently ingesting recordings is: "
