import json
import configparser
import sys
from urllib.parse import urlencode
from pprint import pformat

from markupsafe import escape
from flask import Flask, request, render_template, redirect, url_for
import logging
from datetime import datetime
import zingest.logger
from zingest.rabbit import Rabbit
from zingest.zoom import Zoom
from zingest.opencast import Opencast
import zingest.db

MIN_DURATION = 0

logger = logging.getLogger("webhook")
logger.info("Startup")

try:
    config = configparser.ConfigParser()
    config.read('settings.ini')
except FileNotFoundError:
    sys.exit("No settings found")

try:
    if bool(config['logging']['debug']):
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    PORT_NUMBER = int(config["Webhook"]["Port"])
    logger.debug(f"Webhook port is {PORT_NUMBER}")
    HOST_NAME = config["Webhook"]["Url"]
    logger.debug(f"Hostname is {HOST_NAME}")
    MIN_DURATION = int(config["Webhook"]["Min_Duration"])
    logger.debug(f"Minimum duration is is {MIN_DURATION}")
except KeyError as err:
    sys.exit("Key {0} was not found".format(err))
except ValueError as err:
    sys.exit("Invalid value, integer expected : {0}".format(err))

zingest.db.init(config)
z = Zoom(config)
r = Rabbit(config, z)
o = Opencast(config, r, z)

app = Flask(__name__)


def validate_date(date_string):
    if date_string != None:
        return datetime.strptime(date_string, '%Y-%m-%d').date()
    else:
        return None


def get_query_params():
    from_date = validate_date(request.args.get('from', None))
    to_date = validate_date(request.args.get('to', None))
    page_size = request.args.get('page_size', None)
    return { 'from': from_date, 'to': to_date, 'page_size': page_size }


def build_query_string(param_dict = None):
    if None == param_dict:
        param_dict = get_query_params()
    clean_dict = { key: value for key, value in param_dict.items() if None != value }
    query_string = urlencode(clean_dict)
    logger.debug(f"Query string is { query_string }")
    return query_string


@app.route('/recordings/<user_id>', methods=['GET'])
def do_list_recordings(user_id):
    query_params = get_query_params()
    query_string = build_query_string(query_params)

    renderable = z.get_user_recordings(user_id, from_date = query_params['from'], to_date = query_params['to'], page_size = query_params['page_size'])
    for item in renderable:
        item['url'] = f'/recording/{ item["id"] }?{ query_string }'
    return render_template("recordings.html", recordings=renderable, user=user_id)


@app.route('/recording/<recording_id>', methods=['GET', 'POST'])
def single_recording(recording_id):
    if request.method == "GET":
        series_id = request.args.get("sid", None)
        acl_id = request.args.get("acl", None)
        query_string = build_query_string()
        return get_single_recording(recording_id, series_id = series_id, query_string = query_string)
    elif request.method == "POST":
        return ingest_single_recording(recording_id)


def get_single_recording(recording_id, series_id = None, acl_id = None, workflow_id = None, query_string=None):
    renderable = z.get_renderable_recording(recording_id)
    series = None
    if series_id:
        series = o.get_single_series(series_id)
    acl = None
    if acl_id:
        acl = o.get_single_acl(acl_id)
    return render_template("ingest.html", recording=renderable, workflow_list = o.get_workflows(), series_list = o.get_series(), series = series, acl_list = o.get_acls(), acl = acl, workflow = workflow_id, query_string = query_string)


def ingest_single_recording(recording_id):
    logger.info(f"Ingesting for { recording_id }")
    user_id = request.form['origin_email']
    query_string = request.form['origin_query_string']
    #TODO: Validate required terms are present
    #TODO: Handle upload failure
    recording_json = z.get_recording(recording_id)
    params = { key: value for key, value in request.form.items() if not key.startswith("origin") and not '' == value }
    recording_json['zingest_params'] = params
    _queue_recording(recording_json)
    return redirect(f'/recordings/{ user_id }?{ query_string }')


@app.route('/series', defaults={'series_id': None}, methods=['GET', 'POST'])
@app.route('/series/<series_id>', methods=['GET', 'POST']) #FIXME: The GET here only partially renders correctly, POST should be PUT to reflect OC api use of PUT for modifying existing series
def get_series_list(series_id=None):
    if request.method == "GET":
        series = None
        if None != series_id:
            series = o.get_single_series(series_id)
            #TODO: Need to get the theme and acl data from the respective endpoints ({sid}/acl and {sid}/properties -> { 'theme': $id })
        epId = request.args.get('epid', "")
        return render_template("series.html", series = series, acl_list = o.get_acls(), theme_list = o.get_themes(), origin_epid = epId)
    elif request.method == "POST":
        #TODO: Validate required terms are present
        epid = request.form['origin_epid']
        #Create the series
        new_series_id = o.create_series(**request.form)
        acl_id = request.form.get('acl_id', None)
        #Redirect either to the episode (epId) or back to the create series bits in case of error
        if acl_id:
            return redirect(f'recording/{ epid }?sid={ new_series_id }&acl={ acl_id }')
        return redirect(f'recording/{ epid }?sid={ new_series_id }')




@app.route('/', methods=['GET'])
def do_GET():
    users = z.list_available_users()
    return render_template("users.html", users = users)


@app.route('/', methods=['POST'])
@app.errorhandler(400)
def do_POST():
    """Respond to Webhook"""
    logger.debug("POST recieved")
    content_length = int(request.headers.get('Content-Length'))
    if content_length < 5:
        logger.error("Content too short")
        return render_template_string("No data received", ""), 400

    #Check UTF8 safeness of this
    body = request.get_json(force=True)
    if "payload" not in body:
        logger.error("Payload is missing")
        return render_template_string("Missing payload field in webhook body", ""), 400

    payload = body["payload"]
    try:
        z.validate_payload(payload)
    except BadWebhookData as e:
        logger.error("Payload failed validation")
        return render_template_string("Payload failed validation", ""), 400

    if "download_token" in body:
        token = body["download_token"]
        logger.debug(f"Token is {token}")
    else:
        token = None
        logger.debug("Token missing, using None")

    return _queue_recording(payload['object'], token)


def _queue_recording(obj, token=None):
    try:
        z.validate_object(obj)
    except BadWebhookData as e:
        logger.error("Object failed validation")
        return render_template_string("Object failed validation", ""), 400
    except NoMp4Files as e:
        logger.error("No mp4 files found!")
        return render_template_string("No mp4 files found!", ""), 400

    if obj["duration"] < MIN_DURATION:
        logger.error("Recording is too short")
        return render_template_string("Recording is too short", ""), 400

    logger.debug("Sending rabbit message")
    r.send_rabbit_msg(obj, token)

    logger.debug("POST processed successfully")
    return f"Successfully sent { obj['uuid'] } to rabbit"

