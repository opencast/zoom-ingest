import json
import unittest
from unittest.mock import MagicMock, patch
from zingest.rabbit import Rabbit
from zingest.opencast import Opencast

webhook_event = None
with open('example-recording-completed.json', 'r') as webhook:
    webhook_event = json.loads(webhook.read())

class TestOpencast(unittest.TestCase):

    def setUp(self):
        self.config = {"Opencast": {"Url": "localhost", "User": "test_user", "Password": "test_password" },
                       "Rabbit": {"host": "localhost", "user": "test_user", "password": "test_password" }}

    def tearDown(self):
        pass

    def test_noConfig(self):
        with self.assertRaises(TypeError):
            Opencast(None, None)

    def test_missingOpencastConfig(self):
        del self.config["Opencast"]
        with self.assertRaises(KeyError):
            Opencast(self.config, None)

    def test_missingUrlConfig(self):
        del self.config["Opencast"]["Url"]
        with self.assertRaises(KeyError):
            Opencast(self.config, None)

    def test_missingUserConfig(self):
        del self.config["Opencast"]["User"]
        with self.assertRaises(KeyError):
            Opencast(self.config, None)

    def test_missingPassConfig(self):
        del self.config["Opencast"]["Password"]
        with self.assertRaises(KeyError):
            Opencast(self.config, None)

    @unittest.skip("This isn't actually checked yet")
    def test_badRabbit(self):
        Opencast(self.config, None)

    def test_goodConfig(self):
        #TODO: THis should be a mock
        self.rabbit = Rabbit(self.config, None)
        self.opencast = Opencast(self.config, self.rabbit)

        self.assertEqual(self.config["Opencast"]["Url"], self.opencast.url)
        self.assertEqual(self.config["Opencast"]["User"], self.opencast.user)
        self.assertEqual(self.config["Opencast"]["Password"], self.opencast.password)


if __name__ == '__main__':
    unittest.main()
