"""Opt-in demo calibration, isolated from strategy workers. No daily order cap."""

import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import time
import uuid
from pathlib import Path
from statistics import mean, median
from urllib.request import urlopen

from websockets.sync.client import connect

from backend.app.exness import ExnessError, ExnessReadOnly, signed_headers
from backend.app.exness_demo_client import ExnessDemoClient, positive_decimal, validate_quote

JOURNAL = Path("research-output/exness-slippage-journal.jsonl")
REPORT = Path("research-output/exness-slippage-report.json")
STOP = Path("research-output/exness-slippage.stop")
EVENTS_PATH = "/v1/server-events/accounts/{account_id}/ws/events"


def adverse_bps(action, direction, expected, fill):
    if action not in ("open", "close") or direction not in ("buy", "sell"):
        raise ValueError("Invalid fill direction")
    expected, fill = float(positive_decimal(expected)), float(positive_decimal(fill))
    pays = (action == "open") == (direction == "buy")
    return ((fill - expected) if pays else (expected - fill)) / expected * 10000


def log(entry):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), **entry}, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def entries():
    return [json.loads(line) for line in JOURNAL.read_text().splitlines()] if JOURNAL.exists() else []


def live_quote():
    deadline = time.monotonic() + 5
    with urlopen("http://127.0.0.1:8000/api/workspaces/exness_forex/quotes/stream", timeout=5) as stream:
        while time.monotonic() < deadline:
            line = stream.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                snapshot = json.loads(line[6:])
                if snapshot.get("symbol") == "XAUUSD" and snapshot.get("status") == "live":
                    return validate_quote(snapshot.get("quote") or {})
        raise ExnessError("Fresh XAUUSD quote unavailable; do not open")


class Transactions:
    def __init__(self, socket):
        self.socket = socket

    def wait(self, key=None, operation_id=None, snapshot=False):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            event = json.loads(self.socket.recv(timeout=max(0.01, deadline - time.monotonic())))
            if event.get("error"):
                raise ExnessError("Transaction stream rejected; no new orders")
            if snapshot and event.get("event_type") == "trading_state_snapshot":
                return event["payload"]
            correlated = (event.get("client_request_id") == key or
                          operation_id is not None and event.get("operation_id") == operation_id)
            if not snapshot and event.get("event_type") == "transaction_event" and correlated:
                if event.get("status") in ("rejected", "failed"):
                    raise ExnessError("Broker rejected operation; reconcile before another order")
                if event.get("status") == "success":
                    return event
        raise ExnessError("No correlated transaction; reconcile before another order")


def fill_deal(event, kind, side, volume, position_id=None):
    deals = [row for row in event.get("payload", {}).get("deals", [])
             if row.get("type") == kind and row.get("instrument") == "XAUUSD"
             and (position_id is None or row.get("position_id") == position_id)]
    if len(deals) != 1:
        raise ExnessError("Expected exactly one correlated XAUUSD fill")
    deal = deals[0]
    identifier = deal.get("position_id")
    if not isinstance(identifier, str) or not identifier.isdigit():
        raise ExnessError("Invalid correlated position identifier")
    if deal.get("direction") != side or positive_decimal(deal.get("volume")) != positive_decimal(volume):
        raise ExnessError("Fill volume or direction mismatch")
    positive_decimal(deal.get("price"))
    return deal


def submit(client, events, run, kind, key, **kwargs):
    log({"event": kind + "_intent", "run": run, "key": key,
         "body": kwargs.get("body"), "quote": kwargs.get("quote"),
         "position_id": kwargs.get("position_id")})
    ack = {}
    try:
        kwargs["on_submit"] = lambda submission: log({"event": kind + "_submitting", "run": run,
                                                      "submission": submission})
        ack = client.mutate("POST" if kind == "open" else "DELETE", key, **kwargs)
        log({"event": kind + "_ack", "run": run, "ack": ack,
             "submission": client.last_submission})
    except Exception as exc:
        if client.last_submission is None:
            log({"event": kind + "_not_submitted", "run": run, "error_type": type(exc).__name__})
            raise
        log({"event": kind + "_uncertain", "run": run, "error_type": type(exc).__name__})
    event = events.wait(key, ack.get("operation_id"))
    event["submission"] = client.last_submission
    return event


def ensure_flat(client):
    snapshot = client.snapshot()
    if snapshot.get("positions") or snapshot.get("orders"):
        raise ExnessError("Demo not flat; unrelated trades will not be closed")


def round_trip(client, events, condition, side):
    run = uuid.uuid4().hex
    open_key, close_key = f"slip-o-{run}", f"slip-c-{run}"
    ensure_flat(client)
    quote = live_quote()
    body = client.open_body(side, condition, quote, open_key)
    position_id = None
    closing = False
    try:
        event = submit(client, events, run, "open", open_key, body=body, quote=quote,
                       quote_source=live_quote, condition=condition)
        log({"event": "open_transaction", "run": run, "transaction": event})
        own = [row for row in event.get("payload", {}).get("deals", [])
               if row.get("type") == "open" and row.get("instrument") == "XAUUSD"]
        if len(own) == 1 and isinstance(own[0].get("position_id"), str) and own[0]["position_id"].isdigit():
            position_id = own[0]["position_id"]
        deal = fill_deal(event, "open", side, body["volume"])
        quote = event["submission"]["quote"]
        expected = quote["ask" if side == "buy" else "bid"]
        log({"event": "open_fill", "run": run, "deal": deal, "expected": expected,
             "adverse_bps": adverse_bps("open", side, expected, deal["price"])})
        try:
            exit_quote = live_quote()
        except Exception:
            exit_quote = None
        closing = True
        closed = submit(client, events, run, "close", close_key,
                        position_id=position_id, quote=exit_quote, quote_source=live_quote)
        log({"event": "close_transaction", "run": run, "transaction": closed})
        deal = fill_deal(closed, "close", "sell" if side == "buy" else "buy", body["volume"], position_id)
        exit_quote = closed["submission"]["quote"]
        expected = exit_quote["bid" if side == "buy" else "ask"] if exit_quote else None
        ensure_flat(client)
        log({"event": "close_fill", "run": run, "deal": deal, "expected": expected,
             "adverse_bps": adverse_bps("close", side, expected, deal["price"]) if expected else None})
        log({"event": "flat_verified", "run": run})
    finally:
        if position_id and not closing:
            closing = True
            closed = submit(client, events, run, "close", close_key, position_id=position_id)
            log({"event": "emergency_close", "run": run, "transaction": closed})
            ensure_flat(client)
            log({"event": "flat_verified", "run": run})


def summarize():
    rows = [row for row in entries() if row["event"] in ("open_fill", "close_fill")]
    values = [row["adverse_bps"] for row in rows if row.get("adverse_bps") is not None]
    report = {"version": "exness_slippage_v1", "as_of": time.time(), "fills": len(values),
              "round_trips": sum(row["event"] == "close_fill" for row in rows), "demo_only": True,
              "adverse_bps": None if not values else {"mean": mean(values), "median": median(values),
                                                      "max": max(values), "min": min(values)},
              "per_side_mean_bps": None if not values else mean(values),
              "note": "Versus 1 Hz quote before request. Includes quote age and latency drift. Demo is not live execution. No cost settings changed."}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(REPORT)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=2, help="0 runs until stop file or failure")
    parser.add_argument("--pause", type=float, default=60)
    parser.add_argument("--confirm-demo", action="store_true")
    args = parser.parse_args()
    if (args.rounds < 0 or not math.isfinite(args.pause) or args.pause < 5
            or not args.confirm_demo or os.getenv("EXNESS_DEMO_ORDERS_ENABLED") != "true"):
        parser.error("requires --confirm-demo, EXNESS_DEMO_ORDERS_ENABLED=true and pause >=5s")
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    api = ExnessReadOnly.from_env()
    fingerprint = hashlib.sha256(api.account_id.encode()).hexdigest()[:16]
    lock = JOURNAL.parent / f"exness-probe-{fingerprint}.lock"
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = entries()
        pending = {row["run"] for row in previous if row["event"] == "open_intent"} - {
            row["run"] for row in previous if row["event"] in ("flat_verified", "open_not_submitted")}
        if pending:
            parser.error("Unresolved journal: reconcile broker operations and positions; do not delete journal to retry")
        try:
            if STOP.exists():
                raise ExnessError("Stop file present; remove explicitly to enable another run")
            api.use_access_point()
            client = ExnessDemoClient(api)
            condition = client.prepare()
            path = EVENTS_PATH.format(account_id=api.account_id)
            with connect(api.base_url.replace("https://", "wss://", 1) + path,
                         additional_headers=signed_headers(api.api_key, api.key, path),
                         subprotocols=["exness-ws-protocol"], compression=None, proxy=None,
                         open_timeout=10, close_timeout=5, max_size=65536) as socket:
                socket.send(json.dumps({"id": "slippage-probe", "subscribe": {"event": "transactions"}}))
                events = Transactions(socket)
                snapshot = events.wait(snapshot=True)
                if snapshot.get("positions") or snapshot.get("orders"):
                    raise ExnessError("Demo not flat at subscription")
                index = 0
                while not STOP.exists() and (args.rounds == 0 or index < args.rounds):
                    round_trip(client, events, condition, "buy" if index % 2 == 0 else "sell")
                    print(json.dumps(summarize()), flush=True)
                    index += 1
                    if args.rounds == 0 or index < args.rounds:
                        deadline = time.monotonic() + args.pause
                        while not STOP.exists() and time.monotonic() < deadline:
                            time.sleep(min(1, max(0, deadline - time.monotonic())))
        finally:
            print(json.dumps(summarize()), flush=True)
            api.client.close()


if __name__ == "__main__":
    main()
