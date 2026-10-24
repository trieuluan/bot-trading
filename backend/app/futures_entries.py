"""Optional entry policy gene. Absent or "market" keeps the next-open market entry."""

import math
from dataclasses import replace

from .futures_rule_tree import frame_rows

ENTRY_POLICIES = ("market", "body38", "body50", "ema20_m15", "breakout_h1", "retest_h1")
WAIT_BARS = 4

def entry_level(policy, history, side, stop):
    direction = 1 if side == "long" else -1
    signal = history[-1]
    if policy in ("body38", "body50"):
        fraction = 0.38 if policy == "body38" else 0.5
        level = signal.close - direction * fraction * abs(signal.close - signal.open)
    elif policy == "ema20_m15":
        if len(history) < 20:
            return None
        level = history[0].close
        for bar in history[1:]:
            level += 2 / 21 * (bar.close - level)
    elif policy in ("breakout_h1", "retest_h1"):
        hours = frame_rows(history, "H1")
        if len(hours) < 21:
            return None
        prior = hours[-21:-1]
        level = (max(bar.high for bar in prior) if direction == 1
                 else min(bar.low for bar in prior))
        if direction * (hours[-1].close - level) <= 0:
            return None
    else:
        raise ValueError(f"Unknown entry policy: {policy}")
    return level if (math.isfinite(level) and level > 0
                     and direction * (signal.close - level) > 0
                     and direction * (level - stop) > 0) else None

def entry_fields(genome, history, setup, stamp):
    """Extra sample fields at signal time; {} for market."""
    policy = genome.get("entry_policy", "market")
    if policy == "market":
        return {}
    level = entry_level(policy, history, setup.side, setup.stop)
    fields = {"entry_policy": policy, "level": level, "first_entry_time": stamp + 900}
    if level is None:
        fields.update(status="resolved", exit_reason="no_valid_level", net_r=0.0,
                      label_available_at=stamp + 900)
    return fields

def wait(sample, bar, funding):
    """Pre-fill phase of a limit/retest entry.

    Returns (bar, funding) for the caller to settle once filled, else None
    (still waiting, or status set to "cancelled"). Limits fill on trade-through only,
    at the limit price; stop is checked first and a fill-bar target counts only if
    the bar closed beyond it.
    """
    if bar.time < sample["entry_time"]:
        return None
    if bar.time >= sample["first_entry_time"] + WAIT_BARS * 900:
        sample.update(status="cancelled", exit_reason="expired")
        return None
    direction = 1 if sample["side"] == "long" else -1
    level = sample["level"]
    touched = bar.low < level if direction == 1 else bar.high > level
    target_touched = (bar.high >= sample["target"] if direction == 1
                      else bar.low <= sample["target"])
    if sample["entry_policy"] == "retest_h1":
        if direction * (bar.close - level) < 0:
            sample.update(status="cancelled", exit_reason="failed_retest")
        elif target_touched:
            sample.update(status="cancelled", exit_reason="target_before_confirmation")
        elif touched and direction * (bar.close - level) > 0 and direction * (bar.close - bar.open) > 0:
            sample.update(confirmed=True, entry_time=bar.time + 900)
        return None
    if not touched:
        if target_touched:
            sample.update(status="cancelled", exit_reason="target_before_entry")
        return None
    sample.update(entry=level, risk=direction * (level - sample["stop"]),
                  funding_cost=0.0, entry_time=bar.time)
    fill_bar = bar
    if target_touched and direction * (bar.close - sample["target"]) < 0:
        edge = math.nextafter(sample["target"], -direction * math.inf)
        fill_bar = replace(bar, **{"high" if direction == 1 else "low": edge})
    return fill_bar, [row for row in funding if row["time"] != bar.time]
