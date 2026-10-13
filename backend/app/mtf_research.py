"""Frozen confirmation features and forward-only hypothetical outcomes. No execution."""

import math

from sqlalchemy import select

from .db import MtfResearchSample
from .mexc import SOURCE, SYMBOL, TIMEFRAME
from .strategy import aggregate


def extract_features(candles, snapshot):
    last = candles[-1]
    if snapshot["confirm_time"] != last.time or len(candles) < 21:
        raise ValueError("Features require confirmation on the latest closed candle")
    if any(
        not math.isfinite(value)
        for candle in candles
        for value in (candle.open, candle.high, candle.low, candle.close, candle.volume)
    ):
        raise ValueError("Non-finite candle data")
    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(candles[-15:-1], candles[-14:])
    ]
    atr = sum(true_ranges) / 14
    higher = aggregate(candles, 14400)
    if len(higher) < 50 or atr <= 0 or last.close <= 0:
        raise ValueError("Insufficient history or invalid ATR/price")
    ema = higher[0].close
    for candle in higher[1:]:
        ema += 2 / 51 * (candle.close - ema)
    mean_volume = sum(candle.volume for candle in candles[-21:-1]) / 20
    direction = 1 if snapshot["side"] == "long" else -1
    return {
        "version": "mtf_features_v1",
        "source": SOURCE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "strategy": snapshot["strategy"],
        "parameters": snapshot["parameters"],
        "side": snapshot["side"],
        "breakout_time": snapshot["breakout_time"],
        "confirm_time": last.time,
        "available_at": last.time + 900,
        "hour_utc": (last.time + 900) // 3600 % 24,
        "atr_15m_14": atr,
        "atr_percent": atr / last.close * 100,
        "ema_4h_50": ema,
        "directional_ema_distance_atr": direction * (last.close - ema) / atr,
        "range_width_atr": (snapshot["range_high"] - snapshot["range_low"]) / atr,
        "confirmation_volume_ratio": last.volume / mean_volume if mean_volume > 0 else None,
        "confirmation_body_atr": direction * (last.close - last.open) / atr,
        "risk_percent_at_confirmation": abs(last.close - snapshot["stop"]) / last.close * 100,
        "bars_after_breakout": (last.time - snapshot["breakout_time"] - 3600) // 900 + 1,
        "bars_after_touch": (last.time - snapshot["touch_time"]) // 900,
        "funding_rate": None,
    }


def advance(evaluation, candle):
    state = dict(evaluation)
    if state["status"] not in ("pending", "open") or candle.time <= state["last_candle"]:
        return state
    if candle.time != state["last_candle"] + 900:
        return {**state, "status": "censored", "reason": "missing_candles", "label": None}
    state["last_candle"] = candle.time
    direction = 1 if state["side"] == "long" else -1
    slip = state["slippage_bps"] / 10000
    if state["status"] == "pending":
        entry = candle.open * (1 + direction * slip)
        if direction * (entry - state["stop"]) <= 0 or direction * (state["target"] - entry) <= 0:
            return {**state, "status": "invalid", "reason": "entry_outside_levels", "label": None}
        state.update(status="open", entry=entry, entry_time=candle.time, bars=0)
    state["bars"] += 1
    stop, target = state["stop"], state["target"]
    if direction * (candle.open - stop) <= 0:
        exit_price, outcome = candle.open, "stop_gap"
    elif candle.low <= stop if direction == 1 else candle.high >= stop:
        exit_price, outcome = stop, "stop"
    elif candle.high >= target if direction == 1 else candle.low <= target:
        exit_price, outcome = target, "target"
    elif state["bars"] >= state["horizon_bars"]:
        exit_price, outcome = candle.close, "timeout"
    else:
        return state
    exit_price *= 1 - direction * slip
    entry = state["entry"]
    pnl = direction * (exit_price - entry) - (entry + exit_price) * state["fee_bps"] / 10000
    return {
        **state,
        "status": "resolved",
        "outcome": outcome,
        "exit": exit_price,
        "exit_candle": candle.time,
        "label_available_at": candle.time + 900,
        "net_r_ex_funding": pnl / abs(entry - stop),
        "label": int(pnl > 0),
    }


def sample_values(candles, snapshot):
    return dict(
        confirm_time=candles[-1].time,
        features=extract_features(candles, snapshot),
        evaluation={
            "version": "next_open_24h_v1",
            "provenance": snapshot.get("provenance", "live"),
            "status": "pending",
            "label": None,
            "side": snapshot["side"],
            "stop": snapshot["stop"],
            "target": snapshot["target"],
            "last_candle": candles[-1].time,
            "horizon_bars": 96,
            "fee_bps": 5,
            "slippage_bps": 2,
            "funding_included": False,
        },
    )

def collect_sample(session, candles, snapshot):
    last = candles[-1]
    samples = session.scalars(
        select(MtfResearchSample).where(
            MtfResearchSample.evaluation["status"].as_string().in_(("pending", "open"))
        )
    ).all()
    for sample in samples:
        sample.evaluation = advance(sample.evaluation, last)
    if snapshot["status"] != "confirmed" or snapshot["confirm_time"] != last.time:
        return
    if session.get(MtfResearchSample, last.time) is not None:
        return
    session.add(MtfResearchSample(**sample_values(candles, snapshot)))
