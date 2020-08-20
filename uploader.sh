#!/bin/bash

gunicorn -w 2 -b 127.0.0.1:8081 uploader:app
