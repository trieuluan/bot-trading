"""Isolated, opt-in demo calibration. The regular Exness client stays GET-only."""

import hashlib
import json
import math
import os
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import httpx

from .exness import ExnessError, b64url


def positive_decimal(value):
    if isinstance(value, bool):
        raise ValueError("Invalid numeric value")
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("Expected finite positive value")
    return number


def validate_quote(quote, now=None):
    now = time.time() if now is None else now
    if any(isinstance(quote.get(key), bool) or not isinstance(quote.get(key), (int, float))
           or not math.isfinite(quote[key]) for key in ("bid", "ask", "time", "received_at")):
        raise ValueError("Invalid quote")
    if not (0 < quote["bid"] <= quote["ask"] and quote["ask"] - quote["bid"] <= 1
            and 0 <= now - quote["time"] <= 2 and 0 <= now - quote["received_at"] <= 2):
        raise ValueError("Stale quote or excessive spread")
    return quote


def signed_mutation_headers(api, method, path, body, key, now_ms=None):
    if method not in ("POST", "DELETE") or not key:
        raise ValueError("Mutation requires method and stable idempotency key")
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    payload = json.dumps({
        "api_key": api.api_key, "idempotency_key": key, "timestamp": timestamp,
        "sign_version": 1, "method": method, "path": path,
        "body_hash": b64url(hashlib.sha256(body).digest()),
    }, separators=(",", ":")).encode()
    return {"EXN-API-KEY": api.api_key, "EXN-IDEMPOTENCY-KEY": key,
            "EXN-TIMESTAMP": str(timestamp), "EXN-SIGN-VERSION": "1",
            "EXN-DATA": b64url(payload), "EXN-SIGN": b64url(api.key.sign(payload)),
            "Content-Type": "application/json"}


class ExnessDemoClient:
    def __init__(self, api):
        self.api = api

    def verify_demo(self):
        if os.getenv("EXNESS_ENVIRONMENT") != "demo" or os.getenv("EXNESS_SYMBOL") != "XAUUSD":
            raise ExnessError("Demo XAUUSD configuration required")
        account = self.api.account()
        if account.get("is_demo") is not True or str(account.get("account_id")) != self.api.account_id:
            raise ExnessError("Execution requires verified matching demo account")
        if account.get("settings", {}).get("currency") != "USD":
            raise ExnessError("Only USD demo accounts supported")
        return account

    def snapshot(self):
        snapshot = self.api.get(f"/v1/trading/accounts/{self.api.account_id}/snapshot")
        if not isinstance(snapshot.get("account_state"), dict):
            raise ExnessError("Invalid account snapshot")
        if any(not isinstance(snapshot.get(key, []), list) for key in ("positions", "orders")):
            raise ExnessError("Invalid trading state")
        return snapshot

    def prepare(self):
        self.verify_demo()
        condition = self.api.condition("XAUUSD")
        if (condition.get("instrument") != "XAUUSD" or condition.get("trade_mode") != "enabled"
                or condition.get("exec_mode") != "market" or condition.get("quote_currency") != "USD"):
            raise ExnessError("Only enabled USD XAUUSD market execution supported")
        volume = positive_decimal(condition.get("volume_min"))
        if volume > Decimal("0.01") or volume > positive_decimal(condition.get("volume_max")):
            raise ExnessError("Minimum lot exceeds calibration ceiling 0.01")
        positive_decimal(condition.get("volume_step"))
        positive_decimal(condition.get("contract_size"))
        digits = condition.get("point_digits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 10:
            raise ExnessError("Invalid price precision")
        limits = self.api.limits()["limits"]["rest"]
        required = {"openPosition", "closePosition", "getTradingStateSnapshot", "getAccountDetails"}
        methods = limits["methods"]
        if not required <= {row["api_operation_id"] for row in methods}:
            raise ExnessError("Required trading methods unavailable")
        rates = [limits["global_account_rate"], *[row["rate_limit"] for row in methods
                 if row["api_operation_id"] in required]]
        self.api.request_interval = max(1.05, self.api.request_interval, *[
            float(positive_decimal(rate["window_seconds"]) / positive_decimal(rate["limit"])) + 0.05
            for rate in rates])
        snapshot = self.snapshot()
        if snapshot.get("positions") or snapshot.get("orders"):
            raise ExnessError("Calibration requires empty demo account; unrelated trades untouched")
        return condition

    def open_body(self, side, condition, quote, key):
        validate_quote(quote)
        if side not in ("buy", "sell"):
            raise ValueError("Invalid direction")
        volume = positive_decimal(condition["volume_min"])
        units = volume * positive_decimal(condition["contract_size"])
        entry = positive_decimal(quote["ask" if side == "buy" else "bid"])
        if volume > Decimal("0.01") or units * entry > 10000:
            raise ExnessError("Calibration lot or notional ceiling exceeded")
        tick = Decimal(1).scaleb(-condition["point_digits"])
        stop = (entry + (-1 if side == "buy" else 1) * Decimal(5) / units).quantize(
            tick, rounding=ROUND_CEILING if side == "buy" else ROUND_FLOOR)
        if stop <= 0 or (side == "buy" and stop >= Decimal(str(quote["bid"]))) or (
                side == "sell" and stop <= Decimal(str(quote["ask"]))):
            raise ExnessError("Cannot place protective stop within planned $5 risk")
        return {"instrument": "XAUUSD", "side": side, "volume": format(volume, "f"),
                "stop_loss_price": format(stop, "f"), "comment": key[:32]}

    def mutate(self, method, key, body=None, position_id=None, quote=None,
               quote_source=None, condition=None, on_submit=None):
        self.last_submission = None
        if os.getenv("EXNESS_DEMO_ORDERS_ENABLED") != "true":
            raise ExnessError("Demo execution is disabled")
        if method not in ("POST", "DELETE") or not isinstance(key, str) or not key:
            raise ValueError("Explicit method and stable idempotency key required")
        if method == "POST" and (position_id is not None or not isinstance(body, dict)
                                  or body.get("instrument") != "XAUUSD"):
            raise ValueError("Invalid demo open request")
        if method == "DELETE" and (not isinstance(position_id, str) or not position_id.isdigit() or body):
            raise ValueError("Invalid demo close request")
        self.verify_demo()
        delay = self.api.next_request - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if quote_source:
            try:
                quote = quote_source()
            except Exception:
                if method == "POST":
                    raise
                quote = None
        if method == "POST":
            validate_quote(quote)
            if condition is not None:
                body = self.open_body(body["side"], condition, quote, key)
            if body.get("side") not in ("buy", "sell") or positive_decimal(body.get("volume")) > Decimal("0.01"):
                raise ValueError("Invalid demo lot or direction")
            positive_decimal(body.get("stop_loss_price"))
        path = f"/v1/trading/accounts/{self.api.account_id}/positions"
        if method == "DELETE":
            path += "/" + position_id
        data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode() if body else b""
        self.api.next_request = time.monotonic() + self.api.request_interval
        self.last_submission = {"submitted_at": time.time(), "method": method, "key": key,
                                "quote_age_s": time.time() - quote["time"] if quote else None,
                                "quote": quote, "body": body}
        if on_submit:
            on_submit(self.last_submission)
        try:
            response = self.api.client.request(method, self.api.base_url + path, content=data,
                headers=signed_mutation_headers(self.api, method, path, data, key), follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ExnessError("Submission outcome unknown; do not retry or open another position") from exc
        if response.status_code != 202:
            raise ExnessError(f"Mutation HTTP {response.status_code}; reconcile before retry")
        return response.json()
