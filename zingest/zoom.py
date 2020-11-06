import functools
import logging
from datetime import datetime, timedelta
from urllib.parse import quote
import time

import jwt
from zoomus import ZoomClient

from zingest import db
from zingest.common import BadWebhookData, NoMp4Files


class Zoom:

    JWT_HEADERS = { "alg": "HS256", "typ": "JWT" }

    def __init__(self, config):
        self.logger = logging.getLogger(__name__)

        self.api_key = config['JWT']['Key']
        self.api_secret = config['JWT']['Secret']
        if self.api_key:
            self.logger.debug(f"Init with Zoom API key {self.api_key[0:3]}XXX{self.api_key[-3:]}")
        else:
            raise ValueError("Zoom API key not set")
        if not self.api_secret:
            raise ValueError("Zoom API secrete not set")
        self.zoom_client = None
        self.zoom_client_exp = None
        self.jwt_token = None
        self.jwt_token_exp = None

    def validate_payload(self, payload):

        required_payload_fields = [
            "object"
        ]
        try:
            for field in required_payload_fields:
                if field not in payload.keys():
                    raise BadWebhookData(f"Missing required payload field '{field}'. Keys found: {payload.keys()}")
        except Exception as e:
            raise BadWebhookData("Unrecognized payload format. {}".format(e))

    def validate_object(self, obj):
        required_object_fields = [
            "id",  # zoom series id
            "uuid",  # unique id of the meeting instance,
            "host_id",
            "topic",
            "start_time",
            "duration",  # duration in minutes
            "recording_files"
        ]
        required_file_fields = [
            "id",  # unique id for the file
            "recording_start",
            "recording_end",
            "download_url",
            "file_type",
            "file_size",
            "recording_type",
            "status"
        ]

        try:
            for field in required_object_fields:
                if field not in obj.keys():
                    raise BadWebhookData(f"Missing required object field '{field}'. Keys found: {obj.keys()}")

            files = obj["recording_files"]
            self.logger.debug(f"Found {len(files)} potential files")

            # make sure there's some mp4 files in here somewhere
            mp4_files = any(x["file_type"].lower() == "mp4" for x in files)
            if not mp4_files:
                raise NoMp4Files("No mp4 files in recording data")

            for file in files:
                if "file_type" not in file:
                    raise BadWebhookData("Missing required file field 'file_type'")
                if file["file_type"].lower() != "mp4":
                    continue
                for field in required_file_fields:
                    if field not in file.keys():
                        raise BadWebhookData(f"Missing required file field '{field}'")
                if file["status"].lower() != "completed":
                    raise BadWebhookData(f"File with incomplete status {file['status']}")
            self.logger.debug(f"Event {obj['uuid']} passed validation!")
        except NoMp4Files:
            # let these bubble up as we handle them differently depending
            # on who the caller is
            raise
        except Exception as e:
            raise BadWebhookData("Unrecognized object format. {}".format(e))

    def parse_recording_files(self, payload):
        recording_files = []
        for file in payload["recording_files"]:
            if file["file_type"].lower() == "mp4":
                recording_files.append({
                    "recording_id": file["id"],
                    "recording_start": file["recording_start"],
                    "recording_end": file["recording_end"],
                    "download_url": file["download_url"],
                    "file_type": file["file_type"],
                    "file_size": file["file_size"],
                    "recording_type": file["recording_type"]
                })
        return recording_files

    def get_download_token(self):
        if not self.jwt_token or datetime.utcnow() + timedelta(seconds=1) > self.jwt_token_exp:
            #Expires after 5 minutes
            self.jwt_token_exp = datetime.utcnow() + timedelta(minutes=5)
            payload = {"iss": self.api_key, "exp": self.jwt_token_exp}
            self.jwt_token = jwt.encode(payload, self.api_secret, algorithm='HS256', headers=Zoom.JWT_HEADERS).decode("utf-8")
        return self.jwt_token

    def _get_zoom_client(self):
        #Note: There is a ZoomClient.refresh_tokens(), but this appears to be *broken* somehow.  Creating a new client works though...
        if not self.zoom_client or datetime.utcnow() + timedelta(seconds=1) > self.zoom_client_exp:
            self.logger.debug("Creating new zoom client")
            # zoom client library set this interval, so we
            self.zoom_client_exp = datetime.utcnow() + timedelta(hours=1)
            self.zoom_client = ZoomClient(self.api_key, self.api_secret)
        return self.zoom_client

    def _make_zoom_request(self, function, args, count=5, accept=[200]):
        self.logger.debug(f"Making zoom call to { function.__qualname__ } with { args }")
        counter = 0
        while counter < count:
            try:
                resp = function(**args)
                if resp.status_code not in accept:
                    self.logger.debug(
                        f"Attempt { counter + 1 } Call to { function.__qualname__ } failed with "
                        f"http code { resp.status_code }, retrying { count - counter } more times")
                    counter += 1
                    time.sleep(counter * 2)
                elif 400 <= resp.status_code < 500:
                    self.logger.debug(
                        f"Attempt { counter + 1 } Call to { function.__qualname__ } failed with "
                        f"http code { resp.status_code }, *NOT* retrying")
                    return {}
                else:
                    return resp.json()
            except Exception as e:
                self.logger.exception(
                    f"Attempt { counter + 1 } Call to { function.__qualname__ } threw this exception, "
                    f"retrying { count - counter } more times")
                counter += 1
                time.sleep(counter * 2)
        self.logger.error(f"Giving up calling { function }, failed 5 times")
        return {}

    def list_available_users(self, page):
        #300 is the maximum page size per the docs
        self.logger.debug(f"Fetching 300 users, page { page }")
        fn = self._get_zoom_client().user.list
        args = {'page_size': 300, 'page_number': page}
        return self._make_zoom_request(fn, args)

    def get_user_name(self, user_id_or_email):
        self.logger.debug(f"Looking up plaintext name for { user_id_or_email }")
        user = self.get_user(user_id_or_email)
        return f"{ user['last_name'] }, { user['first_name'] }"

    def get_recording_creator(self, payload):
        return payload['host_id']
        #RATELIMIT: 30/80 req/s

    @functools.lru_cache(maxsize=32)
    def get_user(self, email_or_id):
        fn = self._get_zoom_client().user.get
        args = {'id': email_or_id}
        return self._make_zoom_request(fn, args)

    def get_user_email(self, user_id):
        self.logger.debug(f"Looking up email for { user_id }")
        user = self.get_user(user_id)
        return user['email']

    #We explicitly do not want to cache here since someone might want to know about their recordings *now* rather than when the cache lets them
    def _get_user_recordings(self, user_id, from_date=None, to_date=None, page_size=None):
        if None == from_date:
            from_date = datetime.utcnow() - timedelta(days = 7)
        if None == to_date:
            to_date = datetime.utcnow()
        if None == page_size:
            page_size = 30
        #This defaults to 30 records / page -> appears to be 30 *meetings* per call.  We'll deal with paging later
        #RATELIMIT: 20/60 req/s
        params = {
            'user_id': user_id,
            'from': from_date.strftime('%Y-%m-%d'),
            'to': to_date.strftime('%Y-%m-%d'),
            'page_size': int(page_size),
            'trash_type': 'meeting_recordings',
            'mc': 'false'
        }
        fn = self._get_zoom_client().recording.list
        return self._make_zoom_request(fn, params)

    def get_user_recordings(self, user_id, from_date=None, to_date=None, page_size=None):
        #Get the list of recordings from Zoom
        zoom_results = self._get_user_recordings(user_id, from_date, to_date, page_size)
        if 'meetings' not in zoom_results:
            self.logger.warning("Got a response from Zoom, but data was invalid")
            self.logger.debug(f"{ zoom_results }")
            return []
        zoom_meetings = zoom_results['meetings']
        self.logger.debug(f"Got a list of { len(zoom_meetings) } meetings")
        return self._build_renderable_event_list(zoom_meetings)

    @db.with_session
    def _build_renderable_event_list(dbs, self, zoom_meetings):
        zoom_rec_meeting_ids = [ x['uuid'] for x in zoom_meetings ]
        self.logger.debug(f"Building renderable objects for zoom meetings: { zoom_rec_meeting_ids }")
        existing_db_recordings = dbs.query(db.Recording).filter(db.Recording.uuid.in_(zoom_rec_meeting_ids)).all()
        existing_data = { e.uuid: { 'status': e.status } for e in existing_db_recordings }
        self.logger.debug(f"There are { len(existing_data) } db records matching those IDs")
        renderable = []
        for element in zoom_meetings:
            rec_uuid = element['uuid']
            status = db.Status.str(existing_data[rec_uuid]['status']) if rec_uuid in existing_data else db.Status.str(db.Status.NEW)
            email = element['host_email'] if 'host_email' in element else self.get_user_email(element['host_id'])
            host = self.get_user_name(email)
            item = {
                'id': rec_uuid,
                'title': element['topic'],
                'date': element['start_time'],
                'duration': element['duration'],
                'url': element['share_url'],
                'email': email,
                'host': host,
                'status': status
            }
            renderable.append(item)
        return renderable

    @functools.lru_cache(maxsize=32)
    def get_recording(self, recording_id):
        if not recording_id:
            raise ValueError('Recording ID not set or is empty.')
        #RATELIMIT: 30/80 req/s
        self.logger.debug(f"Getting recording { recording_id }")
        fn = self._get_zoom_client().recording.get
        # If recording_id starts with / or contains //, we must **double encode** the recording_id
        # before making an API request.
        # See https://marketplace.zoom.us/docs/api-reference/zoom-api/cloud-recording/recordingget
        if recording_id.startswith('/') or '//' in recording_id:
            args = { 'meeting_id': quote(quote(recording_id, safe=''), safe='') }
        else:
            args = { 'meeting_id': recording_id }
        return self._make_zoom_request(fn, args)

    def get_renderable_recording(self, recording_id):
        recording = self.get_recording(recording_id)
        #We pass in a list of one, so we know that the returned list is of size 1
        return self._build_renderable_event_list([ recording ])[0]
