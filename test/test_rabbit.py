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

    @patch("zingest.rabbit.Rabbit._send_rabbit_msg")
    def test_messageConstruction(self, _send_rabbit_msg):
        rabbit = Rabbit(self.config, self.zoom)
        _send_rabbit_msg.side_effect = lambda x: None
        rabbit.send_rabbit_msg(payload=webhook_event["payload"], token="token")


if __name__ == '__main__':
    unittest.main()
