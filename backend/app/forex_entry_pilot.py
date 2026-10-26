"""Fixed paired entry cohort, separate from the evolving V3 population."""

import copy
import fcntl
import hashlib
import json
import time
from collections import Counter, defaultdict
from statistics import mean

from .forex_entry_policy import SCHEMA, control_tree, tree_id, with_entry
from .forex_migration import atomic_json

PROVENANCE = "live_forward_htf_ast4_entry_pilot"
RUNTIME = "virtual_entry_pilot_1"


def ledger_path(account, symbol):
    from .forex_htf_v2 import ledger_path as control_path
    path = control_path(account, symbol)
    return path.with_name(path.name.replace("ast3", "ast4-entry"))


def logic_version():
    from pathlib import Path

    from . import forex_entry_policy
    from .forex_htf_v2 import logic_version as control_version
    body = Path(forex_entry_policy.__file__).read_bytes() + control_version().encode()
    return hashlib.sha256(body).hexdigest()[:16]


def initialize(path, account, symbol, sources, now):
    if path.exists():
        return read_ledger(account, symbol, path=path)
    from .forex_htf_v2 import tree_id as control_id
    if (len(sources) != 56 or len({control_id(tree) for tree in sources}) != 56
            or any(source.get("schema") != "forex_htf_ast_v3" for source in sources)):
        raise ValueError("Pilot requires 56 unique control trees")
    trees = [with_entry(tree) for tree in sources]
    members = {tree_id(tree): {"tree": tree, "control_tree_id": control_id(source),
                               "source_snapshot": copy.deepcopy(source), "generation": 0, "parents": [], "born_at": now}
               for source, tree in zip(sources, trees)}
    manifest = {"cutoff": now, "source_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                "count": len(members)}
    ledger = {"runtime": RUNTIME, "schema": SCHEMA, "account": account, "symbol": symbol, "since": now,
              "manifest": manifest, "population": trees, "members": members, "samples": [], "last_capture": None}
    atomic_json(path, ledger)
    return ledger


def read_ledger(account, symbol, *, path=None):
    ledger = json.loads((path or ledger_path(account, symbol)).read_text())
    if (ledger.get("runtime"), ledger.get("schema"), ledger.get("account"), ledger.get("symbol")) != (RUNTIME, SCHEMA, account, symbol):
        raise ValueError("Pilot ledger identity mismatch")
    trees = ledger["population"]
    if len(trees) != 56 or len({tree_id(tree) for tree in trees}) != 56:
        raise ValueError("Pilot ledger population mismatch")
    for tree in trees:
        member = ledger["members"][tree_id(tree)]
        if member["tree"] != tree or member["source_snapshot"] != control_tree(tree):
            raise ValueError("Pilot source snapshot mismatch")
    sources = [ledger["members"][tree_id(tree)]["source_snapshot"] for tree in trees]
    checksum = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    if ledger["manifest"]["source_sha256"] != checksum or ledger["manifest"]["count"] != 56:
        raise ValueError("Pilot manifest mismatch")
    return ledger


def tick(account, symbol, now=None):
    from .db import ForexInstrumentState, Session
    from .exness_feed import WORKSPACE
    from .exness_observation import TICK_WINDOW, audit_frames, load_frames
    from .forex_conversion import cost_context
    from .forex_costs import SPEC_TTL, account_tag
    from .forex_htf_v2 import diagnose, reference_market, setup_identity
    from .forex_htf_v2 import read_ledger as read_control
    from .forex_samples import slippage_estimate

    now = int(time.time()) if now is None else now
    path = ledger_path(account, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = read_ledger(account, symbol) if path.exists() else initialize(path, account, symbol, read_control(account, symbol)["population"], now)
        stamp = now // 900 * 900 - 900
        with Session() as session:
            frames = load_frames(session, now, symbol, window=TICK_WINDOW)
            market = reference_market(session, now, symbol)
            specs = session.get(ForexInstrumentState, (WORKSPACE, symbol))
            condition = dict(specs.snapshot) if specs and specs.snapshot else None
        if audit_frames(frames, symbol)["status"] == "blocked":
            return
        bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
        asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
        candle = next((row for row in bids["15m"] if row.time == stamp), None)
        if candle is None or stamp not in asks:
            return
        from .forex_entry_replay import advance_forward
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
            if (not condition or condition.get("account_tag") != account_tag(account)
                    or not 0 <= now - condition.get("observed_at", 0) < SPEC_TTL):
                raise ValueError("Unverified instrument specs")
            condition = cost_context(condition, stamp + 900, account=account, symbol=symbol)
            commission = condition.get("commission_per_unit")
            if commission is None:
                raise ValueError("Unverified commission")
        except ValueError as exc:
            ledger["last_capture"] = {"time": stamp, "at": now, "status": "blocked_costs",
                                      "reason": str(exc)}
            atomic_json(path, ledger)
            return
        history = [row for row in bids["15m"] if row.time < stamp][-96:]
        used = {(sample["strategy"], sample["setup_id"]) for sample in ledger["samples"]}
        rejected, hits = Counter(), 0
        slip = slippage_estimate(symbol)
        for tree in ledger["population"]:
            signal, reason = diagnose(tree, candle, bids["1h"], history, bids["4h"], asks[stamp], slip["bps"], commission, symbol,
                                      {**market, "asks": asks})
            if signal is None:
                rejected[reason] += 1
                continue
            signal.update(symbol=symbol, setup_id=setup_identity(signal, tree), ref_price=asks[stamp].close if signal["side"] == "long" else candle.close)
            if (signal["strategy"], signal["setup_id"]) in used:
                rejected["setup_reused"] += 1
                continue
            sample = {"strategy": signal["strategy"], "tree": tree, "signal": signal, "side": signal["side"],
                      "setup_id": signal["setup_id"], "confirm_time": stamp, "recorded_at": now,
                      "strategy_version": logic_version(), "provenance": PROVENANCE,
                      "status": "waiting", "entry": {**signal["entry_policy"], "side": signal["side"], "status": "waiting"},
                      "condition": condition, "costs": {"verified": True, "slippage_bps": slip["bps"],
                                                        "commission_per_unit": commission, "conversion": condition.get("conversion")}}
            ledger["samples"].append(sample)
            hits += 1
        ledger["last_capture"] = {"time": stamp, "at": now, "status": "captured", "confirmed": hits, "rejected": dict(rejected)}
        atomic_json(path, ledger)


def candidates(account, symbol, now):
    ledger = read_ledger(account, symbol)
    return [sample for sample in ledger["samples"] if sample["status"] == "waiting"
            and sample["strategy_version"] == logic_version() and sample["provenance"] == PROVENANCE
            and sample["recorded_at"] >= ledger["since"] and sample["costs"].get("verified") is True
            and sample["signal"]["available_at"] <= now < sample["signal"]["entry_policy"]["expires_at"]]


def summary(account, symbol):
    try:
        ledger = read_ledger(account, symbol)
        counts = Counter(sample["status"] for sample in ledger["samples"])
        from .forex_htf_v2 import read_ledger as read_control
        from .forex_htf_v2 import setup_identity
        controls = read_control(account, symbol)
        market_samples = {(sample["strategy"], sample["confirm_time"], sample["side"],
                           setup_identity(sample["signal"], sample["tree"])): sample
                          for sample in controls["samples"] if sample.get("recorded_at", 0) >= ledger["since"]}
        pairs = []
        for sample in ledger["samples"]:
            control_id = ledger["members"][sample["strategy"]]["control_tree_id"]
            control = market_samples.get((control_id, sample["confirm_time"], sample["side"], sample["setup_id"]))
            if control:
                pairs.append((control, sample))
        resolved_pairs = [(control["net_r_ex_swap"], sample["net_r_ex_swap"]) for control, sample in pairs
                          if control["status"] == sample["status"] == "resolved"]
        paired = []
        samples_by_tree = defaultdict(list)
        for sample in ledger["samples"]:
            samples_by_tree[sample["strategy"]].append(sample)
        for tree in ledger["population"]:
            identifier = tree_id(tree)
            member = ledger["members"][identifier]
            samples = samples_by_tree[identifier]
            resolved = [sample["net_r_ex_swap"] for sample in samples if sample["status"] == "resolved"]
            paired.append({"id": identifier, "control_tree_id": member["control_tree_id"],
                           "entry_policy": tree["root"]["children"][3]["policy"],
                           "entry_params": tree["root"]["children"][3]["params"],
                           "h1_setup": tree["root"]["children"][1]["family"],
                           "m15_trigger": tree["root"]["children"][2]["family"],
                           "exit_policy": tree["root"]["children"][4]["policy"],
                           "samples_count": len(samples), "resolved_count": len(resolved),
                           "mean_r": mean(resolved) if resolved else None})
        return {"status": "ready", "schema": SCHEMA, "tree_count": len(ledger["population"]),
                "since": ledger["since"], "manifest": ledger["manifest"], "counts": dict(counts),
                "trees": paired,
                "signals": [{"strategy": sample["strategy"], "side": sample["side"],
                             "available_at": sample["signal"]["available_at"], "status": sample["status"]}
                            for sample in sorted(ledger["samples"], key=lambda row: row["confirm_time"], reverse=True)[:100]],
                "comparison": {"matched_signals": len(pairs), "resolved_pairs": len(resolved_pairs),
                               "market_states": dict(Counter(control["status"] for control, _ in pairs)),
                               "hybrid_states": dict(Counter(sample["status"] for _, sample in pairs)),
                               "market_mean_r": mean(pair[0] for pair in resolved_pairs) if resolved_pairs else None,
                               "hybrid_mean_r": mean(pair[1] for pair in resolved_pairs) if resolved_pairs else None,
                               "delta_r": mean(pair[1] - pair[0] for pair in resolved_pairs) if resolved_pairs else None},
                "last_capture": ledger.get("last_capture"), "note": "Limit/Stop ảo — bot canh giá; không phải pending trên sàn"}
    except FileNotFoundError:
        return {"status": "not_initialized", "tree_count": 0}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "tree_count": 0, "error": type(exc).__name__ + ": " + str(exc)[:160]}
