#!/bin/bash

#FLASK_RUN_PORT=8080 FLASK_APP=webhook.py FLASK_DEBUG=1 python -m flask run
gunicorn -w 2 -b 127.0.0.1:8000 webhook:app --preload
