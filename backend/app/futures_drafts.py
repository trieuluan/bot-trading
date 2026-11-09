"""Frozen child genomes awaiting replay evidence and a safe forward slot."""

import copy
from uuid import uuid4

from .futures_entries import confirmation_enabled
from .futures_native import NATIVE_WARMUP
from .futures_strategies import (
    exit_policy_enabled,
    genome_fingerprint,
    genome_name,
    validate_genome,
)
from .futures_symbols import SYMBOLS

MAX_PENDING = 3
RETRY_SECONDS = 7 * 86400
BACKOFF_SECONDS = 300
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 1200
UNIVERSE_DAYS = 340
PAIR_DAYS = 60
UNIVERSE_TIMEOUT_SECONDS = 3600
UNIVERSE_SCOPE = "native_universe_v1"
PENDING = {"queued", "running", "insufficient", "waiting_slot"}


def enqueue(state, genome, now, parents):
    validate_genome(genome)
    drafts = state.setdefault("drafts", {})
    known = {**state.get("rule_archive", {}), **state["candidates"]}
    fingerprint, name = genome_fingerprint(genome), genome_name(genome)
    if (sum(row["status"] in PENDING for row in drafts.values()) >= MAX_PENDING
            or not exit_policy_enabled(genome)
            or not confirmation_enabled(genome)
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
    native = draft["genome"].get("signal_frame") in ("D1", "H4")
    interval = 86400 if native else 900
    end = now // interval * interval
    if native and "data_start" not in (draft.get("window") or {}):
        draft["window"] = None
    warmup_bars = NATIVE_WARMUP if draft["genome"].get("signal_frame") else 1600
    data_start = end - (UNIVERSE_DAYS if native else PAIR_DAYS) * 86400
    window = draft.get("window") or {
        "start": data_start + warmup_bars * 900 if native else data_start,
        "end": end,
        "warmup_bars": warmup_bars,
        **({"scope": UNIVERSE_SCOPE, "symbols": list(SYMBOLS), "data_start": data_start} if native else {}),
    }
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
    universe = report.get("scope") == UNIVERSE_SCOPE
    if universe:
        checks.update(
            universe_complete=report.get("symbols") == list(SYMBOLS)
            and set(report.get("per_symbol", {})) == set(SYMBOLS),
            validation_symbols_gte_3=validation.get("contributing_symbols", 0) >= 3,
            validation_pair_dd_lt_10r=validation.get("max_pair_drawdown_r", float("inf")) < 10,
        )
    sparse = (report["end_time"] - report["start_time"] >= 90 * 86400
              and train["resolved"] + validation["resolved"] < 5)
    status = "rejected" if sparse or universe and not checks["universe_complete"] else "insufficient" if not sufficient else "waiting_slot" if all(checks.values()) else "rejected"
    return status, {"checks": checks, "total_samples": train["resolved"] + validation["resolved"],
                    "train_samples": train["resolved"], "validation_samples": validation["resolved"],
                    "train_mean_r": train["mean_r"], "validation_mean_r": validation["mean_r"],
                    "validation_drawdown_r": validation["max_drawdown_r"],
                    **({key: report[key] for key in ("scope", "symbols", "per_symbol", "screening_note")}
                       if universe else {}),
                    **({"genome_fingerprint": genome_fingerprint(row["genome"])} if "genome" in row else {}),
                    **{key: report[key] for key in ("start_time", "end_time", "train_before", "bars",
                                                    "warmup_bars", "fee_bps", "slippage_bps",
                                                    "dataset_sha256", "signal_inputs")}}


def complete(draft, token, now, *, report=None, error=None, retryable=True):
    if draft["status"] != "running" or draft.get("run_token") != token:
        return False
    if error is None and draft["genome"].get("signal_frame") in ("D1", "H4"):
        rows = report.get("strategies", []) if report else []
        if (not report or report.get("scope") != UNIVERSE_SCOPE or len(rows) != 1
                or rows[0].get("strategy") != draft["strategy"]
                or genome_fingerprint(rows[0]["genome"]) != draft["fingerprint"]
                or report.get("start_time") != draft["window"]["start"]
                or report.get("end_time") != draft["window"]["end"]):
            error, retryable = "Invalid frozen native universe report", False
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
