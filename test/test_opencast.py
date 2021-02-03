import os
import tempfile
import json
import unittest
import requests_mock
import re
import xmltodict
from unittest.mock import MagicMock, patch
from logger import init_logger
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast
import tempfile
import shutil
import zingest.db

webhook_event = None
with open('test/resources/zoom/example-recording-completed.json', 'r') as webhook:
    webhook_event = json.loads(webhook.read())
series_json = None
with open('test/resources/opencast/series.json', 'r') as series:
    series_json = series.read()
acl_json = None
with open('test/resources/opencast/acls.json', 'r') as acl:
    acl_json = acl.read()
themes_json = None
with open('test/resources/opencast/themes.json', 'r') as themes:
    themes_json = themes.read()
wfs_json = None
with open('test/resources/opencast/workflows.json', 'r') as wfs:
    wfs_json = wfs.read()
rabbit_msg = None
with open('test/resources/internal/rabbit_msg.json', 'r') as rabbit:
    rabbit_msg = rabbit.read()
ingest = dict()
for event in ("add-dc", "add-ethterms", "add-security", "add-track", "create-mp", "ingest"):
    with open(f'test/resources/opencast/{ event }.xml', 'r') as xml:
        ingest[event] = xml.read()


class TestOpencast(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.config = {"Opencast": {"Url": "http://localhost", "User": "test_user", "Password": "test_password" },
                       "Rabbit": {"host": "http://localhost", "user": "test_user", "password": "test_password" },
                       "JWT": {"Key": "test_key", "Secret": "test_secret" },
                       "Filter": {'workflow_filter': None },
                       "TESTING": {"IN_PROGRESS_ROOT": self.tempdir}}
        self.zoom = Zoom(self.config)
        self.rabbit = Rabbit(self.config, self.zoom)
        self.rabbit.start_consuming_rabbitmsg = MagicMock(return_value=None)
        self.fd, self.dbfile = tempfile.mkstemp()
        zingest.db.init({'Database': {'database': 'sqlite:///' + self.dbfile}})

    def createOC(self, config, rabbit, zoom):
        return Opencast(config, rabbit, zoom)

    def tearDown(self):
        os.close(self.fd)
        os.remove(self.dbfile)
        shutil.rmtree(self.tempdir)

    def test_noConfig(self):
        with self.assertRaises(TypeError):
            Opencast(None, self.rabbit, self.zoom)

    def test_missingOpencastConfig(self):
        del self.config["Opencast"]
        with self.assertRaises(KeyError):
            Opencast(self.config, self.rabbit, self.zoom)

    def test_missingUrlConfig(self):
        del self.config["Opencast"]["Url"]
        with self.assertRaises(KeyError):
            Opencast(self.config, self.rabbit, self.zoom)

    def test_missingUserConfig(self):
        del self.config["Opencast"]["User"]
        with self.assertRaises(KeyError):
            Opencast(self.config, self.rabbit, self.zoom)

    def test_missingPassConfig(self):
        del self.config["Opencast"]["Password"]
        with self.assertRaises(KeyError):
            Opencast(self.config, self.rabbit, self.zoom)

    def test_NoRabbit(self):
        with self.assertRaises(TypeError):
            Opencast(self.config, None, self.zoom)

    def test_badRabbit(self):
        with self.assertRaises(TypeError):
            Opencast(self.config, "", self.zoom)

    def test_badZoom(self):
        with self.assertRaises(TypeError):
            Opencast(self.config, self.rabbit, "")

    def create_mock_opencast(self, mocker):
        return self.create_mock_opencast_with_params(mocker, acl_json, themes_json, wfs_json, series_json)

    def create_mock_opencast_with_params(self, m, acl_text, themes_text, wfs_text, series_text):
        acls = m.get('//localhost/acl-manager/acl/acls.json', text=acl_text)
        themes = m.get('//localhost/admin-ng/themes/themes.json?limit=100', text=themes_text)
        wfs = m.get(re.compile("//localhost/api/workflow-definitions"), text=wfs_text)
        series = m.get('//localhost/series/series.json?count=100', text=series_text)
        create = m.get("//localhost/ingest/createMediaPackage", text=ingest['create-mp'])
        attach = m.post("//localhost/ingest/addAttachment", text=ingest['add-security'])
        catalog = m.post("//localhost/ingest/addDCCatalog", text=ingest['add-dc'])
        track = m.post("//localhost/ingest/addTrack", text=ingest['add-track'])
        start = m.post(re.compile("//localhost/ingest/ingest/"), text=ingest['ingest'])

        #NB: The order of declaration is vital here.  When matching, requests-mock goes in *reverse declared order*.
        #If the general regex were declared last it would cover *all* downloads, rather than just the ones *not* matching other regexes
        #This is faking *most* zoom downloads
        m.get(re.compile("us02web.zoom.us/rec/download"), body="")
        #This is faking a specific file (ie, the one in resources/rabbit_msg.json
        regex = webhook_event['payload']['object']['recording_files'][0]['download_url'][8:] + "\/\?access_token=.*"
        download = m.get(re.compile(regex), body="")


        opencast = Opencast(self.config, self.rabbit, self.zoom)

        return opencast, [ acls, themes, wfs, series ] , {'create': create, 'attach': attach, 'catalog': catalog, 'track': track, 'start': start, 'download': download }

    def assert_called(self, thing, expected_count):
        self.assertTrue(thing.called)
        self.assertEqual(expected_count, thing.call_count)

    @requests_mock.Mocker()
    def test_goodConfig(self, mocker):
        opencast, startups, _ = self.create_mock_opencast(mocker)

        self.assertEqual(self.config["Opencast"]["Url"], opencast.url)
        self.assertEqual(self.config["Opencast"]["User"], opencast.user)
        self.assertEqual(self.config["Opencast"]["Password"], opencast.password)
        self.assertTrue(startups[0].called) #acls
        self.assertTrue(startups[1].called) #themes
        self.assertTrue(startups[2].called) #workflows
        self.assertTrue(startups[3].called) #series

    @requests_mock.Mocker()
    def test_callback(self, mocker):
        opencast, _, mock_dict = self.create_mock_opencast(mocker)

        opencast.rabbit_callback("", "", rabbit_msg)

        self.assert_called(mock_dict['download'], 1)
        self.assert_called(mock_dict['create'], 1) #created
        self.assert_called(mock_dict['attach'], 1) #attachment
        self.assert_called(mock_dict['catalog'], 2) #ep dc catalog and ethterms (which is empty, but still present)
        self.assert_called(mock_dict['track'], 1) #track
        self.assert_called(mock_dict['start'], 1) #ingest/ingest

        db = zingest.db.get_session()
        db_record = db.query(zingest.db.Recording).one_or_none()
        self.assertEqual("b1d7f8d2-91fd-4710-8c63-17e3e14749a9", db_record.get_mediapackage_id())
        self.assertEqual("5267", db_record.get_workflow_id())

    #@requests_mock.Mocker()
    def asdftest_ocUpload(self, mocker):
        opencast, _, mock_dict = self.create_mock_opencast(mocker)
        wfdict = xmltodict.parse(ingest['ingest']) #start is the response ingest.xml, which is what the mock request returns
        mpid, wfInstId = opencast.oc_upload("fake_uuid", "test/resources/fake", acl_id="test_acl", workflow_id="test_workflow")#, some="other_param")

        self.assert_called(mock_dict['create'], 1) #created
        self.assert_called(mock_dict['attach'], 1) #attachment
        self.assert_called(mock_dict['catalog'], 2) #ep dc catalog and ethterms (which is empty, but still present)
        self.assert_called(mock_dict['track'], 1) #track
        self.assert_called(mock_dict['start'], 1) #ingest/ingest

        self.assertEqual(mpid, wfdict['wf:workflow']['mp:mediapackage']['@id'])
        self.assertEqual(wfInstId, wfdict['wf:workflow']['@id'])


if __name__ == '__main__':
    unittest.main()
