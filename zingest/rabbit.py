import pika
import time
import json
from time import sleep
import zingest.logger
import logging

class Rabbit():

    def __init__(self, config, zoom):
        self.logger = logging.getLogger("rabbit")
        self.logger.setLevel(logging.DEBUG)

        self.rabbit_url = config["Rabbit"]["host"]
        self.rabbit_user = config["Rabbit"]["user"]
        self.rabbit_pass = config["Rabbit"]["password"]
        #TODO: Check that this is really a Zoom object
        self.zoom = zoom
        self.logger.info("Setup complete")
        self.logger.debug(f"Init with {self.rabbit_user}:{self.rabbit_pass} attached to {self.rabbit_url}")

    def send_rabbit_msg(self, payload, token):
        self.logger.debug("Prepping message")
        msg = self._construct_rabbit_msg(payload, token)
        self._send_rabbit_msg(msg)

    def _construct_rabbit_msg(self, payload, token):
        now = time.asctime()

        creator = self.zoom.get_recording_creator(payload)
        recording_files = self.zoom.parse_recording_files(payload)

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
            "creator": creator
        }
        self.logger.debug(f"Message is {rabbit_msg}")

        return rabbit_msg

    def _send_rabbit_msg(self, msg):
        self.logger.debug(f"Sending message to {self.rabbit_url}")
        credentials = pika.PlainCredentials(self.rabbit_user, self.rabbit_pass)
        connection = pika.BlockingConnection(pika.ConnectionParameters(self.rabbit_url, credentials=credentials))
        channel = connection.channel()
        channel.queue_declare(queue="zoomhook")
        channel.basic_publish(exchange='',
                              routing_key="zoomhook",
                              body=json.dumps(msg))
        connection.close()
        self.logger.debug("Done!")

    def start_consuming_rabbitmsg(self, callback):
        self.logger.debug(f"Connecting to {self.rabbit_url} as {self.rabbit_user}")
        credentials = pika.PlainCredentials(self.rabbit_user, self.rabbit_pass)
        connection = pika.BlockingConnection(pika.ConnectionParameters(self.rabbit_url, credentials=credentials))
        channel = connection.channel()
        rcv_queue = channel.queue_declare(queue="zoomhook")
        while True:
            msg_count = rcv_queue.method.message_count
            while msg_count > 0:
                method,prop,body = rcv_channel.basic_get(queue="zoomhook", auto_ack=True)
                callback(method, prop, body)
                count_queue = rcv_channel.queue_declare(queue="zoomhook", passive=True)
                msg_count = count_queue.method.message_count
            if msg_count == 0:
                sleep(5)
        rcv_channel.close()
        self.logger.debug("Closing rabbit connection")
        rcv_connection.close()
