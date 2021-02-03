import json
import unittest
from unittest.mock import MagicMock, patch
from zingest.common import BadWebhookData, NoMp4Files
from zingest.zoom import Zoom

class TestZoom(unittest.TestCase):

    def setUp(self):
        self.config={"JWT": {"Key": "test_key", "Secret": "test_secret" }}
        with open('test/resources/zoom/example-recording-completed.json', 'r') as webhook:
            self.event = json.loads(webhook.read())['payload']

    def tearDown(self):
        pass

    def test_noConfig(self):
        with self.assertRaises(KeyError):
          Zoom(None)

    def test_missingZoomConfig(self):
        del self.config["JWT"]
        with self.assertRaises(KeyError):
          Zoom(self.config)

    def test_missingKeyConfig(self):
        del self.config["JWT"]["Key"]
        with self.assertRaises(KeyError):
          Zoom(self.config)

    def test_missingSecretConfig(self):
        del self.config["JWT"]["Secret"]
        with self.assertRaises(KeyError):
          Zoom(self.config)

    def test_badKeyConfig(self):
        self.config["JWT"]["Key"] = None
        with self.assertRaises(ValueError):
          Zoom(self.config)

    def test_badSecretConfig(self):
        self.config["JWT"]["Secret"] = None
        with self.assertRaises(ValueError):
          Zoom(self.config)

    def test_goodConfig(self):
        zoom = Zoom(self.config)
        self.assertEqual(self.config["JWT"]["Key"], zoom.api_key)
        self.assertEqual(self.config["JWT"]["Secret"], zoom.api_secret)

    def validate_bad_data(self, payload):
        zoom = Zoom(self.config)
        with self.assertRaises(BadWebhookData):
            zoom.validate_payload(payload)
            zoom.validate_object(payload['object'])

    def validate_no_mp4(self, payload):
        zoom = Zoom(self.config)
        with self.assertRaises(NoMp4Files):
            zoom.validate_payload(payload)
            zoom.validate_object(payload['object'])

    def test_validate_mising_object(self):
        del self.event['object']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_id(self):
        del self.event['object']['id']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_uuid(self):
        del self.event['object']['uuid']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_host_id(self):
        del self.event['object']['host_id']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_topic(self):
        del self.event['object']['topic']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_start_time(self):
        del self.event['object']['start_time']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_duration(self):
        del self.event['object']['duration']
        self.validate_bad_data(self.event)

    def test_validate_mising_object_recordingFiles(self):
        del self.event['object']['recording_files']
        self.validate_bad_data(self.event)

    def test_validate_blank_recording_files(self):
        self.event['object']['recording_files'] = []
        self.validate_no_mp4(self.event)

    def test_validate_no_mp4_files(self):
        #Take a look at the example file, only the second file is an MP4
        del self.event['object']['recording_files'][0]
        self.validate_no_mp4(self.event)

    def test_validate_missing_file_type(self):
        #We're removing the filetype from the *non mp4* file since the presence of an mp4 is checked elsewhere
        del self.event['object']['recording_files'][1]['file_type']
        self.validate_bad_data(self.event)

    def test_validate_missing_file_id(self):
        del self.event['object']['recording_files'][0]['id']
        self.validate_bad_data(self.event)

    def test_validate_missing_file_start(self):
        del self.event['object']['recording_files'][0]['recording_start']
        self.validate_bad_data(self.event)

    def test_validate_missing_file_end(self):
        del self.event['object']['recording_files'][0]['recording_end']
        self.validate_bad_data(self.event)

    def test_validate_missing_file_url(self):
        del self.event['object']['recording_files'][0]['download_url']
        self.validate_bad_data(self.event)

    def test_validate_missing_recording_type(self):
        del self.event['object']['recording_files'][0]['recording_type']
        self.validate_bad_data(self.event)

    def test_validate_missing_status(self):
        del self.event['object']['recording_files'][0]['status']
        self.validate_bad_data(self.event)

    def test_validate_bad_status(self):
        self.event['object']['recording_files'][0]['status'] = "incorrect-status"
        self.validate_bad_data(self.event)

    def test_valid_data(self):
        zoom = Zoom(self.config)
        zoom.validate_payload(self.event)
        zoom.validate_object(self.event['object'])

    def test_parse_recordings(self):
        zoom = Zoom(self.config)
        recordings = zoom.parse_recording_files(self.event)
        self.assertEqual(1, len(recordings))

        recording = recordings[0]
        self.assertEqual(recording["recording_id"], self.event["object"]["recording_files"][0]["id"])
        self.assertEqual(recording["recording_start"], self.event["object"]["recording_files"][0]["recording_start"])
        self.assertEqual(recording["recording_end"], self.event["object"]["recording_files"][0]["recording_end"])
        self.assertEqual(recording["download_url"], self.event["object"]["recording_files"][0]["download_url"])
        self.assertEqual(recording["file_type"], self.event["object"]["recording_files"][0]["file_type"])
        self.assertEqual(recording["recording_type"], self.event["object"]["recording_files"][0]["recording_type"])

    def test_parse_recordings(self):
        zoom = Zoom(self.config)
        del self.event['object']['recording_files'][0]
        recordings = zoom.parse_recording_files(self.event)
        self.assertEqual(0, len(recordings))

    @unittest.skip("FIXME: Zoom library users requests in the backend, we should mock the responses and test zoom.py better")
    def test_parse_recordings(self):
        zoom = Zoom(self.config)
        creator = zoom.get_recording_creator(self.event)
        self.fail("This test is testing something that's hardcoded!")


if __name__ == '__main__':
    unittest.main()
