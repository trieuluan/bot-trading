"""Replace stopped, flat demo cohorts; preserve compressed audit archives, not hot samples."""

import argparse
import fcntl
import gzip
import hashlib
import json
import time

from backend.app.forex_entry_policy import G0_CATALOG, SCHEMA, tree_id
from backend.app.forex_htf_v2 import validate_htf2_tree
from backend.app.forex_htf_v4 import RUNTIME, TOTAL_TREES, ledger_path, new_ledger, read_ledger
from backend.app.forex_migration import atomic_bytes, atomic_json


def read_source(path, account, symbol):
    ledger = json.loads(path.read_text())
    if (ledger.get("runtime"), ledger.get("schema"), ledger.get("account"), ledger.get("symbol")) != (
            RUNTIME, SCHEMA, account, symbol):
        raise ValueError("V4 ledger identity mismatch")
    if ledger.get("population_model") is not None:
        return read_ledger(account, symbol, path=path)
    trees = ledger.get("population", [])
    if len(trees) != TOTAL_TREES or len({tree_id(tree) for tree in trees}) != TOTAL_TREES:
        raise ValueError("Invalid legacy population")
    for tree in trees:
        validate_htf2_tree(tree)
        if ledger.get("members", {}).get(tree_id(tree), {}).get("tree") != tree:
            raise ValueError("Invalid legacy member")
    return ledger


def replace_ledger(path, account, symbol, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            atomic_json(path, new_ledger(account, symbol, now))
            return "initialized"
        previous = read_source(path, account, symbol)
        if previous["manifest"].get("catalog") == G0_CATALOG:
            return "already_current"
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        archive = path.parent / "archive" / G0_CATALOG / (path.stem + "-" + digest[:16] + ".json.gz")
        if archive.exists():
            if gzip.decompress(archive.read_bytes()) != raw:
                raise ValueError("Archive checksum mismatch")
        else:
            atomic_bytes(archive, gzip.compress(raw, mtime=0))
        replacement = new_ledger(account, symbol, now)
        replacement["migration"] = {"catalog": G0_CATALOG, "at": now, "archive": str(archive),
                                    "sha256": digest, "old_tree_count": len(previous["population"]),
                                    "old_samples": len(previous["samples"])}
        atomic_json(path, replacement)
        return "replaced"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    from backend.app.db import ExnessEntryIntent, ExnessExecutionState, Session
    from backend.app.exness_demo_client import ExnessDemoClient
    from backend.app.exness_feed import connect_exness
    from backend.app.forex_entry_intents import RESERVED
    from backend.app.forex_htf_v4 import busy_strategies
    from backend.app.forex_symbols import configured_symbols

    api = connect_exness()
    client = ExnessDemoClient(api)
    client.verify_demo()
    broker = client.snapshot()
    if broker["positions"] or any(order.get("order_type") != "sltp" for order in broker["orders"]):
        raise RuntimeError("Cutover requires flat demo account; positions and external orders untouched")
    symbols = configured_symbols()
    with Session() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        if state is None or state.enabled:
            raise RuntimeError("Pause demo execution and stop exness-feed before cutover")
        if session.query(ExnessEntryIntent).filter(ExnessEntryIntent.account == api.account_id,
                                                ExnessEntryIntent.status.in_(RESERVED)).first():
            raise RuntimeError("Unreconciled entry intent; no ledger changed")
        for symbol in symbols:
            if busy_strategies(session, api.account_id, symbol):
                raise RuntimeError(f"Unreconciled execution for {symbol}; no ledger changed")
    for symbol in symbols:
        path = ledger_path(api.account_id, symbol)
        if path.exists():
            read_source(path, api.account_id, symbol)
    now = int(time.time())
    for symbol in symbols:
        print(symbol, replace_ledger(ledger_path(api.account_id, symbol), api.account_id, symbol, now), flush=True)


if __name__ == "__main__":
    main()
