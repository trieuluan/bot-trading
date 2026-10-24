"""Read-only replay execution with durable public-market caches, never learner writes."""

import gzip
import json
import os
from dataclasses import asdict
from pathlib import Path

from .db import Session
from .feed import load_candles
from .futures_context import load_signal_context
from .futures_history import WARMUP, replay
from .futures_rule_tree import nodes
from .futures_strategies import genome_fingerprint, validate_genome
from .futures_symbols import SYMBOLS, symbol_scope
from .market import Candle
from .mexc import MexcFuturesAdapter, validate_candles


class ReplayDataError(ValueError):
    pass


class MissingInputError(ReplayDataError):
    """A required real input does not exist; retrying the same window cannot create it."""


def read_cache(path):
    if not path.exists():
        return None
    with gzip.open(path, "rt") as stream:
        return json.load(stream)


def write_cache(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    with gzip.open(temporary, "wt") as stream:
        json.dump(payload, stream, allow_nan=False, separators=(",", ":"))
    temporary.replace(path)


def cached_candles(adapter, start, end, now, cache, progress, *, fair=False):
    path = cache / f'{adapter.symbol}-{"fair" if fair else "trade"}.json.gz'
    stored = read_cache(path) or []
    known = {row["time"]: Candle(**row) for row in stored}
    if not fair:
        with symbol_scope(adapter.symbol), Session() as session:
            for bar in load_candles(session, since=start, until=end):
                known[bar.time] = bar
    expected = list(range(start, end, 900))
    missing = [stamp for stamp in expected if stamp not in known]
    progress(len(expected) - len(missing), len(expected))
    cursor = 0
    while cursor < len(missing):
        first = missing[cursor]
        last = first + 900
        cursor += 1
        while cursor < len(missing) and missing[cursor] == last and last - first < 9000 * 900:
            last += 900
            cursor += 1
        bars = adapter.history(first, last, now, fair=fair)
        if [bar.time for bar in bars] != list(range(first, last, 900)):
            raise ReplayDataError(f"Incomplete {adapter.symbol} candle coverage")
        known.update({bar.time: bar for bar in bars})
        write_cache(path, [asdict(known[stamp]) for stamp in sorted(known)])
        progress(len(expected) - len(missing) + cursor, len(expected))
    rows = [known[stamp] for stamp in expected]
    validate_candles(rows)
    return rows


def cached_funding(adapter, start, end, cache):
    path = cache / f"{adapter.symbol}-funding.json.gz"
    cached = read_cache(path)
    if not cached or cached["start"] > start or cached["end"] < end:
        cached = {"start": start, "end": end, "rows": adapter.funding_history(start, end)}
        write_cache(path, cached)
    rows = [row for row in cached["rows"] if start <= row["time"] < end]
    if not rows or rows[0]["time"] - start > rows[0]["cycle_hours"] * 3600:
        raise ReplayDataError("Incomplete initial funding coverage")
    if end - rows[-1]["time"] > rows[-1]["cycle_hours"] * 3600:
        raise ReplayDataError("Incomplete final funding coverage")
    for previous, current in zip(rows, rows[1:]):
        if current["time"] - previous["time"] != current["cycle_hours"] * 3600:
            raise ReplayDataError("Funding cache gap")
    return rows


def validate_oi(history, start, end):
    known = sorted(stamp for stamp, value in history if 0 < value < float("inf"))
    if (not known or known[0] > start - 3600 or known[-1] < end - 900
            or any(after - before > 900 for before, after in zip(known, known[1:]))):
        raise MissingInputError("Missing historical OI observations; synthetic backfill is forbidden")


def run_screen(symbol, draft, emit=lambda **changes: None, cache_dir=None):
    validate_genome(draft["genome"])
    if genome_fingerprint(draft["genome"]) != draft["fingerprint"]:
        raise ValueError("Frozen draft genome changed")
    cache = Path(cache_dir or os.getenv("FUTURES_REPLAY_CACHE", "/tmp/futures-replay-cache"))
    window = draft["window"]
    start, end = window["start"], window["end"]
    since = start - WARMUP * 900
    features = {node.get("feature") for _, node in nodes(draft["genome"]["rule"])}
    peers = set(SYMBOLS) - {symbol} if features & {"market_alignment", "relative_strength"} else set()
    with symbol_scope(symbol):
        adapter = MexcFuturesAdapter(symbol=symbol)
        try:
            now = adapter.server_time()
            if end > now // 900 * 900:
                raise ReplayDataError("Replay window includes an open candle")
            emit(phase="candles", completed=None, total=None)
            candles = cached_candles(adapter, since, end, now, cache,
                                     lambda done, total: emit(phase="candles", completed=done, total=total))
            emit(phase="fair_price", completed=None, total=None)
            fair = cached_candles(adapter, since, end, now, cache,
                                  lambda done, total: emit(phase="fair_price", completed=done, total=total), fair=True)
            emit(phase="funding", completed=None, total=None)
            funding = cached_funding(adapter, since - 2 * 86400, end, cache)
        finally:
            adapter.close()
        benchmarks = {}
        for peer in sorted(peers):
            emit(phase=f"benchmark:{peer}", completed=None, total=None)
            adapter = MexcFuturesAdapter(symbol=peer)
            try:
                benchmarks[peer] = cached_candles(adapter, since, end, now, cache,
                    lambda done, total, peer=peer: emit(phase=f"benchmark:{peer}", completed=done, total=total))
            finally:
                adapter.close()
        oi_history = None
        if "oi_surge" in features:
            emit(phase="open_interest", completed=None, total=None)
            with Session() as session:
                oi_history = load_signal_context(session, start - 4500, end).get("oi_history") or []
            validate_oi(oi_history, start, end)
        emit(phase="replay", completed=0, total=(end - start) // 900)
        report = replay({draft["strategy"]: {"genome": draft["genome"]}}, candles, fair,
                        funding, start, end, lambda done, total: emit(phase="replay", completed=done, total=total),
                        benchmarks=benchmarks or None, oi_history=oi_history, strict_samples=True)
        report["screening_note"] = "Validation is time-separated, not independent of parent selection"
        return report
