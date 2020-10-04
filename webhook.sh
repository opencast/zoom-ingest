#!/bin/bash

#The long timeout here is important, don't remove it!
# See https://github.com/benoitc/gunicorn/issues/1801#issuecomment-670801360
# The gist of it is that some browser open an empty, predicted connection, which sits doing nothing
# We have to out-wait it, or else we get ugly CRITICAL WORKER TIMEOUT errors
gunicorn -w 2 -b 0.0.0.0:8000 webhook:app -t 5 --preload --timeout=200
