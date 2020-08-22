import logging
import configparser
import signal
import multiprocessing
from flask import Flask, request
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast
import zingest.logger

logger = logging.getLogger("main")
logger.setLevel(logging.DEBUG)
logger.debug("Main init")

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
app = Flask(__name__)

def sigint_handler(signum, frame):
    '''Intercept sigint and terminate services gracefully.
    '''
    utils.terminate(True)


def sigterm_handler(signum, frame):
    '''Intercept sigterm and terminate all processes.
    '''
    sigint_handler(signum, frame)
    for process in multiprocessing.active_children():
        process.terminate()
    sys.exit(0)

@app.route('/', methods=['GET'])
def get_index():
    return "Doc links, links to /count"

@app.route('/count', methods=['GET'])
def get_count():
    return "Count of currently ingesting recordings is: "
