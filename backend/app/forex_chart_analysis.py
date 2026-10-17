from datetime import datetime, timezone

from .forex_strategies import _closed_hours, ema, rsi

BUILT_INS = {
    "trend_pullback": {"family": "pullback", "entry_ema": 20, "fast_ema": 20},
    "range_rejection": {"family": "range", "lookback": 20, "edge": 0.1},
    "donchian_breakout": {"family": "channel", "lookback": 20},
    "liquidity_sweep": {"family": "sweep", "lookback": 20},
    "squeeze_breakout": {"family": "squeeze", "lookback": 40, "bars": 10, "ratio": 3},
    "session_breakout": {"family": "session", "start_hour": 7, "end_hour": 12},
    "fvg_retest": {"family": "fvg", "min_gap": 0},
    "rsi_reversion": {"family": "rsi", "period": 14, "oversold": 30, "overbought": 70},
}


def chart_analysis(params, candle, hours, minutes, higher=(), snapshot=None, generated=False):
    family = params["family"]
    history = [row for row in minutes if row.time < candle.time][-(96 if generated else 60):]
    closed = _closed_hours(candle, hours)
    result = {"levels": [], "oscillator": None, "note": None, "parameters": params,
              "as_of": candle.time + 900, "price_type": "bid"}

    def line(label, price, start):
        result["levels"].append({"label": label, "price": price, "from": start,
                                 "to": candle.time})

    def band(window, label):
        high, low = max(row.high for row in window), min(row.low for row in window)
        line(label + " high", high, window[0].time)
        line(label + " low", low, window[0].time)
        return high, low

    if not history or history[-1].time != candle.time - 900:
        result["note"] = "Thieu nen M15 lien tuc de ve phan tich"
        return result
    if any(b.time - a.time != 900 for a, b in zip(history, history[1:])):
        result["gap"] = True  # market break in window: levels are real, entry rules may refuse
    if family in ("orb", "vsa", "mss", "vwap"):
        from .forex_extra_families import extra_setup
        _, levels = extra_setup(params, candle, history)
        for label, price, start in levels:
            line(label, price, start)
        if family in ("vsa", "vwap"):
            result["note"] = "Tick-volume proxy; not exchange volume" if levels else "Missing valid tick volume or history"
    elif family in ("channel", "sweep", "squeeze"):
        count = params["lookback"]
        window = history[-count:]
        if len(window) == count:
            if family == "squeeze":
                recent, prior = window[-params["bars"]:], window[:-params["bars"]]
                high, low = band(recent, "Compression")
                typical = sum(row.high - row.low for row in prior) / len(prior)
                if typical <= 0 or high - low > params["ratio"] * typical:
                    result["note"] = "Vung theo doi chua dat dieu kien compression"
            else:
                band(window, f"M15 {count}")
    elif family == "pullback":
        period = params["entry_ema"]
        if len(history) >= period:
            window = history[-60:]
            line(f"M15 EMA{period}", ema([row.close for row in window], period), window[0].time)
        if len(closed) >= 50:
            for period in (params["fast_ema"], 50):
                line(f"H1 EMA{period}", ema([row.close for row in closed], period), closed[0].time)
    elif family == "range":
        count = params["lookback"]
        window = [row for row in closed if row.time + 3600 <= candle.time][-count:]
        if len(window) == count:
            high, low = band(window, f"H1 range {count}")
            line("Upper rejection edge", high - params["edge"] * (high - low), window[0].time)
            line("Lower rejection edge", low + params["edge"] * (high - low), window[0].time)
    elif family == "session":
        stamp = datetime.fromtimestamp(candle.time, timezone.utc)
        day = int(datetime(stamp.year, stamp.month, stamp.day, tzinfo=timezone.utc).timestamp())
        end = day + params["start_hour"] * 3600
        window = [row for row in history if day <= row.time < end]
        if [row.time for row in window] == list(range(day, end, 900)):
            band(window, f"Session 00-{params['start_hour']:02d} UTC")
            if not params["start_hour"] <= stamp.hour < params["end_hour"]:
                result["note"] = "Ngoai khung gio vao lenh; chi ve vung tham chieu"
    elif family == "fvg" and len(history) >= max(14 if generated else 3, 3):
        first, middle, last = history[-3:]
        atr = sum(row.high - row.low for row in history[-14:]) / 14 if generated else 0
        if last.low > first.high and last.low - first.high >= params["min_gap"] * atr and middle.close > middle.open:
            line("Bull FVG lower", first.high, first.time)
            line("Bull FVG upper", last.low, first.time)
        elif last.high < first.low and first.low - last.high >= params["min_gap"] * atr and middle.close < middle.open:
            line("Bear FVG lower", last.high, first.time)
            line("Bear FVG upper", first.low, first.time)
        else:
            result["note"] = "Khong co FVG hop le trong 3 nen dang danh gia"
    elif family == "rsi":
        period = params["period"]
        if len(history) > period:
            result["oscillator"] = {
                "label": f"RSI{period}", "low": params["oversold"], "high": params["overbought"],
                "points": [{"time": history[index].time,
                            "value": rsi([row.close for row in history[:index + 1]], period)}
                           for index in range(period, len(history))],
            }
    elif family == "retest":
        if not generated:
            snap = snapshot or {}
            start = snap.get("range_start")
            if start is not None:
                for key in ("range_high", "range_low", "level"):
                    if snap.get(key) is not None:
                        line(key, snap[key], start)
        else:
            h4 = [row for row in higher if row.time + 14400 <= candle.time + 900][-60:]
            if len(h4) >= 50 and h4[-1].time + 28800 > candle.time + 900:
                avg = ema([row.close for row in h4], 50)
                direction = 1 if h4[-1].close > avg else -1 if h4[-1].close < avg else 0
                for index in range(len(closed) - 1, params["lookback"] - 1, -1):
                    breakout = closed[index]
                    available = breakout.time + 3600
                    if candle.time < available or candle.time + 900 - available > params["wait_hours"] * 3600:
                        continue
                    window = closed[index - params["lookback"]:index]
                    level = max(row.high for row in window) if direction == 1 else min(row.low for row in window)
                    if not direction or direction * (breakout.close - level) <= 0:
                        continue
                    since = [row for row in history if row.time >= available]
                    if candle.time != available and (not since or since[0].time != available):
                        break
                    if any(direction * (row.close - level) < 0 or
                           (row.low <= level < row.close if direction == 1 else row.high >= level > row.close)
                           for row in since):
                        break
                    line("H1 breakout retest", level, available)
                    break
    elif family == "router" and len(closed) >= 50:
        closes = [row.close for row in closed]
        fast, slow = ema(closes, 20), ema(closes, 50)
        width = sum(row.high - row.low for row in closed[-14:]) / 14
        name = "trend_pullback" if abs(fast - slow) > params["separation"] * width else "range_rejection"
        result = chart_analysis(BUILT_INS[name], candle, hours, history, higher, generated=generated)
        result["parameters"] = params
        result["note"] = f"Router dang danh gia {name}"
    if not result["levels"] and not result["oscillator"] and not result["note"]:
        result["note"] = "Chua co vung hop le hoac chua du nen cho tham so nay"
    return result
