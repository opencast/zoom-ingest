import configparser
import logging
import os.path
import sys
import threading
import time

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

enable_email = config["Email"]["enabled"].lower() in ['true'] #I hate python sometimes...

zingest.db.init(config)
z = Zoom(config)
r = Rabbit(config, z)
o = Opencast(config, r, z, enable_email)

def run_and_notify_about(thing):
    while True:
        try:
            thing()
        except Exception as e:
            if enable_email:
                email_logger = logging.getLogger("mail")
                email_logger.exception("Zoom Uploader general error, will retry in 10 seconds after emailing...")
            else:
                logger.exception("Zoom Uploader general error, will retry in 10 seconds...")
            time.sleep(10)

thread = threading.Thread(
        target=run_and_notify_about(o.run),
        daemon=True)
thread.start()

thread = threading.Thread(
        target=run_and_notify_about(o.process_backlog),
        daemon=True)
thread.start()


app = Flask(__name__)

@app.route('/', methods=['GET'])
def get_index():
    return "Doc links, links to /count"

@app.route('/count', methods=['GET'])
def get_count():
    return "Count of currently ingesting recordings is: "
