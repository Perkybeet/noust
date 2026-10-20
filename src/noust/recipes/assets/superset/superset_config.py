# Apache Superset's configuration, read from /app/pythonpath/superset_config.py.
# It reads the environment only: nothing secret is written here.
# Reference: https://superset.apache.org/docs/configuration/configuring-superset
import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_DATABASE_URI"]

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_URL": os.environ["REDIS_URL"],
}
DATA_CACHE_CONFIG = CACHE_CONFIG

# nginx ends TLS and sets the forwarded headers.
ENABLE_PROXY_FIX = True
TALISMAN_ENABLED = False
SESSION_COOKIE_SAMESITE = "Lax"

# The examples and the telemetry are off.
SUPERSET_LOAD_EXAMPLES = False
SCARF_ANALYTICS = False
