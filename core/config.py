# -*- coding: utf-8 -*-
import os
import json

with open('.version', 'r') as f:
    __version__ = f.read()


class _Config:
    # Defaults matching config.example.cfg
    DC_BOT_TOKEN = ""
    DC_CLIENT_ID = 0
    DC_CLIENT_SECRET = ""
    DC_INVITE_LINK = ""
    DC_OWNER_ID = 0
    DC_SLASH_SERVERS = []
    DB_URI = ""
    LOG_LEVEL = "DEBUG"
    COMMANDS_URL = ""
    HELP = ""
    STATUS = ""
    WS_ENABLE = False
    WS_HOST = ""
    WS_PORT = 443
    WS_OAUTH_REDIRECT_URL = ""
    WS_ROOT_URL = ""
    WS_SSL_CERT_FILE = ""
    WS_SSL_KEY_FILE = ""


cfg = _Config()

# Load from environment variables
_int_keys = {'DC_CLIENT_ID', 'DC_OWNER_ID', 'WS_PORT'}
_bool_keys = {'WS_ENABLE'}
_list_keys = {'DC_SLASH_SERVERS'}

for _key in vars(_Config):
    if not _key.isupper():
        continue
    _val = os.environ.get(_key)
    if _val is None:
        continue
    if _key in _int_keys:
        setattr(cfg, _key, int(_val))
    elif _key in _bool_keys:
        setattr(cfg, _key, _val.lower() in ('1', 'true', 'yes'))
    elif _key in _list_keys:
        setattr(cfg, _key, json.loads(_val))
    else:
        setattr(cfg, _key, _val)
