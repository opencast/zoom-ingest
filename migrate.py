import configparser
import logging
import os.path
import sys
import urllib.parse
from datetime import datetime, date, timedelta
from urllib.parse import urlencode, parse_qs
import json
from zingest.db import Status
from sqlalchemy import Column, Integer, String, LargeBinary, DateTime, \
    Boolean, create_engine, func, or_, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from logger import init_logger
from zingest.zoom import Zoom
from zingest import db


try:
    config = configparser.ConfigParser()
    if os.path.isfile("etc/zoom-ingest/settings.ini"):
        config.read("etc/zoom-ingest/settings.ini")
    else:
        config.read("/etc/zoom-ingest/settings.ini")
except FileNotFoundError:
    sys.exit("No settings found")

db.init(config)
z = Zoom(config)


#DB schema definition.  Cribbed from blob/aeaade282ed0738b46fb2cb7dc88872a7e081b44
#NB: Depends on the old data being in the recording_old table, and the prexisting user table being deleted

Base = declarative_base()
# Database Schema Definition
class OldRecording(Base):
    """Database definition of a recording."""

    __tablename__ = 'recording_old'

    rec_id = Column('id', Integer, primary_key=True)
    uuid = Column('uuid', String(length=32), nullable=False)
    user_id = Column('user_id', String(length=32), nullable=False)
    data = Column('data', LargeBinary(), nullable=False)
    status = Column('status', Integer(), nullable=False,
                    default=Status.NEW)
    timestamp = Column('timestamp', DateTime(), nullable=False,
                    default=datetime.utcnow())
    mediapackage_id = Column('mediapackage_id', String(length=36), nullable=True, default=None)
    workflow_id = Column('workflow_id', String(length=36), nullable=True, default=None)

    def __init__(self, data):
        self.uuid = data['uuid']
        self.user_id = data['host_id']
        self.data = json.dumps(data).encode('utf-8')
        self.update_status(Status.NEW)
        self.mediapackage_id = None
        self.workflow_id = None

    def get_id(self):
        return self.rec_id

    def get_data(self):
        """Load JSON data from event."""
        return json.loads(self.data.decode('utf-8'))

    def get_user_id(self):
        return self.user_id

    def status_str(self):
        """Return status as string."""
        return Status.str(self.status)

    def update_status(self, new_status):
        self.status = new_status
        self.timestamp = datetime.utcnow()

    def set_mediapackage_id(self, mediapackage_id):
        self.mediapackage_id = mediapackage_id

    def get_mediapackage_id(self):
        return self.mediapackage_id

    def set_workflow_id(self, workflow_id):
        self.workflow_id = workflow_id

    def get_workflow_id(self):
        return self.workflow_id

    def __repr__(self):
        """
        Return a string representation of an artist object.

        :return: String representation of object.
        """
        return f'<Recording(uuid={self.uuid}, status={self.status})>'

    def serialize(self):
        """
        Serialize this object as dictionary usable for conversion to JSON.

        :return: Dictionary representing this object.
        """
        return {
            'uuid': self.uid,
            'user_id': self.user_id,
            'data': self.get_data(),
            'status': self.status_str(),
            'wf_id': self.workflow_id,
            'timestamp': str(self.timestamp)
        }


@db.with_session
def migrate(dbs):
    recordings = dbs.query(OldRecording).all()
    count = len(recordings)
    counter = 1
    for recording in recordings:
        print(f"Processing { counter }/ {count }")
        data = recording.get_data()
        #Why yes there is at least one entry with a very strange character.  You don't need that, remove it.
        data['topic'] = data['topic'].replace('\u200b', '')
        data['zingest_params']['title'] = data['zingest_params']['title'].replace('\u200b', '')
        zingest = data['zingest_params']
        names = zingest['creator'].split(', ')
        print(data)
        rec = db.create_recording(data)
        db.ensure_user(rec.get_user_id(), names[1], names[0], z.get_user_email(rec.get_user_id()))
        ingest_id = db.create_ingest(rec.get_rec_id(), zingest)
        dbs.commit()

        ingest = dbs.query(db.Ingest).filter(db.Ingest.ingest_id == ingest_id).first()
        ingest.set_workflow_id(recording.get_workflow_id())
        ingest.set_mediapackage_id(recording.get_mediapackage_id())
        ingest.update_status(recording.status)
        dbs.commit()
        counter = counter + 1

migrate()
