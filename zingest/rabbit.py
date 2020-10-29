import json
import logging
import time

import pika

import zingest


class Rabbit:

    def __init__(self, config, zoom):
        if not zoom or type(zoom) != zingest.zoom.Zoom:
            raise TypeError("Zoom is missing or the wrong type!")
        self.logger = logging.getLogger(__name__)
        self.rabbit_url = config["Rabbit"]["host"]
        self.rabbit_user = config["Rabbit"]["user"]
        self.rabbit_pass = config["Rabbit"]["password"]
        self.zoom = zoom
        self.logger.info("Setup complete")
        self.logger.debug(f"Init rabbitmq connection to {self.rabbit_url} with user {self.rabbit_user}")

    def send_rabbit_msg(self, payload, token):
        self.logger.debug("Prepping message")
        msg = self._construct_rabbit_msg(payload, token)
        self._send_rabbit_msg(msg)

    def _construct_rabbit_msg(self, payload, token):
        now = time.asctime()

        creator = self.zoom.get_recording_creator(payload)
        recording_files = self.zoom.parse_recording_files(payload)

        rabbit_msg = {
            "uuid": payload["uuid"],
            "zoom_series_id": payload["id"],
            "topic": payload["topic"],
            "start_time": payload["start_time"],
            "duration": payload["duration"],
            "host_id": payload["host_id"],
            "share_url": payload["share_url"],
            "recording_files": recording_files,
            "zingest_params": payload['zingest_params'],
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
        rcv_channel = connection.channel()
        rcv_channel.queue_declare(queue="zoomhook")
        for method_frame, properties, body in rcv_channel.consume('zoomhook'):
            self.logger.debug(f"Message {method_frame.delivery_tag}, running callback")
            callback(method_frame, properties, body)
            rcv_channel.basic_ack(method_frame.delivery_tag)
        requeued_messages = rcv_channel.cancel()
        self.logger.debug('Requeued %i messages' % requeued_messages)
        rcv_channel.close()
        self.logger.debug("Closing rabbit connection")
        connection.close()
