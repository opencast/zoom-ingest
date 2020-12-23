#!/bin/bash

coverage run --omit=lib/* -m unittest discover test -v
coverage report
coverage html
