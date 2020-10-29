#!/bin/bash

curl -vvv --data-binary @example-recording-completed.json http://localhost:8000/webhook
