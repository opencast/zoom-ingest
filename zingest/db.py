import json
import logging
import string
from datetime import datetime
from functools import wraps

from sqlalchemy import Column, Integer, String, LargeBinary, DateTime, \
    Boolean, create_engine, func, or_, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


def init(config):
    """
    Initialize connection to database. Additionally the basic database
    structure will be created if nonexistent.
    :param config:
    """
    global engine
    log = logging.getLogger(__name__)
    db = 'sqlite:///zoom.db'
    if "Database" in config and "database" in config['Database']:
        db = (config['Database']['database']).strip()
        engine = create_engine(db)
        log.info("Database connection string loaded from config file")
    else:
        log.warn("Using default SQLite database, this is probably not what you want!")
        engine = create_engine(db)
    Base.metadata.create_all(engine)


def get_session():
    """
    Get a session for database communication. If necessary a new connection
    to the database will be established.

    :return:  Database session
    """
    if 'engine' not in globals():
        init()
    Session = sessionmaker(bind=engine)
    return Session()


def with_session(f):
    """
    Wrapper for f to make a SQLAlchemy session present within the function

    :param f: Function to call
    :type f: Function
    :raises e: Possible exception of f
    :return: Result of f
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        session = get_session()
        try:
            result = f(session, *args, **kwargs)
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
        return result
    return decorated


@with_session
def create_recording(dbs, j):
    rec = Recording(j)
    dbs.add(rec)
    dbs.commit()
    dbs.refresh(rec)
    return rec

@with_session
def create_recording_if_needed(dbs, j):
    existing_recording = dbs.query(Recording).filter(Recording.uuid == j['uuid']).one_or_none()
    if not existing_recording:
        existing_recording = create_recording(j)
    return existing_recording

@with_session
def create_ingest(dbs, uuid, params):
    ingest = Ingest(uuid, params)
    dbs.add(ingest)
    dbs.commit()
    dbs.refresh(ingest)
    return ingest.get_id()

@with_session
def ensure_user(dbs, user_id, first_name, last_name, email):
    existing_user = dbs.query(User).filter(User.user_id == user_id).one_or_none()
    if not existing_user:
        existing_user = create_user(user_id, first_name, last_name, email)
    else:
        #This isn't likely, but if the user's details change we should keep them up to date
        if existing_user.first_name != first_name or \
           existing_user.last_name != last_name or \
           existing_user.email != email:
            existing_user.update(first_name, last_name, email)
            dbs.merge(existing_user)
            dbs.commit()
    return existing_user

@with_session
def create_user(dbs, user_id, first_name, last_name, email):
    user = User(user_id, first_name, last_name, email)
    dbs.add(user)
    dbs.merge(user)
    dbs.commit()
    dbs.refresh(user)
    return user


def __ilike(thing, searches):
    return [ thing.ilike(search) for search in searches ]

def __wildcard(thing):
    return [ f"%{ element }%" for element in thing.lower().split(" ") ]

@with_session
def find_recordings_matching(dbs, title=None, user=None, date=None):
    log = logging.getLogger(__name__)
    #TODO: Verify that this is safe, SQL-wise
    title_preds, user_preds, date_preds = [], [], []
    if title:
        wildcarded = __wildcard(title)
        log.debug(f"Title searching for { wildcarded }")
        title_preds = __ilike(Recording.title, wildcarded)
    if user:
        wildcarded = __wildcard(user)
        log.debug(f"User searching for { wildcarded }")
        user_preds.extend(__ilike(User.first_name, wildcarded))
        user_preds.extend(__ilike(User.last_name, wildcarded))
        user_preds.extend(__ilike(User.email, wildcarded))
        #NB: We're expanding the contents of the user_preds list
        user_preds = or_( *user_preds )
    if date:
        wildcarded = __wildcard(date)
        log.debug(f"Date searching for { wildcarded }")
        date_preds = __ilike(Recording.start_time, wildcarded)

    if not user and len(title_preds) + len(date_preds) < 1:
        return []

    pred = and_( *title_preds, *date_preds)
    #NB: We're expanding the contents of the title and date preds here,
    #but *not* the user preds because we've already done that above
    if user:
        pred = and_( *title_preds, *date_preds, user_preds)
    return dbs.query(Recording).filter(pred).all()


@with_session
def find_users_matching(dbs, query):
    #TODO: Verify that this is safe, SQL-wise
    wildcarded = [ f"%{ element }%" for element in query.lower().split(" ") ]
    return dbs.query(User).filter(or_(
                User.first_name.ilike(wildcarded),
                User.last_name.ilike(wildcarded),
                User.email.ilike(wildcarded)
            )).all()


class Constants:

    @classmethod
    def str(cls, value):
        """Convert status (id) to its string name."""
        for k, v in cls.__dict__.items():
            if k[0] in string.ascii_uppercase and v == value:
                return k.lower().replace('_', ' ')


class Status(Constants):
    """Event status definitions"""
    NEW = 0
    IN_PROGRESS = 1
    FINISHED = 2


# Database Schema Definition
class Recording(Base):
    """Database definition of a recording."""

    __tablename__ = 'recording'

    rec_id = Column('id', Integer, primary_key=True)
    duration = Column('duration', Integer, nullable=False)
    uuid = Column('uuid', String(length=32), nullable=False)
    user_id = Column('user_id', String(length=32), nullable=False)
    start_time = Column('start_time', String(length=32), nullable=False)
    title = Column('title', String(length=256), nullable=False)

    def __init__(self, data):
        self.uuid = data['uuid']
        self.duration = int(data['duration'])
        self.user_id = data['host_id']
        self.start_time = data['start_time']
        self.title = data['topic']

    def set_title(self, new_title):
        self.title = new_title

    def get_id(self):
        return self.rec_id

    def get_title(self):
        return self.title

    def get_duration(self):
        return self.duration

    def get_rec_id(self):
        return self.uuid

    def get_user_id(self):
        return self.user_id

    def serialize(self):
        """
        Serialize this object as dictionary usable for conversion to JSON.

        :return: Dictionary representing this object.
        """
        return {
            'id': self.uuid,
            'title': self.title,
            'date': self.start_time[:10],
            'time': self.start_time[11:-1],
            'datetime': self.start_time,
            'duration': self.duration,
            'host': self.user_id
        }


class Ingest(Base):
    """Database definition of an ingest to Opencast."""

    __tablename__ = 'ingest'

    ingest_id = Column('id', Integer(), primary_key=True, autoincrement=True)
    uuid = Column('uuid', String(length=32), nullable=False)
    status = Column('status', Integer(), nullable=False,
                    default=Status.NEW)
    timestamp = Column('timestamp', DateTime(), nullable=False,
                    default=datetime.utcnow())
    webhook_ingest = Column('is_webhook', Boolean(), default=False, nullable=False)
    params = Column('zingest_parms', LargeBinary(), nullable=False)
    mediapackage_id = Column('mediapackage_id', String(length=36), nullable=True, default=None)
    workflow_id = Column('workflow_id', String(length=36), nullable=True, default=None)

    def __init__(self, uuid, params="{}"):
        self.uuid = uuid
        if 'is_webhook' in params:
            self.webhook_ingest = str(params['is_webhook']) in ['true', 'True']
        self.params = json.dumps(params).encode('utf-8')
        self.update_status(Status.NEW)
        self.mediapackage_id = None
        self.workflow_id = None

    def get_id(self):
        return self.ingest_id

    def get_recording_id(self):
        return self.uuid

    def get_params(self):
        return self.params

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

    def serialize(self):
        """
        Serialize this object as dictionary usable for conversion to JSON.

        :return: Dictionary representing this object.
        """
        return {
            'uuid': self.uuid,
            'status': self.status,
            'mediapackage_id': self.mediapackage_id,
            'workflow_id': self.workflow_id,
        }

class User(Base):
    """Database definition of a Zoom user."""

    __tablename__ = 'user'

    user_id = Column('user_id', String(length=32), nullable=False, primary_key=True)
    first_name = Column('first_name', String(length=32), nullable=False)
    last_name = Column('last_name', String(length=32), nullable=False)
    email = Column('email', String(length=256), nullable=False) #yay RFC 5321
    updated = Column('updated', DateTime(), nullable=False, default=datetime.utcnow())

    def __init__(self, user_name, first_name, last_name, email):
        self.user_id = user_name
        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.updated = datetime.utcnow()

    def update(self, first_name, last_name, email):
        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.updated = datetime.utcnow()
