import logging
import configparser

from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast
import zingest.logger

if __name__ == '__main__':

    logger = logging.getLogger("main")
    logger.setLevel(logging.DEBUG)
    logger.debug("Main init")

    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
    except FileNotFoundError:
        sys.exit("No settings found")


    z = Zoom(config)
    r = Rabbit(config, z)
    o = Opencast(config, r)
