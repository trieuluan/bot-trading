"""One-time, account-locked cutover; callers hold the V2 ledger lock."""

import fcntl
import hashlib
import json
import os
import tempfile

MIGRATION_ID = "htf_v2_only_1"


def atomic_bytes(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, allow_nan=False).encode())


def migrate_ledger(path, account, now):
    from .forex_htf_v2 import RUNTIME, SCHEMA, g0_trees, tree_id

    existing = json.loads(path.read_text()) if path.exists() else None
    if existing and existing.get("runtime") == RUNTIME:
        if existing.get("account") != account or existing.get("schema") != SCHEMA:
            raise ValueError("Invalid V2 ledger identity")
        return existing
    tag = hashlib.sha256(account.encode()).hexdigest()[:16]
    archive = path.parent / "archive" / MIGRATION_ID / tag
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "migration": MIGRATION_ID, "account_hash": tag, "cutoff": now, "files": {},
    }
    sources = {path, path.with_suffix(".error.json")}
    for pattern in ("exness-learning*.json", "exness-forward-shadow*.json", "exness-ast-shadow*.json",
                    "exness-htf-ast-shadow*.json", "exness-scalp*.json", "exness-mtf-*.json"):
        sources.update(path.parent.glob(pattern))
    for source in sorted(sources):
        if not source.exists():
            continue
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        saved = archive / source.name
        if saved.exists():
            if saved.read_bytes() != raw:
                raise ValueError("Archive source changed during migration")
        else:
            archive.mkdir(parents=True, exist_ok=True)
            atomic_bytes(saved, raw)
        manifest["files"][source.name] = {"sha256": digest, "bytes": len(raw)}
    atomic_json(manifest_path, manifest)
    trees = g0_trees()
    ledger = {
        "runtime": RUNTIME, "account": account, "schema": SCHEMA, "since": now,
        "migration": {"id": MIGRATION_ID, "cutoff": now, "archive": str(archive)},
        "samples": [], "last_capture": None, "generation": 0, "population": trees,
        "members": {tree_id(tree): {"tree": tree, "generation": 0, "parents": [], "born_at": now}
                    for tree in trees},
        "rejections": {},
    }
    atomic_json(path, ledger)
    return ledger


def migrate_symbol_ledger(path, legacy, account, symbol, now):
    from .forex_htf_v2 import RUNTIME, SCHEMA, g0_trees, tree_id

    if path.exists():
        ledger = json.loads(path.read_text())
        if (ledger.get("account"), ledger.get("symbol"), ledger.get("runtime"), ledger.get("schema")) != (account, symbol, RUNTIME, SCHEMA):
            raise ValueError("Invalid multi-symbol V2 ledger identity")
        return ledger
    legacy.parent.mkdir(parents=True, exist_ok=True)
    with legacy.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            ledger = json.loads(path.read_text())
            if (ledger.get("account"), ledger.get("symbol"), ledger.get("runtime"), ledger.get("schema")) != (account, symbol, RUNTIME, SCHEMA):
                raise ValueError("Invalid multi-symbol V2 ledger identity")
            return ledger
        if symbol == "XAUUSD" and legacy.exists():
            ledger = json.loads(legacy.read_text())
            if ((ledger.get("account"), ledger.get("runtime"), ledger.get("schema")) != (account, RUNTIME, SCHEMA)
                    or ledger.get("symbol") not in (None, "XAUUSD")):
                raise ValueError("Invalid legacy V2 ledger identity; migration blocked")
            manifest = path.with_suffix(".migration.json")
            raw = legacy.read_bytes()
            archive = path.parent / "archive" / "forex_multi_symbol_1" / legacy.name
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists() and archive.read_bytes() != raw:
                raise ValueError("Legacy ledger changed during multi-symbol migration")
            if not archive.exists():
                atomic_bytes(archive, raw)
            atomic_json(manifest, {"id": "forex_multi_symbol_1", "symbol": symbol,
                                   "sha256": hashlib.sha256(raw).hexdigest(), "archive": str(archive)})
            ledger = {**ledger, "symbol": symbol}
        else:
            trees = g0_trees()
            ledger = {"runtime": RUNTIME, "schema": SCHEMA, "account": account, "symbol": symbol,
                      "since": now, "generation": 0, "population": trees, "samples": [],
                      "last_capture": None, "rejections": {},
                      "members": {tree_id(tree): {"tree": tree, "generation": 0, "parents": [], "born_at": now}
                                  for tree in trees}}
        atomic_json(path, ledger)
        return ledger
