import json
import requests
import os
import os.path
from requests.auth import HTTPDigestAuth
import sys
import configparser
from urllib.error import HTTPError
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom

import logging
import zingest.logger

class Opencast:

    IN_PROGRESS_ROOT = "in-progress"

    def __init__(self, config, rabbit):
        self.logger = logging.getLogger("opencast")
        self.logger.setLevel(logging.DEBUG)

        self.url = config["Opencast"]["Url"]
        self.logger.debug(f"Opencast url is {self.url}")
        self.user = config["Opencast"]["User"]
        self.logger.debug(f"Opencast user is {self.user}")
        self.password = config["Opencast"]["Password"]
        self.logger.debug(f"Opencast password is {self.password}")
        self.logger.info("Setup complete, consuming rabbits")
        self.set_rabbit(rabbit)

    def set_rabbit(self, rabbit):
        if not rabbit or type(rabbit) != zingest.rabbit.Rabbit:
            raise TypeError("Rabbit is missing or the wrong type!")
        else:
            self.rabbit = rabbit

    def run(self):
        self.rabbit.start_consuming_rabbitmsg(self.rabbit_callback)


    def _do_download(self, url, output):
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
                                headers={'X-Requestedresponse = -Auth': 'Digest'})


    def _do_post(self, url, data, files=None):
        return requests.post(url, data=data, files=files,
                      auth=HTTPDigestAuth(self.user, self.password), headers={'X-Requested-Auth': 'Digest'})


    def rabbit_callback(self, method, properties, body):
        #TODO: Record ${body} somewhere (DB?) so that if we get abuptly killed we don't lose state!
        #TODO: Handle Exceptions (fetch_file raises, as does _do_download)
        j = json.loads(body)
        uuid = j['uuid']
        if not os.path.isdir(f'{self.IN_PROGRESS_ROOT}'):
            os.mkdir(f'{self.IN_PROGRESS_ROOT}')
        with open(f'{self.IN_PROGRESS_ROOT}/{uuid}.recording', 'w') as dump:
            dump.write(json.dumps(j))
            dump.close()
        self.logger.info(f"Fetching {uuid}")
        try:
            data, id = self.fetch_file(body)
        except HTTPError as er:
            self.logger.exception("Unable to fetch file")
            return
        self.logger.info(f"Uploading {uuid} as {id}.mp4 to {self.url}")
        self.oc_upload(data["creator"], data["topic"], id)
        self._rm(f'{self.IN_PROGRESS_ROOT}/{id}.mp4')
        self._rm(f'{self.IN_PROGRESS_ROOT}/{uuid}.recording')

    def _rm(self, path):
        self.logger.debug(f"Removing {path}")
        if os.path.isfile(path):
            os.remove(path)

    def fetch_file(self, body):
        #NB: Previously decoded here, unsure if needed
        data = json.loads(body)#.decode("utf-8"))
        if "recording_files" not in data:
            self.logger.error("No recording found")
            raise NoMp4Files("No recording found")
        files = data["recording_files"]
        
        dl_url = ''
        id = ''
        for key in files[0].keys():
            if key == "download_url":
                self.logger.debug(f"Download url found: {dl_url}")
                dl_url = files[0][key]
            elif key == "recording_id":
                id = files[0][key]
                self.logger.debug(f"Recording id found: {id}")

        self.logger.debug(f"Downloading from {dl_url}/?access_token={data['token']} to {id}.mp4")
        self._do_download(f"{dl_url}/?access_token={data['token']}", f"{id}.mp4")
        self.logger.debug(f"Id is ${id}")
        return data, id


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
                id = series["id"]

        #TODO: This needs to be optional, and/or support a default series
        if not series_found:
            #TODO: What if this fails?
            id = self.create_series(creator, series_title)

        with open(rec_id+'.mp4', 'rb') as fobj:
            #TODO: The flavour here needs to be configurable.  Maybe.
            data = {"title": title, "creator": creator, "isPartOf": id, "flavor": 'presentation/source'}
            body = {'body': fobj}
            url = self.url + '/ingest/addMediaPackage'
            #TODO: What if this fails?
            self._do_post(url, data=data, files=body, auth=auth)
 

    def create_series(self, creator, title):

        metadata = [{"label": "Opencast Series DublinCore",
                     "flavor": "dublincore/series",
                     "fields": [{"id": "title",
                                 "value": title},
                                {"id": "creator",
                                 "value": [creator]}]}]
        #TODO: Load a default ACL template from somewhere
        acl = [{"allow": True,
                "action": "write",
                "role": "ROLE_AAI_USER_"+creator},
               {"allow": True,
                "action": "read",
                "role": "ROLE_AAI_USER_"+creator}]

        data = {"metadata": json.dumps(metadata),
                "acl": json.dumps(acl)}

        response = self._do_post(url+'/api/series', data=data)

        self.logger.debug(f"Creating series {title} get a {response.status_code} response")

        #What if response is something other than success?
        return json.loads(response.content.decode("utf-8"))["identifier"]
