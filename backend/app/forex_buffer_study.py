"""Bounded SL/TP study with chronological paired validation against default exits."""

import math
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from statistics import mean, stdev

from .exness import ExnessError
from .exness_management import exit_reason, protective_stop, size_lot, split_bodies, structure_stop_buffer
from .exness_sessions import regular_session_gap, session_closed_ahead
from .exness_targets import structural_target, target_context

DEFAULT_VARIANT = "k0.25_structural"
VARIANTS = {f"k{factor}_{mode}": {"sl_buffer_k": factor, "tp_mode": mode}
            for factor in (0.0, 0.25, 0.5, 1.0) for mode in ("structural", "1.5r", "2r", "3r")}
MIN_SAMPLES = 30


def variant_settings(name):
    if name not in VARIANTS:
        raise ExnessError("Unknown exit variant")
    return dict(VARIANTS[name])


def exit_target(signal, entry, stop, tick):
    mode = variant_settings(signal.get("exit_variant", DEFAULT_VARIANT))["tp_mode"]
    if mode == "structural":
        if signal.get("target_context") is None:
            raise ExnessError("Missing closed-candle TP context")
        return structural_target(signal["side"], entry, stop, signal["target_context"], tick)
    direction = Decimal(1) if signal["side"] == "long" else Decimal(-1)
    risk = direction * (entry - stop)
    if risk <= 0:
        raise ExnessError("Invalid structure stop")
    return (entry + direction * Decimal(mode[:-1]) * risk).quantize(tick), f"fixed_{mode}"


def context_key(signal, features):
    return f"{signal['strategy']}:{signal['side']}:{features['trend']}:{features['utc_session']}"


def new_variants(signal, minutes, hours, higher, features, candle, condition):
    atr = structure_stop_buffer(minutes, multiplier=1)
    targets = target_context(candle, hours, higher)
    key = context_key(signal, features)
    return [{"status": "pending", "variant": name, "context": key,
             "confirm_time": candle.time, "last_candle": candle.time,
             "signal": {**signal, "exit_variant": name, "target_context": targets},
             "atr": atr, "condition": condition}
            for name in VARIANTS if not signal.get("generated") or name.startswith("k0.25_")]


def advance_variant(row, bid, ask, minutes, slip, commission, horizon=96):
    if row["status"] not in ("pending", "open"):
        return
    if bid.time != ask.time or bid.time != row["last_candle"] + 900:
        row.update(status="excluded", reason="session_gap" if regular_session_gap(row["last_candle"], bid.time) else "data_gap")
        return
    row["last_candle"] = bid.time
    signal, condition = row["signal"], row["condition"]
    direction = 1 if signal["side"] == "long" else -1
    tick = Decimal(1).scaleb(-condition["point_digits"])
    exit_bar = bid if direction == 1 else ask
    if row["status"] == "pending":
        if session_closed_ahead(bid.time, 4):
            row.update(status="skipped", reason="session_close_near")
            return
        entry = Decimal(str((ask.open if direction == 1 else bid.open) * (1 + direction * slip)))
        original = Decimal(str(signal["stop"]))
        if direction * (entry - original) <= 0:
            row.update(status="invalid", reason="next_open_outside_stop")
            return
        factor = variant_settings(row["variant"])["sl_buffer_k"]
        pad = 0 if signal.get("generated") else max(row["atr"] * factor, ask.open - bid.open)
        stop = (original - direction * Decimal(str(pad))).quantize(tick, rounding=ROUND_FLOOR if direction == 1 else ROUND_CEILING)
        risk = direction * (entry - stop)
        if stop <= 0 or (direction == 1 and stop >= Decimal(str(bid.open))) or (direction == -1 and stop <= Decimal(str(ask.open))):
            row.update(status="invalid", reason="stop_inside_spread")
            return
        try:
            target, reason = exit_target(signal, entry, stop, tick)
            volume = size_lot(risk, condition)
        except ExnessError as exc:
            row.update(status="skipped", reason=str(exc))
            return
        if volume * Decimal(str(condition["contract_size"])) * entry > 50000:
            row.update(status="skipped", reason="notional_ceiling")
            return
        body = {"side": "buy" if direction == 1 else "sell", "volume": str(volume),
                "stop_loss_price": str(stop), "take_profit_price": str(target)}
        legs = split_bodies(body, condition, entry)
        for leg in legs:
            leg["weight"] = float(Decimal(leg["volume"]) / volume)
            leg["open"] = True
        row.update(status="open", entry=float(entry), entry_time=bid.time, risk=float(risk),
                   initial_stop=str(stop), legs=legs, bars=0, net_r=0.0, tp_reason=reason)
    row["bars"] += 1
    management_rows = minutes if len(minutes) >= 21 else []
    early = exit_reason("buy" if direction == 1 else "sell", row["entry_time"], management_rows)
    for leg in row["legs"]:
        if not leg["open"]:
            continue
        stop, target = float(leg["stop_loss_price"]), float(leg["take_profit_price"])
        stop_hit = exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop
        target_hit = exit_bar.high >= target if direction == 1 else exit_bar.low <= target
        outcome = None
        if direction * (exit_bar.open - stop) <= 0:
            price, outcome = exit_bar.open, "stop_gap"
        elif direction * (exit_bar.open - target) >= 0:
            price, outcome = target, "target"
        elif stop_hit:
            price, outcome = stop, "stop"
        elif target_hit:
            price, outcome = target, "target"
        elif early or session_closed_ahead(bid.time, 2):
            price, outcome = exit_bar.close, early or "session_close"
        if outcome:
            price *= 1 - direction * slip
            row["net_r"] += leg["weight"] * (direction * (price - row["entry"]) - commission) / row["risk"]
            leg.update(open=False, outcome=outcome)
    if not any(leg["open"] for leg in row["legs"]):
        row.update(status="resolved", resolved_at=bid.time + 900)
        return
    if row["bars"] >= horizon:
        row.update(status="excluded", reason="unresolved_horizon")
        return
    partial_done = any(leg["role"] == "tp1" and leg.get("outcome") == "target" for leg in row["legs"])
    for leg in row["legs"]:
        if leg["open"]:
            action = protective_stop({"open_price": str(row["entry"]), "direction": "buy" if direction == 1 else "sell"},
                                     row["initial_stop"], {"bid": bid.close, "ask": ask.close}, leg,
                                     condition, management_rows, partial_done)
            if action:
                leg["stop_loss_price"] = action["stop"]


def lower_bound(values):
    if len(values) < 2:
        return -1.0
    return mean(values) - 3.3 * stdev(values) / math.sqrt(len(values))


def build_buffer_model(records, cutoff, now, costs_verified=False):
    groups = defaultdict(lambda: defaultdict(dict))
    for row in records:
        if row["status"] == "resolved" and row["resolved_at"] < now:
            groups[row["context"]][row["variant"]][row["confirm_time"]] = row
    model = {}
    for key, variants in groups.items():
        baseline = variants.get(DEFAULT_VARIANT, {})
        scores = []
        for name, observations in variants.items():
            paired = [(row, baseline[stamp]) for stamp, row in observations.items() if stamp in baseline]
            train = [(row, base) for row, base in paired if max(row["resolved_at"], base["resolved_at"]) < cutoff]
            validation = [(row, base) for row, base in paired if row["confirm_time"] >= cutoff]
            returns = [row["net_r"] for row, _ in train]
            scores.append({"variant": name, "train_count": len(train), "validation_count": len(validation),
                           "train_mean_r": mean(returns) if returns else 0,
                           "train_lower_r": lower_bound(returns),
                           "train_edge_lower_r": lower_bound([row["net_r"] - base["net_r"] for row, base in train]),
                           "validation_lower_r": lower_bound([row["net_r"] for row, _ in validation]),
                           "validation_edge_lower_r": lower_bound([row["net_r"] - base["net_r"] for row, base in validation])})
        candidates = [score for score in scores if score["variant"] != DEFAULT_VARIANT and score["train_count"] >= MIN_SAMPLES
                      and score["train_lower_r"] > 0 and score["train_edge_lower_r"] > 0]
        best = max(candidates, key=lambda score: score["train_lower_r"], default=None)
        eligible = bool(costs_verified and best and best["validation_count"] >= MIN_SAMPLES
                        and best["validation_lower_r"] > 0 and best["validation_edge_lower_r"] > 0)
        model[key] = {"selected": best["variant"] if eligible else DEFAULT_VARIANT,
                      "recommended": best["variant"] if best else DEFAULT_VARIANT,
                      "eligible": eligible, "scores": scores}
    return model


def select_buffer(signal, features, model):
    context = model.get(context_key(signal, features), {})
    name = context.get("selected", DEFAULT_VARIANT) if context.get("eligible") is True else DEFAULT_VARIANT
    settings = variant_settings(name)
    return {"exit_variant": name, **settings, "exit_variant_mode": "validated" if name != DEFAULT_VARIANT else "default"}
