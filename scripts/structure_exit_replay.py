"""Offline H1 structural invalidation overlay; no changes to trading code or VPS.

Run: /private/tmp/btvenv3/bin/python -m scripts.structure_exit_replay
"""

import bisect
import hashlib
import json
from pathlib import Path

from backend.app import futures_exits as exits
from backend.app import futures_learner as learner
from backend.app.futures_rule_tree import structure_level
from scripts.entry_timing_paired import simulate
from scripts.exit_tuning_replay import run_grid

OUTPUT = Path("research-output/structure-exit-replay.json")


def structure_exit(position, candles, confirm_hours=1):
    if not candles:
        return None
    hours = exits.aggregate(candles, 3600)
    if not hours or hours[-1].time + 3600 != candles[-1].time + 900:
        return None
    current = hours[-1]
    if current.time + 3600 <= position["entry_time"]:
        return None
    direction = 1 if position["side"] == "long" else -1
    pending = position.pop("structure_pending", None)
    if pending is not None and current.time == pending["time"] + 3600:
        if direction * (current.close - pending["price"]) < 0:
            return "h1_structure_2close"
    level = structure_level(hours, -direction, 20)
    if level is None or direction * (current.close - level[0]) >= 0:
        return None
    if confirm_hours == 1:
        return "h1_structure_1close"
    position["structure_pending"] = dict(price=level[0], time=current.time)
    return None


def settle_boundary(sample, rows, fair, funding, times, entry_index, boundary, history):
    result = simulate(sample, rows, fair, funding, times, entry_index, boundary, history)
    if result["status"] == "excluded" and result.get("exit_reason") in ("boundary", "dataset_boundary"):
        index = bisect.bisect_left([bar.time for bar in rows], boundary) - 1
        if index < entry_index or rows[index].time + 900 != boundary:
            raise ValueError("Missing final closed candle for administrative settlement")
        learner.finish(result, 1 if result["side"] == "long" else -1,
                       rows[index].close, "boundary_mark", rows[index])
    return result


def main():
    configs = [
        ("baseline_48h_be1.0_weak", 192, 1.0, False),
        ("no_max_hold_be1.0_weak", 999999, 1.0, False),
        ("structure_h1_1close_no_cap", 999999, 1.0, False),
        ("structure_h1_2close_no_cap", 999999, 1.0, False),
    ]
    overrides = {
        "structure_h1_1close_no_cap": structure_exit,
        "structure_h1_2close_no_cap": lambda position, candles: structure_exit(position, candles, 2),
    }
    run_grid(configs=configs, output=OUTPUT, exit_overrides=overrides,
             simulate_trade=settle_boundary, notes=[
                 "Fixed archived cohort of 1,532 baseline-resolved signals across 5 adaptive seeds; entries and overlapping trades unchanged",
                 "H1 level: latest unbroken 2-left/2-right confirmed pivot in prior 20 H1 bars, known before break candle",
                 "Short exits on H1 close above pivot high; long exits on H1 close below pivot low; no wick-based exits",
                 "2close requires next distinct H1 close beyond same frozen level; resets if reclaimed or data gap",
                 "Existing SL/TP checked before structural exit; fills at H1 close with existing slippage, fees and funding; partial and trailing unchanged",
                 "No holding cap in structural branches; remaining positions marked closed at split/dataset boundary including costs, not discarded",
                 "boundary_mark is administrative settlement, not an actual strategy exit; prior no-cap report excluded 10 boundary positions",
                 "Validation has been inspected in earlier research; not a fresh holdout; nominal week-block CI without multiple-testing correction",
                 "Signal-level experiment, not portfolio ROI, drawdown or evidence that mass stop-outs are eliminated",
             ])
    report = json.loads(OUTPUT.read_text())
    report["structure_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
