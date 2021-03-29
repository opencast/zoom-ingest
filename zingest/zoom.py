import functools
import logging
from datetime import datetime, timedelta
from random import random
import urllib.parse
from urllib.parse import quote
import time
from requests import HTTPError

import jwt
from zoomus import ZoomClient

from zingest import db
from zingest.common import BadWebhookData, NoMp4Files, get_config


class Zoom:

    JWT_HEADERS = { "alg": "HS256", "typ": "JWT" }

    def __init__(self, config):
        self.logger = logging.getLogger(__name__)

        self.api_key = get_config(config, 'Zoom', 'JWT_Key')
        self.api_secret = get_config(config, 'Zoom', 'JWT_Secret')
        self.logger.debug(f"Init with Zoom API key {self.api_key[0:3]}XXX{self.api_key[-3:]}")
        self.gdpr = get_config(config, 'Zoom', 'GDPR').lower() == 'true'
        self.logger.info(f"GDPR compliant endpoints in use: { self.gdpr }")
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
            if self.gdpr:
                self.zoom_client = ZoomClient(self.api_key, self.api_secret, base_uri=zoomus.client.API_BASE_URIS[zoomus.util.API_GDPR])
            else:
                self.zoom_client = ZoomClient(self.api_key, self.api_secret)
        return self.zoom_client

    def _cleaner(self, thing):
        if type(thing) is not dict:
            return
        for key, value in thing.items():
            if type(value) is str:
                thing[key] = value.replace('\u200b', '')  #Some users are somehow putting zero width spaces into their metadata, let's remove them
            elif type(value) is dict:
                self._cleaner(value)
            elif type(value) is list:
                for element in value:
                      self._cleaner(element)
            elif type(value) in [int, bool]:
                #Do nothing, don't need to filter this
                pass
            else:
                #this shouldn't happen, but let's log it anyway
                self.logger.debug(f'Unexpected type cleaning { thing }')
                self.logger.debug(f'{ type(value) }')
                pass

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
        resp_dict = resp.json()
        self._cleaner(resp_dict)
        return resp_dict

    def get_user_name(self, user_id_or_email):
        self.logger.debug(f"Looking up plaintext name for { user_id_or_email }")
        user = self.get_user(user_id_or_email)
        return self.format_user_name(user)

    def format_user_name(self, user):
        return f"{ user['last_name'] }, { user['first_name'] }"

    @functools.lru_cache(maxsize=32)
    def get_user(self, email_or_id):
        #Search the DB for the user
        existing_user = db.find_user_by_id_or_email(email_or_id)
        if existing_user:
            return existing_user.serialize()

        #Else, create the user in the db and return it
        fn = self._get_zoom_client().user.get
        args = {'id': email_or_id}
        user_json = self._make_zoom_request(fn, args)
        self.logger.debug(f"{ user_json }")
        existing_user = db.ensure_user(user_json)
        return existing_user.serialize()

    @functools.lru_cache(maxsize=32)
    def __get_user_from_zoom(self, email_or_id):
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

    @db.with_session
    def get_user_recordings(dbs, self, user_id, from_date=None, to_date=None, page_size=None, min_duration=0):
        #Get the list of recordings from Zoom
        zoom_results = self._get_user_recordings(user_id, from_date, to_date, page_size)
        if 'meetings' not in zoom_results:
            self.logger.warning("Got a response from Zoom, but data was invalid")
            self.logger.debug(f"{ zoom_results }")
            return []
        #get_user ensure the user is present in the DB
        self.get_user(user_id)
        zoom_meetings = zoom_results['meetings']
        for meeting in zoom_meetings:
            db.create_recording_if_needed(meeting)
        self.logger.debug(f"Got a list of { len(zoom_meetings) } meetings")
        #We're requerying the DB here since we need to get *all* of the recordings, not the ones we just created
        ids = [ m['uuid'] for m in zoom_meetings ]
        db_recordings = dbs.query(db.Recording).filter(db.Recording.uuid.in_(ids)).order_by(db.Recording.start_time.desc()).all()
        return self._build_renderable_event_list(db_recordings, min_duration)

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

    def get_recordings_from_db(self, title=None, user=None, date=None, min_duration=0):
        db_recordings = db.find_recordings_matching(title=title, user=user, date=date)
        self.logger.debug(f"Found { len(db_recordings) } matching recordings")
        return self._build_renderable_event_list(db_recordings, min_duration)

    def _build_renderable_event_list(self, db_recordings, min_duration=0):
        existing_data = self._get_statuses_for([ meet.get_rec_id() for meet in db_recordings ])

        renderable = []
        for rec in db_recordings:
            rec_uuid = rec.get_rec_id()
            render = rec.serialize()
            render['host'] = self.get_user_name(render['host'])
            render['too_short'] =  int(render['duration']) < int(min_duration)
            render['status'] = db.Status.str(existing_data[rec_uuid]) if rec_uuid in existing_data else db.Status.str(db.Status.NEW)
            renderable.append(render)
        return renderable

    @functools.lru_cache(maxsize=32)
    @db.with_session
    def get_recording(dbs, self, recording_id):
        try:
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
        except HTTPError as e:
            self.logger.debug(f"HTTPError fetching { recording_id }")
            if e.response.status_code == 404:
                self.logger.debug(f"Zoom has returned a 404 for recording ID { recording_id }, removing it from the db")
                rec = dbs.query(db.Recording).filter(db.Recording.uuid == recording_id).one_or_none()
                if rec:
                  dbs.delete(rec)
                  dbs.commit()
            raise #Rather than returning some error token let's let the caller deal with it

    def get_renderable_recording(self, recording_id):
        #Get the recording from Zoom
        recording = self.get_recording(recording_id)
        #Turn it into a database entry
        recording = db.create_recording_if_needed(recording)
        #We pass in a list of one, so we know that the returned list is of size 1
        return self._build_renderable_event_list([ recording ])[0]

    def get_user_list(self, q, token=None):
        #Ensure we're using a valid token
        used_token = token if token and len(token) > 0 else None
        response = self.search_user(search_key=q, next_page_token=token)
        users = []
        token_quoted = None
        if response and 'contacts' in response:
            resp_token = response.get('next_page_token', None)
            if resp_token != None:
                # double quote token
                token_quoted = urllib.parse.quote(urllib.parse.quote(resp_token, safe=''), safe='')
            users = sorted([{
                'id': item.get('id'),
                'email': item.get('email'),
                'first_name': item.get('first_name'),
                'last_name': item.get('last_name'),
            } for item in response.get('contacts')],
            key = lambda x : self.format_user_name(x))
            #Ensure the users are all in the DB too
            for user in users:
                db.ensure_user(user)
        return users, token_quoted

    # Do not cache result as the next_page_token expire after 15 minutes
    def search_user(self, search_key, page_size=25, next_page_token=None):
        fn = self._get_zoom_client().contacts.search
        args = {
            'search_key': search_key,
            'query_presence_status': 'false',
            'page_size': page_size,
        }
        if next_page_token and len(next_page_token) > 0:
            args['next_page_token'] = next_page_token
        self.logger.debug(f"Search zoom contacts with params: " + str(args))
        return self._make_zoom_request(fn, args)
