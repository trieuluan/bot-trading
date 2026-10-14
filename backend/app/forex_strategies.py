"""Experimental closed-candle signals shared by replay and forward paper."""

STRATEGY_CHANNELS = {"trend": "trend_pullback", "range": "range_rejection"}

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

RULES = {"trend_pullback": trend_pullback, "range_rejection": range_rejection}


def strategy_snapshot(strategy, candle, hours, minutes):
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
    needed = 50 if strategy == "trend_pullback" else 20
    if len(hours) < needed or len(minutes) < (20 if strategy == "trend_pullback" else 1):
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
        target = candle.close + direction * 2 * risk if risk > 0 else None
        return {**snapshot, **signal, "status": "confirmed", "reference_entry": candle.close,
                "stop": stop, "target": target, "confirm_time": candle.time}
    return {
        **snapshot, "status": "waiting_pullback" if strategy == "trend_pullback" else "waiting_range_rejection",
    }
