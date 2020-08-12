from http.server import HTTPServer
from http.server import BaseHTTPRequestHandler

import json
from io import BytesIO
import configparser
import sys

from rabbit import Rabbit
from zoom import Zoom

HOST_NAME = ''
PORT_NUMBER = 8080

MIN_DURATION = 0

import logging
import logger

z = None
r = None


class BadWebhookData(Exception):
    pass


class NoMp4Files(Exception):
    pass


class MyHandler(BaseHTTPRequestHandler):

    logger = logging.getLogger("handler")
    logger.setLevel(logging.DEBUG)

    def log_debug(self, format, *args):
        self.logger.debug(format%args)

    def log_message(self, format, *args):
        self.logger.info(format%args)

    def log_error(self, format, *args):
        self.logger.error(format%args)

    def do_HEAD(s):
        s.log_message("HEAD")
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_GET(s):
        s.log_message("GET")
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_POST(s):
        """Respond to Webhook"""
        s.log_message("POST")
        content_length = int(s.headers['Content-Length'])
        if content_length < 5:
            s.log_error("Content too short")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'No data received')
            s.wfile.write(response.getvalue())
            return

        body = json.loads(s.rfile.read(content_length).decode("utf-8"))
        if "payload" not in body:
            s.log_error("Payload is missing")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Missing payload field in webhook body')
            s.wfile.write(response.getvalue())
            return

        payload = body["payload"]
        try:
            z.validate_payload(payload)
        except BadWebhookData as e:
            s.log_error("Payload failed validation")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Bad data')
            s.wfile.write(response.getvalue())
            return
        except NoMp4Files as e:
            s.log_error("No mp4 files found!")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Unrecognized payload format')
            s.wfile.write(response.getvalue())
            return

        if payload["object"]["duration"] < MIN_DURATION:
            s.log_error("Recording is too short")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Recording is too short')
            s.wfile.write(response.getvalue())
            return

        token = body["download_token"]
        s.log_debug(f"Token is {token}")
        
        r.send_rabbit_msg(payload, token)

        s.send_response(200)
        s.end_headers()
        response = BytesIO()
        response.write(b'Success')
        s.wfile.write(response.getvalue())





if __name__ == '__main__':

    logger = logging.getLogger("main")
    logger.setLevel(logging.DEBUG)
    logger.debug("Main init")

    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
    except FileNotFoundError:
        sys.exit("No settings found")

    try:
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

    server_class = HTTPServer
    httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
    logger.info(f"Server startup complete on {HOST_NAME}:{PORT_NUMBER}")
    try:
        httpd.serve_forever()

    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logger.info("Server shutdown complete")
