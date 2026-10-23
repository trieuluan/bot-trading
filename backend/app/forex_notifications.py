"""Transactional notifications for Exness Forex demo executions."""

import hashlib
import math
import os

from .notifications import esc, price, queue, signed_price, when


def enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None

def shown(value, signed=False):
    parsed = number(value)
    return (signed_price(parsed) if signed else price(parsed)) if parsed is not None else "chưa xác minh"

def event_key(now, trade, pid, event):
    identity = f"{trade.account}:{getattr(trade, 'symbol', 'XAUUSD')}:{trade.signal_time}:{pid}:{event}"
    return f"{now:010d}-forex-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def notify_reconciled(session, trade, previous, updated, position, protection, deals, now):
    """Enqueue Telegram alerts inside the reconcile transaction without extra locks."""
    symbol = esc(getattr(trade, "symbol", None) or "XAUUSD")
    stamp = trade.signal_time
    pid = updated.get("position_id")
    signal = trade.snapshot.get("signal", {})
    body = updated.get("body", {})
    if protection and position:
        updated["broker_stop"] = protection.get("stop_loss_price")
        updated["broker_target"] = protection.get("take_profit_price")
    if not enabled() or not pid:
        return
    matched_deals = sorted((deal for deal in deals if deal.position_id == pid and deal.event_time <= now),
                           key=lambda deal: (deal.event_time, deal.deal_id))
    opens = [deal.snapshot for deal in matched_deals if deal.snapshot.get("type") == "open"]
    direction = (position or {}).get("direction") or (opens[0].get("direction") if opens else None) or body.get("side") or signal.get("side")
    side = {"buy": "BUY", "long": "BUY", "sell": "SELL", "short": "SELL"}.get(direction, "chưa xác minh")
    role = updated.get("role", "runner")
    role_vn = {"tp1": "Chốt 1 (TP1)", "partial1": "Chốt phần 1", "partial2": "Chốt phần 2",
               "runner": "Gồng chính (Runner)"}.get(role, str(role))

    # 1. Position confirmed OPEN
    if previous.get("status") != "open" and updated.get("status") == "open":
        open_price = (position or {}).get("open_price")
        stop = (protection or {}).get("stop_loss_price")
        target = (protection or {}).get("take_profit_price")
        icon = "🟢" if side == "BUY" else "🔴"
        vol = (position or {}).get("volume")
        strat = signal.get("strategy", "HTF V2")
        branch_text = ""
        if signal.get("entry_branch"):
            branch_name = {"limit": "Limit giữa", "stop": "Stop phá nến", "market": "Market"}.get(signal["entry_branch"], "Chưa xác minh")
            branch_text = f"\n🎯 <b>Nhánh kích hoạt:</b> {branch_name} (bot canh giá)"

        queue(
            session,
            event_key(now, trade, pid, "open"),
            "\n".join([
                f"{icon} <b>MỞ VỊ THẾ FOREX · {side} {symbol}</b>",
                f"🌲 <b>Chiến thuật:</b> <code>{esc(strat)}</code>",
                *( [branch_text.strip()] if branch_text else [] ),
                "🧪 Exness DEMO · khớp sàn đã đối soát",
                f"🎯 <b>Vai trò:</b> {esc(role_vn)} · Khối lượng: <code>{shown(vol)} lot</code>",
                "",
                f"▫️ Giá khớp sàn: <code>{shown(open_price)}</code>",
                f"🛑 SL: <code>{shown(stop)}</code> · 🎯 TP: <code>{shown(target)}</code>",
                f"🕒 Nến xác nhận: {when(stamp + 900)}",
            ]),
        )

    # 2. SL update confirmed
    old_mgmt = previous.get("management", {})
    new_mgmt = updated.get("management", {})
    stop_changed = previous.get("broker_stop") is not None and number(previous["broker_stop"]) != number(updated.get("broker_stop"))
    newly_confirmed = new_mgmt.get("status") == "confirmed" and old_mgmt.get("status") != "confirmed"
    if position and protection and (stop_changed or newly_confirmed):
        new_stop = protection.get("stop_loss_price")
        old_stop = previous.get("broker_stop", new_mgmt.get("previous_stop"))
        stage = new_mgmt.get("stage", "adaptive_stop")
        diff = number(new_stop) - number(old_stop) if number(new_stop) is not None and number(old_stop) is not None else None
        stage_text = {
            "breakeven": "Dời về Hòa Vốn (BE)",
            "trailing": "Trailing Stop theo nến",
            "adaptive_stop": "Trailing Stop thích ứng",
        }.get(stage, f"Dời SL ({stage})")
        entry, initial = number(position.get("open_price")), number(body.get("stop_loss_price"))
        risk = abs(entry - initial) if entry is not None and initial is not None else 0
        sign = 1 if side == "BUY" else -1
        risk_line = []
        if risk and side in ("BUY", "SELL") and number(old_stop) is not None and number(new_stop) is not None:
            old_r = sign * (number(old_stop) - entry) / risk
            new_r = sign * (number(new_stop) - entry) / risk
            risk_line = [f"R tại SL (theo giá): <b>{old_r:+.2f}R → {new_r:+.2f}R</b> · chưa phí/swap"]

        queue(
            session,
            event_key(now, trade, pid, f"sl:{new_mgmt.get('key')}:{old_stop}:{new_stop}"),
            "\n".join([
                f"🛡️ <b>CẬP NHẬT DỜI SL FOREX · {symbol}</b>",
                f"📌 Vị thế #{esc(pid)} · {esc(stage_text)} · {esc(signal.get('management_frame', ''))}",
                "",
                f"🛑 SL cũ: <code>{shown(old_stop)}</code> ➔ Mức mới: <code>{shown(new_stop)}</code>" + (f" (Δ giá {shown(diff, signed=True)})" if diff is not None else ""),
                *risk_line,
                f"🕒 Lúc: {when(now)}",
            ]),
        )

    # 2b. TP update confirmed
    target_changed = previous.get("broker_target") is not None and number(previous["broker_target"]) != number(updated.get("broker_target"))
    if position and protection and target_changed:
        new_target = protection.get("take_profit_price")
        old_target = previous.get("broker_target")
        diff_target = number(new_target) - number(old_target) if number(new_target) is not None and number(old_target) is not None else None
        entry, initial = number(position.get("open_price")), number(body.get("stop_loss_price"))
        risk = abs(entry - initial) if entry is not None and initial is not None else 0
        sign = 1 if side == "BUY" else -1
        target_risk_line = []
        if risk and side in ("BUY", "SELL") and number(old_target) is not None and number(new_target) is not None:
            old_tp_r = sign * (number(old_target) - entry) / risk
            new_tp_r = sign * (number(new_target) - entry) / risk
            delta_tp_r = new_tp_r - old_tp_r
            target_risk_line = [f"R tại TP (theo giá): <b>{old_tp_r:+.2f}R → {new_tp_r:+.2f}R (Δ {delta_tp_r:+.2f}R)</b> · chưa phí/swap"]

        queue(
            session,
            event_key(now, trade, pid, f"tp:{old_target}:{new_target}"),
            "\n".join([
                f"🎯 <b>CẬP NHẬT DỜI TP FOREX · {symbol}</b>",
                f"📌 Vị thế #{esc(pid)} · {esc(role_vn)} · {esc(signal.get('management_frame', ''))}",
                "",
                f"🎯 TP cũ: <code>{shown(old_target)}</code> ➔ Mức mới: <code>{shown(new_target)}</code>" + (f" (Δ giá {shown(diff_target, signed=True)})" if diff_target is not None else ""),
                *target_risk_line,
                f"🕒 Lúc: {when(now)}",
            ]),
        )

    # 3. Position confirmed CLOSED
    if previous.get("status") != "closed" and updated.get("status") == "closed":
        open_deals = opens
        close_deals = [deal.snapshot for deal in matched_deals if deal.snapshot.get("type") == "close"]
        open_p = open_deals[0].get("price") if open_deals else None
        close_p = close_deals[-1].get("price") if close_deals else None
        profits = [number(deal.get("profit")) for deal in close_deals]
        profit = sum(profits) if profits and all(value is not None for value in profits) else None
        vol = open_deals[0].get("volume") if open_deals else None
        tp_filled = updated.get("take_profit_filled")
        reason = "take_profit" if tp_filled else updated.get("close_reason", "closed_by_broker")
        pnl_icon = "✅" if profit is not None and profit > 0 else "🛑" if profit is not None and profit < 0 else "⚪"
        reason_vn = {
            "take_profit": "Chạm Take Profit (TP)",
            "stop_loss": "Chạm Stop Loss (SL)",
            "session_close": "Đóng cuối phiên",
            "early_risk_exit": "Thoát sớm do cấu trúc suy yếu",
            "manual": "Đóng thủ công",
            "closed_by_broker": "Sàn đã đóng; chưa xác minh nguyên nhân",
            "missing_protection": "Thoát bảo vệ do thiếu SL/TP",
        }.get(reason, reason or "Khớp lệnh thoát trên sàn")

        queue(
            session,
            event_key(now, trade, pid, "close"),
            "\n".join([
                f"{pnl_icon} <b>ĐÓNG VỊ THẾ FOREX · {side} {symbol}</b>",
                f"📌 Vị thế #{esc(pid)} · {esc(role_vn)} · Khối lượng: <code>{shown(vol)} lot</code>",
                f"💡 Lý do: <b>{esc(reason_vn)}</b>",
                "",
                f"▫️ Giá vào: <code>{shown(open_p)}</code> ➔ Giá ra: <code>{shown(close_p)}</code>",
                f"💰 Profit deal sàn: <b>{shown(profit, signed=True)} USD</b> · chưa xác minh phí/swap, không phải PnL ròng",
                f"🕒 Lúc: {when(now)}",
            ]),
        )
