import json
import unittest
from unittest.mock import MagicMock, patch
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom

webhook_event = None
with open('test/resources/zoom/webhook-recording-completed.json', 'r') as webhook:
    webhook_event = json.loads(webhook.read())

class TestRabbit(unittest.TestCase):

    def setUp(self):
        self.config = {"Rabbit": {"host": "localhost", "user": "test_user", "password": "test_password" }}
        zoom_config={"JWT": {"Key": "test_key", "Secret": "test_secret" }}
        self.zoom = Zoom(zoom_config)

    def tearDown(self):
        pass

    def test_noConfig(self):
        with self.assertRaises(KeyError):
          Rabbit(None, self.zoom)

    def test_missingRabbitConfig(self):
        del self.config["Rabbit"]
        with self.assertRaises(KeyError):
          Rabbit(self.config, self.zoom)

    def test_missingHostConfig(self):
        del self.config["Rabbit"]["host"]
        with self.assertRaises(KeyError):
          Rabbit(self.config, self.zoom)

    def test_missingUserConfig(self):
        del self.config["Rabbit"]["user"]
        with self.assertRaises(KeyError):
          Rabbit(self.config, self.zoom)

    def test_missingPassConfig(self):
        del self.config["Rabbit"]["password"]
        with self.assertRaises(KeyError):
          Rabbit(self.config, self.zoom)

    def test_noZoom(self):
        with self.assertRaises(TypeError):
          Rabbit(self.config, None)

    def test_badZoom(self):
        with self.assertRaises(TypeError):
          Rabbit(self.config, "")


    def test_goodConfig(self):
        rabbit = Rabbit(self.config, self.zoom)
        self.assertEqual(self.config["Rabbit"]["host"], rabbit.rabbit_url)
        self.assertEqual(self.config["Rabbit"]["user"], rabbit.rabbit_user)
        self.assertEqual(self.config["Rabbit"]["password"], rabbit.rabbit_pass)

    def ae(self, a, b, key):
        self.assertEqual(a[key], b[key])

    def assert_rabbitmsg(self, msg, expected_ingest_id):
        src = webhook_event["payload"]["object"]
        print("gdl")
        print(msg)
        self.assertEqual(src['uuid'], msg['uuid'])
        self.assertEqual(expected_ingest_id, msg['ingest_id'])

    def test_messageConstruction(self):
        rabbit = Rabbit(self.config, self.zoom)
        msg = rabbit._construct_rabbit_msg(webhook_event['payload']['object']['uuid'], 12345)
        self.assert_rabbitmsg(msg, 12345)

    @unittest.skip("FIXME: We need to mock the internals of the Pika connection before this will work")
    def test_sendingMessages(self):
        rabbit = Rabbit(self.config, self.zoom)
        rabbit.send_rabbit_msg = MagicMock(return_value=None)
        mock = rabbit.send_rabbit_msg
        message = rabbit._construct_rabbit_msg(webhook_event['payload']['object']['uuid'], 12345)
        rabbit.send_rabbit_msg(webhook_event['payload']['object']['uuid'], 12345)

        rabbit.send_rabbit_msg.assert_called_once()
        #sent is now the UUID, rather than the whole message, so it's kinda pointless to test against
        sent = rabbit.send_rabbit_msg.call_args[0][0]
        self.assert_rabbitmsg(sent, 12345)


if __name__ == '__main__':
    unittest.main()
