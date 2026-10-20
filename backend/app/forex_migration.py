"""One-time, account-locked cutover; callers hold the V2 ledger lock."""

import hashlib
import json
import os
import tempfile

MIGRATION_ID = "htf_v2_only_1"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


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
            with saved.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
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
