import configparser
import logging
import os.path
import sys
import urllib.parse
from datetime import datetime, date, timedelta
from urllib.parse import urlencode, parse_qs

from flask import Flask, request, render_template, render_template_string, redirect

from zingest import db
from logger import init_logger
from zingest.common import BadWebhookData, NoMp4Files, get_config_ignore
from zingest.filter import RegexFilter
from zingest.opencast import Opencast
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom

MIN_DURATION = 0
WEBHOOK_SECRET = None

init_logger()
logger = logging.getLogger(__name__)
logger.info("Startup")

try:
    config = configparser.ConfigParser()
    config_path = '/etc/zoom-ingest/settings.ini'
    if os.path.isfile(config_path):
        config.read(config_path)
    else:
        config_path = 'etc/zoom-ingest/settings.ini'
        config.read(config_path)
    logger.debug(f'Configuration read from {config_path}')
except FileNotFoundError:
    sys.exit("No settings found")

try:
    MIN_DURATION = int(config["Webhook"]["Min_Duration"])
    logger.debug(f"Minimum duration is {MIN_DURATION}")

    EPISODE_FIELDS = get_config_ignore(config, 'Visibility', 'episode', True).split(" ")
    if "" != EPISODE_FIELDS[0]:
        logger.debug(f"Visible episode metadata fields configured to be { EPISODE_FIELDS }")
    else:
        EPISODE_FIELDS = None
        logger.debug("All episode metadata fields are visible")
    SERIES_FIELDS = get_config_ignore(config, 'Visibility', 'series', True).split(" ")
    if "" != SERIES_FIELDS[0]:
        logger.debug(f"Visible series metadata fields configured to be { SERIES_FIELDS }")
    else:
        SERIES_FIELDS = None
        logger.debug("All series metadata fields are visible")
    SERIES_CREATE_ENABLED = get_config_ignore(config, 'Visibility', 'series_create_enabled', True)
    if not SERIES_CREATE_ENABLED or SERIES_CREATE_ENABLED.lower() == 'true':
        SERIES_CREATE_ENABLED = True
    else:
        SERIES_CREATE_ENABLED = False
    WEBHOOK_SERIES = (config['Webhook']['default_series_id']).strip()
    WEBHOOK_ACL = (config['Webhook']['default_acl_id']).strip()
    WEBHOOK_WORKFLOW = (config['Webhook']['default_workflow_id']).strip()
    if len(WEBHOOK_WORKFLOW) == 0 and not (len(WEBHOOK_SERIES) > 0 or len(WEBHOOK_ACL) > 0):
        WEBHOOK_ENABLE = False
        logger.info("Webhook is not completely configured and is not functional!")
    else:
        WEBHOOK_ENABLE = True
        logger.info("Webhook is enabled")
        logger.debug(f"Webhook events will be ingested to series ID '{WEBHOOK_SERIES}'")
        logger.debug(f"Webhook events will be ingested with ACL ID '{WEBHOOK_ACL}'")
        logger.debug(f"Webhook events will be ingested with workflow ID '{WEBHOOK_WORKFLOW}'")
        WEBHOOK_SECRET = (config['Webhook']['secret']).strip()
        if len(WEBHOOK_SECRET) != 0:
            logger.debug(f"Webhook pre-shared secret configured: { WEBHOOK_SECRET[0:3] }XXX{ WEBHOOK_SECRET[-3:] }")
        else:
            logger.debug(f"Webhook pre-shared secre not configured")
            WEBHOOK_SECRET = None
except KeyError as err:
    sys.exit("Key {0} was not found".format(err))
except ValueError as err:
    sys.exit("Invalid value, integer expected : {0}".format(err))

db.init(config)
z = Zoom(config)
r = Rabbit(config, z)
o = Opencast(config, r, z)

recording_filter = RegexFilter(config)

app = Flask(__name__)

## Utility Methods

def validate_date(date_obj):
    if not date:
        logger.error(f"Date object is None")
        #FIXME: throw an exception
        return None
    if date_obj and isinstance(date_obj, str):
        return datetime.strptime(date_obj, '%Y-%m-%d').date()
    elif date_obj and isinstance(date_obj, date):
        return date_obj
    else:
        logger.error(f"Unparsable date { date_obj }")
        #FIXME: This should *really* throw an exception
        return None


def get_query_params():
    return {
        'from': validate_date(request.args.get('from', date.today() - timedelta(days = 30))),
        'to': validate_date(request.args.get('to', date.today())),
        'page_size': request.args.get('page_size', None),
        'dur_check': request.args.get('dur_check', "true").lower() == 'true',
        'oref': request.args.get('oref', request.referrer)
    }


def build_query_string(param_dict = None):
    if None == param_dict:
        param_dict = get_query_params()
    clean_dict = { key: value for key, value in param_dict.items() if None != value }
    query_string = urlencode(clean_dict)
    logger.debug(f"Query string is { query_string }")
    return query_string

## List of recordings for a single user,

@app.route('/recordings/<user_id>', methods=['GET'])
def do_list_recordings(user_id):
    query_params = get_query_params()
    query_string = build_query_string(query_params)

    from_date = query_params['from']
    to_date = query_params['to']
    month_back = from_date - timedelta(days = 30)
    month_forward = to_date + timedelta(days = 30)
    dur_check = query_params['dur_check']
    min_duration = int(MIN_DURATION) if dur_check else 0

    renderable = z.get_user_recordings(user_id, from_date = from_date, to_date = to_date, page_size = query_params['page_size'], min_duration=min_duration)
    user = z.get_user_name(user_id)
    email = z.get_user_email(user_id)

    return render_template("list-user-recordings.html", recordings=renderable, user=user, email=email, from_date=from_date, to_date=to_date, month_back=month_back, month_forward=month_forward, dur_check = dur_check, workflow_list = o.get_workflows(), series_list = o.get_series(), acl_list = o.get_acls())

def get_user_list(dbs, q, token=None):
    response = z.search_user(search_key=q, next_page_token=token)
    users = []
    token_quoted = None
    if response and 'contacts' in response.json():
        token = response.json().get('next_page_token', None)
        # double quote token
        token_quoted = urllib.parse.quote(urllib.parse.quote(token, safe=''), safe='')
        users = [{
            'id': item.get('id'),
            'email': item.get('email'),
            'first_name': item.get('first_name'),
            'last_name': item.get('last_name'),
        } for item in response.json().get('contacts')]
    return users, token_quoted

## Handling of a single recording

@app.route('/recording/<path:recording_id>', methods=['GET', 'POST'])
def single_recording(recording_id):
    # We should double quote the recording_id as it may contain, start or end with an /
    recording_id_decoded = urllib.parse.unquote(recording_id)
    logger.debug(f'GETting recording with ID { recording_id_decoded }')
    if request.method == "GET":
        series_id = request.args.get("sid", None)
        acl_id = request.args.get("acl", None)
        query_params = get_query_params()
        query_string = build_query_string()

        renderable = z.get_renderable_recording(recording_id_decoded)
        series = None
        if series_id:
            #The template partially supports autofilling most of the variables based on the series
            # but it's not 100% working, so let's just ignore it completely!
            series = {'identifier': series_id}
            o.get_single_series(series_id)
        acl = None
        if acl_id:
            acl = o.get_single_acl(acl_id)
        workflow_id = None
        query_string = build_query_string(query_params)
        return render_template("ingest-recording.html",
                               recording=renderable,
                               referrer=query_params['oref'],
                               workflow_list=o.get_workflows(),
                               series_list=o.get_series(),
                               series=series,
                               acl_list=o.get_acls(),
                               acl=acl,
                               workflow=workflow_id,
                               query_string=query_string,
                               url_query_string=urllib.parse.quote_plus(query_string),
                               visibility=EPISODE_FIELDS,
                               series_create_enabled=SERIES_CREATE_ENABLED,
                               dur_check=query_params['dur_check'])
    elif request.method == "POST":
        user_id, query_string = _ingest_single_recording(recording_id_decoded)
        return redirect(f'/?{ query_string }')


def _ingest_single_recording(recording_id, dur_check=True):
    logger.info(f"Ingesting for { recording_id }")
    user_id = request.form['origin_email']
    query_string = urllib.parse.unquote_plus(request.form.get('origin_query_string',""))
    params = { key: value for key, value in request.form.items() if not key.startswith("origin") and not key.startswith("bulk_") and not '' == value }
    params['is_webhook'] = False
    params['dur_check'] = dur_check
    _queue_recording(recording_id, params)
    return user_id, query_string


## Handling of a single series

@app.route('/series', defaults={'series_id': None}, methods=['GET', 'POST'])
@app.route('/series/<series_id>', methods=['GET', 'POST']) #FIXME: The GET here only partially renders correctly, POST should be PUT to reflect OC api use of PUT for modifying existing series
def get_series_list(series_id=None):
    if request.method == "GET":
        series = None
        if None != series_id:
            series = o.get_single_series(series_id)
            #TODO: Need to get the theme and acl data from the respective endpoints ({sid}/acl and {sid}/properties -> { 'theme': $id })
        epId = request.args.get('epid', "")
        origin_email = request.args.get('oem', "")
        origin_query_string = request.args.get('oqs', "")
        origin = { "email": origin_email, "query_string": urllib.parse.quote_plus(origin_query_string), "epid": urllib.parse.quote_plus(epId) }
        return render_template("create-series.html", series = series, acl_list = o.get_acls(), theme_list = o.get_themes(), origin = origin, visibility = SERIES_FIELDS)
    elif request.method == "POST":
        #TODO: Validate required terms are present
        epid = urllib.parse.unquote_plus(request.form['origin_epid'])
        origin_query_string = urllib.parse.unquote_plus(request.form['origin_query_string'])
        #Create the series
        new_series_id = o.create_series(**request.form)
        acl_id = request.form.get('acl_id', None)
        #Redirect either to the episode (epId) or back to the create series bits in case of error
        if acl_id:
            return redirect(f'recording/{ epid }?sid={ new_series_id }&acl={ acl_id }')
        return redirect(f'recording/{ epid }?sid={ new_series_id }&{ origin_query_string }')

## Cancelling an ingest

@app.route('/cancel', methods=['GET', 'POST'])
def do_cancels():
    if request.method == "GET":
        current = o.get_in_progress()
        return render_template("cancel-ingest.html", recordings = current)
    else:
        o.cancel_ingest(request.form['ingestid'])
        current = o.get_in_progress()
        return render_template("cancel-ingest.html", recordings = current)

## Deleting an ingest which has already happened (this is mainly for testing)

@app.route('/delete', methods=['GET', 'POST'])
def do_deletes():
    if request.method == "GET":
        current = o.get_finished()
        return render_template("delete-record.html", recordings = current)
    else:
        o.cancel_ingest(request.form['ingestid'])
        current = o.get_finished()
        return render_template("delete-record.html", recordings = current)

@app.route('/', methods=["GET"])
@app.errorhandler(400)
def do_search():
    q = request.args.get('q', None)
    token = request.args.get('token', '')
    query_params = get_query_params()

    logger.debug(f"Running search for query '{ q }'")

    #get recording renderable
    params = { k: v for k, v in query_params.items() }
    params['min_duration'] = int(MIN_DURATION) if query_params['dur_check'] else 0

    recordings = []
    users = []
    token_quoted = ''
    if q and len(q) > 0:
        recordings = z.get_recordings_from_db(q, params['min_duration'])
        logger.debug(f"Found { len(recordings) } recordings matching { q }")
        if token and len(token) > 0:
            token = urllib.parse.unquote(token)
        users, token_quoted = get_user_list(q, token)
        logger.debug(f"Found { len(users) } users matching { q }")
    else:
        q = "" #Dummy value to make rendering look better but also do nothing

    #Remember: params is the existing query param dict from above!
    params['query'] = q
    params['token'] = token_quoted
    params['recordings'] = recordings
    params['users'] = users
    #user=user, email=email,
    params['workflow_list'] = o.get_workflows()
    params['series_list'] = o.get_series()
    params['acl_list'] = o.get_acls()

    return render_template("search.html", **params )

## Bulk ingest support

@app.route('/bulk', methods=['POST'])
@app.errorhandler(400)
def do_bulk():
    logger.debug("Bulk POST recieved")
    form_params = request.form
    event_ids = [ urllib.parse.unquote_plus(name[len("_bulk"):]) for name, value in form_params.items() if value == "on" and name.startswith("bulk_") ]
    logger.debug(f"Bulk ingest for events { event_ids }")

    acl_id = form_params.get("acl_id", "None")
    workflow_id = form_params.get("workflow_id", "None")
    series_id = form_params.get("isParfOf", "None")
    dur_check = form_params.get("dur_check", "True") == "True"
    if not workflow_id:
        logger.error("No workflow ID set")
        return render_template_string("No workflow ID set"), 400
    logger.debug(f"Bulk ingest with workflow { workflow_id } and acl id { acl_id } to series { series_id }")

    for event_id in event_ids:
        user_id, query_string = _ingest_single_recording(event_id, dur_check)
    if request.referrer:
        return redirect(request.referrer)
    else:
        return redirect("/")

## Webhook support

@app.route('/webhook', methods=['POST'])
@app.errorhandler(400)
@db.with_session
def do_POST(dbs):
    logger.debug("POST recieved")

    #If this header is missing this will throw a 400 automatically
    content_length = int(request.headers.get('Content-Length'))
    if content_length < 5:
        logger.error("Content too short")
        return render_template_string("No data received"), 400

    #Check UTF8 safeness of this
    body = request.get_json(force=True)
    if WEBHOOK_SECRET and 'authorization' not in request.headers and WEBHOOK_SECRET != request.headers.get('authorization'):
        logger.error("Request pre-shared secret is incorrect")
        return render_template_string("Request pre-shared secret is incorrect"), 400
    elif "payload" not in body:
        logger.error("Payload is missing")
        return render_template_string("Missing payload field in webhook body"), 400
    elif "event" not in body:
        logger.error("Event is missing")
        return render_template_string("Missing event field in webhook body"), 400

    payload = body["payload"]
    event_type = body["event"]
    obj = None
    try:
        z.validate_recording_payload(payload)
        obj = payload['object']
        if "recording.completed" == event_type:
            logger.debug(f"Validating recording.completed event")
            z.validate_recording_object(obj)
            logger.debug(f"Validated recording.completed event for { obj['uuid'] }, processing.")
        elif "recording.renamed" == event_type:
            logger.debug(f"Validating recording.renamed event")
            z.validate_recording_renamed(payload)
            uuid = obj['uuid']
            existing_db_recording = dbs.query(db.Recording).filter(db.Recording.uuid == uuid).one_or_none()
            if existing_db_recording:
                existing_db_recording.set_title(obj['topic'])
                dbs.merge(existing_db_recording)
                dbs.commit()
                logger.debug(f"Recieved a rename event for event { uuid }, renamed to { obj['topic'] }.  No further processing.")
                return f"Recieved a rename event for event { uuid }, renamed to { obj['topic'] }.  No further processing."

            #In this case, we've recieved a rename for something that's *not* in the database
            #So we treat it as if it's a normal recording complete webhook event
            logger.debug(f"Recieved a rename event for event { uuid }, processing.")
            #Swap out the contents of obj
            #before this line it's a small blob giving you the uuid and new name
            obj = z.get_recording(uuid)
            #Validate it again, just in case Zoom changes something
            z.validate_recording_object(obj)
        else:
            self.logger.info(f"Unknown event type { event_type }, but passing initial validations.  Unable to continue processing this event.")
            return f"Unable to ingest, unkonwn event type { event_type }"
    except BadWebhookData as e:
        logger.exception("Payload failed validation")
        return render_template_string("Payload failed validation"), 400
    except NoMp4Files as e:
        logger.error("No mp4 files found!")
        return render_template_string("No mp4 files found!"), 400

    uuid = obj['uuid']
    if "download_token" in body:
        token = body["download_token"]
        logger.debug(f"Token is {token}")
    else:
        token = None
        logger.debug("Token missing, using None")

    zingest_params = {
        'is_webhook': True,
        'workflow_id': WEBHOOK_WORKFLOW,
        'title': obj['topic'],
        'creator': z.get_user_name(obj['host_id']),
        'date': obj['start_time'],
        'duration': obj['duration']
    }
    #At startup we've checked that at least one of these is set already
    if len(WEBHOOK_SERIES) > 0:
        zingest_params['isPartOf'] = WEBHOOK_SERIES
    if len(WEBHOOK_ACL) > 0:
        zingest_params['acl_id'] = WEBHOOK_ACL

    return _queue_recording(uuid, zingest_params, token)

## Actually ingesting the recording (validating things, creating the rabbit message)

@db.with_session
def _queue_recording(dbs, uuid, zingest, token=None):

    #Check if the recording exists, and create it if it does not
    existing_rec = dbs.query(db.Recording).filter(db.Recording.uuid == uuid).one_or_none()
    if not existing_rec:
        existing_rec = z.create_recording_from_uuid(uuid)

    check_duration = zingest['dur_check'] if 'dur_check' in zingest else True
    is_webhook = zingest['is_webhook'] if 'is_webhook' in zingest else False

    logger.debug(f"Checking duration: { check_duration }")
    logger.debug(f"Is a webhook event: { is_webhook }")

    if not WEBHOOK_ENABLE and is_webhook:
        self.logger.debug("Incoming POST is a webhook event, and the webhook is disabled!")
        return render_template_string("Webhook disabled!"), 405

    #Check the duration if the event is a webhook event, or we've told it to for manual events
    if existing_rec.get_duration() < MIN_DURATION and (is_webhook or check_duration):
        logger.error("Recording is too short")
        return render_template_string("Recording is too short"), 400
    elif not recording_filter.matches(existing_rec.get_title()) and is_webhook: #Only filter on webhook events
        logger.info(f"Recording { uuid } does not match the configured filter")
        return render_template_string(f"Recording { uuid } did not match configured filter(s) and has been dropped"), 200
    elif is_webhook and dbs.query(db.Ingest).filter(db.Ingest.uuid == uuid, db.Ingest.webhook_ingest == True).one_or_none():
        logger.info(f"Not creating a new ingest for { uuid } via webhook event because it has already created one")
        return render_template_string(f"Not creating a new ingest for { uuid } via webhook event because it has already created one"), 200

    #Ensure the required metadata is all present
    renderable = z.get_renderable_recording(uuid)
    for el in ('title', 'date', 'time', 'duration'):
        if el not in zingest:
            zingest[el] = renderable[el]
    if 'date' in zingest and 'time' in zingest:
        date = zingest['date']
        time = zingest['time']
        expected_format = "%Y-%m-%dT%H:%M:%SZ"
        #Ensure this parses correctly, if not then set the date param with the combination of date and time
        try:
            parsed = datetime.strptime(date, expected_format)
        except ValueError as e:
            zingest['date'] = datetime.strptime(f"{ date }T{ time }Z", expected_format).strftime(expected_format)
    if "creator" not in zingest:
        zingest["creator"] = renderable["host"]

    #Create the ingest record
    logger.debug(f"Creating ingest record for { uuid } with params { zingest }")
    ingest_id = db.create_ingest(uuid, zingest)

    logger.debug("Sending rabbit message")
    r.send_rabbit_msg(uuid, ingest_id)

    logger.debug("POST processed successfully")
    return f"Successfully sent { uuid } to rabbit"


if __name__ == "__main__":
    app.run()
