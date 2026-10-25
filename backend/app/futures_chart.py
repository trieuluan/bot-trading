"""Chart overlays for rule-tree leaves; drawing only, never used for entries."""

from statistics import fmean, pstdev

from .futures_candles import fit
from .futures_rule_tree import (
    CANDLE_PATTERNS,
    FRAMES,
    H4_FEATURES,
    breakout_level,
    confirmed_pivots,
    describe_rule,
    ema,
    fib_zone,
    frame_rows,
    fresh_fvg,
    is_htf_rule,
    matches,
    nodes,
    session_vwap_points,
    structure_level,
)
from .futures_rules import atr14, rsi14

CURVE_BARS = 100
BANDS = ("bb_pierce", "band_reclaim")
EMAS = {"trend": (50,), "trend_optional": (50,), "ema_touch": (50,), "ema_bounce": (21, 50),
        "ema_cross": (9, 21)}

def rule_overlays(rule, candles, lookback, context=None):
    levels, curves, markers, details, seen = [], [], [], [], set()
    if not candles:
        return {"levels": levels, "curves": curves, "markers": markers, "overlay_details": details}
    context = {**(context or {}), "htf": is_htf_rule(rule)}
    end = candles[-1].time

    def level(title, price, start):
        if title not in seen:
            seen.add(title)
            levels.append({"title": title, "price": price, "from": start, "to": end})

    def curve(title, points):
        if title not in seen and points:
            seen.add(title)
            curves.append({"title": title, "points": [*points, {"time": end, "value": points[-1]["value"]}]
                           if points[-1]["time"] < end else points})

    for _, sequence in nodes(rule):
        if sequence.get("op") != "then":
            continue
        first, second = sequence["children"]
        step = 4 if context["htf"] and all(
            child["frame"] == "H1" for _, child in nodes(sequence) if "feature" in child) else 1
        for direction, side in ((1, "Long"), (-1, "Short")):
            for lag in range(step, min(sequence["within"] * step, len(candles) - 1) + 1, step):
                history = candles[:-lag]
                anchor = breakout_level(first, history, direction, lookback)
                if anchor is None or not matches(first, history, direction, lookback, context=context):
                    continue
                holding = frame_rows(candles, "H1")[-lag // 4:] if step == 4 else candles[-lag:]
                if any(direction * (bar.close - anchor) < 0 for bar in holding):
                    continue
                label = "Retest xác nhận" if matches(second, candles, direction, lookback, anchor, context) else "Retest chờ"
                level(f"{label} · {side}", anchor, history[-1].time)
                break

    used = set()
    for _, node in nodes(rule):
        feat, frame = node.get("feature"), node.get("frame")
        if feat is None:
            continue
        if (feat, frame) in used:
            continue
        used.add((feat, frame))
        seconds = FRAMES[frame]
        shift = seconds - 900
        bars = frame_rows(candles, frame)
        prior = bars[-lookback - 1:-1] if len(bars) > lookback else []
        count_before = len(levels) + len(curves)
        message = ""
        matched = [side for direction, side in ((1, "Long"), (-1, "Short"))
                   if matches(node, candles, direction, lookback, context=context)] if feat != "retest" else []
        if feat in EMAS or feat == "ema_cross_n":
            periods = ((max(5, lookback // 2), lookback) if feat == "ema_cross_n" else EMAS[feat])
            if len(bars) >= max(periods):
                for period in periods:
                    values = ema([bar.close for bar in bars], period)
                    curve(f"EMA{period} {frame}", [{"time": bars[index].time + shift, "value": values[index]}
                          for index in range(max(period - 1, len(bars) - CURVE_BARS), len(bars))])
            else:
                message = f"Cần {max(periods)} nến {frame} đã đóng; hiện có {len(bars)}."
        elif feat in ("breakout", "compression", "compression_current", "sweep", "wick_sweep", "retest", "retest_seq", "mtf_seq", "funding_extreme"):
            if feat == "mtf_seq":
                bars = frame_rows(candles, "H1")
                shift, frame = 2700, "H1"
                prior = bars[-lookback - 1:-1] if len(bars) > lookback else []
            if feat == "funding_extreme":
                bars, frame, shift = candles, "M15", 0
                prior = bars[-11:-1] if len(bars) >= 11 else []
            if prior:
                name = ("Vùng nén" if feat.startswith("compression") else
                        "Quét" if "sweep" in feat else "Vùng retest tham chiếu" if "retest" in feat else "Vùng breakout")
                level(f"{name} {frame} · trên", max(bar.high for bar in prior), prior[0].time)
                level(f"{name} {frame} · dưới", min(bar.low for bar in prior), prior[0].time)
                if feat.startswith("compression"):
                    width = max(bar.high for bar in prior) - min(bar.low for bar in prior)
                    atr = atr14(bars if feat == "compression_current" else bars[:-1])
                    message = f"Biên {width:.6g}; ngưỡng nén ≤ 3 ATR14 = {3 * atr:.6g}. Hộp tham chiếu chưa đồng nghĩa có nén."
                elif feat == "retest":
                    message = "Biên tham chiếu; đường Retest chờ/xác nhận chỉ có khi chuỗi breakout còn hiệu lực."
            else:
                message = f"Thiếu nến {frame} để xác định vùng tham chiếu."
        elif feat == "swing_zone":
            window = prior
            for direction, name in ((1, "Hỗ trợ swing"), (-1, "Kháng cự swing")):
                key = (lambda bar: bar.low) if direction == 1 else (lambda bar: bar.high)
                pivots = [bar for index, bar in enumerate(window[2:-2], 2)
                          if all(direction * (key(bar) - key(window[index + step])) <= 0
                                 for step in (-2, -1, 1, 2))]
                if pivots:
                    level(f"{name} {frame}", key(pivots[-1]), pivots[-1].time)
        elif feat in BANDS:
            band = frame_rows(candles, "H1") if feat == "band_reclaim" else bars
            name, shift = ("H1", 2700) if feat == "band_reclaim" else (frame, shift)
            if len(band) >= 20:
                rows = {"trên": [], "giữa": [], "dưới": []}
                for index in range(max(20, len(band) - CURVE_BARS), len(band) + 1):
                    closes = [bar.close for bar in band[index - 20:index]]
                    middle, spread = fmean(closes), 2 * pstdev(closes)
                    time = band[index - 1].time + shift
                    for key, value in (("trên", middle + spread), ("giữa", middle), ("dưới", middle - spread)):
                        rows[key].append({"time": time, "value": value})
                for key, points in rows.items():
                    curve(f"BB20 {name} · {key}", points)
        elif feat == "wedge":
            size = max(lookback, 20)
            if len(bars) > size:
                window = bars[-size - 1:-1]
                high_slope, high_base = fit([bar.high for bar in window])
                low_slope, low_base = fit([bar.low for bar in window])
                gap_start = high_base - low_base
                gap_end = gap_start + (high_slope - low_slope) * (size - 1)
                converging = (0 < gap_end <= 0.8 * gap_start
                              and min(abs(high_slope), abs(low_slope)) * size >= 0.5 * atr14(bars[:-1])
                              and (high_slope < low_slope < 0 or low_slope > high_slope > 0))
                message = "Hai cạnh hội tụ; chờ nến xác nhận phá nêm." if converging else "Hai cạnh hồi quy tham chiếu; chưa đủ điều kiện nêm."
                for name, values in (("trên", [bar.high for bar in window]), ("dưới", [bar.low for bar in window])):
                    slope, base = fit(values)
                    curve(f"{'Nêm' if converging else 'Hồi quy'} {frame} · cạnh {name}", [
                        {"time": window[0].time + shift, "value": base},
                        {"time": bars[-1].time + shift, "value": base + slope * size}])
        elif feat == "session_vwap":
            points = session_vwap_points(bars, seconds)
            curve(f"VWAP ngày {frame} · volume nến",
                  [{"time": point["time"] + shift, "value": point["value"]} for point in points])
        elif feat in ("fib_retrace", "fvg", "bos"):
            for direction, side in ((1, "Long"), (-1, "Short")):
                if feat == "fib_retrace":
                    zone = fib_zone(bars, direction, lookback)
                    found = zone and (zone[2] + 3 * seconds - 900,
                                      [("Fib 50%", zone[0]), ("Fib 61.8%", zone[1])])
                elif feat == "fvg":
                    zone = fresh_fvg(bars, direction, lookback)
                    found = zone and (zone[2] + seconds - 900,
                                      [("FVG dưới", zone[0]), ("FVG trên", zone[1])])
                else:
                    zone = structure_level(bars, direction, lookback)
                    found = zone and (zone[1] + 3 * seconds - 900, [("Cấu trúc BOS", zone[0])])
                if found:
                    for name, price in found[1]:
                        level(f"{name} {frame} · {side}", price, found[0])
        elif feat in ("rsi", "rsi_divergence"):
            message = f"RSI14 {frame}: {rsi14(bars):.2f}; ngưỡng 30/70." if len(bars) >= 15 else "Thiếu 15 nến đã đóng để tính RSI14."
            if feat == "rsi_divergence":
                for direction, side in ((1, "Long"), (-1, "Short")):
                    pivots = [index for index in confirmed_pivots(bars, -direction, lookback) if index >= 14]
                    if side not in matched or len(pivots) < 2:
                        continue
                    first, second = pivots[-2:]
                    curve(f"Phân kỳ RSI {frame} · {side}", [
                        {"time": bars[index].time + shift,
                         "value": bars[index].low if direction == 1 else bars[index].high}
                        for index in (first, second)])
        elif feat in ("volume", "volume_dryup"):
            base = bars[-lookback - 3:-3] if feat == "volume_dryup" else prior
            current = fmean(bar.volume for bar in bars[-3:]) if feat == "volume_dryup" and bars else bars[-1].volume if bars else 0
            average = fmean(bar.volume for bar in base) if len(base) == lookback else 0
            message = (f"Volume {frame}: {current:.6g} / TB {average:.6g} = {current / average:.2f}×; "
                       + ("cạn ≤ 0.7×." if feat == "volume_dryup" else "bùng nổ ≥ 2×.")) if average > 0 else "Thiếu lịch sử volume hợp lệ."
        elif feat in CANDLE_PATTERNS or feat == "candle_direction":
            message = "Dấu điều kiện trên nến đã đóng, không phải tín hiệu vào lệnh của toàn cây."
        elif feat in H4_FEATURES:
            message = ("Đồng thuận BTC và 10 coin trên H4 đã đóng; thiếu dữ liệu thì không đạt."
                       if feat == "market_alignment" else
                       "Xác nhận từ giá H4 đã đóng; EMA50 bắt buộc, cần thêm ít nhất 1/2 xác nhận H4.")
        else:
            message = "Bộ lọc dữ liệu ngoài giá/phiên; trạng thái tính theo dữ liệu bot, không vẽ đường giá giả."
        if matched and bars and feat in (*CANDLE_PATTERNS, "candle_direction", "breakout", "sweep", "wick_sweep",
                                        "ema_cross", "ema_cross_n", "ema_bounce", "ema_touch", "rsi_divergence", "bos"):
            for side in matched:
                markers.append({"time": bars[-1].time + seconds - 900,
                                "side": side.lower(), "title": describe_rule(node)})
        drawn = len(levels) + len(curves) > count_before
        if not drawn and not message:
            message = "Chưa có swing/vùng hợp lệ hoặc chưa đủ nến đã đóng; không vẽ vùng giả."
        details.append({"feature": feat, "frame": node["frame"], "label": describe_rule(node),
                        "matched": matched, "drawn": drawn, "message": message})
    return {"levels": levels, "curves": curves, "markers": markers, "overlay_details": details}
