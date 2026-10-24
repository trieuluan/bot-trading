"""Transactional paper alerts. Delivery is at least once after DB commit."""

import asyncio
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .alerts import send_alert
from .db import AutoPaperDecision, PaperNotification, Session

EXIT_LABELS = {
    "stop_loss": "Chạm SL", "take_profit": "Chạm TP", "manual": "Đóng thủ công",
    "liquidation": "Thanh lý Cross",
    "time_stop": "Time-stop (lệnh chết)", "ladder_complete": "Hoàn tất bậc chốt", "signal_invalidation": "Tín hiệu HTF bị phá", "funding_exit": "Funding quá cao", "recovered_stop_loss": "Chạm SL (đối soát nến)", "recovered_take_profit": "Chạm TP (đối soát nến)",
}

def enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

def queue(session, key, message):
    if session.get(PaperNotification, key) is None:
        session.add(PaperNotification(id=key, message=message[:4000]))

def price(value):
    return f"{value:,.8f}".rstrip("0").rstrip(".")

def signed_price(value):
    formatted = f"{value:+,.8f}".rstrip("0").rstrip(".")
    return formatted if formatted not in ("+", "-", "") else "0"

def when(timestamp):
    return datetime.fromtimestamp(timestamp, timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M:%S UTC+7")

def audit_of(session, trade):
    decision = session.scalar(select(AutoPaperDecision).where(AutoPaperDecision.trade_id == trade.id))
    return (decision.parameters or {}).get("audit") if decision else None

HTF_VN = {
    "htf_breakout": "Breakout HTF", "htf_ema_cross": "EMA cross HTF",
    "htf_pullback": "Pullback EMA HTF", "htf_squeeze": "Squeeze HTF",
    "htf_retest": "Retest HTF", "htf_swing": "Vùng swing HTF",
    "htf_fib": "Fibonacci thoái lui HTF", "htf_divergence": "Phân kỳ RSI HTF",
    "htf_fvg": "Fair Value Gap HTF", "htf_bos": "Phá vỡ cấu trúc BOS HTF",
    "htf_vwap": "VWAP phiên HTF", "htf_sweep": "Quét thanh khoản HTF",
    "htf_wedge": "Nêm HTF", "htf_bollinger": "Bollinger HTF",
    "htf_dryup": "Hồi cạn volume HTF", "htf_trend_align": "Đồng thuận Trend HTF",
    "htf_other": "Trend + nến",
}

# Mirror of frontend htfProfile(): seed trees = H4 filter, H1 setup (child 1), M15 trigger.
PROFILE_RULES = (
    ("htf_retest", ("retest", "retest_seq")), ("htf_squeeze", ("compression", "compression_current")),
    ("htf_bollinger", ("bb_pierce",)), ("htf_dryup", ("volume_dryup",)), ("htf_breakout", ("breakout",)),
    ("htf_trend_align", ("trend",)), ("htf_sweep", ("wick_sweep", "sweep")),
    ("htf_pullback", ("ema_touch", "ema_bounce")), ("htf_ema_cross", ("ema_cross_n", "ema_cross")),
    ("htf_wedge", ("wedge",)), ("htf_swing", ("swing_zone",)), ("htf_fib", ("fib_retrace",)),
    ("htf_divergence", ("rsi_divergence",)), ("htf_fvg", ("fvg",)), ("htf_bos", ("bos",)),
    ("htf_vwap", ("session_vwap",)),
)

def leaf_features(rule):
    return [rule["feature"]] if "feature" in rule else [f for c in rule.get("children", []) for f in leaf_features(c)]

def profile_of(genome):
    profile = genome.get("exit_profile") or "default"
    if profile != "default":
        return profile
    rule = genome.get("rule") or {}
    if rule.get("op") != "all" or len(rule.get("children", [])) != 3:
        return "default"
    used = leaf_features(rule["children"][1])
    return next((name for name, names in PROFILE_RULES if any(f in used for f in names)), "htf_other")

def strategy_line(audit):
    if not audit:
        return "Chưa có snapshot chiến thuật"
    genome = audit.get("genome") or {}
    name = audit.get("strategy", "")
    code = name.split(":")[-1][:6]
    profile = profile_of(genome) if genome.get("family", "rule_tree") == "rule_tree" else genome.get("family", "rule_tree")
    label = "Cây tự sinh" if profile == "default" else HTF_VN.get(profile, profile.replace("_", " "))
    return f"{label} #{code} · G{audit.get('generation', 0)}"

def exit_text(policy):
    return {"fixed": "SL/TP cố định", "breakeven": "Hòa vốn 1R", "trail": "Hòa vốn + trailing",
            "structure": "TP cấu trúc + hòa vốn",
            "adaptive": "SL/TP tự tính + chốt phần + trailing",
            "atr_chandelier": "Trailing Chandelier theo ATR H1",
            "time_stop": "Thoát theo thời gian",
            "scale_out_ladder": "Chốt theo bậc + trailing H1",
            "signal_invalidation": "Thoát khi tín hiệu HTF bị phá",
            "runner": "Giữ phần chạy theo xu hướng + trailing H1",
            "volatility_stop": "Thoát theo biến động",
            "funding_exit": "Thoát khi funding quá cao"}.get(policy or "fixed", policy or "fixed")

def esc(value):
    return html.escape(str(value), quote=False)

EXIT_ICONS = {"stop_loss": "🛑", "recovered_stop_loss": "🛑", "take_profit": "🎯", "recovered_take_profit": "🎯",
              "manual": "✋", "liquidation": "💥", "time_stop": "⏱", "ladder_complete": "🪜",
              "signal_invalidation": "⚠️", "funding_exit": "💸"}

def strategy_html(trade, audit):
    return esc(strategy_line(audit)) if trade.origin == "auto" else "Thủ công"

def notify_open(session, trade, audit, balance, reason=None):
    notional = trade.entry * trade.quantity
    risk = abs(trade.entry - trade.stop) * trade.quantity
    reward = abs(trade.target - trade.entry) * trade.quantity
    icon, side = ("🟢 📈", "LONG") if trade.side == "long" else ("🔴 📉", "SHORT")
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-0-open", "\n".join([
        f"{icon} <b>MỞ LỆNH {side}</b> · {esc(trade.symbol)} · #{trade.id} ({'AUTO' if trade.origin == 'auto' else 'THỦ CÔNG'})",
        f"🧠 {strategy_html(trade, audit)}",
        "",
        f"▫️ Entry <code>{price(trade.entry)}</code>",
        f"🛑 SL <code>{price(trade.stop)}</code>  <i>(-{risk:,.2f} USDT)</i>",
        f"🎯 TP <code>{price(trade.target)}</code>  <i>(+{reward:,.2f} USDT" + (f" · {reward / risk:.2f}R" if risk else "") + ")</i>",
        "",
        f"💰 Giá trị <b>{notional:,.2f}</b> USDT · KL {trade.quantity:g} ({trade.contracts:g} hợp đồng)",
        f"🔒 Ký quỹ {notional / (trade.leverage or 1):,.2f} USDT · x{trade.leverage} Cross · Phí mở {trade.entry_fee:.2f}",
        f"🧭 Thoát: {esc(exit_text((audit or {}).get('exit_policy')))}",
        *([f"<blockquote expandable>Lý do: {esc(reason)}</blockquote>"] if reason else []),
        f"👛 Ví chung {balance:,.2f} USDT · {when(trade.entry_time)}",
        "<i>SL/TP ước tính gộp, chưa trừ phí/funding.</i>",
    ]))

def notify_partial(session, trade, fill, quantity, net, left, candle_time):
    audit = audit_of(session, trade)
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-1-partial-{candle_time}", "\n".join([
        f"🟡 <b>CHỐT MỘT PHẦN {trade.side.upper()}</b> · {esc(trade.symbol)} · #{trade.id}",
        f"🧠 {strategy_html(trade, audit)}",
        "",
        f"💵 Chốt {quantity:g} tại <code>{price(fill)}</code> (entry <code>{price(trade.entry)}</code>)",
        f"{'✅' if net > 0 else '❌' if net < 0 else '➖'} PnL sau phí <b>{net:+,.2f} USDT</b> <i>(chưa phân bổ funding)</i>",
        f"📦 Còn giữ {left:g} · 🛑 SL <code>{price(trade.stop)}</code> · 🎯 TP <code>{price(trade.target)}</code>",
        f"🕒 Nến {when(candle_time + 900)}",
    ]))

def notify_stop_update(session, trade, old_stop, new_stop, candle_time, old_target=None, ref_price=None):
    """Một tin mỗi nến cho dời SL và/hoặc TP (trade.target đã là giá trị mới)."""
    audit = audit_of(session, trade)
    sign = 1 if trade.side == "long" else -1
    stop_moved = new_stop != old_stop
    target_moved = old_target is not None and trade.target != old_target
    if not stop_moved and not target_moved:
        return
    risk = (trade.monitoring or {}).get("risk")
    def level_r(level):
        return f"{sign * (level - trade.entry) / risk:+.2f}R" if risk and risk > 0 else "chưa có R gốc"

    def move_r(old, new, kind="sl"):
        if not risk or risk <= 0:
            return "chưa có R gốc"
        change = sign * (new - old) / risk
        verb = ("nâng" if change > 0 else "hạ") if kind == "sl" else ("nới thêm" if change > 0 else "thu hẹp")
        return f"{level_r(old)} → {level_r(new)} ({verb} {change:+.2f}R)"

    def distance(level, label):
        if ref_price is None or ref_price <= 0:
            return []
        gap = abs(level - ref_price)
        r_gap = f" · {gap / risk:.2f}R" if risk and risk > 0 else ""
        side = "phía trên" if level > ref_price else "phía dưới" if level < ref_price else "đúng tại"
        return [f"📏 Khoảng cách tới {label}: <b>{price(gap)}</b> "
                f"({gap / ref_price * 100:.2f}%{r_gap}), {side} close nến"]
    est_pnl = sign * (new_stop - trade.entry) * trade.quantity
    title = "SL/TP" if stop_moved and target_moved else "SL" if stop_moved else "TP"
    icon = "🎯" if not stop_moved else "🛡" if est_pnl >= 0 else "📍"
    lines = [
        f"{icon} <b>DỜI {title} {trade.side.upper()}</b> · {esc(trade.symbol)} · #{trade.id}",
        f"🧠 {strategy_html(trade, audit)}",
        "",
    ]
    if stop_moved:
        lines.append(
            f"🛑 SL: <code>{price(old_stop)}</code> → <code>{price(new_stop)}</code> "
            f"<i>(Δ giá {signed_price(new_stop - old_stop)})</i>"
        )
        lines.append(f"R tại SL: {move_r(old_stop, new_stop)}")
        if est_pnl > 0:
            sl_badge = f"✅ Khóa lãi gộp: <b>{est_pnl:+,.2f} USDT</b> ({level_r(new_stop)})"
        elif est_pnl == 0:
            sl_badge = "⚖️ Hòa vốn giá: <b>0.00 USDT</b> (chưa trừ chi phí)"
        else:
            sl_badge = f"🔻 PnL gộp tại SL: <b>{est_pnl:+,.2f} USDT</b> ({level_r(new_stop)})"
        lines.append(sl_badge)
        lines.extend(distance(new_stop, "SL"))
    if target_moved:
        delta_tp = sign * (trade.target - old_target)
        est_tp = sign * (trade.target - trade.entry) * trade.quantity
        tp_verb = "Tăng mục tiêu" if delta_tp > 0 else "Giảm mục tiêu"
        lines.append(
            f"🎯 TP: <code>{price(old_target)}</code> → <code>{price(trade.target)}</code> "
            f"<i>(Δ giá {signed_price(trade.target - old_target)})</i>"
        )
        lines.append(f"{tp_verb}: {move_r(old_target, trade.target, kind='tp')}")
        lines.append(f"🎯 PnL gộp tại TP: <b>{est_tp:+,.2f} USDT</b> ({level_r(trade.target)})")
        lines.extend(distance(trade.target, "TP"))
    lines.append("")
    lines.append(f"▫️ Entry <code>{price(trade.entry)}</code>"
                 + ("" if target_moved else f" · TP <code>{price(trade.target)}</code>")
                 + ("" if stop_moved else f" · SL <code>{price(new_stop)}</code>"))
    if ref_price is not None:
        lines.append(f"▫️ Close nến tham chiếu <code>{price(ref_price)}</code> (không phải giá live)")
    lines.append("<i>PnL gộp trên phần đang giữ, chưa trừ phí, funding và trượt giá. R theo rủi ro giá ban đầu.</i>")
    lines += [f"📦 Khối lượng giữ: {trade.quantity:g}", f"🕒 Nến {when(candle_time + 900)}"]
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-1-stop-{candle_time}", "\n".join(lines))

def notify_close(session, trade):
    audit = audit_of(session, trade)
    net = trade.net_pnl or 0
    realized = (trade.monitoring or {}).get("realized_net", 0)
    risk = (trade.monitoring or {}).get("risk")
    original = (trade.monitoring or {}).get("original_quantity") or trade.quantity
    r_text = f" · {net / (risk * original):+.2f}R" if risk else ""
    icon, word = ("✅", "LÃI") if net > 0 else ("❌", "LỖ") if net < 0 else ("➖", "HÒA")
    reason = EXIT_LABELS.get(trade.exit_reason, trade.exit_reason)
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-2-close", "\n".join([
        f"{icon} <b>ĐÓNG LỆNH {trade.side.upper()} · {word}</b> · {esc(trade.symbol)} · #{trade.id}",
        f"{EXIT_ICONS.get(trade.exit_reason, '🔔')} {esc(reason)}",
        f"🧠 {strategy_html(trade, audit)}",
        "",
        f"💰 PnL ròng <b>{net:+,.2f} USDT</b>{r_text}",
        f"▫️ <code>{price(trade.entry)}</code> → <code>{price(trade.exit)}</code> · Giữ {max(0, (trade.exit_time - trade.entry_time) // 60)} phút",
        f"🧾 Phí đóng {trade.exit_fee:.2f} · Funding {trade.funding_cost:.2f}"
        + (f" · Đã chốt từng phần {realized:+,.2f}" if realized else ""),
        f"🕒 {when(trade.exit_time)}",
    ]))

def notify_blocked(session, symbol, candle_time, setup, kind, detail, strategy=None):
    """Tín hiệu hợp lệ nhưng không vào được lệnh (hết ký quỹ / chạm trần rủi ro). Một tin mỗi cặp mỗi nến."""
    title = {"margin": "THIẾU KÝ QUỸ", "risk_cap": "CHẠM TRẦN RỦI RO", "daily_loss": "CHẠM TRẦN LỖ NGÀY"}[kind]
    queue(session, f"{candle_time:010d}-{symbol}-blocked-{kind}", "\n".join([
        f"⛔ <b>BỎ LỠ TÍN HIỆU {setup.side.upper()} · {title}</b> · {esc(symbol)}",
        *([f"🧠 {esc(strategy)}"] if strategy else []),
        "",
        f"▫️ Entry <code>{price(setup.entry)}</code> · 🛑 SL <code>{price(setup.stop)}</code> · 🎯 TP <code>{price(setup.target)}</code>",
        f"⚠️ {esc(detail)}",
        f"🕒 Nến {when(candle_time + 900)}",
    ]))

def deliver(now=None):
    """Lock only outbox rows, never wallet rows, while sending."""
    if not enabled():
        return
    now = int(time.time() if now is None else now)
    for _ in range(5):
        with Session.begin() as session:
            item = session.scalar(select(PaperNotification).where(
                PaperNotification.sent_at.is_(None), PaperNotification.retry_at <= now,
            ).order_by(PaperNotification.retry_at, PaperNotification.id)
                .with_for_update(skip_locked=True).limit(1))
            if item is None:
                return
            item.attempts += 1
            if send_alert(item.message):
                item.sent_at = now
            else:
                item.retry_at = now + min(300, 5 * 2 ** min(item.attempts, 6))

async def notifications_loop():
    while True:
        try:
            await asyncio.to_thread(deliver)
        except Exception:
            logging.error("Paper notification delivery unavailable; retrying")
        await asyncio.sleep(5)
