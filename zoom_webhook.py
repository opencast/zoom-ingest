import time

from http.server import HTTPServer
from http.server import BaseHTTPRequestHandler
import pika
import json
from io import BytesIO
from zoomus import ZoomClient
import configparser
import sys

HOST_NAME = ''
PORT_NUMBER = 8080

MIN_DURATION = 0

API_KEY = ""
API_SECRET = ""
rabbit_url= ""

zoom_client = None


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
            s.validate_payload(payload)
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

    def construct_rabbit_msg(self, payload,token):
        now = time.asctime()

        user_list_response = zoom_client.user.get(id=payload["object"]["host_id"])
        user_list = json.loads(user_list_response.content.decode("utf-8"))

        recording_files = []
        for file in payload["object"]["recording_files"]:
            if file["file_type"].lower() == "mp4":
                recording_files.append({
                    "recording_id": file["id"],
                    "recording_start": file["recording_start"],
                    "recording_end": file["recording_end"],
                    "download_url": file["download_url"],
                    "file_type": file["file_type"],
                    "recording_type": file["recording_type"]
                })

        rabbit_msg = {
            "uuid": payload["object"]["uuid"],
            "zoom_series_id": payload["object"]["id"],
            "topic": payload["object"]["topic"],
            "start_time": payload["object"]["start_time"],
            "duration": payload["object"]["duration"],
            "host_id": payload["object"]["host_id"],
            "recording_files": recording_files,
            "token": token,
            "received_time": now,
            "creator": user_list["location"]
        }

        return rabbit_msg

    def send_rabbit_msg(self,msg):
        credentials = pika.PlainCredentials("adminuser","EYeeWiz4uvuowei9")
        connection = pika.BlockingConnection(pika.ConnectionParameters(rabbit_url, credentials=credentials))
        channel = connection.channel()
        channel.queue_declare(queue="zoomhook")
        channel.basic_publish(exchange='',
                              routing_key="zoomhook",
                              body=json.dumps(msg))
        connection.close()

    def validate_payload(s,payload):
        required_payload_fields = [
            "object"
        ]
        required_object_fields = [
            "id",  # zoom series id
            "uuid",  # unique id of the meeting instance,
            "host_id",
            "topic",
            "start_time",
            "duration",  # duration in minutes
            "recording_files"
        ]
        required_file_fields = [
            "id",  # unique id for the file
            "recording_start",
            "recording_end",
            "download_url",
            "file_type",
            "recording_type"
        ]

        try:
            for field in required_payload_fields:
                if field not in payload.keys():
                    raise BadWebhookData(
                        "Missing required payload field '{}'. Keys found: {}"
                            .format(field, payload.keys()))

            obj = payload["object"]
            for field in required_object_fields:
                if field not in obj.keys():
                    raise BadWebhookData(
                        "Missing required object field '{}'. Keys found: {}"
                            .format(field, obj.keys()))

            files = obj["recording_files"]

            # make sure there's some mp4 files in here somewhere
            mp4_files = any(x["file_type"].lower() == "mp4" for x in files)
            if not mp4_files:
                raise NoMp4Files("No mp4 files in recording data")

            for file in files:
                if "file_type" not in file:
                    raise BadWebhookData("Missing required file field 'file_type'")
                if file["file_type"].lower() != "mp4":
                    continue
                for field in required_file_fields:
                    if field not in file.keys():
                        raise BadWebhookData(
                            "Missing required file field '{}'".format(field))
                if "status" in file and file["status"].lower() != "completed":
                    raise BadWebhookData(
                        "File with incomplete status {}".format(file["status"])
                    )

        except NoMp4Files:
            # let these bubble up as we handle them differently depending
            # on who the caller is
            raise
        except Exception as e:
            raise BadWebhookData("Unrecognized payload format. {}".format(e))


if __name__ == '__main__':

    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
    except FileNotFoundError:
        sys.exit("No settings found")

    try:
        API_KEY = config["JWT"]["Key"]
        API_SECRET = config["JWT"]["Secret"]
        PORT_NUMBER = int(config["Webhook"]["Port"])
        HOST_NAME = config["Webhook"]["Url"]
        rabbit_url = config["Webhook"]["Rabbit_url"]
        MIN_DURATION = int(config["Webhook"]["Min_Duration"])

    except KeyError as err:
        sys.exit("Key {0} was not found".format(err))
    except ValueError as err:
        sys.exit("Invalid value, integer expected : {0}".format(err))

    zoom_client = ZoomClient(API_KEY, API_SECRET)

    server_class = HTTPServer
    httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
    print(time.asctime(), "Server Starts - %s:%s" % (HOST_NAME, PORT_NUMBER))
    try:
        httpd.serve_forever()

    except KeyboardInterrupt:
        pass
    httpd.server_close()
    print(time.asctime(), "Server Stops - %s:%s" % (HOST_NAME, PORT_NUMBER))