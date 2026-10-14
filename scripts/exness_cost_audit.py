"""Read-only spread audit; paper fills cannot measure broker execution slippage."""
import argparse
import json
import math
import time
from pathlib import Path
from statistics import mean, median, quantiles
from urllib.error import URLError
from urllib.request import urlopen

from sqlalchemy import select

from backend.app.db import Session, WorkspaceCandle

def spread_sample(bid, ask):
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in (bid, ask)) or not 0 < bid <= ask:
        return None
    spread = ask - bid
    return spread, spread / ((bid + ask) / 2) * 10000


def summarize(samples):
    report = {"samples": len(samples)}
    for index, unit in enumerate(("usd_per_oz", "bps_of_mid")):
        values = [sample[index] for sample in samples]
        report[unit] = None if not values else {
            "mean": mean(values), "median": median(values),
            "p95": quantiles(values, n=100, method="inclusive")[94]
            if len(values) > 1 else values[0], "max": max(values),
        }
    return report


def live_spreads(seconds):
    started = time.time()
    deadline = time.monotonic() + seconds
    samples, seen, states = [], set(), {}
    rejected = 0
    error = None
    try:
        with urlopen("http://127.0.0.1:8000/api/workspaces/exness_forex/quotes/stream",
                     timeout=5) as stream:
            while time.monotonic() < deadline:
                line = stream.readline()
                if not line:
                    raise ValueError("Quote stream ended")
                if not line.startswith(b"data: "):
                    continue
                snapshot = json.loads(line[6:])
                status = snapshot.get("status", "unknown")
                states[status] = states.get(status, 0) + 1
                quote = snapshot.get("quote") or {}
                stamp = quote.get("time")
                received = quote.get("received_at")
                now = time.time()
                sample = spread_sample(quote.get("bid"), quote.get("ask"))
                if (snapshot.get("symbol") != "XAUUSD" or status != "live" or sample is None
                        or any(isinstance(value, bool) or not isinstance(value, (int, float))
                               or not math.isfinite(value) or not 0 <= now - value <= 15
                               for value in (stamp, received))):
                    rejected += 1
                    continue
                identity = (stamp, quote["bid"], quote["ask"])
                if identity not in seen:
                    seen.add(identity)
                    samples.append(sample)
    except (OSError, URLError, ValueError, TypeError, AttributeError) as exc:
        error = type(exc).__name__
    return {**summarize(samples), "started_at": started, "ended_at": time.time(),
            "source": "existing SSE stream; snapshots at approximately 1 Hz, not every tick",
            "states": states, "rejected_snapshots": rejected, "error": error}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, default=Path("research-output/exness-cost-audit.json"))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error("--seconds must be between 1 and 3600")
    now = int(time.time())
    with Session() as session:
        rows = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == "exness_forex", WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == "15m", WorkspaceCandle.time <= now - 900))
        bids, asks = {}, {}
        for row in rows:
            (bids if row.price_type == "bid" else asks)[row.time] = row
    stats, hours = [], {}
    invalid = 0
    for stamp in bids.keys() & asks.keys():
        bid, ask = bids[stamp], asks[stamp]
        for point in ("open", "close"):
            sample = spread_sample(getattr(bid, point), getattr(ask, point))
            if sample is None:
                invalid += 1
                continue
            stats.append(sample)
            hours.setdefault(((stamp + (900 if point == "close" else 0)) // 3600) % 24, []).append(sample[1])
    paired = bids.keys() & asks.keys()
    report = {
        "version": "exness_cost_audit_v1", "as_of": now, "symbol": "XAUUSD",
        "historical": {**summarize(stats), "source": "paired M15 open/close; not synchronized ticks",
                       "start": min(paired) if paired else None, "end": max(paired) if paired else None,
                       "unpaired_bars": len(bids.keys() ^ asks.keys()), "invalid_samples": invalid,
                       "median_bps_by_utc_hour": {h: median(v) for h, v in sorted(hours.items())}},
        "live": live_spreads(args.seconds),
        "slippage": {"status": "unmeasured", "bps": None,
                     "reason": "Paper uses assumed fills. Need broker fills and contemporaneous executable bid/ask at order submission."},
        "commission": {"status": "unverified", "reason": "Missing commission is not zero."},
        "costs_calibrated": False,
        "limitations": ["Short live sample is not representative of all sessions or news.",
                        "Replay already includes bid/ask spread; do not add this spread again.",
                        "Quote latency or price movement is not measured execution slippage.",
                        "No broker orders sent and no trading configuration changed."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(report, indent=2, allow_nan=False))

if __name__ == "__main__":
    main()
