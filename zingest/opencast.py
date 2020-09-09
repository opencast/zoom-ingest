import json
import requests
import os
import os.path
from requests.auth import HTTPDigestAuth
import sys
import configparser
from datetime import datetime, timedelta
from urllib.error import HTTPError
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest import db
import time
import logging
import zingest.logger
from zingest.common import NoMp4Files


class OpencastException(Exception):
    pass

class Opencast:

    IN_PROGRESS_ROOT = "in-progress"
    HEADERS = {'X-Requested-Auth': 'Digest'}

    def __init__(self, config, rabbit):
        self.logger = logging.getLogger("opencast")
        self.logger.setLevel(logging.DEBUG)

        self.url = config["Opencast"]["Url"]
        self.logger.debug(f"Opencast url is {self.url}")
        self.user = config["Opencast"]["User"]
        self.logger.debug(f"Opencast user is {self.user}")
        self.password = config["Opencast"]["Password"]
        self.logger.debug(f"Opencast password is {self.password}")
        self.auth = HTTPDigestAuth(self.user, self.password)
        self.set_rabbit(rabbit)
        self.acls_updated = None
        self.acls = None
        self.themes_updated = None
        self.themes = None
        self.workflows_updated = None
        self.workflows = None
        self.series_updated = None
        self.series_full = None
        self.series = None
        self.get_acls()
        self.get_themes()
        self.get_workflows()
        self.get_series()
        self.logger.info("Setup complete")


    def set_rabbit(self, rabbit):
        if not rabbit or type(rabbit) != zingest.rabbit.Rabbit:
            raise TypeError("Rabbit is missing or the wrong type!")
        else:
            self.rabbit = rabbit


    def run(self):
        while True:
            try:
                self.logger.info("Consuming rabbits")
                self.rabbit.start_consuming_rabbitmsg(self.rabbit_callback)
            except Exception as e:
                self.logger.exception("BUG: Should not happen")


    @db.with_session
    def process_backlog(dbs, self):
        while True:
            try:
                self.logger.info("Checking backlog")
                #FIXME: commented this out for testing hour_ago = datetime.utcnow() - timedelta(hours = 1)
                hour_ago = datetime.utcnow() - timedelta(minutes = 1)
                rec_list = dbs.query(db.Recording).filter(db.Recording.status != db.Status.FINISHED, db.Recording.timestamp <= hour_ago).all()
                for rec in rec_list:
                    self._process(rec.get_data())
                time.sleep(60)
            except Exception as e:
                self.logger.exception("BUG: Should not happen")


    def _do_download(self, url, output, expected_size):
        #TODO: unclear what expected_size is in, .getsize is in bytes
        if os.path.isfile(output) and expected_size == os.path.getsize(output):
          self.logger.debug(f"{output} already exists and is the right size")
          return
        with requests.get(url, stream=True) as req:
            #Raises an exception if there is one
            req.raise_for_status()
            with open(output, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    # If you have chunk encoded response uncomment if
                    # and set chunk_size parameter to None.
                    #if chunk:
                    f.write(chunk)
                f.close()
            r.close()


    def _do_get(self, url):
        return requests.get(url, auth=HTTPDigestAuth(self.user, self.password),
                                headers={'X-Requested-Auth': 'Digest'})


    def _do_post(self, url, **kwargs):
        self.logger.debug(f"POSTing { kwargs['data'] } to { url }")#, with { len(files) } files")
        return requests.post(url, auth=self.auth, headers=Opencast.HEADERS, **kwargs)


    @db.with_session
    def rabbit_callback(dbs, self, method, properties, body):
        #TODO: Throwing an error from here appears to push the mesage back into Rabbit, this should be double checked
        j = json.loads(body)
        uuid = j['uuid']
        rec = dbs.query(db.Recording).filter(db.Recording.uuid == uuid).all()
        if 0 == len(rec):
            self.logger.debug(f"{uuid} not found in db, creating new record")
            #Create a database record so that we can recover if we're killed mid process
            db.create_recording(j)
        else:
            self.logger.debug(f"{uuid} found in db")
            return

        self._process(j)


    @db.with_session
    def _process(dbs, self, json):
        uuid = json['uuid']
        try:
            rec = dbs.query(db.Recording).filter(db.Recording.uuid == uuid).one_or_none()
            if None == rec:
                self.logger.warning(f"LIKELY A BUG: {uuid} not found in db, creating record and processing anyway")
                #Create a database record so that we can recover if we're killed mid process
                rec = db.Recording(j)
                rec.update_status(db.Status.IN_PROGRESS)
                dbs.merge(rec)
                dbs.commit()
            else:
                self.logger.debug(f"{uuid} found in db")

            if not os.path.isdir(f'{self.IN_PROGRESS_ROOT}'):
                os.mkdir(f'{self.IN_PROGRESS_ROOT}')

            self.logger.info(f"Fetching {uuid}")
            data, fileid = self.fetch_file(json)
            self.logger.info(f"Uploading {uuid} as {fileid}.mp4 to {self.url}")
            self.oc_upload(data["creator"], data["topic"], id)
            self._rm(f'{self.IN_PROGRESS_ROOT}/{fileid}.mp4')

            rec = dbs.query(db.Recording).filter(db.Recording.uuid == uuid).one_or_none()
            if None == rec:
                self.logger.error(f"BUG: {uuid} not found in db")
                raise Exception(f"BUG: {uuid} not found in db")
            else:
                rec.update_status(db.Status.FINISHED)
                dbs.merge(rec)
                dbs.commit()
        except HTTPError as er:
            self.logger.exception("Unable to fetch file, will retry later")
            #We're going to retry this since it's not in FINISHED, so we don't need to do anything here.
        except Exception as e:
            self.logger.exception("General Exception")
            #We're going to retry this since it's not in FINISHED, so we don't need to do anything here.


    def _rm(self, path):
        self.logger.debug(f"Removing {path}")
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            if os.path.isfile(path):
                self.logger.exception("Exception removing {path}.  File will need to be manually removed.")


    def fetch_file(self, body):
        #NB: Previously decoded here, unsure if needed
        data = body #json.loads(body)#.decode("utf-8"))
        if "recording_files" not in data:
            self.logger.error("No recording found")
            raise NoMp4Files("No recording found")
        files = data["recording_files"]
        
        dl_url = ''
        recording_id = ''
        for key in files[0].keys():
            if key == "download_url":
                self.logger.debug(f"Download url found: {dl_url}")
                dl_url = files[0][key]
            elif key == "recording_id":
                recording_id = files[0][key]
                self.logger.debug(f"Recording id found: {recording_id}")
            elif key == "file_size":
                expected_size = int(files[0][key])
                self.logger.debug(f"Recording size found: {expected_size}")

        self.logger.debug(f"Downloading from {dl_url}/?access_token={data['token']} to {recording_id}.mp4")
        self._do_download(f"{dl_url}/?access_token={data['token']}", f"{recording_id}.mp4", expected_size)
        return data, recording_id


    def get_themes(self):
        if not self.themes or self.themes_updated <= datetime.utcnow() - timedelta(hours = 1):
            self.logger.debug("Refreshing Opencast themes")
            #TODO: Handle paging.  I'm going to guess we don't need this for rev1
            results = self._do_get(self.url + '/admin-ng/themes/themes.json').json()['results']
            self.themes_updated = datetime.utcnow()
            self.themes = { result['id']: result['name'] for result in results }
            self.logger.debug(f"Found { len(self.themes) } themes")
        return self.themes

    def get_acls(self):
        if not self.acls or self.acls_updated <= datetime.utcnow() - timedelta(hours = 1):
            self.logger.debug("Refreshing Opencast ACLs")
            #TODO: Handle paging.  I'm going to guess we don't need this for rev1
            results = self._do_get(self.url + '/acl-manager/acl/acls.json').json()
            self.acls_updated = datetime.utcnow()
            self.acls = { str(result['id']): { 'name': result['name'], 'acl': result['acl']['ace'] } for result in results }
            self.logger.debug(f"Found { len(self.acls) } ACLs")
            self.logger.debug(f'{ self.acls }')
        return self.acls


    def get_single_acl(self, acl_id):
        if not self.acls:
            self.get_acls()
        return self.acls[acl_id]['acl'] if acl_id in self.acls else None


    def get_workflows(self):
        if not self.workflows or self.workflows_updated <= datetime.utcnow() - timedelta(hours = 1):
            self.logger.debug("Refreshing Opencast workflows")
            #TODO: Handle paging.  I'm going to guess we don't need this for rev1
            results = self._do_get(self.url + '/api/workflow-definitions?filter=tag:archive').json()
            self.workflows_updated = datetime.utcnow()
            self.workflows = { result['identifier']: result['title'] for result in results }
            self.logger.debug(f"Found { len(self.workflows) } workflows")
        return self.workflows


    def get_series(self):
        if not self.series or self.series_updated <= datetime.utcnow() - timedelta(hours = 1):
            self.logger.debug("Refreshing Opencast series list")
            #TODO: Handle paging.  I'm going to guess we don't need this for rev1
            #FIXME: This is probably too large at ETH for the defaults, we need to build a way to filter the results based on the presenter
            results = self._do_get(self.url + '/api/series').json()
            self.series_updated = datetime.utcnow()
            self.series_full = { result['identifier']: result for result in results }
            self.series = { result['identifier']: result['title'] for result in results }
        return self.series


    def get_single_series(self, series_id):
        #If there's somehow no series list, fetch them all
        if not self.series:
            self.get_series()
        #if the series is not in the cache, attempt to fetch it directly and add it
        if not series_id in self.series_full:
            series = self._do_get(self.url + f'/api/series/{ series_id }').json()
            self.series_full[series_id] = series
            self.series[series_id] = series['title']
        #If it's still not found, return None, otherwise return the data
        return self.series_full[series_id] if series_id in self.series_full else None


    def oc_upload(self, creator, title, rec_id):

        response = self._do_get(self.url + '/admin-ng/series/series.json')

        series_list = json.loads(response.content.decode("utf-8"))
        #TODO: Abort?  Or upload with a default user?
        try:
            self.username = creator[:creator.index("@")]
        except ValueError:
            self.logger.warning("Invalid username: '@' is missing, upload is aborted")
            return
        series_title = "Zoom Recordings " + username
        series_found = False
        for series in series_list["results"]:
            if series["title"] == series_title:
                series_found = True
                series_id = series["id"]

        #TODO: This needs to be optional, and/or support a default series
        if not series_found:
            #TODO: What if this fails?
            series_id = self.create_series(creator, series_title)

        with open(rec_id+'.mp4', 'rb') as fobj:
            #TODO: The flavour here needs to be configurable.  Maybe.
            data = {"title": title, "creator": creator, "isPartOf": series_id, "flavor": 'presentation/source'}
            body = {'body': fobj}
            url = self.url + '/ingest/addMediaPackage'
            #TODO: What if this fails?
            self._do_post(url, data=data, files=body, auth=auth)
 

    def create_series(self, dc_title, acl_id, theme_id=None, **kwargs):

        fields = []
        for key, value in kwargs.items():
            name = key.replace("dc_","")
            if name in ("origin_epid") or "" == value:
                continue
            if name in ("publisher", "contributor", "presenter", "creator"):
                element = {'id': name , 'value': value.split(",") }
            else:
                element = {'id': name , 'value': value }
            fields.append(element)
        fields.append({'id': 'title', 'value': dc_title })

        metadata = [{"label": "Opencast Series DublinCore",
                     "flavor": "dublincore/series",
                     "fields": fields
                   }]
        self.logger.debug(f"Creating series with fields { fields }")

        #We know this call is safe since for the ACL to render it has to already be present in the local acl list :)
        acl = self.get_single_acl(acl_id)
        self.logger.debug(f"Using ACL id { acl_id }, which looks like { acl }")

        data = {"metadata": json.dumps(metadata),
                "acl": json.dumps(acl)}

        if None != theme_id and "" != theme_id:
            self.logger.debug(f"Theme id is { theme_id }")
            data['theme'] = str(theme_id)
        else:
            self.logger.debug(f"No theme ID found")

        #What if response is something other than success?
        try:
            response = self._do_post(self.url + '/api/series', data = data)
            self.logger.debug(f"Creating series { dc_title } get a {response} response")
            if 201 != response.status_code:
                raise OpencastException(f"Creating series returned a { response.status_code } http response")
            identifier = response.json()['identifier']
            return response.json()['identifier']
        except Exception as e:
            raise OpencastException(e)
