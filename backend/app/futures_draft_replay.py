"""Read-only replay execution with durable public-market caches, never learner writes."""

import gzip
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from .db import Session
from .feed import load_candles
from .futures_context import load_signal_context
from .futures_drafts import UNIVERSE_DAYS, UNIVERSE_SCOPE
from .futures_history import WARMUP, historical_stats, replay
from .futures_native import NATIVE_WARMUP
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
    candidates = {draft["strategy"]: {"genome": draft["genome"]}}
    if draft["genome"].get("signal_frame") in ("D1", "H4"):
        return run_universe(candidates, draft["window"], emit, cache_dir)
    return run_pair(symbol, candidates, draft["window"], emit, cache_dir)

def run_pair(symbol, candidates, window, emit=lambda **changes: None, cache_dir=None,
             include_samples=False, diagnostics=False):
    cache = Path(cache_dir or os.getenv("FUTURES_REPLAY_CACHE", "/tmp/futures-replay-cache"))
    start, end = window["start"], window["end"]
    native = any(candidate["genome"].get("signal_frame") for candidate in candidates.values())
    since = start - (NATIVE_WARMUP if native else WARMUP) * 900
    features = {node.get("feature") for candidate in candidates.values()
                for _, node in nodes(candidate["genome"]["rule"])}
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
        report = replay(candidates, candles, fair,
                        funding, start, end, lambda done, total: emit(phase="replay", completed=done, total=total),
                        benchmarks=benchmarks or None, oi_history=oi_history, strict_samples=True,
                        include_samples=include_samples, diagnostics=diagnostics)
        report["screening_note"] = "Validation is time-separated, not independent of parent selection"
        return report

def pool_reports(reports):
    if set(reports) != set(SYMBOLS):
        raise ReplayDataError("Universe requires all ten pairs; partial results cannot pass")
    first = reports[SYMBOLS[0]]
    keys = ("start_time", "end_time", "train_before", "bars", "warmup_bars", "fee_bps", "slippage_bps")
    genomes = {row["strategy"]: genome_fingerprint(row["genome"]) for row in first["strategies"]}
    for report in reports.values():
        if (any(report[key] != first[key] for key in keys)
                or {row["strategy"]: genome_fingerprint(row["genome"])
                    for row in report["strategies"]} != genomes):
            raise ReplayDataError("Universe windows, costs and genomes must match")
    pooled = []
    for original in first["strategies"]:
        name = original["strategy"]
        pairs = {symbol: next(row for row in reports[symbol]["strategies"] if row["strategy"] == name)
                 for symbol in SYMBOLS}
        row = {"strategy": name, "genome": original["genome"], "prior_eligible": False}
        for partition in ("train", "validation"):
            samples = [{**sample, "symbol": symbol} for symbol, pair in pairs.items()
                       for sample in pair["samples"][partition]]
            result = historical_stats(samples)
            settled = defaultdict(float)
            for sample in samples:
                settled[sample["label_available_at"]] += sample["net_r"]
            balance = peak = drawdown = 0.0
            for stamp in sorted(settled):
                balance += settled[stamp]
                peak = max(peak, balance)
                drawdown = max(drawdown, peak - balance)
            result.update(
                max_drawdown_r=drawdown,
                signals=sum(pair[partition]["signals"] for pair in pairs.values()),
                excluded=sum(pair[partition]["excluded"] for pair in pairs.values()),
                contributing_symbols=sum(pair[partition]["resolved"] > 0 for pair in pairs.values()),
                max_pair_drawdown_r=max(pair[partition]["max_drawdown_r"] for pair in pairs.values()),
            )
            row[partition] = result
        pooled.append(row)
    per_symbol = {symbol: {"dataset_sha256": report["dataset_sha256"],
                          "strategies": [{key: value for key, value in row.items() if key != "samples"}
                                         for row in report["strategies"]]}
                  for symbol, report in reports.items()}
    digest = hashlib.sha256(json.dumps({symbol: report["dataset_sha256"]
                                       for symbol, report in reports.items()}, sort_keys=True).encode()).hexdigest()
    return {**{key: first[key] for key in keys}, "source": "historical", "scope": UNIVERSE_SCOPE,
            "symbols": list(SYMBOLS), "per_symbol": per_symbol, "strategies": pooled,
            "dataset_sha256": digest, "eligible_priors": 0,
            "signal_inputs": {"universe": list(SYMBOLS), "pair_inputs": {
                symbol: report["signal_inputs"] for symbol, report in reports.items()}},
            "screening_note": "Correlated pairs are not independent samples; pooled R is not wallet PnL. "
                              "Validation is time-separated, not independent of strategy selection."}

def run_universe(candidates, window, emit=lambda **changes: None, cache_dir=None):
    data_start = window.get("data_start", 0)
    if (window.get("scope") != UNIVERSE_SCOPE or window.get("symbols") != list(SYMBOLS)
            or window["end"] - data_start != UNIVERSE_DAYS * 86400
            or window["start"] != data_start + window.get("warmup_bars", NATIVE_WARMUP) * 900):
        raise ReplayDataError("Native universe requires a frozen 340-day, ten-pair window")
    if not candidates:
        raise ValueError("Empty native universe candidates")
    for candidate in candidates.values():
        validate_genome(candidate["genome"])
        if candidate["genome"].get("signal_frame") not in ("D1", "H4"):
            raise ValueError("Universe screening supports native D1/H4 genomes only")
    cache = Path(cache_dir or os.getenv("FUTURES_REPLAY_CACHE", "/tmp/futures-replay-cache"))
    sources = ("futures_draft_replay.py", "futures_history.py", "futures_learner.py", "futures_native.py",
               "futures_strategies.py", "futures_entries.py", "futures_exits.py", "futures_rule_tree.py",
               "futures_rules.py", "futures_retirement.py", "strategy.py", "macro_calendar.py")
    engine_hash = hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes() for name in sources)).hexdigest()
    key = hashlib.sha256(json.dumps({"engine": engine_hash, "window": window,
                                    "genomes": {name: genome_fingerprint(row["genome"])
                                                for name, row in candidates.items()}}, sort_keys=True).encode()).hexdigest()
    reports = {}
    for symbol in SYMBOLS:
        def progress(**changes):
            emit(**{**changes, "phase": f"{symbol}:{changes['phase']}"})
        checkpoint = cache / "universe" / key / f"{symbol}.json.gz"
        saved = read_cache(checkpoint)
        if saved is not None:
            progress(phase="cached", completed=1, total=1)
            reports[symbol] = saved
        else:
            reports[symbol] = run_pair(symbol, candidates, window, progress, cache_dir, include_samples=True)
            write_cache(checkpoint, reports[symbol])
    return pool_reports(reports)
