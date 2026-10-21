"""Read-only chart overlays mirroring indicators the V3 trees already use; closed bars only."""

import math
from statistics import mean

from .exness_sessions import _closed_bucket
from .forex_detectors import extra_setup, family_context
from .forex_features import FILTER_DEFAULTS, adx14, daily_ranges, valid_rows
from .forex_htf_v2 import _closed_rows, round_step
from .forex_indicators import ema


def _ema_series(rows, period):
    if len(rows) < period:
        return []
    value = rows[0].close
    points = []
    for index, row in enumerate(rows):
        value += 2 / (period + 1) * (row.close - value)
        if index >= period - 1:
            points.append((row, value))
    return points


def _vwap(minutes, lookback=30, mult=2.5, symbol="XAUUSD"):
    """Same window, tick-volume weighting and deviation as the `vwap` trigger; invalid volume -> no point."""
    out = []
    for end in range(lookback, len(minutes) + 1):
        window = minutes[end - lookback:end]
        if not valid_rows(window, 900, symbol):
            continue
        volumes = [getattr(row, "tick_volume", None) for row in window]
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in volumes):
            continue
        prices = [(row.high + row.low + row.close) / 3 for row in window]
        total = sum(volumes)
        center = sum(p * v for p, v in zip(prices, volumes)) / total
        dev = math.sqrt(sum(v * (p - center) ** 2 for p, v in zip(prices, volumes)) / total)
        out.append({"time": window[-1].time + 900, "center": center, "upper": center + mult * dev,
                    "lower": center - mult * dev})
    return out[-120:]


def _pivots(hours):
    rows = hours[-60:]
    lows, highs = [], []
    for index in range(2, len(rows) - 2):
        around = rows[index - 2:index] + rows[index + 1:index + 3]
        if all(rows[index].low < other.low for other in around):
            lows.append({"time": rows[index].time, "price": rows[index].low})
        if all(rows[index].high > other.high for other in around):
            highs.append({"time": rows[index].time, "price": rows[index].high})
    return {"low": lows[-3:], "high": highs[-3:]}


def chart_overlays(candle, minutes, hours, higher, symbol):
    """Every block is None when its inputs are missing/stale; nothing is filled in."""
    as_of = candle.time + 900
    out = {"h4_trend": None, "day": None, "asia": None, "round": None, "price_zone": None,
           "adr": None, "vwap": None, "pivots": None}
    h4 = _closed_rows(candle, higher, 14400, 50, symbol)
    if h4:
        out["h4_trend"] = {"ema50": [{"time": row.time + 14400, "value": value}
                                     for row, value in _ema_series(h4, 50)[-60:]],
                           "adx": adx14(h4), "adx_threshold": FILTER_DEFAULTS["adx_regime"]["threshold"]}
        zone = h4[-FILTER_DEFAULTS["price_zone"]["lookback"]:]
        out["price_zone"] = {"high": max(r.high for r in zone), "low": min(r.low for r in zone),
                             "midpoint": (max(r.high for r in zone) + min(r.low for r in zone)) / 2,
                             "lookback": len(zone)}
    closed_h1 = [row for row in hours if row.time + 3600 <= as_of]
    today = as_of // 86400 * 86400
    days, reason = daily_ranges(closed_h1, as_of, symbol, 1)
    if not reason:
        out["day"] = {"high": days[0]["high"], "low": days[0]["low"], "day": days[0]["day"], "from": today}
    asian = [row for row in closed_h1 if today <= row.time < today + 6 * 3600]
    if [row.time for row in asian] == list(range(today, today + 6 * 3600, 3600)):
        out["asia"] = {"high": max(r.high for r in asian), "low": min(r.low for r in asian),
                       "from": today + 6 * 3600, "to": today + 10 * 3600}
    step = round_step(symbol)
    last = candle.close
    below = math.floor(last / step) * step
    out["round"] = {"step": step, "levels": [round(v, 6) for v in (below - step, below, below + step, below + 2 * step) if v > 0]}
    spec = FILTER_DEFAULTS["adr_headroom"]
    history, reason = daily_ranges(closed_h1, as_of, symbol, spec["days"])
    closed_m15 = _closed_rows(candle, minutes, 900, 30, symbol)
    session = [row for row in closed_m15 if today <= row.time]
    expected = [stamp for stamp in range(today, as_of, 900) if not _closed_bucket(stamp, 900, symbol)]
    if not reason and session and [row.time for row in session] == expected:
        adr = mean(day["high"] - day["low"] for day in history)
        high, low = max(r.high for r in session), min(r.low for r in session)
        if adr > 0:
            out["adr"] = {"adr": adr, "threshold": spec["threshold"], "used": (high - low) / adr,
                          "day_high": high, "day_low": low, "from": today,
                          "reach_up": low + spec["threshold"] * adr, "reach_down": high - spec["threshold"] * adr}
    out["vwap"] = _vwap(closed_m15[-160:], symbol=symbol) or None
    safe_h1 = _closed_rows(candle, hours, 3600, 5, symbol)
    pivots = _pivots(safe_h1) if safe_h1 and valid_rows(safe_h1[-60:], 3600, symbol) else {"low": [], "high": []}
    out["pivots"] = pivots if pivots["low"] or pivots["high"] else None
    return out

def tree_overlays(tree, candle, minutes, hours, higher, symbol, shared, cache):
    """Owned price lines with the evaluator's timeframe, parameters and EMA windows."""
    children = tree["root"]["children"]
    setup, trigger, exit_node = children[1:4]
    filters = {node["name"]: node.get("params", {}) for node in children[4:]}
    as_of = candle.time + 900
    lines, notes = [], []
    closed_h1 = _closed_rows(candle, hours, 3600, 50, symbol)
    closed_m15 = _closed_rows(candle, minutes, 900, 30, symbol)

    def add(label, points, color, dashed=False):
        if points:
            lines.append({"label": label, "points": points, "color": color, "dashed": dashed})
        else:
            notes.append(f"{label}: chờ đủ nến đã đóng, liên tục và còn mới")

    def flat(label, value, start, end=as_of, color="#d4a72c"):
        start = min(start, end - 900)
        add(label, [{"time": start, "value": value}, {"time": end, "value": value}], color, True)

    def ema_line(rows, period, duration, window, label, color):
        key = ("ema", duration, period, window)
        if key not in cache:
            cache[key] = [{"time": rows[end - 1].time + duration,
                           "value": ema([row.close for row in rows[max(0, end - window):end]], period)}
                          for end in range(50 if duration == 3600 else 20, len(rows) + 1)][-120:]
        add(label, cache[key], color)

    trend = shared.get("h4_trend")
    add("EMA50 H4 · Xu hướng", trend["ema50"] if trend else [], "#b69be2")
    if "adx_regime" in filters:
        threshold = filters["adx_regime"].get("threshold", 25)
        value = trend.get("adx") if trend else None
        notes.append(f"ADX14 H4 {value:.1f} (>{threshold})" if value is not None else "ADX14 H4: chờ dữ liệu")

    setup_params, trigger_params = setup["params"], trigger["params"]
    family = trigger["family"]
    context = family_context(family, trigger_params, candle, closed_h1, higher) if closed_h1 else None
    periods = set()
    if setup["family"] == "pullback":
        periods.update((setup_params.get("entry_ema", 20), setup_params.get("fast_ema", 20), 50))
    if family in ("pullback", "router") or trigger_params.get("trend"):
        periods.update((trigger_params.get("fast_ema", 20), 50))
    for period in sorted(periods):
        ema_line(closed_h1, period, 3600, 60, f"EMA{period} H1", "#e0a458" if period != 50 else "#8fafa1")
    if family == "pullback" or family == "router" and context and context.get("router_mode") == "trend":
        history = [row for row in closed_m15 if row.time < candle.time]
        period = trigger_params.get("entry_ema", 20)
        ema_line(history, period, 900, 60, f"EMA{period} M15", "#55b9d0")
    if family == "router":
        notes.append(f"Router M15: {context['router_mode']}" if context else "Router M15: chờ bối cảnh H1")
    if family in ("range", "router") and context and (family == "range" or context["router_mode"] == "range"):
        band = context["range"]
        for key, title in (("high", "trên"), ("low", "dưới")):
            flat(f"Range H1 {title} ({trigger_params.get('lookback', 20)})", band[key], candle.time)
    if family == "retest" and context and context.get("retest"):
        retest = context["retest"]
        flat("Breakout H1", retest["level"], retest["available"])

    if family in ("channel", "sweep", "squeeze"):
        count = trigger_params.get("bars", trigger_params["lookback"]) if family == "squeeze" else trigger_params["lookback"]
        history = [row for row in closed_m15 if row.time < candle.time][-count:]
        if len(history) == count:
            family_name = {"channel": "Kênh", "sweep": "Quét", "squeeze": "Nén"}.get(family, family.upper())
            for key, title in (("high", "trên"), ("low", "dưới")):
                value = max(row.high for row in history) if key == "high" else min(row.low for row in history)
                flat(f"{family_name} M15 {title} ({count})", value, candle.time)
    if family in ("orb", "vsa", "mss"):
        history = [row for row in closed_m15 if row.time < candle.time][-96:]
        _, levels = extra_setup({"family": family, **trigger_params}, candle, history)
        for title, price, start in levels:
            flat(f"{title} M15", price, max(start, candle.time))
        if family == "vsa":
            notes.append("VSA: tick volume, không phải volume thực")
    if family == "vwap":
        count, mult = trigger_params["lookback"], trigger_params["dev_mult"]
        key = ("vwap", count, mult)
        if key not in cache:
            cache[key] = _vwap([row for row in closed_m15 if row.time < candle.time], count, mult, symbol)
        for field, title in (("center", "VWAP"), ("upper", f"VWAP +{mult}σ"), ("lower", f"VWAP −{mult}σ")):
            add(f"{title} M15 ({count}, tick volume)",
                [{"time": row["time"], "value": row[field]} for row in cache[key]], "#4fa3e0", field != "center")

    if setup["family"] == "pdh_pdl_sweep":
        band = shared.get("day")
        if band:
            flat("PDH · ngày UTC trước", band["high"], band["from"])
            flat("PDL · ngày UTC trước", band["low"], band["from"])
        else:
            notes.append("PDH/PDL: chờ ngày UTC hoàn chỉnh")
    elif setup["family"] == "asia_sweep":
        band = shared.get("asia")
        if band:
            flat("Á cao (00-06h UTC)", band["high"], band["from"])
            flat("Á thấp (00-06h UTC)", band["low"], band["from"])
        else:
            notes.append("Phiên Á: chờ đủ nến 00-06h UTC")
    if setup["family"] == "round_level":
        step = setup_params["step"]
        below = math.floor(candle.close / step) * step
        for price in (below, below + step):
            flat(f"Mốc tròn H1 · bước {step}", price, candle.time)
    if "price_zone" in filters:
        count = filters["price_zone"].get("lookback", 20)
        rows = _closed_rows(candle, higher, 14400, max(50, count), symbol)
        if rows:
            high, low = max(row.high for row in rows[-count:]), min(row.low for row in rows[-count:])
            for title, value in (("cao", high), ("thấp", low), ("trung điểm", (high + low) / 2)):
                flat(f"Vùng H4 {title} ({count})", value, as_of)
        else:
            notes.append("Vùng giá H4: chờ dữ liệu")
    if "adr_headroom" in filters:
        params = {**FILTER_DEFAULTS["adr_headroom"], **filters["adr_headroom"]}
        days, reason = daily_ranges(closed_h1, as_of, symbol, params["days"])
        session = [row for row in closed_m15 if row.time >= as_of // 86400 * 86400]
        expected = [stamp for stamp in range(as_of // 86400 * 86400, as_of, 900) if not _closed_bucket(stamp, 900, symbol)]
        if not reason and session and [row.time for row in session] == expected:
            budget = mean(day["high"] - day["low"] for day in days) * params["threshold"]
            for title, price in (("trên", min(row.low for row in session) + budget), ("dưới", max(row.high for row in session) - budget)):
                flat(f"ADR{params['days']} {params['threshold']:.0%} {title} · không phải cản", price, as_of)
        else:
            notes.append(f"ADR{params['days']}: chờ đủ dữ liệu")
    if exit_node["policy"] == "trail_swing_h1":
        for field, title in (("low", "đáy"), ("high", "đỉnh")):
            for pivot in (shared.get("pivots") or {}).get(field, []):
                flat(f"Pivot {title} H1 · neo trailing, chưa phải SL", pivot["price"], pivot["time"] + 3 * 3600)
    return {"lines": lines, "notes": notes}
