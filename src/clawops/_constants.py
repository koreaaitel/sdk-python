from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(timeout=600.0, connect=5.0)
DEFAULT_MAX_RETRIES = 2
DEFAULT_BASE_URL = "https://api.koreaaitel.com"
INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
DEFAULT_CONNECTION_LIMITS = httpx.Limits(
    max_connections=1000,
    max_keepalive_connections=100,
)
