#!/bin/bash

#Drop any existing data, import the dump, rename the table and drop the old users table (which is blank anyway)
mysqldump -u zoomingest -p zoomingest > $(date +%Y%m%dT%H%M%S).sql
mysql -u zoomingest -p zoomingest -e "rename table recording to recording_old; drop table user;"

#jump into the venv, and migrate
. venv/bin/activate
python migrate.py
