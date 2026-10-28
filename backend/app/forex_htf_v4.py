"""Forex HTF AST V4: 14 balanced bundles x 4 entry policies = 56 trees.

Evolution replaces weakest bundle with new bundle (base setup mutated/crossed,
replicated across all 4 entry policies). Single entry policy chosen round-robin
per setup for live broker execution.
"""

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
    POLICIES,
    SCHEMA,
    control_tree,
    entry_node,
    levels,
    make_v4_g0_bundle,
    tree_id,
    with_entry,
)
from .forex_migration import atomic_json

PROVENANCE = "live_forward_htf_ast4_v2"
RUNTIME = "forex_htf_ast4_v2"
LEDGER = Path(__file__).resolve().parents[2] / "research-output/exness-htf-ast4-v2.json"
MAX_BUNDLES = 14
TREES_PER_BUNDLE = 4
TOTAL_TREES = MAX_BUNDLES * TREES_PER_BUNDLE  # 56


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
    trees, bundles_meta = make_v4_g0_bundle(symbol)
    members = {}
    bundles = {}
    for b_id, b_info in bundles_meta.items():
        base = b_info["base"]
        from .forex_htf_v2 import tree_id as control_id
        c_id = control_id(base)
        bundles[b_id] = {
            "id": b_id,
            "base": base,
            "control_id": c_id,
            "generation": 0,
            "parents": [],
            "born_at": now,
            "trees": b_info["tree_ids"],  # policy -> hast4_...
        }
        for pol, tid in b_info["tree_ids"].items():
            matched_tree = next(t for t in trees if tree_id(t) == tid)
            members[tid] = {
                "tree": matched_tree,
                "bundle_id": b_id,
                "entry_policy": pol,
                "control_id": c_id,
                "generation": 0,
                "parents": [],
                "born_at": now,
                "version": logic_version(),
            }
    manifest = {
        "cutoff": now,
        "bundle_count": len(bundles),
        "tree_count": len(trees),
        "checksum": hashlib.sha256(json.dumps(trees, sort_keys=True).encode()).hexdigest(),
    }
    ledger = {
        "runtime": RUNTIME,
        "schema": SCHEMA,
        "account": account,
        "cohort_id": hashlib.sha256(f"{RUNTIME}:{account}:{symbol}:{now}".encode()).hexdigest()[:24],
        "symbol": symbol,
        "generation": 0,
        "since": now,
        "manifest": manifest,
        "population": trees,
        "bundles": bundles,
        "members": members,
        "samples": [],
        "rr_counter": 0,  # round-robin index for policy execution
        "assignments": {},
        "retired_bundles": {},
        "last_capture": None,
        "evolution_history": [],
    }
    atomic_json(path, ledger)
    return ledger


def read_ledger(account, symbol="XAUUSD", *, path=None):
    file_path = path or ledger_path(account, symbol)
    ledger = json.loads(file_path.read_text())
    if (
        ledger.get("runtime") != RUNTIME
        or ledger.get("schema") != SCHEMA
        or ledger.get("account") != account
        or ledger.get("symbol") != symbol
    ):
        raise ValueError("V4 ledger identity mismatch")
    trees = ledger.get("population", [])
    identifiers = {tree_id(tree) for tree in trees}
    if len(trees) != TOTAL_TREES or len(identifiers) != TOTAL_TREES:
        raise ValueError("V4 population size mismatch")
    if len(ledger["bundles"]) != MAX_BUNDLES:
        raise ValueError("V4 bundle count mismatch")
    expected = set()
    for bundle_id, bundle in ledger["bundles"].items():
        if bundle["id"] != bundle_id or set(bundle["trees"]) != set(POLICIES):
            raise ValueError("V4 bundle identity mismatch")
        for policy, identifier in bundle["trees"].items():
            member = ledger["members"][identifier]
            if (member["bundle_id"] != bundle_id or member.get("retired_at")
                    or member["tree"] != with_entry(bundle["base"], entry_node(policy))
                    or tree_id(member["tree"]) != identifier):
                raise ValueError("V4 member mismatch")
            expected.add(identifier)
    if expected != identifiers:
        raise ValueError("V4 bundle population mismatch")
    assignments = ledger["assignments"]
    if ledger["rr_counter"] != len(assignments):
        raise ValueError("V4 assignment counter mismatch")
    for index, assignment in enumerate(assignments.values()):
        if assignment["policy"] != POLICIES[index % len(POLICIES)]:
            raise ValueError("V4 assignment policy mismatch")
    return ledger


def bundle_stats(bundle_id, bundle, samples_by_strategy, now, version):
    """Fitness of a bundle: computed across matched setups where all 4 entry variants resolved."""
    t_map = bundle["trees"]  # policy -> tid
    tree_samples = {pol: samples_by_strategy.get(tid, []) for pol, tid in t_map.items()}
    
    # Valid resolved samples per policy
    def valid_resolved(s_list):
        return [
            s for s in s_list
            if s.get("status") == "resolved"
            and s.get("provenance") == PROVENANCE
            and s.get("costs", {}).get("verified") is True
            and s.get("strategy_version") == version
            and type(s.get("net_r_ex_swap")) in (int, float)
            and math.isfinite(s["net_r_ex_swap"])
            and bundle.get("born_at", 0) <= s["confirm_time"] + 900
            <= s.get("label_available_at", 0) < now
        ]
    
    res_by_pol = {pol: {(s["setup_id"], s["confirm_time"], s["side"]): s
                        for s in valid_resolved(s_list)} for pol, s_list in tree_samples.items()}
    # Intersect setup_ids across all 4 policies for clean paired evaluation
    common_setups = set.intersection(*(set(d.keys()) for d in res_by_pol.values())) if res_by_pol else set()
    
    count = len(common_setups)
    ready = count >= 10
    
    mean_r = None
    eligible = False
    pol_means = {}
    if common_setups:
        for pol in POLICIES:
            vals = [res_by_pol[pol][sid]["net_r_ex_swap"] for sid in common_setups]
            pol_means[pol] = mean(vals) if vals else 0.0
        mean_r = mean(pol_means.values())
        eligible = ready and (mean_r > 0)
    
    return {
        "bundle_id": bundle_id,
        "matched_count": count,
        "ready": ready,
        "eligible": eligible,
        "mean_r": mean_r,
        "policy_means": pol_means,
    }


def evolve_v4_population(ledger, now, random_seed=None, version=None, *, busy_strategies=()):
    """Replaces weakest ready bundle with child bundle. Keeps 14 bundles x 4 trees = 56 trees."""
    from .forex_htf_v2 import crossover, mutate
    
    version = logic_version() if version is None else version
    last = ledger.get("last_evolution") or {}
    earliest = last.get("at", ledger.get("since", now)) + 3600
    if now < earliest:
        return False
    
    samples = ledger.get("samples", [])
    samples_by_strat = defaultdict(list)
    for s in samples:
        samples_by_strat[s["strategy"]].append(s)
        
    bundles = ledger.get("bundles", {})
    scores = {b_id: bundle_stats(b_id, b, samples_by_strat, now, version) for b_id, b in bundles.items()}
    
    parents = sorted(
        (b for b_id, b in bundles.items() if scores[b_id]["eligible"]),
        key=lambda b: (-scores[b["id"]]["mean_r"], b["id"])
    )[:4]
    if not parents:
        return False
        
    # Active check: bundle can only be replaced if none of its 4 trees have open/pending/waiting samples
    def bundle_busy(b):
        t_ids = set(b["trees"].values())
        return bool(t_ids.intersection(busy_strategies)) or any(
            s["strategy"] in t_ids and s["status"] in ("waiting", "pending", "open") for s in samples)
        
    replacements = sorted(
        (
            b for b_id, b in bundles.items()
            if scores[b_id]["ready"]
            and b not in parents
            and scores[b_id]["mean_r"] < scores[parents[0]["id"]]["mean_r"]
            and not bundle_busy(b)
        ),
        key=lambda b: (scores[b["id"]]["mean_r"], b["id"])
    )[:1]
    if not replacements:
        return False
        
    target_bundle = replacements[0]
    seed = (
        random_seed
        if random_seed is not None
        else int(
            hashlib.sha256(
                f"{ledger.get('account')}:{ledger.get('generation', 0)}:{version}:{now // 900}".encode()
            ).hexdigest()[:16],
            16,
        )
    )
    rng = random.Random(seed)
    
    # Evolve the base setup
    selected_parents = rng.sample(parents, 2) if len(parents) > 1 else [parents[0]]
    if len(selected_parents) == 2:
        child_base = crossover(selected_parents[0]["base"], selected_parents[1]["base"], rng)
    else:
        child_base = selected_parents[0]["base"]
    child_base = mutate(child_base, rng, ledger.get("symbol", "XAUUSD"))
    if child_base["schema"] == SCHEMA:  # mutate() may add an entry node; bundles own entry policy
        child_base = control_tree(child_base)
    
    # Replicate to all 4 entry policies
    new_trees = [with_entry(child_base, entry_node(pol)) for pol in POLICIES]
    new_tids = {pol: tree_id(t) for pol, t in zip(POLICIES, new_trees)}
    
    # Check tree IDs are unique against known
    known_tree_ids = set(ledger["members"].keys()) | {tree_id(t) for t in ledger["population"]}
    if any(tid in known_tree_ids for tid in new_tids.values()):
        return False  # Duplicate child structure rejected
        
    generation = ledger.get("generation", 0) + 1
    retired_bundle_id = target_bundle["id"]
    retired_tree_ids = list(target_bundle["trees"].values())
    
    for tid in retired_tree_ids:
        if tid in ledger["members"]:
            ledger["members"][tid]["retired_at"] = now
            
    from .forex_htf_v2 import tree_id as control_id
    c_id = control_id(child_base)
    child_bundle_id = "bundle_" + c_id.removeprefix("hast3_")
    ledger.setdefault("retired_bundles", {})[retired_bundle_id] = {**target_bundle, "retired_at": now}
    del bundles[retired_bundle_id]
    bundles[child_bundle_id] = {
        "id": child_bundle_id,
        "base": child_base,
        "control_id": c_id,
        "generation": generation,
        "parents": [p["id"] for p in selected_parents],
        "born_at": now,
        "trees": new_tids,
    }
    for pol, t, tid in zip(POLICIES, new_trees, new_tids.values()):
        ledger["members"][tid] = {
            "tree": t,
            "bundle_id": child_bundle_id,
            "entry_policy": pol,
            "control_id": c_id,
            "generation": generation,
            "parents": [p["id"] for p in selected_parents],
            "born_at": now,
            "version": version,
        }
        
    # Update population
    retained_pop = [t for t in ledger["population"] if tree_id(t) not in set(retired_tree_ids)]
    ledger["population"] = retained_pop + new_trees
    ledger["generation"] = generation
    ledger["last_evolution"] = {"at": now, "generation": generation}
    ledger.setdefault("evolution_history", []).append({
        "at": now,
        "generation": generation,
        "seed": seed,
        "version": version,
        "parents": [p["id"] for p in parents[:4]],
        "fitness": scores,
        "retired_bundle": retired_bundle_id,
        "added_bundle": child_bundle_id,
        "retired_trees": retired_tree_ids,
        "added_trees": list(new_tids.values()),
    })
    
    if len(ledger["population"]) != TOTAL_TREES:
        raise ValueError("V4 Evolution changed population size")
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


def assign_policy(ledger, setup_id, now):
    assignments = ledger["assignments"]
    if setup_id not in assignments:
        assignments[setup_id] = {"policy": POLICIES[ledger["rr_counter"] % len(POLICIES)], "assigned_at": now}
        ledger["rr_counter"] += 1
    return assignments[setup_id]["policy"]


def capture_samples(ledger, candle, bids, asks, condition, slip, commission, market, now):
    from .forex_htf_v2 import diagnose, setup_identity

    symbol, stamp = ledger["symbol"], candle.time
    history = [row for row in bids["15m"] if row.time < stamp][-96:]
    used = {(sample["strategy"], sample["setup_id"]) for sample in ledger["samples"]}
    rejected, hits = Counter(), 0
    version = logic_version()
    for bundle in sorted(ledger["bundles"].values(), key=lambda item: item["id"]):
        source, reason = diagnose(bundle["base"], candle, bids["1h"], history, bids["4h"], asks[stamp],
                                  slip["bps"], commission, symbol, {**market, "asks": asks})
        if source is None:
            rejected[reason] += 4
            continue
        setup_id = setup_identity(source, bundle["base"])
        if any((identifier, setup_id) in used for identifier in bundle["trees"].values()):
            rejected["setup_reused"] += 4
            continue
        assigned = assign_policy(ledger, setup_id, now)
        for policy, identifier in bundle["trees"].items():
            tree = ledger["members"][identifier]["tree"]
            signal = {**copy.deepcopy(source), "strategy": identifier, "symbol": symbol,
                      "setup_id": setup_id, "htf_ast_v4": True,
                      "ref_price": asks[stamp].close if source["side"] == "long" else candle.close}
            sample = {"strategy": identifier, "tree": tree, "signal": signal, "side": signal["side"],
                      "cohort_id": ledger["cohort_id"], "bundle_id": bundle["id"], "setup_id": setup_id,
                      "confirm_time": stamp, "recorded_at": now, "strategy_version": version,
                      "provenance": PROVENANCE, "status": "waiting", "live_assigned": policy == assigned,
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
    return {"time": stamp, "at": now, "status": "captured", "confirmed": hits, "rejected": dict(rejected)}


def tick(account, symbol="XAUUSD", now=None):
    from .db import Session
    from .forex_entry_intents import lock_account

    with Session.begin() as session:
        lock_account(session, account)
        return _tick(account, symbol, now, session)


def _tick(account, symbol, now, session):
    """M15 tick for V4: diagnose 56 trees, advance replay, evolve, choose round-robin policy for live."""
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
    ver = logic_version()
    return [
        sample for sample in ledger["samples"]
        if sample.get("live_assigned", False) is True
        and sample["status"] == "waiting"
        and sample["strategy_version"] == ver
        and sample["provenance"] == PROVENANCE
        and sample["recorded_at"] >= ledger["since"]
        and sample["costs"].get("verified") is True
        and sample["signal"]["available_at"] <= now < sample["signal"]["entry_policy"]["expires_at"]
    ]


def summary(account, symbol="XAUUSD"):
    try:
        ledger = read_ledger(account, symbol)
        counts = Counter(sample["status"] for sample in ledger["samples"])
        samples_by_tree = defaultdict(list)
        for s in ledger["samples"]:
            samples_by_tree[s["strategy"]].append(s)
            
        tree_summaries = []
        for tree in ledger["population"]:
            tid = tree_id(tree)
            mem = ledger["members"].get(tid, {})
            b_id = mem.get("bundle_id")
            s_list = samples_by_tree[tid]
            resolved = [s["net_r_ex_swap"] for s in s_list if s["status"] == "resolved" and s.get("net_r_ex_swap") is not None]
            
            tree_summaries.append({
                "id": tid,
                "bundle_id": b_id,
                "control_tree_id": mem.get("control_id"),
                "generation": mem.get("generation", 0),
                "entry_policy": tree["root"]["children"][3]["policy"],
                "entry_params": tree["root"]["children"][3]["params"],
                "h1_setup": tree["root"]["children"][1]["family"],
                "m15_trigger": tree["root"]["children"][2]["family"],
                "exit_policy": tree["root"]["children"][4]["policy"],
                "samples_count": len(s_list),
                "resolved_count": len(resolved),
                "status_counts": dict(Counter(s["status"] for s in s_list)),
                "mean_r": mean(resolved) if resolved else None,
            })
            
        version, now = logic_version(), int(time.time())
        bundle_summaries = {}
        for b_id, bundle in ledger.get("bundles", {}).items():
            stats = bundle_stats(b_id, bundle, samples_by_tree, now, version)
            running = {s["setup_id"] for tid in bundle["trees"].values() for s in samples_by_tree.get(tid, [])
                       if s["status"] in ("waiting", "pending", "open")}
            bundle_summaries[b_id] = {"matched_count": stats["matched_count"], "ready": stats["ready"],
                                      "mean_r": stats["mean_r"], "running_setups": len(running)}

        # Group stats by policy
        policy_stats = {}
        for pol in POLICIES:
            p_trees = [t for t in tree_summaries if t["entry_policy"] == pol]
            p_res = sum(t["resolved_count"] for t in p_trees)
            p_r_vals = [t["mean_r"] for t in p_trees if t["mean_r"] is not None]
            policy_stats[pol] = {
                "tree_count": len(p_trees),
                "resolved_count": p_res,
                "mean_r": mean(p_r_vals) if p_r_vals else None,
            }
            
        return {
            "status": "ready",
            "schema": SCHEMA,
            "provenance": PROVENANCE,
            "generation": ledger.get("generation", 0),
            "bundle_count": len(ledger.get("bundles", {})),
            "tree_count": len(ledger["population"]),
            "since": ledger["since"],
            "counts": dict(counts),
            "policy_stats": policy_stats,
            "bundles": bundle_summaries,
            "trees": tree_summaries,
            "evolution_history": ledger.get("evolution_history", []),
            "signals": [
                {
                    "strategy": s["strategy"],
                    "side": s["side"],
                    "available_at": s["signal"]["available_at"],
                    "status": s["status"],
                    "live_assigned": s.get("live_assigned", False),
                    "setup_id": s.get("setup_id"),
                    "bundle_id": s.get("bundle_id"),
                }
                for s in sorted(ledger["samples"], key=lambda row: row["confirm_time"], reverse=True)[:100]
            ],
            "last_capture": ledger.get("last_capture"),
        }
    except FileNotFoundError:
        return {"status": "not_initialized", "tree_count": 0}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "tree_count": 0, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
