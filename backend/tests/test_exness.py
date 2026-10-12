import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.app.exness import (
    EMPTY_BODY_HASH,
    ExnessError,
    ExnessReadOnly,
    load_private_key,
    signed_headers,
)


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_signature_covers_exact_path_and_verifies():
    seed = bytes(range(32))
    key = load_private_key((seed + seed).hex())  # 64-byte form: only seed is used
    headers = signed_headers("exnsk_test", key, "/v1/x?b=2&a=1", now_ms=1773656070123)
    payload = unb64(headers["EXN-DATA"])
    data = json.loads(payload)
    assert data["path"] == "/v1/x?b=2&a=1" and data["method"] == "GET"
    assert data["body_hash"] == EMPTY_BODY_HASH and data["idempotency_key"] == ""
    assert headers["EXN-TIMESTAMP"] == "1773656070123" and "=" not in headers["EXN-SIGN"]
    Ed25519PrivateKey.from_private_bytes(seed).public_key().verify(
        unb64(headers["EXN-SIGN"]), payload
    )


def test_private_key_and_config_validation():
    with pytest.raises(ExnessError):
        load_private_key("zz")
    with pytest.raises(ExnessError):
        load_private_key("00" * 10)
    good = "00" * 32
    with pytest.raises(ExnessError):
        ExnessReadOnly("k", good, "#123", "https://api.exness.com")
    with pytest.raises(ExnessError):
        ExnessReadOnly("k", good, "123", "https://evil.example")
    with pytest.raises(ExnessError):
        ExnessReadOnly("k", good, "123", "http://x.exness.com")
    api = ExnessReadOnly("k", good, "123", "https://api.exness.com")
    assert not any(name in dir(api) for name in ("order", "buy", "sell", "open", "close"))


def test_candles_filters_incomplete_and_out_of_range():
    good = "00" * 32
    sample = {
        "candles": [
            {"time": "2026-10-12T10:00:00Z", "bid": {"close": 1}, "ask": {"close": 2}},
            {"time": "2026-10-12T11:00:00Z", "bid": {"close": 3}, "ask": {"close": 4}},
            {"time": "2026-10-12T12:00:00Z", "bid": {"close": 5}, "ask": {"close": 6}},
        ]
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=sample)))
    api = ExnessReadOnly("k", good, "123", "https://api.exness.com", client=client)
    t10 = 1791799200
    rows = api.candles("XAUUSD", "H1", t10, t10 + 7200)
    assert [r["time"] for r in rows] == ["2026-10-12T10:00:00Z", "2026-10-12T11:00:00Z"]
    with pytest.raises(ExnessError):
        api.candles("XAUUSD", "D1", t10, t10 + 86400)
