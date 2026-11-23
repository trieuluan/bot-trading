"""Seed active Exness V4 ledgers from the previous demo account."""

import argparse
import copy
import fcntl
import gzip
import hashlib
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path

from backend.app.forex_entry_replay import NORMALIZED_SIZING
from backend.app.forex_htf_v4 import (
    RUNNING,
    ledger_path,
    logic_version,
    read_ledger,
    same_version,
    sample_outcome,
    tree_id,
)
from backend.app.forex_migration import atomic_bytes, atomic_json

MIGRATION = "v4_demo_to_live_v1"
CONTINUATION = "v4_demo_shadow_continuation_v1"
# 4430778 changed the order gate and equity-based lot sizing. The R-normalized
# V4 shadow simulator and its signal/exit rules were unchanged.
NORMALIZED_VERSION_BRIDGE = ("0c20a7065fb2d279", "d2b5f8f2317942ae")
TERMINAL = ("resolved", "expired", "excluded")


def copy_demo_sample(source, cohort, version):
    sample = copy.deepcopy(source)
    sample.update(live_assigned=False, demo_origin={
        "cohort_id": cohort, "strategy_version": source.get("strategy_version"),
    })
    if sample["status"] == "expired" and "reason" not in sample and "execution" not in sample:
        policy = sample["signal"]["entry_policy"].get("policy")
        sample["reason"] = "entry_expired" if policy not in (None, "market") else "demo_archived_expiry"
    if ((sample.get("strategy_version"), version) == NORMALIZED_VERSION_BRIDGE
            and sample.get("sizing_mode") == NORMALIZED_SIZING):
        sample["strategy_version"] = version
    return sample


def replay_demo_shadows(demo, now, version):
    from backend.app.db import Session
    from backend.app.exness_observation import TICK_WINDOW, load_frames
    from backend.app.forex_entry_replay import advance_forward

    samples = []
    for source in demo["samples"]:
        if source["status"] in RUNNING:
            sample = copy_demo_sample(source, demo["cohort_id"], version)
            sample["demo_origin"]["continuation"] = CONTINUATION
            samples.append(sample)
    if not samples:
        return samples
    symbol = demo["symbol"]
    with Session() as session:
        frames = load_frames(session, now, symbol, window={**TICK_WINDOW, "4h": 0})
    bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
    asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
    advance_forward(samples, bids, asks, now)
    for sample in samples:
        if sample["status"] == "expired" and "reason" not in sample:
            sample["reason"] = "entry_expired"
        if sample["status"] != "open" and sample["status"] != "resolved":
            sample.pop("execution", None)
    return samples


def merge_continuation(demo, live, samples, now):
    if live.get(CONTINUATION):
        return None
    if live.get(MIGRATION, {}).get("source_cohort") != demo["cohort_id"]:
        raise ValueError("Live ledger has not been seeded from this demo cohort")
    existing = {(sample["strategy"], sample["setup_id"]) for sample in live["samples"]}
    added = [sample for sample in samples if (sample["strategy"], sample["setup_id"]) not in existing]
    if not added:
        return None
    return {
        **live, "samples": added + live["samples"],
        CONTINUATION: {"at": now, "source_cohort": demo["cohort_id"],
                       "imported": len(added), "duplicates": len(samples) - len(added),
                       "resolved": sum(sample["status"] == "resolved" for sample in added),
                       "expired": sum(sample["status"] == "expired" for sample in added),
                       "running_shadow": sum(sample["status"] in RUNNING for sample in added)},
    }


def merge_ledger(demo, live, now, version):
    if live.get(MIGRATION):
        return None
    if live["generation"] or live["evolution_history"]:
        raise ValueError("Live population already evolved")
    if demo["manifest"]["catalog"] != live["manifest"]["catalog"]:
        raise ValueError("Demo and live catalogues differ")

    merged = copy.deepcopy(live)
    for key in ("population", "generation", "evolution_history"):
        merged[key] = copy.deepcopy(demo[key])
    merged["members"].update(copy.deepcopy(demo["members"]))
    if "last_evolution" in demo:
        merged["last_evolution"] = copy.deepcopy(demo["last_evolution"])

    active = {tree_id(tree) for tree in merged["population"]}
    existing = {(sample["strategy"], sample["setup_id"]) for sample in live["samples"]}
    for sample in merged["samples"]:
        if sample["strategy"] not in active and sample["status"] in RUNNING:
            sample["live_assigned"] = False
    imported = []
    duplicates = 0
    for source in demo["samples"]:
        if source["status"] not in TERMINAL:
            continue
        identity = (source["strategy"], source["setup_id"])
        if identity in existing:
            duplicates += 1
            continue
        existing.add(identity)
        imported.append(copy_demo_sample(source, demo["cohort_id"], version))
    merged["samples"] = imported + merged["samples"]
    merged[MIGRATION] = {
        "at": now, "source_cohort": demo["cohort_id"], "imported": len(imported),
        "resolved": sum(sample["status"] == "resolved" for sample in imported),
        "compatible": sum(same_version(sample.get("strategy_version"), version) for sample in imported),
        "countable_active": sum(
            sample_outcome(sample, version, merged["members"][sample["strategy"]]["born_at"], now) is not None
            for sample in imported if sample["strategy"] in active
        ),
        "duplicates": duplicates,
        "unfinished_demo": sum(sample["status"] not in TERMINAL for sample in demo["samples"]),
        "live_samples_kept": len(live["samples"]),
    }
    return merged


def locked_file(stack, path):
    handle = stack.enter_context(path.open("a"))
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def check_accounts(demo, live):
    from sqlalchemy import select

    from backend.app.db import ExnessEntryIntent, ExnessExecutionState, Session
    from backend.app.forex_entry_intents import RESERVED

    with Session() as session:
        demo_state = session.get(ExnessExecutionState, demo)
        live_state = session.get(ExnessExecutionState, live)
        if (not demo_state or demo_state.enabled or not demo_state.snapshot.get("demo_cutover_flat_at")
                or not live_state or live_state.snapshot.get("account_mode") != "real"):
            raise ValueError("Expected a reconciled demo cutover and a verified live account")
        if session.scalar(select(ExnessEntryIntent.id).where(
            ExnessEntryIntent.account.in_((demo, live)), ExnessEntryIntent.status.in_(RESERVED)).limit(1)):
            raise ValueError("An entry intent is still in flight")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--continue-demo-shadow", action="store_true")
    args = parser.parse_args()
    from backend.app.forex_symbols import configured_symbols

    demo = os.getenv("EXNESS_PREVIOUS_DEMO_ACCOUNT_ID", "").strip()
    live = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    if os.getenv("EXNESS_ENVIRONMENT") != "real" or not demo or not live or demo == live:
        raise ValueError("Run against distinct configured demo and live accounts")
    paths = [(symbol, ledger_path(demo, symbol), ledger_path(live, symbol))
             for symbol in configured_symbols()]
    now, version = int(time.time()), logic_version()
    migration = CONTINUATION if args.continue_demo_shadow else MIGRATION
    replayed = {}
    if args.continue_demo_shadow:
        check_accounts(demo, live)
        for symbol, source, _ in paths:
            ledger = read_ledger(demo, symbol, path=source)
            replayed[symbol] = (ledger["cohort_id"], replay_demo_shadows(ledger, now, version))
    with ExitStack() as stack:
        fingerprint = hashlib.sha256(live.encode()).hexdigest()[:16]
        locked_file(stack, Path("research-output") / f"exness-probe-{fingerprint}.lock")
        for path in sorted(path for _, source, target in paths for path in (source, target)):
            locked_file(stack, path.with_suffix(".lock"))
        check_accounts(demo, live)
        planned = []
        for symbol, source, target in paths:
            demo_ledger = read_ledger(demo, symbol, path=source)
            live_ledger = read_ledger(live, symbol, path=target)
            if args.continue_demo_shadow:
                cohort, samples = replayed[symbol]
                if demo_ledger["cohort_id"] != cohort:
                    raise ValueError("Demo cohort changed during replay")
                result = merge_continuation(demo_ledger, live_ledger, samples, now)
            else:
                result = merge_ledger(demo_ledger, live_ledger, now, version)
            if result is None:
                continue
            raw = target.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            archive = target.parent / "archive" / migration / f"{target.stem}-{digest[:16]}.json.gz"
            result[migration]["backup"] = archive.name
            result[migration]["backup_sha256"] = digest
            json.dumps(result, allow_nan=False)
            planned.append((symbol, result, target, archive, raw))
        for symbol, result, target, archive, raw in planned:
            if args.apply:
                if archive.exists() and gzip.decompress(archive.read_bytes()) != raw:
                    raise ValueError("Backup content mismatch")
                if not archive.exists():
                    atomic_bytes(archive, gzip.compress(raw, mtime=0))
                atomic_json(target, result)
                read_ledger(live, symbol, path=target)
            details = {key: val for key, val in result[migration].items()
                       if key not in ("backup", "backup_sha256", "source_cohort", "at")}
            print(symbol, "applied" if args.apply else "dry run", result["generation"], details, flush=True)


if __name__ == "__main__":
    main()
