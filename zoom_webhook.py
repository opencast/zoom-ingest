import time

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

zoom = None
rabbit = None


class BadWebhookData(Exception):
    pass


class NoMp4Files(Exception):
    pass


class MyHandler(BaseHTTPRequestHandler):
    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_GET(s):

        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_POST(s):
        """Respond to Webhook"""
        content_length = int(s.headers['Content-Length'])
        if content_length < 5:
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'No data received')
            s.wfile.write(response.getvalue())
            return

        body = json.loads(s.rfile.read(content_length).decode("utf-8"))
        if "payload" not in body:
            print("payload missing")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Missing payload field in webhook body')
            s.wfile.write(response.getvalue())
            return

        payload = body["payload"]
        try:
            zoom.validate_payload(payload)
        except BadWebhookData as e:
            print("bad data")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Bad data')
            s.wfile.write(response.getvalue())
            return
        except NoMp4Files as e:
            print("no mp4")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Unrecognized payload format')
            s.wfile.write(response.getvalue())
            return

        if payload["object"]["duration"] < MIN_DURATION:
            print("Recording is too short")
            s.send_response(400)
            s.end_headers()
            response = BytesIO()
            response.write(b'Recording is too short')
            s.wfile.write(response.getvalue())
            return

        token = body["download_token"]
       # token= ''
        rabbit_msg = s.construct_rabbit_msg(payload,token)

        s.send_rabbit_msg(rabbit_msg)
        s.send_response(200)
        s.end_headers()
        response = BytesIO()
        response.write(b'Success')
        s.wfile.write(response.getvalue())





if __name__ == '__main__':

    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
    except FileNotFoundError:
        sys.exit("No settings found")

    try:
        PORT_NUMBER = int(config["Webhook"]["Port"])
        HOST_NAME = config["Webhook"]["Url"]
        MIN_DURATION = int(config["Webhook"]["Min_Duration"])

    except KeyError as err:
        sys.exit("Key {0} was not found".format(err))
    except ValueError as err:
        sys.exit("Invalid value, integer expected : {0}".format(err))

    rabbit = Rabbit(config)
    zoom = Zoom(config)

    server_class = HTTPServer
    httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
    print(time.asctime(), "Server Starts - %s:%s" % (HOST_NAME, PORT_NUMBER))
    try:
        httpd.serve_forever()

    except KeyboardInterrupt:
        pass
    httpd.server_close()
    print(time.asctime(), "Server Stops - %s:%s" % (HOST_NAME, PORT_NUMBER))
