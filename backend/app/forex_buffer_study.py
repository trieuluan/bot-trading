"""Bounded SL/TP study with chronological paired validation against default exits."""

import math
from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from statistics import mean, stdev
from types import SimpleNamespace

from .exness import ExnessError
from .exness_management import (
    exit_reason,
    protective_stop,
    size_lot,
    split_bodies,
    structure_stop_buffer,
)
from .exness_sessions import regular_session_gap, session_closed_ahead
from .exness_targets import structural_target, target_context
from .forex_exits import DEFAULTS as ADAPTIVE_EXITS
from .forex_exits import action as exits_action
from .forex_exits import closed_rows as exits_closed_rows
from .forex_exits import plan_legs
from .forex_htf_v2 import EXIT_TRAITS

DEFAULT_VARIANT = "k0.25_structural"
VARIANTS = {f"k{factor}_{mode}": {"sl_buffer_k": factor, "tp_mode": mode}
            for factor in (0.0, 0.25, 0.5, 1.0) for mode in ("structural", "1.5r", "2r", "3r")}
MIN_SAMPLES = 30


def variant_settings(name):
    if name not in VARIANTS:
        raise ExnessError("Unknown exit variant")
    return dict(VARIANTS[name])


def htf_exit_target(signal, entry, stop, tick, slip=0, commission=0, condition=None):
    verified = signal.get("shadow_only") is False and signal.get("execution_source") in ("live_forward_htf_ast3_shadow", "live_forward_htf_ast4_entry_pilot")
    if ((not signal.get("shadow_only") and not verified) or not isinstance(signal.get("strategy"), str)
            or not signal["strategy"].startswith(("hast_", "hast2_", "hast3_", "hast4_"))
            or signal.get("side") not in ("long", "short")):
        raise ExnessError("invalid_htf_shadow_signal")
    try:
        target = Decimal(str(signal["target"]))
        atr = Decimal(str(signal["htf_structure"]["atr"]))
        reason = signal["htf_structure"]["target_reason"]
        slip, commission = Decimal(str(slip)), Decimal(str(commission))
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ExnessError("invalid_htf_levels") from exc
    if (not all(value.is_finite() for value in (entry, stop, target, atr, slip, commission))
            or min(entry, stop, target, atr) <= 0 or not 0 <= slip < 1 or commission < 0):
        raise ExnessError("invalid_htf_levels")
    direction = Decimal(1) if signal["side"] == "long" else Decimal(-1)
    risk = direction * (entry - stop)
    if risk < Decimal("0.5" if signal["strategy"].startswith("hast4_") else "0.3") * atr:
        raise ExnessError("sl_too_tight")
    if risk > 3 * atr:
        raise ExnessError("sl_too_wide")
    target = target.quantize(tick, rounding=ROUND_FLOOR if direction == 1 else ROUND_CEILING)
    gain_rate = Decimal(str((condition or {}).get("quote_to_account_gain", "1")))
    loss_rate = Decimal(str((condition or {}).get("quote_to_account_loss", "1")))
    fee = commission * gain_rate
    reward = direction * (target * (1 - direction * slip) - entry) * gain_rate - fee
    loss = direction * (entry - stop * (1 - direction * slip)) * loss_rate + fee
    if reward < Decimal("1.5") * loss:
        raise ExnessError("poor_rr")
    if not isinstance(reason, str) or not reason:
        raise ExnessError("invalid_htf_levels")
    return target, reason

def exit_target(signal, entry, stop, tick):
    mode = variant_settings(signal.get("exit_variant", DEFAULT_VARIANT))["tp_mode"]
    if signal.get("htf_ast") or signal.get("htf_ast_v2"):
        return htf_exit_target(signal, entry, stop, tick)
    if signal.get("generated") and "target_r" in signal:
        direction = Decimal(1) if signal["side"] == "long" else Decimal(-1)
        risk = direction * (entry - stop)
        if risk <= 0:
            raise ExnessError("Invalid structure stop")
        value = signal["target_r"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 1.5 <= value <= 3.0:
            raise ExnessError("Invalid genome target_r")
        multiple = Decimal(str(value))
        return (entry + direction * multiple * risk).quantize(tick), f"gen_fixed_{multiple}r"
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
    return f"{signal['strategy']}:{signal['side']}:{features['trend']}"

def slot_key(signal, features):
    return f"{context_key(signal, features)}:{features['utc_session']}"

def coarse(key):
    return ":".join(key.split(":")[:3])


def new_variants(signal, minutes, hours, higher, features, candle, condition):
    if signal.get("htf_ast_v2"):
        policy = signal.get("exit_policy")
        if (not signal.get("shadow_only") or not signal["strategy"].startswith(("hast3_", "hast4_"))
                or policy not in EXIT_TRAITS):
            raise ExnessError("invalid_htf_shadow_signal")
        return [{"status": "pending", "variant": DEFAULT_VARIANT,
                 "context": context_key(signal, features), "slot": slot_key(signal, features),
                 "confirm_time": candle.time, "last_candle": candle.time,
                 "signal": {**signal, "exit_variant": DEFAULT_VARIANT,
                            "partial_be": policy != "fixed_structural"},
                 "atr": signal["htf_structure"]["atr"], "condition": condition}]
    if signal.get("htf_ast"):
        if not signal.get("shadow_only") or not signal["strategy"].startswith("hast_"):
            raise ExnessError("invalid_htf_shadow_signal")
        return [{"status": "pending", "variant": DEFAULT_VARIANT,
                 "context": context_key(signal, features), "slot": slot_key(signal, features),
                 "confirm_time": candle.time, "last_candle": candle.time,
                 "signal": {**signal, "exit_variant": DEFAULT_VARIANT, "partial_be": False},
                 "atr": signal["htf_structure"]["atr"], "condition": condition}]
    atr = structure_stop_buffer(minutes, multiplier=1)
    targets = target_context(candle, hours, higher)
    key = context_key(signal, features)
    return [{"status": "pending", "variant": name, "context": key, "slot": slot_key(signal, features),
             "confirm_time": candle.time, "last_candle": candle.time,
             "signal": {**signal, "exit_variant": name, "target_context": targets},
             "atr": atr, "condition": condition}
            for name in VARIANTS if not signal.get("generated") or (
                name == "k0.25_2r" if "target_r" in signal else name.startswith("k0.25_"))]


def closed_h1_rows(rows, now):
    """Aggregate closed M15 rows into complete H1 rows; partial hours are dropped."""
    buckets = defaultdict(list)
    for row in rows:
        buckets[row.time // 3600 * 3600].append(row)
    out = []
    for start, group in sorted(buckets.items()):
        if len(group) == 4 and start + 3600 <= now and [r.time for r in group] == [start + i * 900 for i in range(4)]:
            out.append(SimpleNamespace(time=start, open=group[0].open, close=group[-1].close,
                                       high=max(r.high for r in group), low=min(r.low for r in group)))
    return out

def initialize_variant(row, bid_open, ask_open, entry_time, slip, commission):
    signal, condition = row["signal"], row["condition"]
    direction = 1 if signal["side"] == "long" else -1
    tick = Decimal(1).scaleb(-condition["point_digits"])
    if session_closed_ahead(entry_time, 4, 900, signal.get("symbol", "XAUUSD")):
        row.update(status="skipped", reason="session_close_near")
        return
    entry = Decimal(str((ask_open if direction == 1 else bid_open) * (1 + direction * slip)))
    original = Decimal(str(signal["stop"]))
    if direction * (entry - original) <= 0:
        row.update(status="invalid", reason="next_open_outside_stop")
        return
    factor = variant_settings(row["variant"])["sl_buffer_k"]
    pad = 0 if signal.get("generated") else max(row["atr"] * factor, ask_open - bid_open)
    stop = (original - direction * Decimal(str(pad))).quantize(tick, rounding=ROUND_FLOOR if direction == 1 else ROUND_CEILING)
    risk = direction * (entry - stop)
    if stop <= 0 or (direction == 1 and stop >= Decimal(str(bid_open))) or (direction == -1 and stop <= Decimal(str(ask_open))):
        row.update(status="invalid", reason="stop_inside_spread")
        return
    try:
        target, reason = (htf_exit_target(signal, entry, stop, tick, slip, commission, condition)
                          if signal.get("htf_ast") or signal.get("htf_ast_v2")
                          else exit_target(signal, entry, stop, tick))
        volume = size_lot(risk, condition, equity=Decimal("2500"))
    except ExnessError as exc:
        row.update(status="skipped", reason=str(exc))
        return
    if volume * Decimal(str(condition["contract_size"])) * entry * Decimal(str(condition.get("quote_to_account_loss", "1"))) > 50000:
        row.update(status="skipped", reason="notional_ceiling")
        return
    body = {"side": "buy" if direction == 1 else "sell", "volume": str(volume),
            "stop_loss_price": str(stop), "take_profit_price": str(target)}
    if signal.get("htf_ast_v2") and signal.get("exit_policy") in ADAPTIVE_EXITS:
        legs = plan_legs(signal, body, condition, entry)
    else:
        legs = split_bodies(body, condition, entry, partial_be=signal.get("split", signal.get("partial_be", True)))
    for leg in legs:
        leg["weight"] = float(Decimal(leg["volume"]) / volume)
        leg["open"] = True
    row.update(status="open", entry=float(entry), entry_time=entry_time, risk=float(risk),
               initial_stop=str(stop), legs=legs, bars=0, net_r=0.0, tp_reason=reason)
    if signal.get("htf_ast_v2"):
        row.update(partial_split=len(legs) > 1, management_history=[])

def advance_variant(row, bid, ask, minutes, slip, commission, horizon=96, hours=(), duration=900):
    if row["status"] not in ("pending", "open"):
        return
    if bid.time != ask.time or bid.time != row["last_candle"] + duration:
        row.update(status="excluded", reason="session_gap" if regular_session_gap(row["last_candle"], bid.time, duration, row["signal"].get("symbol", "XAUUSD")) else "data_gap")
        return
    row["last_candle"] = bid.time
    signal, condition = row["signal"], row["condition"]
    direction = 1 if signal["side"] == "long" else -1
    exit_bar = bid if direction == 1 else ask
    if row["status"] == "pending":
        initialize_variant(row, bid.open, ask.open, bid.time, slip, commission)
        if row["status"] != "open":
            return
    row["bars"] += 1
    management_rows = minutes if len(minutes) >= 21 and all(
        after.time - before.time == 900 for before, after in zip(minutes, minutes[1:])) else []
    if signal.get("htf_ast_v2") and signal.get("management_frame") == "H1":
        management_rows = sorted((hour for hour in hours if hour.time + 3600 <= bid.time + duration),
                                 key=lambda hour: hour.time)[-60:]
        if (len(management_rows) < 21 or management_rows[-1].time + 7200 <= bid.time + duration or
                any(after.time - before.time != 3600 and not regular_session_gap(before.time, after.time, 3600, signal.get("symbol", "XAUUSD"))
                    for before, after in zip(management_rows, management_rows[1:]))):
            management_rows = []
    policy = signal.get("exit_policy")
    adaptive = signal.get("htf_ast_v2") and policy in ADAPTIVE_EXITS
    pending_management = row.pop("pending_management", None) if adaptive else None
    if pending_management and pending_management.get("stop"):
        for leg in row["legs"]:
            if leg["open"] and direction * (float(pending_management["stop"]) - float(leg["stop_loss_price"])) > 0:
                leg["stop_loss_price"] = pending_management["stop"]
    early = None if adaptive or signal.get("htf_ast") or policy == "fixed_structural" else exit_reason(
        "buy" if direction == 1 else "sell", row["entry_time"], management_rows)
    pending_exit = row.pop("pending_exit", None) if signal.get("htf_ast_v2") else None
    if signal.get("htf_ast_v2"):
        if early:
            row["pending_exit"] = early
            row["management_history"].append({"at": bid.time + duration, "action": early,
                                              "effective_at": bid.time + duration, "frame": signal["management_frame"]})
        early = None
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
        elif pending_exit:
            price, outcome = exit_bar.open, pending_exit
        elif pending_management and leg["role"] in pending_management["close_roles"]:
            price, outcome = exit_bar.open, pending_management["reason"]
        elif stop_hit:
            price, outcome = stop, "stop"
        elif target_hit:
            price, outcome = target, "target"
        elif early or session_closed_ahead(bid.time, 2, 900, signal.get("symbol", "XAUUSD")):
            price, outcome = exit_bar.close, early or "session_close"
        if outcome:
            price *= 1 - direction * slip
            gross = direction * (price - row["entry"])
            gain_rate = float(condition.get("quote_to_account_gain", 1))
            loss_rate = float(condition.get("quote_to_account_loss", 1))
            net = gross * (gain_rate if gross >= 0 else loss_rate) - commission * gain_rate
            row["net_r"] += leg["weight"] * net / (row["risk"] * loss_rate)
            leg.update(open=False, outcome=outcome)
            if signal.get("htf_ast_v2"):
                row["management_history"].append({"at": bid.time + duration, "action": outcome,
                                                  "role": leg["role"], "price": price})
    if not any(leg["open"] for leg in row["legs"]):
        row.update(status="resolved", resolved_at=bid.time + duration)
        return
    if row["bars"] >= horizon:
        row.update(status="excluded", reason="unresolved_horizon")
        return
    if adaptive:
        ex_rows = exits_closed_rows(signal, minutes, hours, bid.time + duration)
        first_open = next((leg for leg in row["legs"] if leg["open"]), None)
        if first_open and ex_rows and ex_rows[-1].time > row.get("managed_candle", -1):
            act = exits_action(signal, ex_rows, row["entry"], row["risk"], row["entry_time"],
                               float(first_open["stop_loss_price"]), condition,
                               bid.close if direction == 1 else ask.close, slip, commission)
            if act:
                row["managed_candle"] = ex_rows[-1].time
                row["pending_management"] = act
                row["management_history"].append({**act, "effective_at": bid.time + duration,
                                                  "frame": signal["management_frame"]})
        return
    if signal.get("partial_be") is False:
        return
    partial_done = any(leg["role"] == "tp1" and leg.get("outcome") == "target" for leg in row["legs"])
    for leg in row["legs"]:
        if leg["open"]:
            action = protective_stop({"open_price": str(row["entry"]), "direction": "buy" if direction == 1 else "sell"},
                                     row["initial_stop"], {"bid": bid.close, "ask": ask.close}, leg,
                                     condition, management_rows, partial_done)
            if action:
                leg["stop_loss_price"] = action["stop"]
                if signal.get("htf_ast_v2"):
                    row["management_history"].append({"at": bid.time + duration, "effective_at": bid.time + duration,
                                                      "action": action["stage"], "stop": action["stop"],
                                                      "role": leg["role"], "frame": signal["management_frame"]})


def lower_bound(values):
    if len(values) < 2:
        return -1.0
    return mean(values) - 3.3 * stdev(values) / math.sqrt(len(values))


def build_buffer_model(records, cutoff, now, costs_verified=False):
    groups = defaultdict(lambda: defaultdict(dict))
    for row in records:
        if row["status"] == "resolved" and row["resolved_at"] < now:
            groups[coarse(row["context"])][row["variant"]][row["confirm_time"]] = row
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
    if signal.get("generated") and "target_r" in signal:
        return {"exit_variant": "k0.25_2r", "sl_buffer_k": 0.0,
                "tp_mode": "genome", "exit_variant_mode": "genome"}
    context = model.get(context_key(signal, features), {})
    name = context.get("selected", DEFAULT_VARIANT) if context.get("eligible") is True else DEFAULT_VARIANT
    settings = variant_settings(name)
    return {"exit_variant": name, **settings, "exit_variant_mode": "validated" if name != DEFAULT_VARIANT else "default"}
