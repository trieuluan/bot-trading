"""Frozen child genomes awaiting replay evidence and a safe forward slot."""

import copy
from uuid import uuid4

from .futures_strategies import (
    exit_policy_enabled,
    genome_fingerprint,
    genome_name,
    validate_genome,
)

MAX_PENDING = 3
RETRY_SECONDS = 7 * 86400
BACKOFF_SECONDS = 300
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 1200
PENDING = {"queued", "running", "insufficient", "waiting_slot"}


def enqueue(state, genome, now, parents):
    validate_genome(genome)
    drafts = state.setdefault("drafts", {})
    known = {**state.get("rule_archive", {}), **state["candidates"]}
    fingerprint, name = genome_fingerprint(genome), genome_name(genome)
    if (sum(row["status"] in PENDING for row in drafts.values()) >= MAX_PENDING
            or not exit_policy_enabled(genome)
            or any(genome_fingerprint(row["genome"]) == fingerprint
                   for row in [*known.values(), *drafts.values()])
            or name in known or any(row["strategy"] == name for row in drafts.values())):
        return None
    draft_id = str(uuid4())
    drafts[draft_id] = {
        "id": draft_id, "strategy": name, "genome": copy.deepcopy(genome),
        "fingerprint": fingerprint, "parents": list(parents),
        "generation": max((known[parent]["generation"] for parent in parents if parent in known), default=-1) + 1,
        "created_at": now, "updated_at": now, "status": "queued", "phase": "queued",
        "completed": None, "total": None, "attempts": 0, "runs": [],
        "next_retry_at": None, "reason": None, "replay": None,
        "admitted_at": None, "admitted_strategy": None,
    }
    return draft_id


def ready(draft, now):
    if draft["status"] == "running":
        return True
    if draft["status"] == "insufficient":
        return now >= (draft.get("next_retry_at") or float("inf"))
    return (draft["status"] == "queued" and now >= (draft.get("next_retry_at") or 0)
            and draft["attempts"] < MAX_ATTEMPTS)


def claim(draft, now):
    if not ready(draft, now):
        raise ValueError("Draft is not ready")
    if draft["status"] == "insufficient":
        draft.update(attempts=0, window=None)
    if draft["status"] == "running":
        draft["runs"][-1].update(status="interrupted", finished_at=now)
        draft["attempts"] -= 1
    if draft["attempts"] >= MAX_ATTEMPTS:
        draft.update(status="error", phase="done", reason="attempts_exhausted", updated_at=now)
        return None
    end = now // 900 * 900
    window = draft.get("window") or {"start": end - 90 * 86400, "end": end, "warmup_bars": 1600}
    token = str(uuid4())
    draft.update(status="running", phase="data", completed=None, total=None,
                 attempts=draft["attempts"] + 1, updated_at=now, started_at=now,
                 next_retry_at=None, reason=None, window=window, run_token=token)
    draft["runs"].append({"token": token, "started_at": now, "window": copy.deepcopy(window),
                          "fingerprint": draft["fingerprint"], "status": "running"})
    return token


def gate(report):
    row = report["strategies"][0]
    train, validation = row["train"], row["validation"]
    checks = {"total_samples_gte_30": train["resolved"] + validation["resolved"] >= 30,
              "validation_samples_gte_10": validation["resolved"] >= 10,
              "validation_mean_gt_0": (validation["mean_r"] or 0) > 0,
              "validation_dd_lt_10r": validation["max_drawdown_r"] < 10}
    sufficient = checks["total_samples_gte_30"] and checks["validation_samples_gte_10"]
    status = "insufficient" if not sufficient else "waiting_slot" if all(checks.values()) else "rejected"
    return status, {"checks": checks, "total_samples": train["resolved"] + validation["resolved"],
                    "train_samples": train["resolved"], "validation_samples": validation["resolved"],
                    "train_mean_r": train["mean_r"], "validation_mean_r": validation["mean_r"],
                    "validation_drawdown_r": validation["max_drawdown_r"],
                    **{key: report[key] for key in ("start_time", "end_time", "train_before", "bars",
                                                    "warmup_bars", "fee_bps", "slippage_bps",
                                                    "dataset_sha256", "signal_inputs")}}


def complete(draft, token, now, *, report=None, error=None, retryable=True):
    if draft["status"] != "running" or draft.get("run_token") != token:
        return False
    if error is not None:
        retry = retryable and draft["attempts"] < MAX_ATTEMPTS
        draft.update(status="queued" if retry else "error", reason=str(error),
                     next_retry_at=now + BACKOFF_SECONDS if retry else None)
    else:
        status, result = gate(report)
        draft.update(status=status, replay=result,
                     reason="population_slot" if status == "waiting_slot" else status,
                     next_retry_at=now + RETRY_SECONDS if status == "insufficient" else None)
    draft.update(phase="done", updated_at=now, finished_at=now)
    draft["runs"][-1].update(status=draft["status"], finished_at=now, error=error,
                              result=draft.get("replay") if error is None else None)
    return True
