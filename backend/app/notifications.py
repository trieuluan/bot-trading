"""Transactional paper alerts. Delivery is at least once after DB commit."""

import os
import asyncio
import logging
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

def strategy_line(audit):
    if not audit:
        return "Chưa có snapshot chiến thuật"
    genome = audit.get("genome") or {}
    name = audit.get("strategy", "")
    code = name.split(":")[-1][:6]
    profile = genome.get("exit_profile") or genome.get("family") or "rule_tree"
    label = HTF_VN.get(profile, profile)
    return f"{label} #{code} · G{audit.get('generation', 0)}"

def exit_text(policy):
    return {"fixed": "SL/TP cố định", "breakeven": "Hòa vốn 1R", "trail": "Hòa vốn + trailing",
            "structure": "TP cấu trúc + hòa vốn",
            "adaptive": "SL/TP tự tính + chốt phần + trailing"}.get(policy or "fixed", policy or "fixed")

def notify_open(session, trade, audit, balance, reason=None):
    notional = trade.entry * trade.quantity
    risk = abs(trade.entry - trade.stop) * trade.quantity
    reward = abs(trade.target - trade.entry) * trade.quantity
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-0-open", "\n".join([
        f"PAPER · MỞ LỆNH #{trade.id} · {trade.symbol} {trade.side.upper()} ({'AUTO' if trade.origin == 'auto' else 'THỦ CÔNG'})",
        f"Chiến thuật: {strategy_line(audit) if trade.origin == 'auto' else 'Thủ công'}",
        f"Entry {price(trade.entry)} · SL {price(trade.stop)} · TP {price(trade.target)}",
        f"Khối lượng {trade.quantity:g} ({trade.contracts:g} hợp đồng) · Giá trị {notional:,.2f} USDT",
        f"Ký quỹ {notional / (trade.leverage or 1):,.2f} USDT x{trade.leverage} Cross · Phí mở {trade.entry_fee:.2f}",
        f"Ước tính gộp: SL -{risk:,.2f} USDT · TP +{reward:,.2f} USDT (chưa trừ phí/funding)" + (f" · {reward / risk:.2f}R" if risk else ""),
        f"Thoát lệnh: {exit_text((audit or {}).get('exit_policy'))}",
        *([f"Lý do: {reason}"] if reason else []),
        f"Số dư ví chung: {balance:,.2f} USDT · {when(trade.entry_time)}",
    ]))

def notify_partial(session, trade, fill, quantity, net, left, candle_time):
    audit = audit_of(session, trade)
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-1-partial-{candle_time}", "\n".join([
        f"PAPER · CHỐT MỘT PHẦN #{trade.id} · {trade.symbol} {trade.side.upper()}",
        f"Chiến thuật: {strategy_line(audit) if trade.origin == 'auto' else 'Thủ công'}",
        f"Chốt {quantity:g} tại {price(fill)} (entry {price(trade.entry)}) · PnL sau phí {net:+,.2f} USDT (chưa phân bổ funding)",
        f"Còn giữ {left:g} · SL {price(trade.stop)} · TP {price(trade.target)}",
        f"Thời điểm nến {when(candle_time + 900)}",
    ]))

def notify_close(session, trade):
    audit = audit_of(session, trade)
    net = trade.net_pnl or 0
    realized = (trade.monitoring or {}).get("realized_net", 0)
    risk = (trade.monitoring or {}).get("risk")
    original = (trade.monitoring or {}).get("original_quantity") or trade.quantity
    r_text = f" · {net / (risk * original):+.2f}R" if risk else ""
    queue(session, f"{trade.entry_time:010d}-{trade.symbol}-{trade.id}-2-close", "\n".join([
        f"PAPER · ĐÓNG LỆNH #{trade.id} · {trade.symbol} {trade.side.upper()} · {EXIT_LABELS.get(trade.exit_reason, trade.exit_reason)}",
        f"Chiến thuật: {strategy_line(audit) if trade.origin == 'auto' else 'Thủ công'}",
        f"{price(trade.entry)} -> {price(trade.exit)} · Giữ {max(0, (trade.exit_time - trade.entry_time) // 60)} phút",
        f"PnL ròng {net:+,.2f} USDT{r_text} · {'LÃI' if net > 0 else 'LỖ' if net < 0 else 'HÒA'}",
        f"Phí đóng {trade.exit_fee:.2f} · Funding {trade.funding_cost:.2f}"
        + (f" · Đã chốt từng phần {realized:+,.2f}" if realized else ""),
        f"Kết thúc {when(trade.exit_time)}",
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
