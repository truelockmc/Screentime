#!/home/user/venv/bin/python
import json
import logging
logger = logging.getLogger(__name__)

class AppMapping:
    def __init__(self, path):
        self.path = path
        self.mapping = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.mapping = json.load(f)
        except FileNotFoundError:
            logger.info("map.json not found, using raw app names")
            self.mapping = {}
        except Exception:
            logger.exception("Failed to load map.json")
            self.mapping = {}

    def resolve(self, raw_name):
        entry = self.mapping.get(raw_name)
        if not entry:
            return raw_name, None

        return (
            entry.get("display_name", raw_name),
            entry.get("icon")
        )
