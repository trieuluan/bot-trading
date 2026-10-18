"""Frozen pre-tree entry implementation; regression oracle only."""
from dataclasses import replace
from statistics import fmean
from backend.app.futures_rules import atr14, ema50, funding_filter, liquidity_sweep, mean_reversion, rsi14
from backend.app.strategy import Setup, aggregate, breakout, mtf_retest, retest
from backend.app.futures_exits import structural_target
from backend.app.futures_rule_tree import matches, describe_rule
from backend.app.futures_strategies import validate_genome, RULE_FAMILY

def strategy_setup(genome, candles, funding):
    validate_genome(genome)
    if len(candles) < 21:
        return None
    family, lookback = genome["family"], genome["lookback"]
    if family == RULE_FAMILY:
        found = [side for side in (1, -1) if matches(genome["rule"], candles, side, lookback)]
        if len(found) != 1:
            return None
        direction, last = found[0], candles[-1]
        stop = (min(bar.low for bar in candles[-5:]) if direction == 1
                else max(bar.high for bar in candles[-5:]))
        setup = Setup("long" if direction == 1 else "short", last.close, stop,
                      last.close + direction * 2 * abs(last.close - stop), describe_rule(genome["rule"]))
    elif family == "breakout":
        setup = breakout(candles, lookback, allow_short=True)
    elif family == "mtf_retest":
        setup = mtf_retest(candles, allow_short=True, lookback=lookback)
    elif family == "mean_reversion":
        setup = mean_reversion(candles, allow_short=True, max_er=genome["max_er"])
    elif family == "liquidity_sweep":
        setup = liquidity_sweep(candles, allow_short=True, lookback=lookback)
    elif family == "funding_filter":
        setup = funding_filter(funding, genome["funding_threshold"])(candles, allow_short=True)
    elif family == "retest":
        setup = retest(candles, lookback=lookback, allow_short=True)
    elif family == "volume_spike":
        history = candles[-lookback - 1 : -1]
        mean_vol = fmean(c.volume for c in history) if history else 0
        last = candles[-1]
        if last.volume >= 2.0 * mean_vol and mean_vol > 0:
            high, low = max(c.high for c in history), min(c.low for c in history)
            if last.close > high and last.close > last.open:
                stop = min(c.low for c in candles[-5:])
                setup = Setup("long", last.close, stop, last.close + 2 * (last.close - stop), "Vol Spike Long")
            elif last.close < low and last.close < last.open:
                stop = max(c.high for c in candles[-5:])
                setup = Setup("short", last.close, stop, last.close - 2 * (stop - last.close), "Vol Spike Short")
            else:
                setup = None
        else:
            setup = None
    elif family == "donchian_trend":
        history = candles[-lookback - 1 : -1]
        upper, lower = max(c.high for c in history), min(c.low for c in history)
        hours = aggregate(candles, 3600)
        ema = ema50(hours) if len(hours) >= 50 else None
        last = candles[-1]
        mid = (upper + lower) / 2
        if last.close > upper and (ema is None or last.close > ema):
            setup = Setup("long", last.close, mid, last.close + 2 * (last.close - mid), "Donchian Breakout Long")
        elif last.close < lower and (ema is None or last.close < ema):
            setup = Setup("short", last.close, mid, last.close - 2 * (mid - last.close), "Donchian Breakout Short")
        else:
            setup = None
    elif family == "ema_cross":
        fast_span = max(5, lookback // 2)
        slow_span = lookback
        if len(candles) >= slow_span + 2:
            def calc_ema(span, data):
                mult = 2 / (span + 1)
                val = data[0].close
                for b in data[1:]:
                    val = val * (1 - mult) + b.close * mult
                return val
            prev_fast = calc_ema(fast_span, candles[:-1])
            prev_slow = calc_ema(slow_span, candles[:-1])
            curr_fast = calc_ema(fast_span, candles)
            curr_slow = calc_ema(slow_span, candles)
            last = candles[-1]
            if prev_fast <= prev_slow and curr_fast > curr_slow:
                stop = min(c.low for c in candles[-5:])
                setup = Setup("long", last.close, stop, last.close + 2 * (last.close - stop), "EMA Cross Long")
            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                stop = max(c.high for c in candles[-5:])
                setup = Setup("short", last.close, stop, last.close - 2 * (stop - last.close), "EMA Cross Short")
            else:
                setup = None
        else:
            setup = None
    elif family == "rsi_reversal":
        if len(candles) >= 20:
            prev_rsi = rsi14(candles[:-1])
            curr_rsi = rsi14(candles)
            last = candles[-1]
            if prev_rsi <= 30 and curr_rsi > 30 and last.close > last.open:
                stop = min(c.low for c in candles[-5:])
                setup = Setup("long", last.close, stop, last.close + 2 * (last.close - stop), "RSI Oversold Long")
            elif prev_rsi >= 70 and curr_rsi < 70 and last.close < last.open:
                stop = max(c.high for c in candles[-5:])
                setup = Setup("short", last.close, stop, last.close - 2 * (stop - last.close), "RSI Overbought Short")
            else:
                setup = None
        else:
            setup = None
    else:
        last = candles[-1]
        history = candles[-lookback - 1:-1]
        high, low = max(bar.high for bar in history), min(bar.low for bar in history)
        side = None
        if family == "squeeze":
            if high - low <= 3 * atr14(candles):
                side = "long" if last.close > high else "short" if last.close < low else None
        else:
            hours = aggregate(candles, 3600)
            if len(hours) < 50:
                return None
            ema = hours[0].close
            for bar in hours[1:]:
                ema += 2 / 51 * (bar.close - ema)
            if hours[-1].close > ema and last.low <= ema < last.close and last.close > last.open:
                side = "long"
            elif hours[-1].close < ema and last.high >= ema > last.close and last.close < last.open:
                side = "short"
        direction = 1 if side == "long" else -1
        stop = min(bar.low for bar in candles[-5:]) if direction == 1 else max(bar.high for bar in candles[-5:])
        setup = Setup(side, last.close, stop, last.close + direction * 2 * abs(last.close - stop), family) if side else None
    if setup is None:
        return None
    direction = 1 if setup.side == "long" else -1
    stop = setup.stop - direction * genome["stop_buffer"] * atr14(candles)
    risk = direction * (setup.entry - stop)
    target = setup.target if family == "mean_reversion" else setup.entry + direction * genome["target_r"] * risk
    if genome.get("exit_policy") == "structure":
        target = structural_target(setup.side, setup.entry, stop, target, candles)
        if target is None:
            return None
    if risk <= 0 or target <= 0 or direction * (target - setup.entry) <= 0:
        return None
    return replace(setup, stop=stop, target=target)
