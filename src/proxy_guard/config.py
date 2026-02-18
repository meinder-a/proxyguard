"""service configuration loaded from environment variables"""

import os
import re

# service configuration
SERVICE_SECRET = os.getenv("PG_SECRET", "change-this-to-a-high-entropy-string").encode()
PROXY_PORT = int(os.getenv("PROXY_PORT", "8888"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9090"))
CONNECT_TIMEOUT = int(os.getenv("PG_CONNECT_TIMEOUT", "10"))
BUFFER_SIZE = int(os.getenv("PG_BUFFER_SIZE", "65536"))
LOG_SAMPLE_RATE = int(os.getenv("PG_LOG_SAMPLE_RATE", "1000"))
HEALTH_CHECK_INTERVAL = int(os.getenv("PG_HEALTH_CHECK_INTERVAL", "60"))
STICKY_TTL = int(os.getenv("PG_STICKY_TTL", "0"))
UPSTREAM_LIST = os.getenv("PG_PROXY_LIST", "").split(",")
UPSTREAM_FILE = os.getenv("PG_PROXY_FILE", "proxies.txt")
ENABLE_AUTH = os.getenv("PG_ENABLE_AUTH", "true").lower() == "true"

# compiled regexes
# captures: CONNECT target HTTP/1.1
RE_REQUEST_LINE = re.compile(rb"^CONNECT\s+([^\s]+)\s+HTTP/1.1")
# captures: x-pg-auth: value
RE_AUTH_HEADER = re.compile(rb"(?i)x-pg-auth:\s*([^\r\n]+)")
# captures: Proxy-Authorization: Basic value
RE_PROXY_AUTH_HEADER = re.compile(rb"(?i)Proxy-Authorization:\s*Basic\s+([^\r\n\s]+)")

# latency & usage logic
MAX_LATENCY = float(os.getenv("PG_MAX_LATENCY", "500"))
HIGH_USAGE_THRESHOLD = int(os.getenv("PG_HIGH_USAGE_THRESHOLD", "50"))
