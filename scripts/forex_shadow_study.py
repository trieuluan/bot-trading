"""Record prospective Forex shadow signals and comparisons without submitting orders."""

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path

from sqlalchemy import select, text, tuple_

from backend.app.db import ExnessEntryIntent, ExnessExecutionState, Session, WorkspaceCandle
from backend.app.exness_feed import WORKSPACE
from backend.app.forex_htf_v4 import ledger_path
from backend.app.forex_symbols import configured_symbols

POLL_SECONDS = 5
RISK_MAX_AGE = 60
PRIOR_WEIGHT = 10
POLICY_VERSION = "bayes_10_same_ready_batch_v1"
TERMINAL = {"resolved", "excluded", "expired", "ambiguous"}


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def identity(symbol, sample):
    return encode([symbol, sample["setup_id"], sample["strategy"]])


def features(symbol, sample):
    nodes = sample["tree"]["root"]["children"]
    return [nodes[1]["family"], sample["signal"]["entry_policy"]["policy"],
            sample["signal"]["exit_policy"], symbol]


def win_score(group, history, cutoff):
    """Use disjoint groups and only labels available before the signal."""
    wins, counts = [0] * 4, [0] * 4
    for known_at, other, won in history:
        if known_at >= cutoff:
            continue
        bucket = 0
        if other[:2] == group[:2]:
            bucket = 1 + (other[2] == group[2]) + (other[2:] == group[2:])
        wins[bucket] += won
        counts[bucket] += 1
    probability = (wins[0] + 1) / (counts[0] + 2)
    for won, count in zip(wins[1:], counts[1:]):
        probability = (won + PRIOR_WEIGHT * probability) / (count + PRIOR_WEIGHT)
    return probability, counts


def risk_at(history, cutoff):
    for observed_at, snapshot in reversed(history):
        verified = snapshot.get("verified_at", 0)
        if not (observed_at <= cutoff and 0 <= cutoff - verified <= RISK_MAX_AGE):
            continue
        day = snapshot.get("live_risk_day", {})
        try:
            drawdown = 100 * (1 - float(snapshot["broker"]["account_state"]["equity"])
                              / float(day["initial_equity"]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if day.get("day") == cutoff // 86400 and math.isfinite(drawdown):
            limit = float(os.environ["EXNESS_LIVE_MAX_DAILY_DRAWDOWN_PCT"])
            halted = bool(day.get("halted")) or drawdown >= limit
            return {"observed_at": observed_at, "verified_at": verified, "drawdown_pct": drawdown,
                    "daily_limit_pct": limit, "halted": halted,
                    "reason": "daily_drawdown" if halted else None}
    return {"halted": None, "reason": "risk_snapshot_unavailable"}


def compare_batch(rows, observed_at):
    """Rank only setups observed together while their entry windows are still open."""
    ranked = sorted(rows, key=lambda row: (-row["score"], row["id"]))
    ids = [row["id"] for row in ranked]
    batch_id = hashlib.sha256(encode([POLICY_VERSION, ids]).encode()).hexdigest()
    random_pick = min(ids, key=lambda key: hashlib.sha256(f"{batch_id}/{key}".encode()).digest())
    return {"id": batch_id, "observed_at": observed_at, "ranks": ids,
            "score_pick": ids[0], "random_pick": random_pick}


def open_study(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outcomes (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS labels (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS live_decisions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
    """)
    return db


def store(db, table, key, data, *, update=False):
    conflict = "DO NOTHING"
    if update:
        conflict = f"DO UPDATE SET data=excluded.data WHERE {table}.data<>excluded.data"
    db.execute(f"INSERT INTO {table} VALUES (?, ?) ON CONFLICT(id) {conflict}", (key, encode(data)))


def snapshot_and_market(account, candle_keys, start):
    with Session() as session:
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        state = session.get(ExnessExecutionState, account)
        snapshot = state.snapshot if state and state.snapshot.get("account_mode") == "real" else {}
        candles = {}
        if candle_keys:
            rows = session.execute(select(
                WorkspaceCandle.symbol, WorkspaceCandle.time, WorkspaceCandle.price_type, WorkspaceCandle.close
            ).where(WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.timeframe == "15m",
                    tuple_(WorkspaceCandle.symbol, WorkspaceCandle.time).in_(candle_keys)))
            for symbol, stamp, side, close in rows:
                candles.setdefault((symbol, stamp), {})[side] = close
        intents = session.scalars(select(ExnessEntryIntent).where(
            ExnessEntryIntent.account == account, ExnessEntryIntent.available_at >= start
        ))
        decisions = {(row.symbol, row.setup_id, row.strategy): {
            "status": row.status, "reason": row.snapshot.get("reason"),
            "reason_code": row.snapshot.get("reason_code"), "updated_at": row.updated_at.timestamp(),
        } for row in intents}
        return snapshot, candles, decisions


def collect(db, account, symbols, started_at, risks, ledgers, signatures):
    now = time.time()
    for symbol in symbols:
        path = ledger_path(account, symbol)
        try:
            stat = path.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signatures.get(symbol) != signature:
                ledgers[symbol] = json.loads(path.read_text())
                signatures[symbol] = signature
        except FileNotFoundError:
            continue
    known_ids = {row[0] for row in db.execute("SELECT id FROM signals")}
    entries, fresh = {}, {}
    for symbol, ledger in ledgers.items():
        capture = ledger.get("last_capture") or {}
        if capture.get("at", 0) >= started_at:
            store(db, "captures", encode([symbol, capture["time"], capture["status"]]),
                  {"symbol": symbol, **capture})
        for sample in ledger["samples"]:
            if not sample.get("live_assigned") or not sample["signal"].get("entry_policy"):
                continue
            key = identity(symbol, sample)
            entries[key] = (symbol, sample)
            if key not in known_ids and sample["recorded_at"] >= started_at:
                fresh[key] = (symbol, sample)

    candle_keys = {(symbol, sample["confirm_time"]) for symbol, sample in fresh.values()}
    snapshot, quotes, decisions = snapshot_and_market(account, candle_keys, max(started_at, now - 86400))
    now = time.time()
    risks.append((now, snapshot))
    while risks and risks[0][0] < now - 900:
        risks.popleft()

    journal = {(row.get("symbol"), row.get("time"), row.get("selected")): row
               for row in snapshot.get("journal", [])}
    for key, (symbol, sample) in entries.items():
        label_at = sample.get("label_available_at")
        net_r = sample.get("net_r_ex_swap")
        if (sample["status"] == "resolved" and label_at is not None and label_at <= now
                and isinstance(net_r, (int, float)) and math.isfinite(net_r)):
            # Existing labels seed the study. New labels are usable from first observation.
            known_at = label_at if label_at < started_at and sample["recorded_at"] < started_at else now
            store(db, "labels", key, [known_at, features(symbol, sample), net_r > 0])
        if key not in known_ids and key not in fresh:
            continue
        if sample["status"] in TERMINAL:
            store(db, "outcomes", key, {
                field: sample.get(field) for field in ("status", "reason", "entry_time", "label_available_at",
                                                        "net_r_ex_swap", "resolution_seconds")
            }, update=True)
        decision = decisions.get((symbol, sample["setup_id"], sample["strategy"]))
        record = journal.get((symbol, sample["confirm_time"], sample["strategy"]))
        if decision or record:
            store(db, "live_decisions", key, {
                "intent": decision, "journal": {
                    field: record.get(field) for field in ("outcome", "reason", "at", "events")
                } if record else None,
            }, update=True)

    if not fresh:
        return
    history = [json.loads(value) for (value,) in db.execute("SELECT data FROM labels")]
    for key, (symbol, sample) in fresh.items():
        signal = sample["signal"]
        group = features(symbol, sample)
        score, counts = win_score(group, history, signal["available_at"])
        quote = quotes.get((symbol, sample["confirm_time"]), {})
        spread = quote["ask"] - quote["bid"] if quote.keys() >= {"bid", "ask"} else None
        risk = risk_at(risks, sample["recorded_at"])
        entry = signal["entry_policy"]
        row = {"id": key, "symbol": symbol, "setup_id": sample["setup_id"], "strategy": sample["strategy"],
               "strategy_version": sample["strategy_version"], "features": group, "score": score,
               "sample_counts": counts, "score_version": POLICY_VERSION, "side": sample["side"],
               "candle_time": sample["confirm_time"], "available_at": signal["available_at"],
               "recorded_at": sample["recorded_at"], "first_observed_at": now,
               "expires_at": entry["expires_at"], "status_at_observation": sample["status"],
               "spread": spread if spread is not None and spread >= 0 else None,
               "spread_source": "M15 bid/ask close", "costs": {
                   field: sample["costs"].get(field) for field in ("commission_per_unit", "slippage_bps")
               }, "risk": risk}
        store(db, "signals", key, row)
    stamps = {sample["signal"]["available_at"] for _, sample in fresh.values()}
    eligible = defaultdict(list)
    for key, (_, sample) in entries.items():
        signal = sample["signal"]
        stamp = signal["available_at"]
        if (stamp in stamps and sample["status"] in {"waiting", "pending"}
                and stamp <= now < signal["entry_policy"]["expires_at"]):
            saved = db.execute("SELECT data FROM signals WHERE id=?", (key,)).fetchone()
            if saved:
                eligible[stamp].append(json.loads(saved[0]))
    for stamp, rows in eligible.items():
        batch = compare_batch(rows, now)
        store(db, "batches", batch["id"], {**batch, "available_at": stamp, "keep_halt_pick": None,
                                           "risk": risk_at(risks, now), "policy_version": POLICY_VERSION})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    account = os.environ["EXNESS_ACCOUNT_ID"]
    symbols = configured_symbols()
    db = open_study(Path("/study/forex-shadow-study.sqlite3"))
    with db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('started_at', ?)", (str(int(time.time())),))
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('policy_version', ?)", (POLICY_VERSION,))
    settings = dict(db.execute("SELECT key, value FROM metadata"))
    if settings["policy_version"] != POLICY_VERSION:
        raise RuntimeError("Shadow study policy changed; start a separate study")
    started_at = int(settings["started_at"])
    risks, ledgers, signatures = deque(), {}, {}
    logging.info("Forex shadow study active for %d symbols, policy %s", len(symbols), POLICY_VERSION)
    while True:
        try:
            with db:
                collect(db, account, symbols, started_at, risks, ledgers, signatures)
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_poll_at', ?)", (str(int(time.time())),))
        except Exception as exc:
            logging.error("Shadow study poll failed: %s", type(exc).__name__)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
