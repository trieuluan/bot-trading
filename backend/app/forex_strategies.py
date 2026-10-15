"""Experimental closed-candle signals shared by replay and forward paper."""
from datetime import datetime, timezone

STRATEGY_CHANNELS = {"trend": "trend_pullback", "range": "range_rejection", "router": "regime_router"}

def ema(values, period):
    out = values[0]
    for value in values[1:]:
        out += 2 / (period + 1) * (value - out)
    return out

def _closed_hours(candle, hours):
    available = candle.time + 900
    return [row for row in hours if row.time + 3600 <= available][-60:]

def trend_pullback(candle, hours, minutes):
    """H1 EMA20/50 trend; M15 touches its own EMA20 and closes with trend."""
    hours = _closed_hours(candle, hours)
    minutes = [row for row in minutes if row.time < candle.time][-60:]
    if len(hours) < 50 or len(minutes) < 20 or minutes[-1].time != candle.time - 900:
        return None
    closes = [row.close for row in hours]
    fast, slow = ema(closes, 20), ema(closes, 50)
    entry_ema = ema([row.close for row in minutes], 20)
    if fast > slow and closes[-1] > fast and candle.low <= entry_ema < candle.close and candle.close > candle.open:
        return {"side": "long", "stop": min(candle.low, minutes[-1].low), "reason": "H1 tăng, M15 chạm EMA20 rồi đóng xanh"}
    if fast < slow and closes[-1] < fast and candle.high >= entry_ema > candle.close and candle.close < candle.open:
        return {"side": "short", "stop": max(candle.high, minutes[-1].high), "reason": "H1 giảm, M15 chạm EMA20 rồi đóng đỏ"}
    return None

def range_rejection(candle, hours, minutes):
    """H1 sideway (20 bars); M15 wicks into range edge and closes back inside."""
    hours = [row for row in hours if row.time + 3600 <= candle.time][-20:]
    if len(hours) < 20:
        return None
    high, low = max(row.high for row in hours), min(row.low for row in hours)
    width = high - low
    if width <= 0 or abs(hours[-1].close - hours[0].close) > 0.5 * width:
        return None
    edge = 0.1 * width
    if candle.high >= high - edge and candle.close < high - edge and candle.close < candle.open:
        return {"side": "short", "stop": candle.high, "reason": "H1 sideway, M15 bị từ chối ở biên trên"}
    if candle.low <= low + edge and candle.close > low + edge and candle.close > candle.open:
        return {"side": "long", "stop": candle.low, "reason": "H1 sideway, M15 bật lên từ biên dưới"}
    return None

def donchian_breakout(candle, hours, minutes):
    history = [row for row in minutes if row.time < candle.time][-20:]
    closed = _closed_hours(candle, hours)
    if len(history) < 20 or len(closed) < 50:
        return None
    fast = ema([row.close for row in closed], 20)
    slow = ema([row.close for row in closed], 50)
    if candle.close > max(row.high for row in history) and fast > slow:
        return {"side": "long", "stop": min(row.low for row in history[-3:]), "reason": "M15 Donchian20 breakout; H1 EMA20 > EMA50"}
    if candle.close < min(row.low for row in history) and fast < slow:
        return {"side": "short", "stop": max(row.high for row in history[-3:]), "reason": "M15 Donchian20 breakdown; H1 EMA20 < EMA50"}
    return None

def liquidity_sweep(candle, hours, minutes):
    history = [row for row in minutes if row.time < candle.time][-20:]
    if len(history) < 20:
        return None
    high, low = max(row.high for row in history), min(row.low for row in history)
    if candle.low < low < candle.close and candle.close > candle.open and candle.high <= high:
        return {"side": "long", "stop": candle.low, "reason": "M15 sweeps prior 20-bar low and reclaims"}
    if candle.high > high > candle.close and candle.close < candle.open and candle.low >= low:
        return {"side": "short", "stop": candle.high, "reason": "M15 sweeps prior 20-bar high and rejects"}
    return None

def squeeze_breakout(candle, hours, minutes):
    history = [row for row in minutes if row.time < candle.time][-40:]
    if len(history) < 40:
        return None
    recent, prior = history[-10:], history[:-10]
    width = max(row.high for row in recent) - min(row.low for row in recent)
    typical = sum(row.high - row.low for row in prior) / len(prior)
    if typical <= 0 or width > 3 * typical:
        return None
    if candle.close > max(row.high for row in recent):
        return {"side": "long", "stop": min(row.low for row in recent), "reason": "M15 10-bar compression breaks upward"}
    if candle.close < min(row.low for row in recent):
        return {"side": "short", "stop": max(row.high for row in recent), "reason": "M15 10-bar compression breaks downward"}
    return None

def session_breakout(candle, hours, minutes):
    """M15 closes beyond the Asian range (00-07 UTC) during 07-12 UTC; first break of the day only."""
    dt = datetime.fromtimestamp(candle.time, timezone.utc)
    if not 7 <= dt.hour < 12:
        return None
    start = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
    asian = [row for row in minutes if start <= row.time < start + 25200]
    if len(asian) < 24:
        return None
    high, low = max(row.high for row in asian), min(row.low for row in asian)
    if any(row.close > high or row.close < low for row in minutes if start + 25200 <= row.time < candle.time):
        return None
    if candle.close > high and candle.close > candle.open:
        return {"side": "long", "stop": low, "reason": "M15 phá đỉnh phiên Á trong phiên London"}
    if candle.close < low and candle.close < candle.open:
        return {"side": "short", "stop": high, "reason": "M15 phá đáy phiên Á trong phiên London"}
    return None

def fvg_retest(candle, hours, minutes):
    """Bullish/bearish 3-bar M15 gap; candle dips into the gap and closes back with the impulse."""
    history = [row for row in minutes if row.time < candle.time][-3:]
    if len(history) < 3 or history[-1].time != candle.time - 900:
        return None
    first, middle, last = history
    if last.low > first.high and middle.close > middle.open and candle.low <= last.low and candle.close > last.low and candle.close > candle.open:
        return {"side": "long", "stop": min(candle.low, first.high), "reason": "M15 hồi về lấp FVG tăng rồi đóng xanh"}
    if last.high < first.low and middle.close < middle.open and candle.high >= last.high and candle.close < last.high and candle.close < candle.open:
        return {"side": "short", "stop": max(candle.high, first.low), "reason": "M15 hồi về lấp FVG giảm rồi đóng đỏ"}
    return None

def rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in deltas[:period]) / period
    loss = sum(max(-d, 0) for d in deltas[:period]) / period
    for d in deltas[period:]:
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

def rsi_reversion(candle, hours, minutes):
    """Prior-bar RSI14 extreme (<30 / >70) followed by a rejecting candle."""
    history = [row for row in minutes if row.time < candle.time][-60:]
    if len(history) < 30 or history[-1].time != candle.time - 900:
        return None
    value, prev = rsi([row.close for row in history]), history[-1]
    if value < 30 and candle.close > candle.open and candle.close > prev.close:
        return {"side": "long", "stop": min(candle.low, prev.low), "reason": f"M15 RSI {value:.0f} quá bán, nến đóng xanh"}
    if value > 70 and candle.close < candle.open and candle.close < prev.close:
        return {"side": "short", "stop": max(candle.high, prev.high), "reason": f"M15 RSI {value:.0f} quá mua, nến đóng đỏ"}
    return None

RULES = {"trend_pullback": trend_pullback, "range_rejection": range_rejection,
         "donchian_breakout": donchian_breakout, "liquidity_sweep": liquidity_sweep,
         "squeeze_breakout": squeeze_breakout, "session_breakout": session_breakout,
         "fvg_retest": fvg_retest, "rsi_reversion": rsi_reversion}
STRATEGIES = ("breakout_retest", *RULES, "regime_router")


def router_snapshot(candle, hours, minutes, higher=(), breakout=None):
    """Fixed experimental rules; all inputs filtered at the decision's availability time."""
    available = candle.time + 900
    hours = _closed_hours(candle, hours)
    minutes = [row for row in minutes if row.time < candle.time][-60:]
    higher = [row for row in higher if row.time + 14400 <= available][-60:]
    snapshot = {
        "strategy": "regime_router", "candle_time": candle.time, "available_at": available,
        "price_type": "bid", "status": "warming_up", "regime": "unknown",
        "reason": "Chờ 50 nến H4/H1 và 20 nến M15 đã đóng",
        "parameters": {"version": 1, "target_r": 2, "ema_h4": 50, "ema_h1": [20, 50],
                       "trend_separation_atr": 0.5, "range_efficiency_max": 0.3},
    }
    if len(hours) < 50 or len(higher) < 50 or len(minutes) < 20:
        return snapshot
    if (minutes[-1].time != candle.time - 900 or hours[-1].time + 7200 <= available
            or higher[-1].time + 28800 <= available):
        return {**snapshot, "status": "data_paused", "reason": "Khung dữ liệu chưa đồng bộ; không vào lệnh"}
    closes = [row.close for row in hours]
    fast, slow = ema(closes, 20), ema(closes, 50)
    ema_h4 = ema([row.close for row in higher], 50)
    direction_h4 = 1 if higher[-1].close > ema_h4 else -1 if higher[-1].close < ema_h4 else 0
    direction_h1 = 1 if fast > slow and closes[-1] > fast else -1 if fast < slow and closes[-1] < fast else 0
    atr = sum(max(hours[index].high - hours[index].low,
                  abs(hours[index].high - hours[index - 1].close),
                  abs(hours[index].low - hours[index - 1].close)) for index in range(-14, 0)) / 14
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(-19, 0))
    efficiency = abs(closes[-1] - closes[-20]) / path if path else 0
    separation = abs(fast - slow) / atr if atr > 0 else 0
    trending = direction_h4 == direction_h1 != 0 and separation >= 0.5
    sideways = atr > 0 and separation < 0.5 and efficiency <= 0.3
    snapshot.update(status="waiting_regime_setup", regime="trend" if trending else "range" if sideways else "unclear",
                    trend="up" if direction_h4 == 1 else "down" if direction_h4 == -1 else "flat",
                    ema_4h=ema_h4, closed_4h_time=higher[-1].time,
                    ema_h1_fast=fast, ema_h1_slow=slow, atr_h1=atr,
                    separation_atr=separation, efficiency_h1=efficiency,
                    reason="Trạng thái chưa rõ; không vào lệnh")
    if (not breakout or breakout.get("candle_time") != candle.time
            or breakout.get("available_at", available) > available):
        return {**snapshot, "status": "data_paused", "reason": "Chờ quan sát breakout đúng nến, không dùng tín hiệu cũ/tương lai"}
    components = [strategy_snapshot(name, candle, hours, minutes) for name in ("trend_pullback", "range_rejection")]
    candidates = [row for row in [breakout, *components] if row.get("status") == "confirmed"]
    snapshot["component_signals"] = [{"strategy": "breakout_retest" if row is breakout else row["strategy"],
                                      "side": row["side"]} for row in candidates]
    if len({row["side"] for row in candidates}) > 1:
        return {**snapshot, "status": "conflicting_signals", "reason": "Các chiến lược báo ngược chiều; không vào lệnh"}
    same_direction = breakout.get("side") == ("long" if direction_h4 == 1 else "short") and direction_h1 == direction_h4 != 0
    if breakout.get("status") == "confirmed" and same_direction:
        selected, name = breakout, "breakout_retest"
        snapshot["regime"] = "breakout"
    elif trending:
        selected, name = components[0], "trend_pullback"
    elif sideways and breakout.get("status") not in ("waiting_retest", "waiting_confirm", "confirmed"):
        selected, name = components[1], "range_rejection"
    else:
        return snapshot
    snapshot.update(selected_strategy=name, reason=f"Chọn {name}: {selected['reason']}")
    for key in ("range_high", "range_low", "range_start", "range_end", "level", "ema_m15"):
        if key in selected:
            snapshot[key] = selected[key]
    if selected.get("status") != "confirmed":
        return snapshot
    direction = 1 if selected["side"] == "long" else -1
    risk = direction * (candle.close - selected["stop"])
    if risk <= 0:
        return {**snapshot, "status": "invalid", "reason": "SL không đúng phía giá xác nhận"}
    return {**snapshot, "status": "confirmed", "side": selected["side"], "stop": selected["stop"],
            "reference_entry": candle.close, "target": candle.close + direction * 2 * risk,
            "confirm_time": candle.time, "confluence": len(candidates)}


def strategy_snapshot(strategy, candle, hours, minutes, *, higher=(), breakout=None):
    if strategy == "regime_router":
        return router_snapshot(candle, hours, minutes, higher, breakout)
    if strategy not in RULES:
        raise ValueError("Unknown forex strategy")
    hours = _closed_hours(candle, hours)
    minutes = [row for row in minutes if row.time < candle.time][-60:]
    snapshot = {
        "strategy": strategy, "candle_time": candle.time, "available_at": candle.time + 900,
        "status": "warming_up", "reason": "Chờ đủ nến H1/M15 đã đóng", "price_type": "bid",
        "parameters": {"target_r": 2, "ema_1h": [20, 50], "ema_m15": 20}
        if strategy == "trend_pullback" else {"target_r": 2, "lookback_1h": 20, "edge_fraction": 0.1},
    }
    needed = 20 if strategy == "range_rejection" else 50
    if len(hours) < needed or len(minutes) < (40 if strategy == "squeeze_breakout" else 1 if strategy == "range_rejection" else 20):
        return snapshot
    if minutes[-1].time != candle.time - 900 or hours[-1].time + 7200 <= snapshot["available_at"]:
        return {**snapshot, "status": "data_paused", "reason": "Khoảng trống dữ liệu; chưa xác nhận tín hiệu"}
    
    if strategy == "trend_pullback":
        closes = [row.close for row in hours]
        fast, slow = ema(closes, 20), ema(closes, 50)
        entry_ema = ema([row.close for row in minutes], 20)
        trend = "up" if fast > slow and closes[-1] > fast else "down" if fast < slow and closes[-1] < fast else "flat"
        snapshot.update(trend=trend, level=entry_ema, ema_h1_slow=slow, ema_h1_fast=fast,
                        ema_m15=entry_ema, closed_h1_time=hours[-1].time,
                        reason="H1 tăng; chờ M15 chạm EMA20 và đóng xanh" if trend == "up" else
                        "H1 giảm; chờ M15 chạm EMA20 và đóng đỏ" if trend == "down" else
                        "H1 chưa có xu hướng EMA20/50 rõ; chờ thêm nến đóng")
    elif strategy == "range_rejection":
        sub_h = [row for row in hours if row.time + 3600 <= candle.time][-20:]
        if len(sub_h) < 20:
            return snapshot
        high, low = max(row.high for row in sub_h), min(row.low for row in sub_h)
        width = high - low
        sideways = width > 0 and abs(sub_h[-1].close - sub_h[0].close) <= 0.5 * width
        snapshot.update(range_high=high, range_low=low, range_start=sub_h[0].time,
                        range_end=sub_h[-1].time + 3600, trend="sideway" if sideways else "trending",
                        range_eligible=sideways,
                        reason="H1 đi ngang; chờ M15 chạm biên và đóng từ chối vào trong range"
                        if sideways else "H1 chưa đủ điều kiện sideway; không tìm tín hiệu rejection")

    signal = RULES[strategy](candle, hours, minutes)
    if signal:
        stop = signal["stop"]
        direction = 1 if signal["side"] == "long" else -1
        risk = direction * (candle.close - stop)
        if risk <= 0:
            return {**snapshot, "status": "invalid", "reason": "Invalid structural stop"}
        target = candle.close + direction * 2 * risk if risk > 0 else None
        return {**snapshot, **signal, "status": "confirmed", "reference_entry": candle.close,
                "stop": stop, "target": target, "confirm_time": candle.time}
    return {
        **snapshot,
        "status": "waiting_pullback" if strategy == "trend_pullback" else
                  "waiting_range_rejection" if strategy == "range_rejection" else "waiting_regime_setup",
    }
