"""Closed-M15 entry confirmation; legacy policies remain readable for research."""

import math
from dataclasses import replace

from .futures_native import NATIVE_ENTRY, entry_session_allowed, hold_seconds
from .futures_rule_tree import confirmed_pivots, frame_rows, structure_level

ENTRY_POLICIES = ("retest_h1", "sweep_h1", "pivot_retest_h1", "pivot_sweep_h1",
                  "micro_bos_m15", "sweep_m15", "body38_confirm", "body50_confirm", "ema20_m15_confirm")
LEGACY_POLICIES = ("market", "body38", "body50", "ema20_m15", "breakout_h1")
WAIT_BARS = 4

def confirmation_enabled(genome):
    return genome.get("entry_policy") in (*ENTRY_POLICIES, NATIVE_ENTRY)

def support_pivot(rows, direction):
    pivots = confirmed_pivots(rows, -direction, 20)
    if not pivots:
        return None
    pivot = pivots[-1]
    level = rows[pivot].low if direction == 1 else rows[pivot].high
    if any((bar.low < level if direction == 1 else bar.high > level) for bar in rows[pivot + 1:]):
        return None
    return level

def entry_level(policy, history, side, stop):
    direction = 1 if side == "long" else -1
    signal = history[-1]
    if policy in ("body38_confirm", "body50_confirm", "ema20_m15_confirm"):
        policy = policy.removesuffix("_confirm")
    if policy in ("body38", "body50"):
        fraction = 0.38 if policy == "body38" else 0.5
        level = signal.close - direction * fraction * abs(signal.close - signal.open)
    elif policy == "ema20_m15":
        if len(history) < 20:
            return None
        level = history[0].close
        for bar in history[1:]:
            level += 2 / 21 * (bar.close - level)
    elif policy in ("breakout_h1", "retest_h1", "sweep_h1"):
        hours = frame_rows(history, "H1")
        if len(hours) < 21:
            return None
        prior = hours[-21:-1]
        level = (max(bar.high for bar in prior) if direction == 1
                 else min(bar.low for bar in prior))
        if direction * (hours[-1].close - level) <= 0:
            return None
    elif policy in ("pivot_retest_h1", "pivot_sweep_h1"):
        hours = frame_rows(history, "H1")
        pivot = structure_level(hours, direction, 20) if len(hours) >= 22 else None
        if pivot is None or direction * (hours[-1].close - pivot[0]) <= 0:
            return None
        level = pivot[0]
    elif policy in ("micro_bos_m15", "sweep_m15"):
        level = support_pivot(frame_rows(history, "H1") if policy == "micro_bos_m15" else history, direction)
        if level is None:
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
    if policy == NATIVE_ENTRY:
        fields = {"entry_policy": policy, "confirmed": True,
                  "signal_frame": genome.get("signal_frame"), "target_r": genome.get("target_r", 2.0)}
        if genome.get("entry_confirmation") == "h1":
            fields.update(confirmed=False, entry_confirmation="h1", first_entry_time=stamp + 900,
                          entry_session=genome["entry_session"], next_confirmation_bar=stamp + 900,
                          hold_until=stamp + 900 + hold_seconds("H4"))
        return fields
    level = entry_level(policy, history, setup.side, setup.stop)
    fields = {"entry_policy": policy, "level": level, "first_entry_time": stamp + 900}
    if genome.get('signal_version') == 2:
        fields['signal_version'] = 2
    if level is None:
        fields.update(status="resolved", exit_reason="no_valid_level", net_r=0.0,
                      label_available_at=stamp + 900)
    return fields

def wait_confirmation(sample, bar, history):
    direction = 1 if sample["side"] == "long" else -1
    policy, level = sample["entry_policy"], sample["level"]
    stopped = bar.low <= sample["stop"] if direction == 1 else bar.high >= sample["stop"]
    targeted = bar.high >= sample["target"] if direction == 1 else bar.low <= sample["target"]
    touched = bar.low < level if direction == 1 else bar.high > level
    sweep = policy in ("sweep_h1", "pivot_sweep_h1", "sweep_m15")
    if stopped:
        sample.update(status="cancelled", exit_reason="stop_before_confirmation")
    elif policy != "micro_bos_m15" and direction * (bar.close - level) < 0:
        sample.update(status="cancelled", exit_reason="closed_through_level" if sweep else "failed_retest")
    elif targeted:
        sample.update(status="cancelled", exit_reason="target_before_confirmation")
    elif policy == "micro_bos_m15":
        touched = bar.low <= level if direction == 1 else bar.high >= level
        if touched and not sample.get("zone_touched"):
            pivot = structure_level(history, direction, 20) if history else None
            if pivot is None or direction * (bar.close - pivot[0]) > 0:
                sample.update(status="cancelled", exit_reason="no_valid_micro_pivot")
            else:
                sample.update(zone_touched=True, bos_trigger=pivot[0])
        elif sample.get("zone_touched") and direction * (bar.close - sample["bos_trigger"]) > 0 and direction * (bar.close - bar.open) > 0:
            sample.update(confirmed=True, entry_time=bar.time + 900, confirmation_time=bar.time)
    elif touched and direction * (bar.close - level) > 0 and (sweep or direction * (bar.close - bar.open) > 0):
        sample.update(confirmed=True, entry_time=bar.time + 900, confirmation_time=bar.time)

def wait_native_h1(sample, bar, history):
    """Stop-first, closed H1 confirmation within four hours of the H4 signal."""
    first = sample["first_entry_time"]
    if bar.time != sample.get("next_confirmation_bar", first):
        sample.update(status="cancelled", exit_reason="missing_confirmation_bar")
        return
    sample["next_confirmation_bar"] = bar.time + 900
    direction = 1 if sample["side"] == "long" else -1
    if (bar.low <= sample["stop"] if direction == 1 else bar.high >= sample["stop"]):
        sample.update(status="cancelled", exit_reason="stop_before_confirmation")
        return
    close = bar.time + 900
    if close > first + 14400 or not entry_session_allowed(sample, close):
        sample.update(status="cancelled", exit_reason="confirmation_outside_session_or_expired")
        return
    if close % 3600:
        return
    hour = [row for row in (history or []) if close - 3600 <= row.time < close]
    if [row.time for row in hour] != list(range(close - 3600, close, 900)) or hour[0].time < first:
        sample.update(status="cancelled", exit_reason="incomplete_confirmation_hour")
    elif direction * (bar.close - hour[0].open) > 0:
        sample.update(confirmed=True, entry_time=close, confirmation_time=bar.time)
    elif close == first + 14400:
        sample.update(status="cancelled", exit_reason="expired")

def wait(sample, bar, funding, history=None):
    """Pre-fill phase of a limit/retest entry.

    Returns (bar, funding) for the caller to settle once filled, else None
    (still waiting, or status set to "cancelled"). Limits fill on trade-through only,
    at the limit price; stop is checked first and a fill-bar target counts only if
    the bar closed beyond it.
    """
    if bar.time < sample["entry_time"]:
        return None
    if sample.get("entry_confirmation") == "h1" and sample["entry_policy"] == NATIVE_ENTRY:
        wait_native_h1(sample, bar, history)
        return None
    if bar.time >= sample["first_entry_time"] + WAIT_BARS * 900:
        sample.update(status="cancelled", exit_reason="expired")
        return None
    if sample["entry_policy"] in ENTRY_POLICIES:
        wait_confirmation(sample, bar, history)
        return None
    if sample["entry_policy"] not in LEGACY_POLICIES or sample["entry_policy"] == "market":
        sample.update(status="cancelled", exit_reason="unsupported_entry_policy")
        return None
    direction = 1 if sample["side"] == "long" else -1
    level = sample["level"]
    touched = bar.low < level if direction == 1 else bar.high > level
    target_touched = (bar.high >= sample["target"] if direction == 1
                      else bar.low <= sample["target"])
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
