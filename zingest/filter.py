import zingest.logger
import logging
import re

class RegexFilter:

    def __init__(self, config):
        self.logger = logging.getLogger("regex-filter")
        self.logger.setLevel(logging.DEBUG)
        self.pattern = config["Filter"]["regex"]
        self.regex = re.compile(config["Filter"]["regex"])
        self.logger.debug(f"Filtering with regex {self.pattern} against recording topics")

    def matches(self, obj):
        return self.regex.search(obj['topic']) != None
