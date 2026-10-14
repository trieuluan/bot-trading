"""Read-only Exness Public Trader API client. GET only: no order, position or WebSocket code."""

import base64
import json
import math
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

EMPTY_BODY_HASH = "47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU"


class ExnessError(RuntimeError):
    def __init__(self, message, retry_after=60, rate_limited=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited


def validate_host(base_url):
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.netloc != host
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or not host.endswith((".exness.com", ".exness-api.com"))):
        raise ExnessError("EXNESS_API_BASE_URL must be an official Exness host")
    return base_url.rstrip("/")

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_private_key(value: str) -> Ed25519PrivateKey:
    """Accepts 32-byte seed or 64-byte seed+public key as hex; never echoes the value."""
    try:
        raw = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise ExnessError("EXNESS_PRIVATE_KEY must be hex") from exc
    if len(raw) not in (32, 64):
        raise ExnessError("EXNESS_PRIVATE_KEY must be 32 or 64 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw[:32])


def signed_headers(api_key, private_key, path_with_query, now_ms=None) -> dict:
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    payload = json.dumps(
        {
            "api_key": api_key,
            "idempotency_key": "",
            "timestamp": timestamp,
            "sign_version": 1,
            "method": "GET",
            "path": path_with_query,
            "body_hash": EMPTY_BODY_HASH,
        },
        separators=(",", ":"),
    ).encode()
    return {
        "EXN-API-KEY": api_key,
        "EXN-IDEMPOTENCY-KEY": "",
        "EXN-TIMESTAMP": str(timestamp),
        "EXN-SIGN-VERSION": "1",
        "EXN-DATA": b64url(payload),
        "EXN-SIGN": b64url(private_key.sign(payload)),
    }


class ExnessReadOnly:
    def __init__(self, api_key, private_key, account_id, base_url, client=None):
        base_url = validate_host(base_url)
        if not account_id.isdigit():
            raise ExnessError("EXNESS_ACCOUNT_ID must be digits only, without #")
        self.api_key, self.account_id, self.base_url = api_key, account_id, base_url
        self.key = load_private_key(private_key)
        self.client = client or httpx.Client(base_url=base_url, timeout=10)
        self.request_interval = 0.55
        self.next_request = 0
        self.rate_limit_until = 0

    @classmethod
    def from_env(cls):
        names = ("EXNESS_API_KEY", "EXNESS_PRIVATE_KEY", "EXNESS_ACCOUNT_ID", "EXNESS_API_BASE_URL")
        values = [os.getenv(name, "").strip() for name in names]
        missing = [name for name, value in zip(names, values) if not value]
        if missing:
            raise ExnessError("Missing " + ", ".join(missing))
        return cls(*values)

    def get(self, path, params=None):
        if not path.startswith("/v1/") or "?" in path or "#" in path:
            raise ExnessError("Only relative /v1/ GET paths are allowed")
        cooldown = self.rate_limit_until - time.monotonic()
        if cooldown > 0:
            raise ExnessError("Exness rate limit; waiting before retry", math.ceil(cooldown), rate_limited=True)
        query = urlencode(params or {})
        target = httpx.URL(f"{path}?{query}" if query else path).raw_path.decode("ascii")
        delay = self.next_request - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.next_request = time.monotonic() + self.request_interval
        try:
            response = self.client.get(
                self.base_url.rstrip("/") + target,
                headers=signed_headers(self.api_key, self.key, target),
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ExnessError("Cannot reach Exness API") from exc
        if response.status_code == 429:
            try:
                cooldown = float(response.headers.get("Retry-After", "60"))
                if not math.isfinite(cooldown):
                    raise ValueError
            except ValueError:
                cooldown = 60
            cooldown = max(2, min(cooldown, 3600))
            self.rate_limit_until = time.monotonic() + cooldown
            raise ExnessError("Exness rate limit; waiting before retry", cooldown, rate_limited=True)
        if response.status_code != 200:
            raise ExnessError(f"Exness HTTP {response.status_code}")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except ValueError as exc:
            raise ExnessError("Invalid Exness JSON response") from exc

    def use_access_point(self):
        """Switch to the account's dedicated host (api.exness.com returns 404 for account routes)."""
        host = self.get("/v1/trading/access-point", {"account_id": self.account_id})["access_point"]
        self.base_url = validate_host(host)

    def account(self):
        return self.get(f"/v1/configuration/accounts/{self.account_id}/account")

    def limits(self):
        return self.get(f"/v1/configuration/accounts/{self.account_id}/limits")

    def instruments(self):
        return self.get(f"/v1/configuration/accounts/{self.account_id}/instruments")

    def condition(self, instrument):
        return self.get(
            f"/v1/configuration/accounts/{self.account_id}/instruments/{quote(instrument, safe='')}/conditions"
        )

    def candles(self, instrument, timeframe, start: int, end: int, now=None):
        """Closed bid+ask candles in [start, end), UTC seconds."""
        duration = {"M1": 60, "M15": 900, "H1": 3600, "H4": 14400}.get(timeframe)
        if duration is None or start >= end:
            raise ExnessError("Unsupported candle timeframe or invalid time range")
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        data = self.get(
            f"/v1/market-data/accounts/{self.account_id}/candles",
            {
                "instrument": instrument,
                "timeframe": timeframe,
                "price_type": "both",
                "from": iso(start),
                "to": iso(end),
            },
        )
        cutoff = min(end, int(time.time()) if now is None else now)
        rows = []
        try:
            if not isinstance(data["candles"], list):
                raise ValueError
            for row in data["candles"]:
                stamp = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError
                opened = stamp.timestamp()
                if start <= opened and opened + duration <= cutoff:
                    rows.append(row)
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ExnessError("Invalid Exness candle timestamps") from exc
        return rows
