#!/bin/bash

docker stop rabbit
docker rm rabbit
docker run -d --name rabbit -e RABBITMQ_DEFAULT_USER=rabbit -e RABBITMQ_DEFAULT_PASS=rabbit -p 15672:15672 -p 5671:5671 -p 5672:5672 -p 4369:4369 rabbitmq:3-management
