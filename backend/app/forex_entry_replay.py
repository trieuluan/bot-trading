"""Conservative offline entry replay on unmodified bid/ask candles."""

import copy
import math
from collections import Counter
from types import SimpleNamespace

from .forex_buffer_study import advance_variant, initialize_variant, new_variants
from .forex_entry_policy import replay_touch, touched


def compare(bundle):
    from .forex_entry_policy import POLICIES, entry_node, levels
    results = []
    for sample in bundle["signals"]:
        signal = sample["signal"]
        frames = bundle["candles"][signal["symbol"]]
        def series(frame, side):
            return [SimpleNamespace(**row) for row in frames.get(frame, {}).get(side, [])]
        minutes, hours = series("15m", "bid"), series("1h", "bid")
        candle = next((row for row in minutes if row.time == signal["candle_time"]), None)
        duration = 60 if frames.get("1m") else 900
        frame = "1m" if duration == 60 else "15m"
        for policy in POLICIES:
            try:
                if candle is None:
                    raise ValueError("trigger_candle_missing")
                entry = levels(signal, candle, entry_node(policy))
                result = run({**copy.deepcopy(signal), "entry_policy": entry},
                             series(frame, "bid"), series(frame, "ask"), minutes, hours,
                             sample["condition"], sample["costs"], bundle["as_of"], duration=duration)
            except (KeyError, TypeError, ValueError) as exc:
                result = {"status": "excluded", "reason": str(exc)}
            results.append({"symbol": signal["symbol"], "strategy": signal["strategy"],
                            "setup_id": signal.get("setup_id"), "signal_time": signal["available_at"],
                            "policy": policy, **result})
    return {"source": "offline_replay_not_forward", "signals": len(bundle["signals"]),
            "counts": {policy: dict(Counter(row["status"] for row in results if row["policy"] == policy))
                       for policy in POLICIES}, "results": results}


def advance_forward(samples, bids, asks, now):
    from sqlalchemy import select

    from .db import Session, WorkspaceCandle
    from .exness_feed import WORKSPACE
    # expired without reason/execution came from the final fallthrough; re-evaluate recent ones (deterministic, idempotent)
    active = [sample for sample in samples if sample["status"] in ("waiting", "pending", "open") or
              sample["status"] == "expired" and "reason" not in sample and "execution" not in sample
              and now - sample["signal"]["entry_policy"]["expires_at"] < 86400]
    if not active:
        return
    with Session() as session:
        rows = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE,
            WorkspaceCandle.symbol == active[0]["signal"]["symbol"], WorkspaceCandle.timeframe == "1m",
            WorkspaceCandle.time >= min(sample["signal"]["available_at"] for sample in active),
            WorkspaceCandle.time + 60 <= now).order_by(WorkspaceCandle.time)).all()
    for sample in active:
        start = sample["signal"]["available_at"]
        bid_rows = [row for row in rows if row.price_type == "bid" and row.time >= start]
        ask_rows = [row for row in rows if row.price_type == "ask" and row.time >= start]
        # ponytail: partial M1 stays on M15 until both sides are continuous through the latest closed minute.
        use_minute = bool(bid_rows) and bid_rows[0].time == start and all(
            right.time - left.time == 60 for left, right in zip(bid_rows, bid_rows[1:]))
        use_minute = use_minute and [bar.time for bar in bid_rows] == [bar.time for bar in ask_rows] and (
            bid_rows[-1].time + 60 == int(now) // 60 * 60)
        if not use_minute:
            bid_rows, ask_rows = bids["15m"], list(asks.values())
        sample.update(run(sample["signal"], bid_rows, ask_rows, bids["15m"], bids["1h"], sample["condition"],
                          sample["costs"], now, duration=60 if use_minute else 900))


def run(signal, bid_rows, ask_rows, minutes, hours, condition, costs, now, *, duration=60):
    if duration not in (60, 900):
        raise ValueError("Replay requires M1 or explicit M15 fallback")
    result = {"status": "pending", "resolution_seconds": duration,
              "assumptions": {"commission_per_unit": costs["commission_per_unit"],
                              "slippage_bps": costs["slippage_bps"], "conversion": condition.get("conversion"),
                              "spread": "bid/ask OHLC; spread at touch approximated by bar-open spread"}}
    if any(type(costs[key]) not in (int, float) or not math.isfinite(costs[key]) or costs[key] < 0
           for key in ("commission_per_unit", "slippage_bps")):
        return {**result, "status": "excluded", "reason": "invalid_replay_costs"}
    asks = {bar.time: bar for bar in ask_rows if bar.time + duration <= now}
    rows = sorted((bar for bar in bid_rows if signal["available_at"] <= bar.time and bar.time + duration <= now),
                  key=lambda bar: bar.time)
    policy = signal["entry_policy"]
    slip, commission = costs["slippage_bps"] / 10000, costs["commission_per_unit"]
    simulation = None
    previous = signal["available_at"] - duration
    for bid in rows:
        ask = asks.get(bid.time)
        if ask is None or bid.time != previous + duration:
            return {**result, "status": "excluded", "reason": "replay_data_gap"}
        if any(not all(type(getattr(bar, key)) in (float, int) and math.isfinite(getattr(bar, key))
                       and getattr(bar, key) > 0 for key in ("open", "high", "low", "close"))
               or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) for bar in (bid, ask)):
            return {**result, "status": "excluded", "reason": "invalid_replay_bar"}
        if any(getattr(ask, key) < getattr(bid, key) for key in ("open", "high", "low", "close")):
            return {**result, "status": "excluded", "reason": "invalid_bid_ask"}
        previous = bid.time
        closed_minutes = [bar for bar in minutes if bar.time + 900 <= bid.time + duration]
        closed_hours = [bar for bar in hours if bar.time + 3600 <= bid.time + duration]
        if simulation is None:
            if bid.time >= policy["expires_at"]:
                return {**result, "status": "expired", "reason": "entry_expired"}
            direction = 1 if signal["side"] == "long" else -1
            if any(bar.time + 3600 > signal["available_at"] and
                   bar.time + 3600 <= bid.time and
                   direction * (bar.close - signal["htf_structure"]["invalidation"]) <= 0 for bar in closed_hours):
                return {**result, "status": "excluded", "reason": "setup_invalidated"}
            price_bar = ask if direction == 1 else bid
            if policy["policy"] == "market":
                touch = {"status": "filled", "winner": "market", "entry": price_bar.open, "intrabar": False}
            else:
                if bid.time == signal["available_at"] and touched(policy, signal["side"], price_bar.open):
                    return {**result, "status": "excluded", "reason": "level_already_crossed"}
                touch = replay_touch(policy, signal["side"], bid, ask)
            if touch["status"] == "ambiguous":
                scenarios = {}
                for branch in touch["branches"]:
                    alternative = copy.deepcopy(signal)
                    alternative["entry_policy"]["limit_price" if branch == "stop" else "stop_price"] = None
                    scenarios[branch] = run(alternative, bid_rows, ask_rows, minutes, hours, condition,
                                            costs, now, duration=duration)
                lows = [row.get("lower_r", row.get("net_r_ex_swap")) for row in scenarios.values()]
                highs = [row.get("upper_r", row.get("net_r_ex_swap")) for row in scenarios.values()]
                return {**result, **touch, "reason": "entry_order_unknown", "scenarios": scenarios,
                        "lower_r": min(lows) if all(value is not None for value in lows) else None,
                        "upper_r": max(highs) if all(value is not None for value in highs) else None}
            if touch["status"] != "filled":
                continue
            captured = type("Capture", (), {"time": signal["candle_time"]})()
            features = {"trend": signal["side"], "utc_session": signal["available_at"] // 21600 % 4}
            simulation = new_variants(signal, [], [], [], features, captured, condition)[0]
            spread = ask.open - bid.open
            conversion = condition.get("conversion")
            if ((condition.get("quote_currency", "USD") != "USD" and not conversion)
                    or conversion and (not conversion.get("verified") or not 0 <= bid.time - conversion.get("observed_at", 0) <= 3600)):
                return {**result, "status": "excluded", "reason": "conversion_unavailable_at_entry"}
            if spread < 0:
                return {**result, "status": "excluded", "reason": "invalid_bid_ask"}
            bid_open = touch["entry"] - spread if direction == 1 else touch["entry"]
            ask_open = touch["entry"] if direction == 1 else touch["entry"] + spread
            initialize_variant(simulation, bid_open, ask_open, bid.time, slip, commission)
            if simulation["status"] != "open":
                return {**result, "status": "excluded", "reason": simulation.get("reason", "entry_preflight_failed")}
            result.update(winner=touch["winner"], entry=simulation["entry"], entry_time=bid.time)
            exit_bar = bid if direction == 1 else ask
            crossed = any((exit_bar.low <= float(leg["stop_loss_price"]) or exit_bar.high >= float(leg["take_profit_price"]))
                          if direction == 1 else (exit_bar.high >= float(leg["stop_loss_price"]) or exit_bar.low <= float(leg["take_profit_price"]))
                          for leg in simulation["legs"])
            if touch["intrabar"] and crossed:
                def net_r(key):
                    total = 0
                    for leg in simulation["legs"]:
                        gross = direction * (float(leg[key]) * (1 - direction * slip) - simulation["entry"])
                        gain, loss = float(condition.get("quote_to_account_gain", 1)), float(condition.get("quote_to_account_loss", 1))
                        total += leg["weight"] * (gross * (gain if gross >= 0 else loss) - commission * gain) / (simulation["risk"] * loss)
                    return total
                return {**result, "status": "ambiguous", "reason": "entry_exit_order_unknown",
                        "lower_r": net_r("stop_loss_price"), "upper_r": net_r("take_profit_price")}
            simulation["last_candle"] = bid.time - duration
            if touch["intrabar"]:
                simulation["last_candle"] = bid.time
                continue
        advance_variant(simulation, bid, ask, closed_minutes, slip, commission,
                        horizon=96 * 900 // duration, hours=closed_hours, duration=duration)
        if simulation["status"] not in ("open", "pending"):
            return {**result, "status": simulation["status"], "execution": simulation,
                    "net_r_ex_swap": simulation.get("net_r"), "label_available_at": simulation.get("resolved_at")}
    if simulation:
        return {**result, "status": "open", "execution": simulation}
    # ponytail: expire only once evaluated bars cover the deadline (M15 fallback / lagging M1 must not expire early)
    covered = previous + duration >= policy["expires_at"]
    return {**result, "status": "expired" if now >= policy["expires_at"] and covered else "pending"}
