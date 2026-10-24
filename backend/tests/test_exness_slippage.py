import base64
import hashlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.app.exness import ExnessError, b64url, load_private_key
from backend.app.exness_demo_client import ExnessDemoClient, signed_mutation_headers, validate_quote
from scripts import exness_slippage_probe as probe
from scripts.exness_slippage_probe import adverse_bps

CONDITION = {"volume_min": "0.01", "contract_size": "100", "point_digits": 3}

def quote(**extra):
    now = time.time()
    return {"bid": 4000.0, "ask": 4000.26, "time": now, "received_at": now, **extra}

def test_adverse_sign_convention():
    assert adverse_bps("open", "buy", 100, 100.01) == pytest.approx(1)
    assert adverse_bps("open", "sell", 100, 100.01) == pytest.approx(-1)
    assert adverse_bps("close", "buy", 100, 99.99) == pytest.approx(1)
    assert adverse_bps("close", "sell", 100, 100.01) == pytest.approx(1)

def test_stale_or_wide_quotes_rejected():
    validate_quote(quote())
    with pytest.raises(ValueError):
        validate_quote(quote(time=time.time() - 5))
    with pytest.raises(ValueError):
        validate_quote(quote(ask=4005))

def test_open_body_has_min_lot_and_protective_stop_within_5_usd():
    body = ExnessDemoClient(None).open_body("buy", CONDITION, quote(), "k")
    assert body["volume"] == "0.01" and body["instrument"] == "XAUUSD"
    assert float(body["stop_loss_price"]) == pytest.approx(4000.26 - 5, abs=0.001)
    short = ExnessDemoClient(None).open_body("sell", CONDITION, quote(), "k")
    assert float(short["stop_loss_price"]) == pytest.approx(4000.0 + 5, abs=0.001)

def test_notional_ceiling_blocks_larger_contract():
    with pytest.raises(Exception):
        ExnessDemoClient(None).open_body("buy", {**CONDITION, "volume_min": "0.5"}, quote(), "k")

def test_mutation_requires_idempotency_key():
    api = type("A", (), {"api_key": "k", "key": None})()
    with pytest.raises(ValueError):
        signed_mutation_headers(api, "POST", "/v1/x", b"", "")
    with pytest.raises(ValueError):
        signed_mutation_headers(api, "GET", "/v1/x", b"", "id")


def test_signature_covers_exact_method_path_body_and_stable_key():
    key = load_private_key("00" * 32)
    api = SimpleNamespace(api_key="test", key=key)
    for method, body in (("POST", b'{"volume":"0.01"}'), ("DELETE", b"")):
        headers = signed_mutation_headers(api, method, "/v1/trading/accounts/1/positions", body, "stable", 10)
        raw = base64.urlsafe_b64decode(headers["EXN-DATA"] + "==")
        payload = json.loads(raw)
        assert payload["method"] == method
        assert payload["body_hash"] == b64url(hashlib.sha256(body).digest())
        assert payload["idempotency_key"] == "stable"
        key.public_key().verify(base64.urlsafe_b64decode(headers["EXN-SIGN"] + "=="), raw)


@pytest.mark.parametrize("demo", [False, None, "true", 1])
def test_no_mutation_on_unverified_real_account(monkeypatch, demo):
    monkeypatch.setenv("EXNESS_ENVIRONMENT", "demo")
    monkeypatch.setenv("EXNESS_SYMBOL", "XAUUSD")
    monkeypatch.setenv("EXNESS_DEMO_ORDERS_ENABLED", "true")
    api = SimpleNamespace(account_id="1", account=lambda: {
        "account_id": "1", "is_demo": demo, "settings": {"currency": "USD"}})
    with pytest.raises(ExnessError, match="demo account"):
        ExnessDemoClient(api).mutate("DELETE", "key", position_id="1")


def test_timeout_never_retries_submission(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_ORDERS_ENABLED", "true")
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise httpx.ReadTimeout("timeout")
    api = SimpleNamespace(account_id="1", api_key="test", key=load_private_key("00" * 32),
                          base_url="https://test.exness.com", next_request=0, request_interval=0.55,
                          client=SimpleNamespace(request=fail))
    client = ExnessDemoClient(api)
    monkeypatch.setattr(client, "verify_demo", lambda: None)
    with pytest.raises(ExnessError, match="outcome unknown"):
        client.mutate("DELETE", "stable", position_id="10")
    assert len(calls) == 1


def test_transaction_wait_ignores_unrelated_positions():
    rows = iter([
        {"event_type": "transaction_event", "client_request_id": "someone-else", "status": "success"},
        {"event_type": "transaction_event", "client_request_id": "ours", "status": "success", "payload": {}},
    ])
    events = probe.Transactions(SimpleNamespace(recv=lambda **kwargs: json.dumps(next(rows))))
    assert events.wait("ours")["client_request_id"] == "ours"


def test_failure_after_correlated_open_attempts_cleanup_once(monkeypatch):
    logs, calls = [], []
    monkeypatch.setattr(probe, "ensure_flat", lambda client: None)
    monkeypatch.setattr(probe, "live_quote", quote)
    monkeypatch.setattr(probe, "log", logs.append)
    def submit(client, events, run, kind, key, **kwargs):
        calls.append((kind, kwargs.get("position_id")))
        return {"payload": {"deals": [{"type": "open", "instrument": "XAUUSD",
                                       "position_id": "123", "direction": "buy",
                                       "volume": "0.02", "price": "4000"}]}}
    monkeypatch.setattr(probe, "submit", submit)
    with pytest.raises(ExnessError, match="volume"):
        probe.round_trip(ExnessDemoClient(None), None, CONDITION, "buy")
    assert calls == [("open", None), ("close", "123")]
    assert logs[-1]["event"] == "flat_verified"

def test_unmeasured_pair_uses_assumed_slippage(monkeypatch, tmp_path):
    from backend.app import forex_samples
    monkeypatch.setattr(forex_samples, "REPORT", tmp_path / "exness-learning.json")
    assert forex_samples.slippage_estimate("USDCHF") == {
        "bps": forex_samples.ASSUMED_SLIPPAGE_BPS, "measured": False, "fills": 0, "source": "assumed"}
