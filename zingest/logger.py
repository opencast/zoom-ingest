import logging.handlers
import sys

LOGGING_FORMAT = "%Y-%m-%d %H:%M:%S %Z"

out = logging.StreamHandler(sys.stderr)
#10 MB default rollover size
logfile = logging.handlers.RotatingFileHandler("uploader.log", encoding="UTF-8", maxBytes=10000000)
logging.basicConfig(level=logging.ERROR, handlers=[out, logfile], format='%(asctime)s | %(name)s | %(levelname)s | %(message)s', datefmt=LOGGING_FORMAT)

