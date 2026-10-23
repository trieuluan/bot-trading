"""Shared signed sessions for browser login and authenticated internal quote reads."""

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit
from urllib.request import Request

COOKIE = "signal_desk_auth"
AUTH_TTL = 86400


def auth_token(ttl=AUTH_TTL):
    payload = f"{int(time.time()) + ttl}.{secrets.token_hex(16)}"
    signature = hmac.new(os.getenv("APP_PASSWORD", "").encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def quote_request(symbol, *, entry=False):
    from .forex_symbols import checked_symbol
    checked_symbol(symbol)
    base = os.getenv("EXNESS_QUOTE_BASE_URL", "http://api:8000").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in ("http", "https") or parsed.hostname not in ("api", "localhost", "127.0.0.1")
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Quote credentials require an internal API origin")
    headers = {"Cookie": f"{COOKIE}={auth_token(30)}"} if os.getenv("APP_PASSWORD") else {}
    return Request(base + "/api/workspaces/exness_forex/quotes/stream?symbol=" + symbol
                   + ("&purpose=entry" if entry else ""), headers=headers)
