from zoomus import ZoomClient
import json

class Zoom:

    def __init__(self, config):
        api_key = config['JWT']['Key']
        api_secret = config['JWT']['Secret']  
        self.zoom_client = ZoomClient(api_key, api_secret)

    def validate_payload(self, payload):

        required_payload_fields = [
            "object"
        ]
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
            "recording_type"
        ]

        try:
            for field in required_payload_fields:
                if field not in payload.keys():
                    raise BadWebhookData(
                        "Missing required payload field '{}'. Keys found: {}"
                            .format(field, payload.keys()))

            obj = payload["object"]
            for field in required_object_fields:
                if field not in obj.keys():
                    raise BadWebhookData(
                        "Missing required object field '{}'. Keys found: {}"
                            .format(field, obj.keys()))

            files = obj["recording_files"]

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
                        raise BadWebhookData(
                            "Missing required file field '{}'".format(field))
                if "status" in file and file["status"].lower() != "completed":
                    raise BadWebhookData(
                        "File with incomplete status {}".format(file["status"])
                    )

        except NoMp4Files:
            # let these bubble up as we handle them differently depending
            # on who the caller is
            raise
        except Exception as e:
            raise BadWebhookData("Unrecognized payload format. {}".format(e))


    def parse_recording_files(self, payload):
        recording_files = []
        for file in payload["object"]["recording_files"]:
            if file["file_type"].lower() == "mp4":
                recording_files.append({
                    "recording_id": file["id"],
                    "recording_start": file["recording_start"],
                    "recording_end": file["recording_end"],
                    "download_url": file["download_url"],
                    "file_type": file["file_type"],
                    "recording_type": file["recording_type"]
                })
        return recording_files

    def get_recording_creator(self, payload):
        #user_list_response = self.zoom_client.user.get(id=payload["object"]["host_id"])
        #user_list = json.loads(user_list_response.content.decode("utf-8"))
        #return user_list['email']
        return "test@example.org"
