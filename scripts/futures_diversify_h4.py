"""One-off: give active default H4 trees varied confirmation pairs.

Dry-run by default. Use --apply to write. Changed trees get new IDs and an empty
forward history; protected trees and the default control group stay unchanged.
"""
import argparse
import copy
import json
import random
import sys
import time

from backend.app.db import FuturesLearningState, Session
from backend.app.futures_learner import active_strategies, read_state, save_state
from backend.app.futures_rule_tree import H4_CONFIRMATIONS, consensus_slot, h4_consensus
from backend.app.futures_strategies import (
    genome_fingerprint,
    genome_name,
    validate_genome,
)
from backend.app.futures_symbols import SYMBOLS, state_id, symbol_scope
from backend.app.paper import lock_wallet

DEFAULT = ("swing_structure", "market_alignment")
CONTROL_RATIO = 0.1

def pairs():
    return [(left, right) for index, left in enumerate(H4_CONFIRMATIONS)
            for right in H4_CONFIRMATIONS[index + 1:] if {left, right} != set(DEFAULT)]

def default_slot(genome):
    slot = (genome.get("rule") or {}).get("children", [{}])[0]
    return consensus_slot(slot) and tuple(node["feature"] for node in slot["children"][1:]) == DEFAULT

def protected_names(state, active):
    if active is None:
        raise ValueError("Cannot prove ownership of an active Paper order")
    protected = set(active)
    protected.update(row["strategy"] for row in state["samples"]
                     if row.get("status") == "open" or row.get("status") == "resolved")
    decision = state.get("last_decision") or {}
    if decision.get("selected"):
        protected.add(decision["selected"])
    return protected

def diversify(state, active, now, seed):
    if state.get("h4_diversified_at"):  # one-off per symbol; control group stays control
        return {"eligible": 0, "control": 0, "protected": 0, "changed": 0, "pairs": 0, "replaced": {}}
    protected = protected_names(state, active)
    known = {**state.get("rule_archive", {}), **state["candidates"], **{
        row["strategy"]: row for row in state.get("drafts", {}).values()}}
    fingerprints = {genome_fingerprint(row["genome"]) for row in known.values()}
    eligible = sorted(name for name, row in state["candidates"].items()
                      if name not in protected and default_slot(row["genome"]))
    rng = random.Random(seed)
    rng.shuffle(eligible)
    keep = max(1, round(len(eligible) * CONTROL_RATIO)) if eligible else 0
    choices = pairs()
    rng.shuffle(choices)
    replaced, born, cursor = {}, (now // 900 + 1) * 900, 0
    for old in eligible[keep:]:
        candidate = state["candidates"][old]
        for _ in range(len(choices)):
            confirmations = choices[cursor % len(choices)]
            cursor += 1
            genome = copy.deepcopy(candidate["genome"])
            genome["rule"]["children"][0] = h4_consensus(confirmations)
            validate_genome(genome)
            name, fingerprint = genome_name(genome), genome_fingerprint(genome)
            if name in known or fingerprint in fingerprints:
                continue
            state.setdefault("rule_archive", {})[old] = {
                **state["candidates"].pop(old),
                "retirement": {"at": now, "reason": "h4_diversified", "samples": 0,
                               "mean_r": 0.0, "max_drawdown_r": 0.0,
                               "retained_strategy": name, "overlap": None},
            }
            state["candidates"][name] = {
                **copy.deepcopy(candidate), "genome": genome, "born_at": born,
                "windows": [], "promoted": False, "h4_diversified_from": old,
            }
            state["candidates"][name].pop("draft_id", None)
            known[name] = state["candidates"][name]
            fingerprints.add(fingerprint)
            replaced[old] = {"strategy": name, "confirmations": confirmations}
            break
    state["h4_diversified_at"] = now
    return {"eligible": len(eligible), "control": keep, "protected": len(protected),
            "changed": len(replaced), "pairs": len({tuple(row["confirmations"]) for row in replaced.values()}),
            "replaced": replaced}

def run(apply):
    now = int(time.time())
    summary = {}
    for symbol in SYMBOLS:
        with symbol_scope(symbol), Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, now)
            try:
                result = diversify(state, active_strategies(session, state), now, f"{symbol}:{now // 900}")
            except ValueError as error:  # owner of an open order not provable: skip symbol
                summary[symbol] = {"skipped": str(error)}
                session.rollback()
                continue
            if apply:
                save_state(session, state)
            else:
                session.rollback()
            summary[symbol] = {key: value for key, value in result.items() if key != "replaced"}
            assert session.get(FuturesLearningState, state_id()) is not None
    print(json.dumps({"applied": apply, "summary": summary}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    run(parser.parse_args().apply)
    sys.exit(0)
