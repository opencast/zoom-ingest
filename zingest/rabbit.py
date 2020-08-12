import pika
import time
import json

class Rabbit():

    def __init__(self, config, zoom):
        self.rabbit_url = config["Rabbit"]["host"]
        self.rabbit_user = config["Rabbit"]["user"]
        self.rabbit_pass = config["Rabbit"]["password"]
        #TODO: Check that this is really a Zoom object
        self.zoom = zoom

    def send_rabbit_msg(self, payload, token):
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

        return rabbit_msg

    def _send_rabbit_msg(self, msg):
        credentials = pika.PlainCredentials(self.rabbit_user, self.rabbit_pass)
        connection = pika.BlockingConnection(pika.ConnectionParameters(self.rabbit_url, credentials=credentials))
        channel = connection.channel()
        channel.queue_declare(queue="zoomhook")
        channel.basic_publish(exchange='',
                              routing_key="zoomhook",
                              body=json.dumps(msg))
        connection.close()

    def start_consuming_rabbitmsg(self, callback):
        credentials = pika.PlainCredentials(self.rabbit_user, self.rabbit_pass)
        rcv_connection = pika.BlockingConnection(pika.ConnectionParameters(self.rabbit_url, credentials=credentials))
        rcv_channel = rcv_connection.channel()
        queue = rcv_channel.queue_declare(queue="zoomhook")
        msg_count = queue.method.message_count
        while msg_count > 0:
            method,prop,body = rcv_channel.basic_get(queue="zoomhook", auto_ack=True)
            callback(method, prop, body)
            count_queue = rcv_channel.queue_declare(queue="zoomhook", passive=True)
            msg_count = count_queue.method.message_count
        rcv_channel.close()
        rcv_connection.close()
