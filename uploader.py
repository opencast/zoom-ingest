import pika
import json
import requests
import wget
import os
from requests.auth import HTTPDigestAuth
import sys
import configparser

url = ""
user = ""
password = ""


def rcv_rabbit_callback(method, properties, body):
    data = json.loads(body.decode("utf-8"))
    if "recording_files" not in data:
        print("No recording found")
    files = data["recording_files"]
    dl_url = ''
    id = ''
    for key in files[0].keys():
        if key == "download_url":
            dl_url = files[0][key]
        elif key == "recording_id":
            id = files[0][key]

    print(dl_url+'/?access_token='+data["token"])
    try:

        wget.download(dl_url+'/?access_token='+data["token"],id+'.mp4')


    except Exception as e:
        print("Could not download file {}".format(e))
        print(e)

    print("id : " +  id)
    oc_upload(data["creator"],data["topic"], id)


def oc_upload(creator,title, rec_id):

    response = requests.get(url + '/admin-ng/series/series.json', auth=HTTPDigestAuth(user, password),
                            headers={'X-Requestedresponse = -Auth': 'Digest'})

    series_list = json.loads(response.content.decode("utf-8"))
    try:
        username = creator[:creator.index("@")]
    except ValueError:
        print("Invalid username: '@' is missing, upload is aborted")
        username = "Dennis Pfahl"
    series_title = "Zoom Recordings "+username
    series_found = False
    for series in series_list["results"]:
        if series["title"] == series_title:
            series_found = True
            id = series["id"]

    if not series_found:
        id = create_series(creator, series_title)

    with open(rec_id+'.mp4', 'rb') as fobj:
        data = {"title": title, "creator": creator, "isPartOf": id, "flavor": 'presentation/source'}
        body = {'body': fobj}
        requests.post(url + '/ingest/addMediaPackage', data=data, files=body, auth=HTTPDigestAuth(user, password),
                                 headers={'X-Requested-Auth': 'Digest'})

    os.remove(rec_id+'.mp4')



def start_consuming_rabbitmsg():
    credentials = pika.PlainCredentials("adminuser", "EYeeWiz4uvuowei9")
    rcv_connection = pika.BlockingConnection(pika.ConnectionParameters('zoomctl.ssystems.de', credentials=credentials))
    rcv_channel = rcv_connection.channel()
    queue = rcv_channel.queue_declare(queue="zoomhook")
    msg_count = queue.method.message_count
    while msg_count > 0:
        method,prop,body =rcv_channel.basic_get(queue="zoomhook", auto_ack=True)
        rcv_rabbit_callback(method,prop,body)
        count_queue = rcv_channel.queue_declare(queue="zoomhook", passive=True)
        msg_count = count_queue.method.message_count
    rcv_channel.close()
    rcv_connection.close()

def create_series(creator,title):

    print("creating series")
    metadata = [{"label": "Opencast Series DublinCore",
                 "flavor": "dublincore/series",
                 "fields": [{"id": "title",
                             "value": title},
                            {"id": "creator",
                             "value": [creator]}]}]

    acl = [{"allow": True,
            "action": "write",
            "role": "ROLE_AAI_USER_"+creator},
           {"allow": True,
            "action": "read",
            "role": "ROLE_AAI_USER_"+creator}]

    data = {"metadata": json.dumps(metadata),
            "acl": json.dumps(acl)}

    response = requests.post(url+'/api/series',data=data,auth=HTTPDigestAuth(user, password),headers={'X-Requested-Auth': 'Digest'})

    print(response.status_code)

    return json.loads(response.content.decode("utf-8"))["identifier"]


if __name__ == '__main__':

    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
    except FileNotFoundError:
        sys.exit("No settings found")

    try:
        url = config["Opencast"]["Url"]
        user = config["Opencast"]["User"]
        password = config["Opencast"]["Password"]
    except KeyError as err:
        sys.exit("Key {0} was not found".format(err))

    start_consuming_rabbitmsg()
