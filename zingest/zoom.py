import functools
import logging
from datetime import datetime, timedelta
from random import random
from urllib.parse import quote
import time

import jwt
from zoomus import ZoomClient

from zingest import db
from zingest.common import BadWebhookData, NoMp4Files, get_config


class Zoom:

    JWT_HEADERS = { "alg": "HS256", "typ": "JWT" }

    def __init__(self, config):
        self.logger = logging.getLogger(__name__)

        self.api_key = get_config(config, 'JWT', 'Key')
        self.api_secret = get_config(config, 'JWT', 'Secret')
        self.logger.debug(f"Init with Zoom API key {self.api_key[0:3]}XXX{self.api_key[-3:]}")
        self.zoom_client = None
        self.zoom_client_exp = None
        self.jwt_token = None
        self.jwt_token_exp = None

    def _validate_object_fields(self, required_object_fields, obj):
        try:
            for field in required_object_fields:
                if field not in obj.keys():
                    raise BadWebhookData(f"Missing required object field '{field}'. Keys found: {obj.keys()}")
        except Exception as e:
            raise BadWebhookData("Unrecognized object format. {}".format(e))

    def validate_recording_renamed(self, payload):
        required_payload_fields = [
            "old_object",
            "object"
        ]
        required_object_fields = [
            "uuid",
            "topic"
        ]
        self._validate_object_fields(required_payload_fields, payload)
        self._validate_object_fields(required_object_fields, payload['old_object'])
        self._validate_object_fields(required_object_fields, payload['object'])

    def validate_recording_payload(self, payload):

        required_payload_fields = [
            "object"
        ]
        self._validate_object_fields(required_payload_fields, payload)

    def validate_recording_object(self, obj):
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
            self._validate_object_fields(required_object_fields, obj)

            files = obj["recording_files"]
            self.logger.debug(f"Found {len(files)} potential files")

            # make sure there's some mp4 files in here somewhere
            found_mp4 = False
            for file in files:
                self._validate_object_fields(required_file_fields, file)
                if file["file_type"].lower() != "mp4":
                    continue
                found_mp4 = True
                if file["status"].lower() != "completed":
                    raise BadWebhookData(f"File with incomplete status {file['status']}")
            if not found_mp4:
                raise NoMp4Files("No mp4 files in recording data")
            self.logger.debug(f"Event {obj['uuid']} passed validation!")
        except NoMp4Files:
            # let these bubble up as we handle them differently depending
            # on who the caller is
            raise
        except Exception as e:
            raise BadWebhookData("Unrecognized object format. {}".format(e))

    def _parse_recording_files(self, payload):
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

    def get_recording_files(self, rec_id):
        rec = self.get_recording(rec_id)
        self.validate_recording_object(rec)
        return self._parse_recording_files(rec)

    def create_recording_from_uuid(self, uuid):
        data = self.get_recording(uuid)
        return self._create_recording_from_data(data)

    def _create_recording_from_data(self, data):
        required_data = { x: data[x] for x in data if x in ('uuid', 'host_id', 'start_time', 'topic', 'duration') }
        return db.create_recording(required_data)

    def get_download_token(self):
        if not self.jwt_token or datetime.utcnow() + timedelta(seconds=1) > self.jwt_token_exp:
            #Expires after 5 minutes
            self.jwt_token_exp = datetime.utcnow() + timedelta(minutes=5)
            payload = {"iss": self.api_key, "exp": self.jwt_token_exp}
            self.jwt_token = jwt.encode(payload, self.api_secret, algorithm='HS256', headers=Zoom.JWT_HEADERS)
            #PyJWT 2.0 and newer return a string, older versions need to be decoded
            if type(self.jwt_token) is not str:
                self.jwt_token = self.jwt_token.decode("utf-8")
        return self.jwt_token

    def _get_zoom_client(self):
        #Note: There is a ZoomClient.refresh_tokens(), but this appears to be *broken* somehow.  Creating a new client works though...
        if not self.zoom_client or datetime.utcnow() + timedelta(seconds=1) > self.zoom_client_exp:
            self.logger.debug("Creating new zoom client")
            # zoom client library set this interval, so we
            self.zoom_client_exp = datetime.utcnow() + timedelta(hours=1)
            self.zoom_client = ZoomClient(self.api_key, self.api_secret)
        return self.zoom_client

    def _make_zoom_request(self, function, args, attempts=5):
        self.logger.debug(f"Making zoom call to { function.__qualname__ } with { args }")
        resp = function(**args)
        if 400 <= resp.status_code < 500:
            if resp.status_code == 429:
                # we hit the Zoom API rate limit,
                # see https://marketplace.zoom.us/docs/api-reference/rate-limits
                if attempts > 0:
                    self.logger.warning(
                        f"Calling {function.__qualname__} failed due to Zoom API rate limitation. "
                        f"Retry {attempts} more times.")
                    time.sleep(random(1, 5))
                    return self._make_zoom_request(self, function, args, attempts=(attempts - 1))
            resp.raise_for_status()
        return resp.json()

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

    @functools.lru_cache(maxsize=32)
    @db.with_session
    def get_user(dbs, self, email_or_id):
        fn = self._get_zoom_client().user.get
        args = {'id': email_or_id}
        return self._make_zoom_request(fn, args)

    def ensure_user_in_db(self, email_or_id):
        data = self.get_user(email_or_id)
        db.ensure_user(data['id'], data['first_name'], data['last_name'], data['email'])
        return data


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

    def get_user_recordings(self, user_id, from_date=None, to_date=None, page_size=None, min_duration=0):
        #Get the list of recordings from Zoom
        zoom_results = self._get_user_recordings(user_id, from_date, to_date, page_size)
        if 'meetings' not in zoom_results:
            self.logger.warning("Got a response from Zoom, but data was invalid")
            self.logger.debug(f"{ zoom_results }")
            return []
        self.ensure_user_in_db(user_id)
        zoom_meetings = zoom_results['meetings']
        for meeting in zoom_meetings:
            db.create_recording_if_needed(meeting)
        self.logger.debug(f"Got a list of { len(zoom_meetings) } meetings")
        return self._build_renderable_event_list(zoom_meetings, min_duration)

    @db.with_session
    def _get_statuses_for(dbs, self, meetings):
        self.logger.debug(f"Building renderable objects for meetings: { meetings }")
        existing_db_ingests = dbs.query(db.Recording.uuid, db.Ingest.status) \
            .filter(db.Recording.uuid.in_(meetings), db.Recording.uuid == db.Ingest.uuid) \
            .distinct(db.Recording.uuid) \
            .all()
        existing_data = {}
        for uuid, status in existing_db_ingests:
            if existing_data.get(uuid) and (existing_data.get(uuid) != 2 or status != 2):
                existing_data[uuid] = 1
            else:
                existing_data[uuid] = status
        self.logger.debug(f"There are { len(existing_data) } db records matching those IDs")
        return existing_data


    def get_recordings_from_db(self, query, min_duration=0):
        db_recordings = db.find_recordings_matching(query)
        print(f"Found { len(db_recordings) } matching recordings")
        existing_data = self._get_statuses_for([ x.get_rec_id() for x in db_recordings ])

        renderable = []
        for rec in db_recordings:
            rec_uuid = rec.get_rec_id()
            render = rec.serialize()
            render['too_short'] =  int(render['duration']) < int(min_duration)
            render['status'] = db.Status.str(existing_data[rec_uuid]) if rec_uuid in existing_data else db.Status.str(db.Status.NEW)
            renderable.append(render)
        return renderable

    def _build_renderable_event_list(self, zoom_meetings, min_duration=0):
        zoom_rec_meeting_ids = [ x['uuid'] for x in zoom_meetings ]
        existing_data = self._get_statuses_for(zoom_rec_meeting_ids)

        renderable = []
        for element in zoom_meetings:
            rec_uuid = element['uuid']
            status = db.Status.str(existing_data[rec_uuid]) if rec_uuid in existing_data else db.Status.str(db.Status.NEW)
            host = self.get_user_name(element['host_id'])
            item = {
                'id': rec_uuid,
                'title': element['topic'],
                'date': element['start_time'][:10],
                'time': element['start_time'][11:-1],
                'duration': element['duration'],
                'host': host,
                'status': status,
                'too_short': int(element['duration']) < int(min_duration)
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

    # Do not cache result as the next_page_token expire after 15 minutes
    def search_user(self, search_key, page_size=25, next_page_token=None):
        # TODO: use zoom client implementation
        #       see https://github.com/prschmid/zoomus/pull/146
        params = {
            'search_key': search_key,
            'query_presence_status': 'false',
            'page_size': page_size,
        }
        if next_page_token and len(next_page_token) > 0:
            params['next_page_token'] = next_page_token
        self.logger.debug(f"Search zoom contacts with params: " + str(params))
        return self._get_zoom_client().get_request("/contacts", params=params)
