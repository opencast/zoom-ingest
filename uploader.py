import logging
import configparser
from flask import Flask, request
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast
import zingest.logger
import threading

logger = logging.getLogger("main")
logger.setLevel(logging.DEBUG)
logger.debug("Main init")

def uploader():
    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
    except FileNotFoundError:
        sys.exit("No settings found")

    try:
        if bool(config['logging']['debug']):
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug logging enabled")
    except KeyError as err:
        sys.exit("Key {0} was not found".format(err))

    z = Zoom(config)
    r = Rabbit(config, z)
    o = Opencast(config, r)
    o.run()

if not 'thread' in locals():
    thread = threading.Thread(target=uploader, daemon=True)
    thread.start()


app = Flask(__name__)

@app.route('/', methods=['GET'])
def get_index():
    return "Doc links, links to /count"

@app.route('/count', methods=['GET'])
def get_count():
    return "Count of currently ingesting recordings is: "
