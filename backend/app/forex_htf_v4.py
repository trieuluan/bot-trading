"""Independent Forex trees: per-tree fitness, inherited entries and bounded mutation."""

import copy
import fcntl
import hashlib
import json
import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .forex_entry_policy import (
    G0_CATALOG,
    POLICIES,
    SCHEMA,
    control_tree,
    levels,
    make_v4_g0_population,
    tree_id,
)
from .forex_entry_replay import NORMALIZED_SIZING
from .forex_migration import atomic_json

PROVENANCE = "live_forward_htf_ast4_independent_v1"
POPULATION_MODEL = "independent_v1"
RUNTIME = "forex_htf_ast4_v2"
LEDGER = Path(__file__).resolve().parents[2] / "research-output/exness-htf-ast4-v2.json"
TOTAL_TREES = 56
MIN_OUTCOMES = 10
# ponytail: same simulation logic, hash changed only by the provenance whitelist fix; drop once these samples age out.
LEGACY_VERSIONS = frozenset({"702f844968bc65e0", "0191f15eecc6f175"})

def same_version(sample_version, version):
    return sample_version == version or sample_version in LEGACY_VERSIONS


def logic_version():
    from . import forex_entry_policy, forex_entry_replay, forex_htf_v2
    body = (
        Path(__file__).read_bytes()
        + Path(forex_entry_policy.__file__).read_bytes()
        + Path(forex_entry_replay.__file__).read_bytes()
        + forex_htf_v2.logic_version().encode()
    )
    return hashlib.sha256(body).hexdigest()[:16]


def ledger_path(account, symbol="XAUUSD"):
    from .forex_symbols import checked_symbol
    checked_symbol(symbol)
    tag = hashlib.sha256(account.encode()).hexdigest()[:16]
    return LEDGER.with_name(f"{LEDGER.stem}-{tag}-{symbol}.json")


def initialize(path, account, symbol, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _initialize(path, account, symbol, now)


def _initialize(path, account, symbol, now):
    if path.exists():
        return read_ledger(account, symbol, path=path)
    ledger = new_ledger(account, symbol, now)
    atomic_json(path, ledger)
    return ledger

def new_ledger(account, symbol, now):
    trees = make_v4_g0_population(symbol)
    version = logic_version()
    return {
        "runtime": RUNTIME, "population_model": POPULATION_MODEL, "schema": SCHEMA,
        "account": account, "symbol": symbol, "generation": 0, "since": now,
        "cohort_id": hashlib.sha256(f"{POPULATION_MODEL}:{account}:{symbol}:{now}".encode()).hexdigest()[:24],
        "manifest": {"catalog": G0_CATALOG, "cutoff": now, "tree_count": len(trees),
                     "checksum": hashlib.sha256(json.dumps(trees, sort_keys=True).encode()).hexdigest()},
        "population": trees,
        "members": {tree_id(tree): {"tree": tree, "generation": 0, "parents": [],
                                     "born_at": now, "version": version} for tree in trees},
        "samples": [], "assignments": {}, "selection_counts": {},
        "last_capture": None, "evolution_history": [],
    }

def read_ledger(account, symbol="XAUUSD", *, path=None):
    from .forex_htf_v2 import validate_htf2_tree

    ledger = json.loads((path or ledger_path(account, symbol)).read_text())
    if (ledger.get("runtime"), ledger.get("schema"), ledger.get("account"), ledger.get("symbol")) != (
            RUNTIME, SCHEMA, account, symbol):
        raise ValueError("V4 ledger identity mismatch")
    if ledger.get("population_model") != POPULATION_MODEL or any(
            key in ledger for key in ("bundles", "retired_bundles", "rr_counter")):
        raise ValueError("Independent population migration required")
    trees = ledger["population"]
    identifiers = {tree_id(tree) for tree in trees}
    if len(trees) != TOTAL_TREES or len(identifiers) != TOTAL_TREES:
        raise ValueError("V4 population size mismatch")
    for tree in trees:
        validate_htf2_tree(tree)
        member = ledger["members"][tree_id(tree)]
        if member["tree"] != tree or member.get("retired_at") is not None or "bundle_id" in member:
            raise ValueError("V4 member mismatch")
    for assignment in ledger["assignments"].values():
        member = ledger["members"].get(assignment["strategy"])
        if (member is None or assignment["side"] not in ("long", "short")
                or assignment["policy"] != member["tree"]["root"]["children"][3]["policy"]):
            raise ValueError("V4 assignment mismatch")
    return ledger

def shadow_trade(sample, symbol):
    signal = sample["signal"]
    execution = sample.get("execution") or {}
    policy = signal.get("entry_policy") or {}

    def number(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and not isinstance(value, bool) else None

    condition = sample.get("condition") or {}
    volumes = [number(leg.get("volume")) for leg in execution.get("legs", [])]
    risk, contract, conversion = (number(execution.get("risk")), number(condition.get("contract_size")),
                                  number(condition.get("quote_to_account_loss")))
    normalized = sample.get("sizing_mode") == NORMALIZED_SIZING
    risk_usd = None
    if (not normalized and condition.get("account_currency") == "USD" and sample.get("costs", {}).get("verified") is True
            and condition.get("conversion", {}).get("verified") is True
            and all(value is not None and value > 0 for value in (risk, contract, conversion))
            and volumes and all(value is not None and value > 0 for value in volumes)):
        risk_usd = number(risk * contract * conversion * sum(volumes))

    return {
        "id": json.dumps([symbol, sample["strategy"], sample.get("setup_id"), signal["available_at"]]),
        "symbol": symbol, "strategy": sample["strategy"], "side": sample["side"],
        "status": sample["status"], "available_at": signal["available_at"],
        "policy": policy.get("policy"), "sizing_mode": sample.get("sizing_mode"), "live_assigned": sample.get("live_assigned", False),
        "reason": sample.get("reason"), "resolution_seconds": sample.get("resolution_seconds"),
        "entry": number(execution.get("entry")), "entry_time": number(execution.get("entry_time")),
        "resolved_at": number(execution.get("resolved_at")),
        "net_r": number(sample.get("net_r_ex_swap")) if sample["status"] == "resolved" else None,
        "valuation": {"risk": number(execution.get("risk")),
                      "risk_usd": risk_usd,
                      "realized_r": number(execution.get("net_r")),
                      "slippage_bps": number(sample.get("costs", {}).get("slippage_bps")),
                      "commission_per_unit": number(sample.get("costs", {}).get("commission_per_unit")),
                      "verified": sample.get("costs", {}).get("verified") is True,
                      "gain_rate": number(sample.get("condition", {}).get("quote_to_account_gain")),
                      "loss_rate": number(sample.get("condition", {}).get("quote_to_account_loss"))},
        "planned_levels": [{"label": label, "price": price} for label, value in (
            ("LIMIT", policy.get("limit_price")), ("STOP ENTRY", policy.get("stop_price")),
            ("SL", signal.get("stop")), ("TP", signal.get("target")))
            if (price := number(value)) is not None and price > 0],
        "legs": [{"role": leg.get("role", "trade"), "open": leg.get("open") is True,
                  "weight": number(leg.get("weight")),
                  "volume": None if normalized else number(leg.get("volume")), "stop": number(leg.get("stop_loss_price")),
                  "target": number(leg.get("take_profit_price")), "outcome": leg.get("outcome")}
                 for leg in execution.get("legs", [])],
        "exits": [{"time": event["at"], "price": price, "role": event.get("role"),
                   "reason": event.get("action")}
                  for event in execution.get("management_history", [])
                  if event.get("role") is not None and number(event.get("at")) is not None
                  and (price := number(event.get("price"))) is not None and price > 0],
    }


def is_zero_r_expiry(s):
    """Unfilled non-market entry that expired: no trade, no P&L = 0R. Market expiry (missed window) is not counted."""
    return (s.get("status") == "expired" and "execution" not in s and s.get("reason", "entry_expired") == "entry_expired"
            and s.get("signal", {}).get("entry_policy", {}).get("policy") not in (None, "market"))


def sample_outcome(s, version, born_at, now):
    """Net R of a finished, countable sample, else None."""
    if (s.get("provenance") != PROVENANCE or s.get("costs", {}).get("verified") is not True
            or not same_version(s.get("strategy_version"), version)):
        return None
    if s.get("status") == "resolved":
        r, end = s.get("net_r_ex_swap"), s.get("label_available_at", 0)
        ok = type(r) in (int, float) and math.isfinite(r)
    elif is_zero_r_expiry(s):
        # ponytail: ceiling = expiry observed at deadline; add per-sample covered-bars proof if M15 lag expires early.
        r, end, ok = 0.0, s["signal"]["entry_policy"]["expires_at"], True
    else:
        return None
    return r if ok and born_at <= s["confirm_time"] + 900 <= end < now else None


def tree_stats(member, samples, now, version):
    outcomes = {(sample["setup_id"], sample["confirm_time"], sample["side"]): outcome
                for sample in samples
                if (outcome := sample_outcome(sample, version, member.get("born_at", 0), now)) is not None}
    average = mean(outcomes.values()) if outcomes else None
    ready = len(outcomes) >= MIN_OUTCOMES
    return {"resolved_count": len(outcomes), "ready": ready,
            "eligible": ready and average > 0, "mean_r": average}

def evolve_v4_population(ledger, now, random_seed=None, version=None, *, busy_strategies=()):
    from .forex_htf_v2 import crossover, mutate, validate_htf2_tree

    version = logic_version() if version is None else version
    if now < ledger.get("last_evolution", {}).get("at", ledger["since"]) + 3600:
        return False
    samples = defaultdict(list)
    for sample in ledger["samples"]:
        samples[sample["strategy"]].append(sample)
    identifiers = [tree_id(tree) for tree in ledger["population"]]
    scores = {identifier: tree_stats(ledger["members"][identifier], samples[identifier], now, version)
              for identifier in identifiers}
    parents = sorted((identifier for identifier in identifiers if scores[identifier]["eligible"]),
                     key=lambda identifier: (-scores[identifier]["mean_r"], identifier))[:4]
    if not parents:
        return False
    busy = set(busy_strategies) | {sample["strategy"] for sample in ledger["samples"]
                                  if sample["status"] in RUNNING}
    targets = sorted((identifier for identifier in identifiers if scores[identifier]["ready"]
                      and identifier not in parents and identifier not in busy
                      and scores[identifier]["mean_r"] < scores[parents[0]]["mean_r"]),
                     key=lambda identifier: (scores[identifier]["mean_r"], identifier))
    if not targets:
        return False
    seed = random_seed if random_seed is not None else int(hashlib.sha256(
        f"{ledger['account']}:{ledger['generation']}:{version}:{now // 900}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    selected = rng.sample(parents, 2) if len(parents) > 1 else parents
    child = copy.deepcopy(ledger["members"][selected[0]]["tree"])
    if len(selected) == 2:
        child = crossover(child, ledger["members"][selected[1]]["tree"], rng)
    child = mutate(child, rng, ledger["symbol"])
    validate_htf2_tree(child)
    identifier = tree_id(child)
    if identifier in ledger["members"]:
        return False
    target = targets[0]
    generation = ledger["generation"] + 1
    ledger["members"][target]["retired_at"] = now
    ledger["members"][identifier] = {"tree": child, "generation": generation,
                                      "parents": selected, "born_at": now, "version": version}
    ledger["population"] = [child if tree_id(tree) == target else tree for tree in ledger["population"]]
    ledger["generation"] = generation
    ledger["last_evolution"] = {"at": now, "generation": generation}
    ledger["evolution_history"].append({"at": now, "generation": generation,
        "parents": selected, "retired_tree": target, "added_tree": identifier,
        "entry_policy": child["root"]["children"][3]["policy"],
        "entry_params": child["root"]["children"][3]["params"]})
    return True

def busy_strategies(session, account, symbol):
    from sqlalchemy import select

    from .db import ExnessEntryIntent, ExnessExecutionTrade
    from .forex_entry_intents import RESERVED

    busy = {row.strategy for row in session.scalars(select(ExnessEntryIntent).where(
        ExnessEntryIntent.account == account, ExnessEntryIntent.symbol == symbol,
        ExnessEntryIntent.status.in_(RESERVED)))}
    for row in session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == account, ExnessExecutionTrade.symbol == symbol)):
        snapshot = row.snapshot
        legs = snapshot.get("legs", [snapshot])
        if any(leg.get("status") not in ("closed", "skipped", "rejected", "cancelled", "failed")
               or leg.get("management", {}).get("status") in ("submitting", "submitted") for leg in legs):
            busy.add(snapshot.get("signal", {}).get("strategy"))
    return busy


def assign_tree(ledger, setup_id, samples, now):
    from .forex_entry_policy import live_entry_allowed

    assignments = ledger["assignments"]
    if setup_id not in assignments:
        eligible = [sample for sample in samples if sample["status"] == "waiting"
                    and live_entry_allowed(sample["signal"]["entry_policy"], sample["side"])]
        if not eligible:
            return None
        counts = ledger["selection_counts"]
        chosen = min(eligible, key=lambda sample: (counts.get(sample["strategy"], 0), sample["strategy"]))
        identifier = chosen["strategy"]
        assignments[setup_id] = {"strategy": identifier, "policy": chosen["signal"]["entry_policy"]["policy"],
                                 "side": chosen["side"], "assigned_at": now}
        counts[identifier] = counts.get(identifier, 0) + 1
    return assignments[setup_id]["strategy"]

def capture_samples(ledger, candle, bids, asks, condition, slip, commission, market, now):
    from .forex_htf_v2 import diagnose, setup_identity

    symbol, stamp = ledger["symbol"], candle.time
    history = [row for row in bids["15m"] if row.time < stamp][-96:]
    used = {(sample["strategy"], sample["setup_id"]) for sample in ledger["samples"]}
    rejected, hits = Counter(), 0
    version = logic_version()
    fresh = defaultdict(list)
    for tree in ledger["population"]:
        base, identifier = control_tree(tree), tree_id(tree)
        source, reason = diagnose(base, candle, bids["1h"], history, bids["4h"], asks[stamp],
                                  slip["bps"], commission, symbol, {**market, "asks": asks})
        if source is None:
            rejected[reason] += 1
            continue
        setup_id = setup_identity(source, base)
        if (identifier, setup_id) in used:
            rejected["setup_reused"] += 1
            continue
        signal = {**copy.deepcopy(source), "strategy": identifier, "symbol": symbol,
                  "setup_id": setup_id, "htf_ast_v4": True,
                  "ref_price": asks[stamp].close if source["side"] == "long" else candle.close}
        sample = {"strategy": identifier, "tree": tree, "signal": signal, "side": signal["side"],
                  "cohort_id": ledger["cohort_id"], "setup_id": setup_id, "confirm_time": stamp,
                  "recorded_at": now, "strategy_version": version, "provenance": PROVENANCE,
                  "status": "waiting", "sizing_mode": NORMALIZED_SIZING, "live_assigned": False,
                  "condition": condition, "costs": {"verified": True, "slippage_bps": slip["bps"],
                  "commission_per_unit": commission, "conversion": condition.get("conversion")}}
        try:
            signal["entry_policy"] = levels(signal, candle, tree["root"]["children"][3])
            sample["entry"] = {**signal["entry_policy"], "side": signal["side"], "status": "waiting"}
            hits += 1
        except ValueError as exc:
            sample.update(status="excluded", reason=str(exc))
            rejected[str(exc)] += 1
        ledger["samples"].append(sample)
        fresh[setup_id].append(sample)
    for setup_id, samples in fresh.items():
        assigned = assign_tree(ledger, setup_id, samples, now)
        for sample in samples:
            sample["live_assigned"] = sample["status"] == "waiting" and sample["strategy"] == assigned
    return {"time": stamp, "at": now, "status": "captured", "confirmed": hits, "rejected": dict(rejected)}

def tick(account, symbol="XAUUSD", now=None):
    from .db import Session

    # ponytail: no account lock; the ledger has its own file lock and this only reads intents (busy_strategies).
    # A multi-second lock here starved the quote consumer into quote_stale. Add a short lock if _tick starts writing intents.
    with Session.begin() as session:
        return _tick(account, symbol, now, session)


def _tick(account, symbol, now, session):
    """Advance independent trees and choose one eligible tree per setup for demo execution."""
    from .db import ForexInstrumentState, Session
    from .exness_feed import WORKSPACE
    from .exness_observation import TICK_WINDOW, audit_frames, load_frames
    from .forex_conversion import cost_context
    from .forex_costs import SPEC_TTL, account_tag
    from .forex_entry_replay import advance_forward
    from .forex_htf_v2 import reference_market
    from .forex_samples import slippage_estimate

    now = int(time.time()) if now is None else now
    path = ledger_path(account, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = read_ledger(account, symbol) if path.exists() else _initialize(path, account, symbol, now)
        stamp = now // 900 * 900 - 900
        
        with Session() as read_session:
            frames = load_frames(read_session, now, symbol, window=TICK_WINDOW)
            market = reference_market(read_session, now, symbol)
            specs = read_session.get(ForexInstrumentState, (WORKSPACE, symbol))
            condition = dict(specs.snapshot) if specs and specs.snapshot else None
            
        if audit_frames(frames, symbol)["status"] == "blocked":
            return
            
        bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
        asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
        candle = next((row for row in bids["15m"] if row.time == stamp), None)
        if candle is None or stamp not in asks:
            return
            
        # Replay simulation advance for all active samples
        advance_forward(ledger["samples"], bids, asks, now)
        
        previous = ledger.get("last_capture") or {}
        if previous.get("time") == stamp and previous.get("status") in ("captured", "missed_window"):
            atomic_json(path, ledger)
            return
            
        if stamp + 900 < ledger["since"] or not 0 <= now - stamp - 900 <= 120:
            ledger["last_capture"] = {"time": stamp, "status": "missed_window"}
            atomic_json(path, ledger)
            return
            
        try:
            if (
                not condition
                or condition.get("account_tag") != account_tag(account)
                or not 0 <= now - condition.get("observed_at", 0) < SPEC_TTL
            ):
                raise ValueError("Unverified instrument specs")
            condition = cost_context(condition, stamp + 900, account=account, symbol=symbol)
            commission = condition.get("commission_per_unit")
            if commission is None:
                raise ValueError("Unverified commission")
        except ValueError as exc:
            ledger["last_capture"] = {
                "time": stamp,
                "at": now,
                "status": "blocked_costs",
                "reason": str(exc),
            }
            atomic_json(path, ledger)
            return
            
        capture = capture_samples(ledger, candle, bids, asks, condition, slip=slippage_estimate(symbol),
                                  commission=commission, market=market, now=now)
        ledger.update(last_capture=capture)
        evolve_v4_population(ledger, now, busy_strategies=busy_strategies(session, account, symbol))
        atomic_json(path, ledger)
        return capture["status"]


def active_start(account, symbol, now):
    """Earliest signal time of still-unresolved simulations, or None when no M1 is needed."""
    try:
        ledger = read_ledger(account, symbol)
    except (OSError, ValueError, KeyError):
        return None
    starts = [s["signal"]["available_at"] for s in ledger["samples"] if s["status"] in ("waiting", "pending", "open")
              or s["status"] == "expired" and "reason" not in s and "execution" not in s
              and now - s["signal"]["entry_policy"]["expires_at"] < 86400]
    return min(starts) if starts else None

def candidates(account, symbol, now):
    """Returns waiting samples that are designated for LIVE broker execution."""
    ledger = read_ledger(account, symbol)
    from .forex_entry_policy import live_entry_allowed
    ver = logic_version()
    return [
        sample for sample in ledger["samples"]
        if sample.get("live_assigned", False) is True
        and sample["status"] == "waiting"
        and live_entry_allowed(sample["signal"]["entry_policy"], sample["signal"].get("side", sample.get("side")))
        and sample["strategy_version"] == ver
        and sample["provenance"] == PROVENANCE
        and sample["recorded_at"] >= ledger["since"]
        and sample["costs"].get("verified") is True
        and sample["signal"]["available_at"] <= now < sample["signal"]["entry_policy"]["expires_at"]
    ]


RUNNING = ("waiting", "pending", "open")


def summary(account, symbol="XAUUSD"):
    try:
        ledger = read_ledger(account, symbol)
        counts = Counter(sample["status"] for sample in ledger["samples"])
        samples_by_tree = defaultdict(list)
        for s in ledger["samples"]:
            samples_by_tree[s["strategy"]].append(s)

        version, now = logic_version(), int(time.time())
        tree_summaries = []
        for tree in ledger["population"]:
            tid = tree_id(tree)
            mem = ledger["members"].get(tid, {})
            s_list = [s for s in samples_by_tree[tid] if same_version(s.get("strategy_version"), version)]
            tree_summaries.append({
                "id": tid,
                "control_tree_id": hashlib.sha256(json.dumps(control_tree(tree), sort_keys=True).encode()).hexdigest()[:16],
                "generation": mem.get("generation", 0),
                "parents": mem.get("parents", []),
                "entry_live_sides": ["long"] if tree["root"]["children"][3]["policy"] == "limit_setup_mid" else ["long", "short"],
                **tree_stats(mem, samples_by_tree[tid], now, version),
                "entry_policy": tree["root"]["children"][3]["policy"],
                "entry_params": tree["root"]["children"][3]["params"],
                "h1_setup": tree["root"]["children"][1]["family"],
                "m15_trigger": tree["root"]["children"][2]["family"],
                "exit_policy": tree["root"]["children"][4]["policy"],
                "samples_count": len(s_list),

                "status_counts": dict(Counter("expired_0r" if is_zero_r_expiry(s) else s["status"] for s in s_list)),

            })
            
        policy_stats = {}
        for policy in POLICIES:
            selected = [tree for tree in tree_summaries if tree["entry_policy"] == policy]
            count = sum(tree["resolved_count"] for tree in selected)
            policy_stats[policy] = {"tree_count": len(selected), "resolved_count": count,
                "mean_r": sum(tree["mean_r"] * tree["resolved_count"] for tree in selected
                              if tree["mean_r"] is not None) / count if count else None}

        return {
            "status": "ready",
            "schema": SCHEMA,
            "provenance": PROVENANCE,
            "generation": ledger.get("generation", 0),
            "population_model": POPULATION_MODEL,
            "min_outcomes": MIN_OUTCOMES,
            "tree_count": len(ledger["population"]),
            "since": ledger["since"],
            "counts": dict(counts),
            "policy_stats": policy_stats,
            "trees": tree_summaries,
            "evolution_history": ledger.get("evolution_history", []),
            "shadow_trades": [shadow_trade(sample, symbol) for sample in sorted(
                ledger["samples"], key=lambda row: row["confirm_time"], reverse=True)
                if sample["status"] in ("waiting", "pending", "open")] + [
                shadow_trade(sample, symbol) for sample in sorted(
                    (row for row in ledger["samples"] if row["status"] not in ("waiting", "pending", "open")),
                    key=lambda row: row["confirm_time"], reverse=True)[:100]],
            "signals": [
                {
                    "strategy": s["strategy"],
                    "side": s["side"],
                    "available_at": s["signal"]["available_at"],
                    "status": s["status"],
                    "live_assigned": s.get("live_assigned", False),
                    "setup_id": s.get("setup_id"),
                }
                for s in sorted(ledger["samples"], key=lambda row: row["confirm_time"], reverse=True)[:100]
            ],
            "last_capture": ledger.get("last_capture"),
        }
    except FileNotFoundError:
        return {"status": "not_initialized", "tree_count": 0}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "tree_count": 0, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
