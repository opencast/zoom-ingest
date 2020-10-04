import logging
import configparser
from flask import Flask, request
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast
import zingest.logger
import threading
import zingest.db
import os.path

logger = logging.getLogger("main")
logger.setLevel(logging.DEBUG)
logger.debug("Main init")

try:
    config = configparser.ConfigParser()
    if os.path.isfile("etc/zoom-ingest/settings.ini"):
        config.read("etc/zoom-ingest/settings.ini")
    else:
        config.read("/etc/zoom-ingest/settings.ini")
except FileNotFoundError:
    sys.exit("No settings found")

try:
    if bool(config['logging']['debug']):
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
except KeyError as err:
    sys.exit("Key {0} was not found".format(err))

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
