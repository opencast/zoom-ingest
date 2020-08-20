import json
import configparser
import sys

from flask import Flask, request
import logging
import zingest.logger
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom

MIN_DURATION = 0

logger = logging.getLogger("webhook")
logger.info("Startup")

try:
    config = configparser.ConfigParser()
    config.read('settings.ini')
except FileNotFoundError:
    sys.exit("No settings found")

try:
    if bool(config['logging']['debug']):
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    PORT_NUMBER = int(config["Webhook"]["Port"])
    logger.debug(f"Webhook port is {PORT_NUMBER}")
    HOST_NAME = config["Webhook"]["Url"]
    logger.debug(f"Hostname is {HOST_NAME}")
    MIN_DURATION = int(config["Webhook"]["Min_Duration"])
    logger.debug(f"Minimum duration is is {MIN_DURATION}")
except KeyError as err:
    sys.exit("Key {0} was not found".format(err))
except ValueError as err:
    sys.exit("Invalid value, integer expected : {0}".format(err))

z = Zoom(config)
r = Rabbit(config, z)

app = Flask(__name__)

@app.route('/', methods=['GET'])
def do_GET():
    return "Hello"


@app.route('/', methods=['POST'])
@app.errorhandler(400)
def do_POST():
    """Respond to Webhook"""
    logger.debug("POST recieved")
    content_length = int(request.headers.get('Content-Length'))
    if content_length < 5:
        logger.error("Content too short")
        return render_template_string("No data received", ""), 400

    #Check UTF8 safeness of this
    body = request.get_json(force=True)
    if "payload" not in body:
        logger.error("Payload is missing")
        return render_template_string("Missing payload field in webhook body", ""), 400

    payload = body["payload"]
    try:
        z.validate_payload(payload)
    except BadWebhookData as e:
        logger.error("Payload failed validation")
        return render_template_string("Payload failed validation", ""), 400
    except NoMp4Files as e:
        logger.error("No mp4 files found!")
        return render_template_string("No mp4 files found!", ""), 400

    if payload["object"]["duration"] < MIN_DURATION:
        logger.error("Recording is too short")
        return render_template_string("Recording is too short", ""), 400

    token = body["download_token"]
    logger.debug(f"Token is {token}")

    logger.debug("Sending rabbit message")
    r.send_rabbit_msg(payload, token)

    logger.debug("POST processed successfully")
    return "Success"

