import json
import logging
import string
from datetime import datetime
from functools import wraps

from sqlalchemy import Column, Integer, String, LargeBinary, DateTime, \
    create_engine
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
        db = config['Database']['database']
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
    rec.update_status(Status.NEW)
    dbs.merge(rec)
    dbs.commit()


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

    def set_workflow_id(self, workflow_id):
        self.workflow_id = workflow_id

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


class User(Base):
    """Database definition of a Zoom user."""

    __tablename__ = 'user'

    user_id = Column('user_id', String(length=32), nullable=False, primary_key=True)
    updated = Column('updated', DateTime(), nullable=False, default=datetime.utcnow())

    def _init__(self, user_name):
        self.user_id = user_name
        self.updated = datetime.utcnow()

