import json
import logging
import os
import os.path
import time
from datetime import datetime, timedelta
from math import floor
from pathlib import Path
from urllib.error import HTTPError
import re
from xml.parsers.expat import ExpatError
import requests
import xmltodict
from requests.auth import HTTPDigestAuth
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
from requests_toolbelt.exceptions import StreamingError
from requests_toolbelt.downloadutils import stream

import zingest
from zingest import db
from zingest.common import NoMp4Files, BadWebhookData, get_config, get_config_ignore


class OpencastException(Exception):
    pass


class Opencast:

    IN_PROGRESS_ROOT = "in-progress"
    HEADERS = {'X-Requested-Auth': 'Digest'}
    """
    Available recording file types are:
      - shared_screen_with_speaker_view
      - shared_screen_with_gallery_view
      - shared_screen
      - active_speaker
    """
    RECORDING_TYPE_PREFERENCE = [ 'shared_screen_with_speaker_view', 'shared_screen' ,'active_speaker' ]

    def __init__(self, config, rabbit, zoom):
        if not rabbit or type(rabbit) != zingest.rabbit.Rabbit:
            raise TypeError("Rabbit is missing or the wrong type!")
        if not zoom or type(zoom) != zingest.zoom.Zoom:
            raise TypeError("Zoom is missing or the wrong type!")

        try:
            #Allow testing to override this via an undocumented key
            self.IN_PROGRESS_ROOT = get_config_ignore(config, "TESTING", "IN_PROGRESS_ROOT", True)
        except KeyError:
            #This is undocumented, so it's not something we need to tell anyone about
            pass

        self.logger = logging.getLogger(__name__)
        self.url = get_config(config, "Opencast", "Url")
        self.logger.debug(f"Opencast url is {self.url}")
        self.user = get_config(config, "Opencast", "User")
        self.logger.debug(f"Opencast user is {self.user}")
        self.password = get_config(config, "Opencast", "Password")
        filter_config = get_config_ignore(config, "Opencast", "workflow_filter", True)
        if filter_config and len(filter_config) > 0:
            self.workflow_filter = filter_config.split(" ")
        else:
            self.workflow_filter = None
        self.logger.debug(f"Workflow filter configured as { self.workflow_filter }")
        filter_config = get_config_ignore(config, "Opencast", "series_filter", True)
        if filter_config and len(filter_config) > 0:
            self.logger.info(f"Series filter configured to be { filter_config }")
        else:
            filter_config = ".*"
            self.logger.warning(f"Using default filter config: \"{ filter_config }\" because user provided config is blank!")
        self.series_filter = re.compile(filter_config)
        self.logger.debug(f"Workflow filter configured as { self.workflow_filter }")
        self.auth = HTTPDigestAuth(self.user, self.password)
        self.rabbit = rabbit
        self.zoom = zoom
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

    def run(self):
        while True:
            try:
                self.logger.info("Consuming rabbits")
                self.rabbit.start_consuming_rabbitmsg(self.rabbit_callback)
            except Exception as e:
                self.logger.exception("Error connecting to rabbit!  Retry in 10 seconds...")
                time.sleep(10)

    def process_backlog(self):
        while True:
            try:
                self._process_backlog()
            except Exception as e:
                self.logger.exception("Catchall while processing the backlog. Please report this as a bug.")
                time.sleep(10)

    @db.with_session
    def _process_backlog(dbs, self):
        self.logger.info("Checking backlog")
        hour_ago = datetime.utcnow() - timedelta(hours=1)
        ing_list = dbs.query(db.Ingest).filter(db.Ingest.status != db.Status.FINISHED, db.Ingest.timestamp <= hour_ago).all()
        for ing in ing_list:
            self._process(ing)
        time.sleep(60)

    def _do_download(self, url, output, expected_size):
        Path(f"{ self.IN_PROGRESS_ROOT }").mkdir(parents=True, exist_ok=True)
        if os.path.isfile(output) and expected_size == os.path.getsize(output):
          self.logger.debug(f"{ output } already exists and is the right size")
          return
        with open(output, 'wb') as fd:
            r = requests.get(url, stream=True)
            stream.stream_response_to_file(r, path=fd, chunksize=8192)

    def _do_get(self, url):
        self.logger.debug(f"GETting { url }")
        return requests.get(url, auth=self.auth, headers=Opencast.HEADERS)

    def create_callback(self, encoder):
        last = 0
        def callback(monitor):
            nonlocal last
            pct = int(monitor.bytes_read / monitor.len * 100)
            #Log every 5%, and only if it's a *new* percentage
            #This callback gets called for every read() of the underlying file (possibly every 512 bytes)
            if pct % 5 == 0 and pct > last:
                last = pct
                #Logging to two decimal places
                self.logger.debug(f"{ '{:4.2f}'.format(pct) }% uploaded")
        return callback

    def _do_post(self, url, data, files=None):
        self.logger.debug(f"POSTing { data } to { url }")
        #Take the data params (form params)
        fields = data
        #Add the files
        #TODO: validate this somehow
        if files:
            fields.update(files)
        e = MultipartEncoder(fields = fields)
        #self.logger.debug(e.to_string())
        m = MultipartEncoderMonitor(e, self.create_callback(e))
        #Clone the defaul headers, then set the content type
        #NB: Without setting this content type the ingest will fail when uploading anything!
        headers = {}
        headers.update(Opencast.HEADERS)
        headers['Content-Type'] = m.content_type
        return requests.post(url, auth=self.auth, headers=headers, data=m)

    def _do_put(self, url, data):
        self.logger.debug(f"PUTing { data } to { url }")
        return requests.put(url, auth=self.auth, headers=Opencast.HEADERS, data=data)

    @db.with_session
    def rabbit_callback(dbs, self, method, properties, body):
        j = json.loads(body)
        rec_id = j['uuid']
        ing_id = int(j['ingest_id'])
        ingest = dbs.query(db.Ingest).filter(db.Ingest.ingest_id == ing_id).one_or_none()
        if ingest:
            self._process(ingest)
        else:
            self.logger.warn(f"Recieved rabbit message for { rec_id } with an invalid ingest id of { ing_id }.")

    @db.with_session
    def _process(dbs, self, ingest):
        uuid = ingest.get_recording_id()
        params = json.loads(ingest.get_params().decode('utf-8'))

        try:
            rec = dbs.query(db.Recording).filter(db.Recording.uuid == uuid).one_or_none()
            if not rec:
                self.logger.error(f"Unable to find recording { uuid }, this is a bug.")
                return

            ingest.update_status(db.Status.IN_PROGRESS);
            dbs.merge(ingest)
            dbs.commit()

            if not os.path.isdir(f'{self.IN_PROGRESS_ROOT}'):
                os.mkdir(f'{self.IN_PROGRESS_ROOT}')

            self.logger.info(f"{ uuid }: Fetching {uuid}")
            files = self.zoom.get_recording_files(uuid)
            filename = self.fetch_file(uuid, files)
            self.logger.info(f"{ uuid }: Uploading {uuid} as {filename} to {self.url}")

            mp_id, workflow_id = self.oc_upload(uuid, filename, **params)
            self._rm(filename)

            ingest.update_status(db.Status.FINISHED)
            ingest.set_workflow_id(workflow_id)
            ingest.set_mediapackage_id(mp_id)
            dbs.merge(ingest)
            dbs.commit()
        except FileNotFoundError as e:
            self.logger.error(f"Unable to ingest { uuid }, file not found, will retry later")
        except ExpatError as e:
            self.logger.error(f"Opencast did not return a valid mediapackage for { uuid }, will retry later")
        except StreamingError as e:
            self.logger.exception(f"Error downloading media for { uuid }, will retry")
        except HTTPError as er:
            self.logger.exception(f"Unable to fetch file for { uuid }, will retry later")
            #We're going to retry this since it's not in FINISHED, so we don't need to do anything here.
        except Exception as e:
            self.logger.exception(f"General Exception processing { uuid }")
            #We're going to retry this since it's not in FINISHED, so we don't need to do anything here.

    def _rm(self, path):
        self.logger.debug(f"Removing { path }")
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            if os.path.isfile(path):
                self.logger.exception(f"Exception removing { path }.  File will need to be manually removed.")

    def fetch_file(self, recording_id, files):
        dl_url = ''
        recording_file = None
        for preference in self.RECORDING_TYPE_PREFERENCE:
            self.logger.debug(f"{ recording_id  }: Checking if recording contains a file of type { preference }")
            for candidate in files:
                if preference == candidate['recording_type']:
                    recording_file = candidate
                    break
            if recording_file:
                self.logger.debug(f"{ recording_id  }: Recording contains a file of type { preference }!")
                #We've found one, quit
                break

        #If we've somehow cycled through all the candidates and nothing matches, fail
        if not recording_file:
            raise NoMp4Files(f"{ Recording_id }: No acceptable filetype found!")

        for key in files[0].keys():
            if key == "download_url":
                dl_url = files[0][key]
                self.logger.debug(f"{ recording_id  }: Download url found: {dl_url}")
            elif key == "file_size":
                expected_size = int(files[0][key])
                self.logger.debug(f"{ recording_id  }: Recording size found: {expected_size}")

        #Output file lives in the in-progress directory
        filename = f"{self.IN_PROGRESS_ROOT}/{recording_id}.mp4"

        #Zoom token gets calculated at download time, regardless of inclusion in the rabbit message
        token = self.zoom.get_download_token()
        url = f"{dl_url}?access_token={ token }"
        self.logger.debug(f"{ recording_id  }: Downloading from { url } to { filename }")
        self._do_download(f"{ url }", filename, expected_size)

        return filename

    def _build_ingest_renderable(self, results):
        ip = []
        for result in results:
            data = result.get_data()
            item = {
                'id': result.rec_id,
                'title': data['topic'],
                'date': data['start_time'],
                'url': data['share_url'],
                'host': self.zoom.get_user_name(result.get_user_id()),
                'status': result.status_str()
            }
            ip.append(item)
        return ip

    def get_themes(self):
        if not self.themes or self.themes_updated <= datetime.utcnow() - timedelta(hours = 1):
            attempts = 1
            successful = False
            while attempts <= 5:
                try:
                    self.logger.debug("Refreshing Opencast themes")
                    #FIXME: There does not appear to *be* another endpoint to use, but the admin-ng namespace is a very bad idea long term.
                    result = self._do_get(f'{ self.url }/admin-ng/themes/themes.json?limit=100').json()
                    if 'total' in result and str(result['total']) == 0:
                        break
                    #We need the total, count, and result fields.  If the count doesn't match the length of results, or any of the fields are missing
                    if 'total' not in result or 'results' not in result or ('count' in result and len(result['results']) != result['count']):
                        self.logger.warn("Bad data from Opencast when loading themes")
                        raise Exception("Bad data from Opencast")
                    self.themes_updated = datetime.utcnow()
                    self.themes = { theme['id']: theme['name'] for theme in result['results'] }
                    counter = 1
                    while len(self.themes.keys()) < int(result['total']):
                        result = self._do_get(f'{ self.url }/admin-ng/themes/themes.json?limit=100&offset={ counter * 100 + 1}').json()
                        self.themes.update({ theme['id']: theme['name'] for theme in result['results'] })
                        counter += 1
                    successful = True
                    break
                except Exception as e:
                    self.logger.error(f"Attempt { attempts } to fetch themes failed with a ConnectionError, retrying in { attempts * 5 }s")
                    attempts += 1
                    time.sleep(attempts * 5)
            if not successful:
                self.logger.error("Unable to update themes!  UI will still function but theme data is missing!")
            else:
                self.logger.info(f"Found { len(self.themes) } themes")
        return self.themes

    def get_acls(self):
        if not self.acls or self.acls_updated <= datetime.utcnow() - timedelta(hours = 1):
            attempts = 1
            successful = False
            while attempts <= 5:
                try:
                    self.logger.debug("Refreshing Opencast ACLs")
                    #NB: This endpoint doesn't support paging at all, so hopefully it returns the full set!
                    results = self._do_get(f'{ self.url }/acl-manager/acl/acls.json').json()
                    self.acls_updated = datetime.utcnow()
                    self.acls = { str(result['id']): { 'name': result['name'], 'acl': result['acl']['ace'] } for result in results }
                    successful = True
                    break
                except Exception as e:
                    self.logger.error(f"Attempt { attempts } to fetch ACLs failed with a ConnectionError, retrying in { attempts * 5 }s")
                    attempts += 1
                    time.sleep(attempts * 5)
            if not successful:
                self.logger.error("Unable to update ACLs!  UI will still function but ACL data is missing!")
            else:
                self.logger.info(f"Found { len(self.acls) } ACLs")
        return self.acls

    def get_single_acl(self, acl_id):
        if not self.acls:
            self.get_acls()
        return self.acls[acl_id]['acl'] if acl_id in self.acls else None

    def get_workflows(self):
        if not self.workflows or self.workflows_updated <= datetime.utcnow() - timedelta(hours = 1):
            attempts = 1
            successful = False
            while attempts <= 5:
                try:
                    self.logger.debug("Refreshing Opencast workflows")
                    #FIXME: This endpoint doesn't tell you how many total definitions there are matching your query
                    # and the /workflow/definitions.(xml|json) endpoint does not support filtering
                    results = self._do_get(f'{ self.url }/api/workflow-definitions?filter=tag:upload&filter=tag:schedule').json()
                    self.workflows_updated = datetime.utcnow()
                    if self.workflow_filter:
                        self.workflows = { result['identifier']: result['title'] for result in results if self.workflow_filter and result['identifier'] in self.workflow_filter }
                    else:
                        self.workflows = { result['identifier']: result['title'] for result in results }
                    successful = True
                    break
                except Exception as e:
                    self.logger.error(f"Attempt { attempts } to fetch workflows failed with a ConnectionError, retrying in { attempts * 5 }s")
                    attempts += 1
                    time.sleep(attempts * 5)
            if not successful:
                self.logger.error("Unable to update workflows!  UI will still function but workflow data is missing!")
            else:
                self.logger.info(f"Found { len(self.workflows) } workflows")
        return self.workflows

    # Desired format is [title] [year] ([names])
    def _render_series_title(self, series):
        title = series['title'][0]['value']
        year = series['created'][0]['value'][:4]
        names = ""
        if 'creator' in series:
            names = [ element['value'] for element in series['creator'] ]
            names = ", ".join(names)
            #Arbirarily cap this at 50 characters for the names to prevent wonky rendering
            #This number is picked at random, and could probably be shorter depending on prod env
            return f"{ title } ({ year }) ({ names[:50] })"
        else:
            return f"{ title } ({ year })"

    def get_series(self):
        DCTERMS = 'http://purl.org/dc/terms/'
        if not self.series or self.series_updated <= datetime.utcnow() - timedelta(hours = 1):
            attempts = 1
            successful = False
            while attempts <= 5:
                try:
                    self.logger.debug("Refreshing Opencast series list")
                    #TODO: Handle paging.  I'm going to guess we don't need this for rev1
                    #FIXME: This is probably too large at ETH for the defaults, we need to build a way to filter the results based on the presenter
                    response = self._do_get(f'{ self.url }/series/series.json?count=100').json()
                    if 'totalCount' in response and int(response['totalCount']) == 0:
                        self.series_updated = datetime.utcnow()
                        self.series = {}
                        successful = True
                        break
                    #We need the total, count, and result fields.  If the count doesn't match the length of results, or any of the fields are missing
                    if 'totalCount' not in response or 'catalogs' not in response:
                        raise Exception("Bad data from Opencast")
                    results = response['catalogs']
                    processed = len(results)
                    self.series_updated = datetime.utcnow()
                    self.series = { result[DCTERMS]['identifier'][0]['value']: self._render_series_title(result[DCTERMS]) for result in results if self.series_filter.match(result[DCTERMS]['title'][0]['value'])}
                    self.logger.debug(f"Processed { processed } series out of { response['totalCount'] }, { len(self.series) } match the filtering requirements")
                    counter = 1
                    while processed < int(response['totalCount']):
                        response = self._do_get(f'{ self.url }/series/series.json?count=100&startPage={ counter }').json()
                        results = response['catalogs']
                        processed += len(results)
                        self.series.update({ result[DCTERMS]['identifier'][0]['value']: result[DCTERMS]['title'][0]['value'] for result in results })
                        self.logger.debug(f"Processed { processed } series out of { response['totalCount'] }, { len(self.series) } match the filtering requirements")
                        counter += 1
                    successful = True
                    break
                except Exception as e:
                    self.logger.exception(f"Attempt { attempts } to fetch series failed with a ConnectionError, retrying in { attempts * 5 }s")
                    attempts += 1
                    time.sleep(attempts * 5)
            if not successful:
                self.logger.error("Unable to update series!  UI will still function but series data is missing!")
            else:
                self.logger.info(f"Found { len(self.series) } series")
        return self.series

    def get_single_series(self, series_id):
        if not self.series:
            self.get_series()
        if series_id in self.series:
            return series_id
        response = self._do_get(f'{ self.url }/series/series.json?seriesId={ series_id }').json()
        if len(response['catalogs']) == 0:
            self.logger.debug(f"Series { series_id } not found")
            return None
        result = response['catalogs'][0]
        sid = result['http://purl.org/dc/terms/']['identifier'][0]['value']
        stitle = result['http://purl.org/dc/terms/']['title'][0]['value']
        self.series[sid] = stitle
        return stitle

    def _ensure_list(self, value):
        if type(value) != list:
            return [ value ]
        return value

    def _prep_metadata_fields(self, **kwargs):
        fields = []
        for name, value in kwargs.items():
            #TODO: This logic is bad, the variables should be prefixed with dc- and everything else filtered out
            if name.startswith("origin") or name.startswith('eth'):
                continue
            if name in ("contributor", "presenter", "creator", "subjects"): #, "publisher"):
                element = {'id': name , 'value': self._ensure_list(value.split(';')) }
            elif name in ("publisher"):
                element = {'id': name , 'value': self._ensure_list(value) }
            elif name == "date":
                element = {'id': 'startDate' , 'value': value }
            else:
                element = {'id': name , 'value': value }
            fields.append(element)
        return fields

    def _prep_dublincore(self, **kwargs):
        dc = {"dublincore": {"@xmlns": "http://www.opencastproject.org/xsd/1.0/dublincore/", "@xmlns:dcterms": "http://purl.org/dc/terms/", "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"}}
        for name, value in kwargs.items():
            #TODO: This logic is bad, the variables should be prefixed with dc- and everything else filtered out
            if name.startswith("origin") or name.startswith('eth'):
                continue
            if name in ("publisher", "contributor", "presenter", "creator", "subjects"):
                element_name = f"dcterms:{ name }"
                element_value = self._ensure_list(value.split(';'))
            elif name == "date":
                element_name = f"dcterms:created"
                element_value = value
            elif name == "duration":
                element_name = f"dcterms:extent"
                dur = int(value)
                hours = floor( dur / 60)
                minutes = dur - hours * 60
                element_value = f"PT{ hours }H{ minutes }M0S"
            else:
                element_name = f"dcterms:{ name }"
                element_value = value
            dc['dublincore'][element_name] = element_value
        if 'dublincore' in dc and not 'dcterms:spatial' in dc['dublincore']:
            # set Zoom as location
            dc['dublincore']['dcterms:spatial'] = "Zoom"
        return xmltodict.unparse(dc)

    def _prep_eth_dublincore(self, **kwargs):
        prefix = "eth-"
        dc = {"ethterms": {"@xmlns": "http://ethz.ch/video/opencast", "@xmlns:ethterms": "http://ethz.ch/video/metadata"}}
        for name, value in kwargs.items():
            if not name.startswith(prefix):
                continue
            element_name = f"ethterms:{ name[len(prefix):] }"
            if "eth-advertised" == name:
                if "on" == value:
                    element_value = "true"
                else:
                    element_value = "false"
            else:
                element_value = value
            dc['ethterms'][element_name] = element_value
        return xmltodict.unparse(dc)

    def _prep_episode_xacml(self, episode_id, acl):
      xacml = {"Policy": {"@PolicyId": episode_id, "@Version": "2.0", "@RuleCombiningAlgId": "urn:oasis:names:tc:xacml:1.0:rule-combining-algorithm:permit-overrides", "@xmlns": "urn:oasis:names:tc:xacml:2.0:policy:schema:os"}}
      xacml['Policy']['Target'] = {"Resources": {"Resource": {"ResourceMatch": {"@MatchId": "urn:oasis:names:tc:xacml:1.0:function:string-equal", "AttributeValue": {"@DataType": "http://www.w3.org/2001/XMLSchema#string", "#text": episode_id}, "ResourceAttributeDesignator": {"@AttributeId": "urn:oasis:names:tc:xacml:1.0:resource:resource-id", "@DataType": "http://www.w3.org/2001/XMLSchema#string"}}}}},
      rules = []
      for ace in acl:
          role_name = ace['role']
          rw = ace['action']
          rules.append({"@RuleId": f"{ role_name }_{ rw }_Permit", "@Effect": "Permit", "Target": {"Actions": {"Action": {"ActionMatch": {"@MatchId": "urn:oasis:names:tc:xacml:1.0:function:string-equal", "AttributeValue": {"@DataType": "http://www.w3.org/2001/XMLSchema#string", "#text": rw}, "ActionAttributeDesignator": {"@AttributeId": "urn:oasis:names:tc:xacml:1.0:action:action-id", "@DataType": "http://www.w3.org/2001/XMLSchema#string"}}}}}, "Condition": {"Apply": {"@FunctionId": "urn:oasis:names:tc:xacml:1.0:function:string-is-in", "AttributeValue": {"@DataType": "http://www.w3.org/2001/XMLSchema#string", "#text": role_name}, "SubjectAttributeDesignator": {"@AttributeId": "urn:oasis:names:tc:xacml:2.0:subject:role", "@DataType": "http://www.w3.org/2001/XMLSchema#string"}}}})
      rules.append({"@RuleId": "DenyRule", "@Effect": "Deny"})
      xacml['Policy']['Rule'] = rules
      return xmltodict.unparse(xacml)

    def _check_valid_mediapackage(self, mp):
        #We throw out the results here, we're just looking for the exception if the mediapackage is invalid
        xmltodict.parse(mp)

    def oc_upload(self, rec_id, filename, acl_id=None, workflow_id=None, **kwargs):

        if not workflow_id:
            self.logger.error(f"Attempting to ingest { rec_id } with no workflow id!")
            raise Exception("Workflow ID is missing!")

        selected_acl = self.get_single_acl(acl_id) if self.get_single_acl(acl_id) is not None else []
        ep_dc = self._prep_dublincore(**kwargs)
        eth_dc = self._prep_eth_dublincore(**kwargs)
        ep_acl = self._prep_episode_xacml(rec_id, selected_acl)

        #TODO: Make this configurable, cf pyca's setup
        wf_config = {'publishToSearch': 'true', 'flagQuality720p':'true', 'publishToApi':'true', 'publishToEngage':'true','straightToPublishing':'true','publishToOaiPmh':'true'}

        with open(filename, 'rb') as fobj:
            self.logger.info(f"{ rec_id  }: Creating mediapackage")
            mp = self._do_get(f'{ self.url }/ingest/createMediaPackage').text
            self._check_valid_mediapackage(mp)

            self.logger.debug(f"{ rec_id  }: Ingesting episode dublin core settings")
            mp = self._do_post(f'{ self.url }/ingest/addDCCatalog', data={'flavor': 'dublincore/episode', 'mediaPackage': mp, 'dublinCore': ep_dc}).text
            self._check_valid_mediapackage(mp)
            if eth_dc:
                self.logger.debug(f"{ rec_id  }: Ingesting episode ethterms")
                mp = self._do_post(f'{ self.url }/ingest/addDCCatalog', data={'flavor': 'ethterms/episode', 'mediaPackage': mp, 'dublinCore': eth_dc}).text
                self._check_valid_mediapackage(mp)
            self.logger.debug(f"{ rec_id  }: Ingesting episode security settings")
            mp = self._do_post(f'{ self.url }/ingest/addAttachment', data={'flavor': 'security/xacml+episode', 'mediaPackage': mp}, files = {"BODY": ("ep-security.xacml", ep_acl, "text/xml") }).text
            self._check_valid_mediapackage(mp)
            self.logger.info(f"{ rec_id  }: Ingesting zoom video")
            mp = self._do_post(f'{ self.url }/ingest/addTrack', data={'flavor': 'presentation/source', 'mediaPackage': mp}, files={ "BODY": (os.path.basename(filename), fobj, "video/mp4") }).text
            self._check_valid_mediapackage(mp)
            self.logger.info(f"{ rec_id  }: Triggering processing")
            workflow = self._do_post(f'{ self.url }/ingest/ingest/{ workflow_id }', data={'mediaPackage': mp}).text

        wfdict = xmltodict.parse(workflow)
        mpid = wfdict['wf:workflow']['mp:mediapackage']['@id']
        workflow_instance_id = wfdict['wf:workflow']['@id']

        self.logger.info(f"Ingested { rec_id } as workflow { workflow_instance_id } on mediapackage { mpid }")
        return mpid, workflow_instance_id

    def create_series(self, title, acl_id, theme_id=None, **kwargs):

        fields = self._prep_metadata_fields(**kwargs)
        fields.append({'id': 'title', 'value': title })

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
        eth_dc = self._prep_eth_dublincore(**kwargs)

        if None != theme_id and "" != theme_id:
            self.logger.debug(f"Theme id is { theme_id }")
            data['theme'] = str(theme_id)
        else:
            self.logger.debug(f"No theme ID found")

        #What if response is something other than success?
        try:
            response = self._do_post(self.url + '/api/series', data = data)
            self.logger.debug(f"Creating series { title } get a { response.status_code } response")
            if 201 != response.status_code:
                raise OpencastException(f"Creating series returned a { response.status_code } http response")
            identifier = response.json()['identifier']
            response = self._do_put(f"{ self.url }/series/{ identifier }/elements/ethterms", data=eth_dc)
            self.logger.debug(f"Adding ethterms to { identifier } got a { response.status_code } response")
            return identifier
        except Exception as e:
            raise OpencastException(e)
