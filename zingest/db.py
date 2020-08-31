import json
import string
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Text, LargeBinary, DateTime, \
    create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from functools import wraps
Base = declarative_base()


def init():
    '''Initialize connection to database. Additionally the basic database
    structure will be created if nonexistent.
    '''
    global engine
    engine = create_engine('sqlite:///zoom.db')
    Base.metadata.create_all(engine)


def get_session():
    '''Get a session for database communication. If necessary a new connection
    to the database will be established.

    :return:  Database session
    '''
    if 'engine' not in globals():
        init()
    Session = sessionmaker(bind=engine)
    return Session()


def with_session(f):
    """Wrapper for f to make a SQLAlchemy session present within the function

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


class Constants():

    @classmethod
    def str(cls, value):
        '''Convert status (id) to its string name.'''
        for k, v in cls.__dict__.items():
            if k[0] in string.ascii_uppercase and v == value:
                return k.lower().replace('_', ' ')


class Status(Constants):
    '''Event status definitions
    '''
    NEW = 0
    IN_PROGRESS = 1
    FINISHED = 2


# Database Schema Definition
class Recording(Base):
    '''Database definition of a recording.'''

    __tablename__ = 'recording'

    uuid = Column('uuid', Text(), nullable=False, primary_key=True)
    user_id = Column('user_id', Text(), nullable=False)
    data = Column('data', LargeBinary(), nullable=False)
    status = Column('status', Integer(), nullable=False,
                    default=Status.NEW)
    timestamp = Column('timestamp', DateTime(), nullable=False,
                    default=datetime.utcnow())

    def __init__(self, data):
        self.uuid = data['uuid']
        self.user_id = data['host_id']
        self.data = json.dumps(data).encode('utf-8')
        self.update_status(Status.NEW)

    def get_data(self):
        '''Load JSON data from event.
        '''
        return json.loads(self.data.decode('utf-8'))

    def status_str(self):
        '''Return status as string.
        '''
        return Status.str(self.status)

    def update_status(self, new_status):
        self.status = new_status
        self.timestamp = datetime.utcnow()

    def __repr__(self):
        '''Return a string representation of an artist object.

        :return: String representation of object.
        '''
        return f'<Recording(uuid={self.uuid}, status={self.status})>'

    def serialize(self):
        '''Serialize this object as dictionary usable for conversion to JSON.

        :return: Dictionary representing this object.
        '''
        return {
            'uuid': self.uid,
            'user_id': self.user_id,
            'data': self.get_data(),
            'status': self.status_str(),
            'timestamp': str(self.timestamp)
        }

