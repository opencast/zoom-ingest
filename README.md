Opencast-Zoom-Ingester
============================

This utility provides a (very basic) web ui for manually triggering the download,
and ingestion of Zoom cloud recordings into your Opencast infrastructure.  This is
designed to be scalable in terms of number of parallel downloads, while also being
run entirely on local infrastructure.

**Requirements**

Python packages:

- `pika`
- `requests`
- `zoomus`
- `flask`
- `gunicon`
- `sqlalchemy`
- `xmltodict`
- `mysqlclient` (Optional)

External dependencies:

- A RabbitMQ server
- A database server (tested against sqlite and mariadb 10)

**Basic configuration:**

The settings.ini file contains the configuration for this utility.  There are five sections at present:

1. Logging:

The `debug` key controls verbose logging.  For production use you probably want this to be false.

2. Zoom:

This is where you configure your [Server-To-Server OAUTH](https://developers.zoom.us/docs/internal-apps/s2s-oauth/)
application.  You *must* create such an application in your account - this tool is not something you can subscribe to
directly.

To create the app:
- Create a Server-to-Server OAuth App within the Zoom App Marketplace.  Beware, there is an standard (non
  server-to-server) OAuth application type which will not work for this application.
  - Your `Account ID`, `Client ID`, and `Client Secret` should be copied into the `Zoom` section in settings.ini.
- Grant the following scopes to your application:
  - `/contact:read:admin`, which is required for user search to work
  - `/user:read:admin`, which is required for user search to work, and user data to populate
  - `/recording:read:admin`, which is required to read the recording data

3. Webhook:

This utilty supports event-driven ingestion.  When a meeting finishes it would be automatically ingested.
This is *disabled* unless both a default workflow id, and one of series or acl id are set.  The webhook must also be
configured on Zoom's end, which is done in the Zoom app under the `Feature` section of the Zoom app you created above.
To accomplish this you must:
- Enable `Event Subscriptions` for the following events:
  - `All Recordings have completed`
  - `Recording Renamed` events.
For security purposes the `Secret Token` from the `Features` section in Zoom should be copied to the `secret` config
key.  Without this secret the incoming webhook POSTs are not verified to be originating from Zoom's servers.

4. Opencast:

This is the url, as well as user and password to use when ingesting to Opencast.  Note that this has only
been tested with the digest user, although there is no reason it would not work with another user.  Note that
the code assumes the use of the digest user, so if non-digest users are required some minor modifications will be
required.

5. Rabbit:

This is the rabbit credentials and url.  I have been testing with rabbit running in docker (via `rabbit.sh`),
and everything worked out of the box without further configuration.  Rabbit running outside of Docker might
require additional fiddling.

**Installation**

The packages above can all be installed via *pip*: `pip3 install -r requirements.txt`.  Note that depending
on the target database you may need to install system packages, and/or additional pip packages.  Notably, on my
Debian system the mysql python bindings required the mysql client *development* package to provide the relevant
headers.  For public release I plan to test against a wider variety of system so these instructions can be more
complete.

**Database Setup**

A user with appropriate permissions for an existing (preferably blank) schema should exist.  SQLAlchemy will
create the tables it requires at runtime.

**Usage**

This utiltiy contains two major components: The webhook, and the uploader.

Webhook: This module provides the webhook, as well as the UI that your users interact with.
When a webhook fires, or a user triggers an ingest, a message is generated and sent to RabbitMQ.
This queue spools the requests for later ingestion by the uploader(s).  To run the webhook use
`webhook.sh`.  This starts a server on port 8000 listening on all interfaces.  Note that this
server provides *no* authentication at all, so please apply appropriate firewall ACLs.

Uploader: This module listens for events in the Rabbit queue, and then ingests those recordings
to Opencast.  This module can be run in parallel across multiple machines, provided that there
is a shared database for all the nodes to connect to so that state can be maintained.
