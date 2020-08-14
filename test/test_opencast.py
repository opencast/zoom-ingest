import json
import unittest
from unittest.mock import MagicMock, patch
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast

webhook_event = None
with open('example-recording-completed.json', 'r') as webhook:
    webhook_event = json.loads(webhook.read())
series_json = None
with open('example-adminui-series.json', 'r') as series:
    series_json = json.loads(series.read())

class TestOpencast(unittest.TestCase):

    def setUp(self):
        self.config = {"Opencast": {"Url": "localhost", "User": "test_user", "Password": "test_password" },
                       "Rabbit": {"host": "localhost", "user": "test_user", "password": "test_password" },
                       "JWT": {"Key": "test_key", "Secret": "test_secret" }}
        self.zoom = Zoom(self.config)
        self.rabbit = Rabbit(self.config, self.zoom)
        self.rabbit.start_consuming_rabbitmsg = MagicMock(return_value=None)

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

    def test_badRabbit(self):
        with self.assertRaises(TypeError):
            Opencast(self.config, None)

    def test_goodConfig(self):
        self.rabbit = Rabbit(self.config, None)
        self.rabbit.start_consuming_rabbitmsg = MagicMock(return_value=None)
        self.opencast = Opencast(self.config, self.rabbit)

        self.assertEqual(self.config["Opencast"]["Url"], self.opencast.url)
        self.assertEqual(self.config["Opencast"]["User"], self.opencast.user)
        self.assertEqual(self.config["Opencast"]["Password"], self.opencast.password)
        self.rabbit.start_consuming_rabbitmsg.assert_called_once_with(self.opencast.rabbit_callback)

    def test_calback(self):
        self.opencast = Opencast(self.config, self.rabbit)
        self.fail("This test needs writing")

    def test_parseQueue(self):
        rabbit_msg = self.rabbit._construct_rabbit_msg(webhook_event['payload'], webhook_event['download_token'])

        self.opencast = Opencast(self.config, self.rabbit)
        self.opencast._do_download = MagicMock(return_value=None)
        self.opencast.parse_queue(json.dumps(rabbit_msg))

        url = webhook_event['payload']['object']['recording_files'][0]['download_url'] + '/?access_token=' + webhook_event['download_token']
        id = webhook_event['payload']['object']['recording_files'][0]['id']
        self.fetch = self.opencast._do_download.call_args[0]
        self.assertEqual(url, self.fetch[0])
        self.assertEqual(id + ".mp4", self.fetch[1])

    def test_ocUpload(self):
        self.opencast = Opencast(self.conf, self.rabbit)
        self.opencast._do_get = MagicMock(return_value=series_json)

        #TODO: Break this test into two, since we might be creating the series, or we might not be
        self.opencast.oc_upload(creator, title, rec_id)

if __name__ == '__main__':
    unittest.main()
