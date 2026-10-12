from dataclasses import dataclass

from .market import Candle


@dataclass(frozen=True)
class Setup:
    side: str
    entry: float
    stop: float
    target: float
    reason: str


def breakout(candles: list[Candle], lookback: int = 20, allow_short: bool = False) -> Setup | None:
    """Evaluate the latest CLOSED candle against previous closed candles only."""
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if len(candles) < lookback + 1:
        return None
    current = candles[-1]
    history = candles[-lookback - 1 : -1]
    level = max(c.high for c in history)
    if current.close > level:
        stop = min(c.low for c in history[-5:])
        risk = current.close - stop
        return (
            Setup(
                "long",
                current.close,
                stop,
                current.close + 2 * risk,
                f"Nến đóng vượt đỉnh {lookback} nến; SL đáy 5 nến; mục tiêu 2R.",
            )
            if risk > 0
            else None
        )
    if allow_short and current.close < min(c.low for c in history):
        stop = max(c.high for c in history[-5:])
        risk = stop - current.close
        target = current.close - 2 * risk
        return (
            Setup(
                "short",
                current.close,
                stop,
                target,
                f"Nến đóng phá đáy {lookback} nến; SL đỉnh 5 nến; mục tiêu 2R.",
            )
            if risk > 0 and target > 0
            else None
        )
    return None


def retest(
    candles: list[Candle], lookback: int = 20, wait: int = 10, allow_short: bool = False
) -> Setup | None:
    """Breakout close, retest of broken level, confirming close. Fires only on the latest closed candle."""
    last = len(candles) - 1
    for b in range(last - 1, max(lookback - 1, last - wait - 1), -1):
        hist = candles[b - lookback : b]
        for d in (1, -1) if allow_short else (1,):
            level = max(c.high for c in hist) if d == 1 else min(c.low for c in hist)
            if d * (candles[b].close - level) <= 0:
                continue
            touched = []
            for j in range(b + 1, min(last, b + wait) + 1):  # expires after `wait` candles
                c = candles[j]
                if d * (c.close - level) < 0:
                    break  # closed back through the zone: setup cancelled
                if touched and d * (c.close - c.open) > 0 and d * (c.close - level) > 0:
                    if j == last:
                        touched.append(c)
                        stop = (
                            min(x.low for x in touched) if d == 1 else max(x.high for x in touched)
                        )
                        risk = d * (c.close - stop)
                        target = c.close + d * 2 * risk
                        if risk > 0 and target > 0:
                            return Setup(
                                "long" if d == 1 else "short",
                                c.close,
                                stop,
                                target,
                                f"Breakout {lookback} nến, retest vùng {level:g}, nến xác nhận giữ vùng; SL theo retest; mục tiêu 2R.",
                            )
                    break  # confirmed earlier (already signalled) or invalid
                if d * (level - (c.low if d == 1 else c.high)) >= 0 or touched:
                    touched.append(c)
    return None


def aggregate(candles: list[Candle], seconds: int) -> list[Candle]:
    """Closed higher-timeframe bars from contiguous 15m bars; partial buckets are dropped."""
    if seconds not in (3600, 14400):
        raise ValueError("Higher timeframe must be 1h or 4h")
    groups: dict[int, list[Candle]] = {}
    for c in candles:
        groups.setdefault(c.time // seconds, []).append(c)
    return [
        Candle(
            g[0].time,
            g[0].open,
            max(c.high for c in g),
            min(c.low for c in g),
            g[-1].close,
            sum(c.volume for c in g),
        )
        for _, g in sorted(groups.items())
        if len(g) == seconds // 900
        and g[0].time % seconds == 0
        and all(current.time - previous.time == 900 for previous, current in zip(g, g[1:]))
    ]


def mtf_scan(
    candles: list[Candle],
    allow_short: bool = True,
    lookback: int = 20,
    wait: int = 12,
    ema_period: int = 50,
) -> dict:
    """4h EMA trend -> 1h breakout of the previous `lookback` bars in trend direction ->
    15m pullback to the broken level -> 15m confirming close. Closed candles only.

    Returns every breakout still inside the `wait`-hour window (`watches`, newest first),
    the one to show (`current`) and a `setup` only when the LATEST 15m candle confirms."""
    if lookback < 2 or wait < 1 or ema_period < 2:
        raise ValueError("Invalid multi-timeframe strategy parameters")
    h1, h4 = aggregate(candles, 3600), aggregate(candles, 14400)
    empty = {"trend": None, "watches": [], "current": None, "setup": None}
    if len(h4) < ema_period or len(h1) < lookback + 1:
        return {**empty, "reason": "Chưa đủ nến 1h/4h đã đóng để xác định xu hướng"}
    ema = h4[0].close
    for c in h4[1:]:
        ema += 2 / (ema_period + 1) * (c.close - ema)
    d = 1 if h4[-1].close > ema else -1
    trend = "up" if d == 1 else "down"
    if d == -1 and not allow_short:
        return {**empty, "trend": trend, "reason": "4h giảm nhưng short đang tắt"}
    last = candles[-1]
    watches, setup = [], None
    for b in range(len(h1) - 1, max(lookback - 1, len(h1) - 1 - wait), -1):
        hist = h1[b - lookback : b]
        level = max(c.high for c in hist) if d == 1 else min(c.low for c in hist)
        if d * (h1[b].close - level) <= 0:
            continue  # not a breakout
        start = h1[b].time + 3600
        deadline = start + wait * 3600
        watch = {
            "side": "long" if d == 1 else "short",
            "level": level,
            "breakout_time": h1[b].time,
            "range_start": hist[0].time,
            "range_end": h1[b].time,
            "range_high": max(c.high for c in hist),
            "range_low": min(c.low for c in hist),
            "touch_time": None,
            "confirm_time": None,
            "stop": None,
            "target": None,
        }
        if any(d * (c.close - level) < 0 for c in h1[b + 1 :]):
            watches.append({**watch, "status": "cancelled", "reason": "1h đóng phá ngược vùng"})
            continue
        touched = []
        outcome = None
        for c in candles:
            if c.time < start or c.time + 900 > deadline:
                continue
            if d * (c.close - level) < 0:
                outcome = {"status": "cancelled", "reason": "15m đóng phá ngược vùng"}
                break
            if touched and d * (c.close - c.open) > 0 and d * (c.close - level) > 0:
                touched.append(c)
                stop = min(x.low for x in touched) if d == 1 else max(x.high for x in touched)
                target = c.close + d * 2 * (d * (c.close - stop))
                if d * (c.close - stop) > 0 and target > 0:
                    outcome = {
                        "status": "confirmed",
                        "confirm_time": c.time,
                        "stop": stop,
                        "target": target,
                        "reason": "15m retest và nến xác nhận giữ vùng; SL theo retest; mục tiêu 2R.",
                    }
                    if c is last and setup is None:
                        setup = Setup(
                            watch["side"],
                            c.close,
                            stop,
                            target,
                            f"4h xu hướng {'tăng' if d == 1 else 'giảm'}; 1h breakout vùng {level:g}; "
                            "15m retest và nến xác nhận giữ vùng; SL theo retest; mục tiêu 2R.",
                        )
                else:
                    outcome = {"status": "cancelled", "reason": "SL/TP không hợp lệ"}
                break
            if d * (level - (c.low if d == 1 else c.high)) >= 0 or touched:
                touched.append(c)
        watch["touch_time"] = touched[0].time if touched else None
        expired = last.time + 900 > deadline
        if outcome is None:
            outcome = (
                {"status": "expired", "reason": f"Quá {wait} giờ chưa có nến xác nhận"}
                if expired
                else {
                    "status": "waiting_confirm" if touched else "waiting_retest",
                    "reason": "Đã retest vùng; chờ nến 15m xanh/đỏ xác nhận giữ vùng"
                    if touched
                    else "Đã breakout 1h; chờ giá quay lại retest vùng",
                }
            )
        watches.append({**watch, **outcome})
    alive = ("waiting_retest", "waiting_confirm")
    current = next(
        (w for w in watches if w["confirm_time"] == last.time),
        next((w for w in watches if w["status"] in alive), watches[0] if watches else None),
    )
    reason = None if current else "Chưa có breakout 1h cùng chiều xu hướng 4h"
    return {
        "trend": trend,
        "watches": watches,
        "current": current,
        "setup": setup,
        "reason": reason,
    }


def mtf_retest(
    candles: list[Candle],
    allow_short: bool = False,
    lookback: int = 20,
    wait: int = 12,
    ema_period: int = 50,
) -> Setup | None:
    return mtf_scan(candles, allow_short, lookback, wait, ema_period)["setup"]
