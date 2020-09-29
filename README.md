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

External dependencies:

- A RabbitMQ server
- A database server (tested against sqlite and mariadb 10)

**Basic configuration:**

The settings.ini file contains the configuration for this utility.  There are five sections at present:

1. Logging:

The `debug` key controls verbose logging.  For production use you probably want this to be false.

2. JWT:

This is the [JSON Web Token](https://jwt.io/) configuration data you get from Zoom.  For now ETH will
have to manually create their app at https://marketplace.zoom.us/develop/create from the main ETH account.
Longer term the goal here would be to have this in the Zoom market, but that is still pending.

3. Webhook:

This utilty supports event-driven ingestion.  When a meeting finishes it would be automatically ingested.
This is *disabled* by default, so for now leave this section alone.  The URL and port are where the webhook
should listen, and must match the settings in Zoom, were you to conigure webhooks.

4. Opencast:

This is the url, as well as user and password to use when ingesting to Opencast.  Note that this has only
been tested with the digest user, although there is no reason it would not work with another user.

5. Rabbit:

This is the rabbit credentials and url.  I have been testing with rabbit running in docker (via `rabbit.sh`),
and everything worked out of the box without further configuration.  Rabbit running outside of Docker might
require additional fiddling.

**Installation**

The packages above can all be installed via *pip*: `pip3 install -r requirements.txt`.  Note that depending
on the target database you may need to install system packages, and/or additional pip packages.

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
