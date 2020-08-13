import json
import unittest
from unittest.mock import MagicMock, patch
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom

webhook_event = None
with open('example-recording-completed.json', 'r') as webhook:
    webhook_event = json.loads(webhook.read())

class TestRabbit(unittest.TestCase):

    def setUp(self):
        self.config = {"Rabbit": {"host": "localhost", "user": "test_user", "password": "test_password" }}
        zoom_config={"JWT": {"Key": "test_key", "Secret": "test_secret" }}
        self.zoom = Zoom(zoom_config)

    def tearDown(self):
        pass

    def test_noConfig(self):
        with self.assertRaises(TypeError):
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

    @unittest.skip("This isn't actually checked yet")
    def test_badZoom(self):
        Rabbit(self.config, None)

    def test_goodConfig(self):
        rabbit = Rabbit(self.config, self.zoom)
        self.assertEqual(self.config["Rabbit"]["host"], rabbit.rabbit_url)
        self.assertEqual(self.config["Rabbit"]["user"], rabbit.rabbit_user)
        self.assertEqual(self.config["Rabbit"]["password"], rabbit.rabbit_pass)

    def ae(self, a, b, key):
        self.assertEqual(a[key], b[key])

    def assert_rabbitmsg(self, msg):
        src = webhook_event["payload"]["object"]
        self.ae(src, msg, "uuid")
        self.ae(src, msg, "topic")
        self.ae(src, msg, "start_time")
        self.ae(src, msg, "duration")
        self.ae(src, msg, "host_id")
        self.assertEqual(src["id"], msg["zoom_series_id"])
        #TODO: recording_files, token, creator

    @patch("zingest.rabbit.Rabbit._send_rabbit_msg")
    def test_messageConstruction(self, _send_rabbit_msg):
        rabbit = Rabbit(self.config, self.zoom)
        _send_rabbit_msg.side_effect = self.assert_rabbitmsg
        rabbit.send_rabbit_msg(payload=webhook_event["payload"], token="token")


if __name__ == '__main__':
    unittest.main()