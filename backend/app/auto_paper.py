"""Closed-bar auto paper execution; never routes orders to an exchange.

The wallet row is the common lock for commands, risk controls, decisions and fills.
Each bar is considered at most once, within 60s of its close. Restarts baseline the
cursor to the latest closed bar, so missed history cannot become present-day orders.
"""

import time
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .db import (
    AutoPaperDecision,
    AutoPaperState,
    BotState,
    Event,
    MarketState,
    PaperPendingOrder,
    PaperTrade,
    PaperWallet,
    Session,
)
from .feed import load_candles, save_market_signal
from .futures_entries import WAIT_BARS, wait
from .futures_exits import has_target
from .futures_learner import read_state, select_for_auto
from .futures_symbols import current_symbol, state_id
from .mexc import SOURCE, MarketDataError, validate_candles
from .notifications import notify_blocked, notify_open
from .paper import (
    MAX_ORDER_MARGIN_PERCENT,
    close_position,
    create_position,
    execution_price,
    fresh_quote,
    leverage_limits,
    lock_wallet,
    master,
    portfolio,
    portfolio_risk_limits,
    stop_risk_per_unit,
    validate_leverage,
)
from .strategy import Setup

router = APIRouter(prefix="/api/paper/auto", tags=["Auto Paper"])
MAX_SIGNAL_AGE = 60
JOURNAL_CANDLES = 96  # 24h of 15m candles shown in the Auto Paper journal


class AutoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    risk_percent: float = Field(default=1, gt=0, le=5)
    leverage: int | None = Field(default=None, ge=1, strict=True)
    max_daily_trades: int = Field(default=100, ge=1, le=100)
    daily_loss_percent: float = Field(default=3, gt=0, le=20)
    max_portfolio_risk_percent: float | None = Field(default=None, ge=0.5, le=50)


class AutoControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def equity(session, wallet):
    cash, unrealized, _ = portfolio(session)
    return cash + unrealized


def roll_day(session, wallet, state, now):
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    account = session.get(AutoPaperState, 1)
    if account.day != day:
        account.day = day
        account.day_start_equity = equity(session, wallet)
    state.day = account.day
    state.day_start_equity = account.day_start_equity


def daily_limits(session, wallet, state, now):
    midnight = int(now) // 86400 * 86400
    count = session.scalar(
        select(func.count())
        .select_from(PaperTrade)
        .where(PaperTrade.symbol == current_symbol(), PaperTrade.origin == "auto", PaperTrade.entry_time >= midnight)
    )
    baseline = session.get(AutoPaperState, 1).day_start_equity
    loss = max(0, baseline - equity(session, wallet))
    percent = loss / baseline * 100 if baseline > 0 else 100
    return count, loss, percent


def limit_reason(session, wallet, state, now):
    count, _, percent = daily_limits(session, wallet, state, now)
    if master(session).balance <= 0:
        return "Ví ảo không còn số dư khả dụng"
    cap = session.get(AutoPaperState, 1).daily_loss_percent
    if percent >= cap:
        return f"Đạt giới hạn lỗ ngày ví chung {cap:g}% (equity, UTC)"
    if count >= state.max_daily_trades:
        return f"Đạt giới hạn {state.max_daily_trades} lệnh auto trong ngày UTC"
    return None


def market_ready(session, now):
    market = session.get(MarketState, state_id())
    if market.source != SOURCE:
        raise HTTPException(409, "Chọn nguồn MEXC Futures trước khi bật Auto Paper")
    if (
        market.error
        or not market.last_sync
        or now - market.last_sync.replace(tzinfo=timezone.utc).timestamp() > 60
    ):
        raise HTTPException(409, "Chờ dữ liệu nến MEXC đồng bộ; nguồn nến đang chậm")
    candles = load_candles(session, limit=21, until=int(now) // 900 * 900)
    if len(candles) != 21 or candles[-1].time != int(now) // 900 * 900 - 900:
        raise HTTPException(409, "Chờ đủ 21 nến MEXC đã đóng liên tục")
    try:
        validate_candles(candles)
    except MarketDataError as exc:
        raise HTTPException(409, str(exc)) from exc
    return candles


def set_status(session, state, message):
    if state.status != message:
        state.status = message
        session.add(Event(kind="auto_paper", message=message))


def note_missed(session, state, now, reason):
    """Record why the latest closed candle went undecided once its entry window has passed."""
    candle_time = int(now) // 900 * 900 - 900
    if now - (candle_time + 900) <= MAX_SIGNAL_AGE:
        return
    if state.last_candle is not None and state.last_candle >= candle_time:
        return
    if session.get(AutoPaperDecision, (current_symbol(), candle_time)):
        return
    record_decision(session, state, candle_time, "missed", reason)


def blocked(session, state, now, reason):
    set_status(session, state, reason)
    note_missed(session, state, now, reason)


def record_decision(
    session, state, candle_time, action, reason, setup=None, trade=None, audit=None
):
    state.last_candle = candle_time
    session.add(
        AutoPaperDecision(
            symbol=current_symbol(),
            candle_time=candle_time,
            action=action,
            reason=reason,
            setup=asdict(setup) if setup else None,
            trade_id=trade.id if trade else None,
            parameters={
                "risk_percent": state.risk_percent,
                "daily_loss_percent": state.daily_loss_percent,
                "max_daily_trades": state.max_daily_trades,
                **({"audit": audit} if audit else {}),
            },
        )
    )
    set_status(session, state, reason)
    if action == "opened" and trade is not None:
        notify_open(session, trade, audit, master(session).balance,
                    (audit or {}).get("reason"))


def size_order(session, wallet, state, now, side, entry, stop):
    """Return notional, risk budget and optional blocking reason for either entry type."""
    _, daily_loss, _ = daily_limits(session, wallet, state, now)
    remaining_budget = max(
        0, state.day_start_equity * session.get(AutoPaperState, 1).daily_loss_percent / 100 - daily_loss
    )
    capital, used_risk = portfolio_risk_limits(session)
    cap = session.get(AutoPaperState, 1).max_portfolio_risk_percent
    headroom = float("inf") if cap is None else max(0.0, capital * cap / 100 - used_risk)
    risk_cash = min(max(0, equity(session, wallet)) * state.risk_percent / 100, remaining_budget, headroom)
    if risk_cash <= 0:
        if headroom <= 0:
            return None, None, ("risk_cap", f"Tổng rủi ro SL ({used_risk:.2f} USDT) chạm trần {cap:g}% ví chung")
        if remaining_budget <= 0:
            return None, None, ("daily_loss", "Hết ngân sách lỗ ngày của ví chung")
        return None, None, (None, "Equity ví chung không còn để tính rủi ro")
    cash, _, margin = portfolio(session)
    free = cash - margin
    fee_rate = wallet.fee_bps / 10000
    max_order_notional = capital * (MAX_ORDER_MARGIN_PERCENT / 100) * state.leverage
    unit_risk = stop_risk_per_unit(side, entry, stop, wallet.fee_bps, wallet.slippage_bps)
    return min(risk_cash / unit_risk * entry, free / (1 / state.leverage + fee_rate), max_order_notional), risk_cash, None

def place_pending(session, state, wallet, candle_time, now, setup, strat_name, learning, decision):
    """Queue a limit/retest entry for the closed bar; fills happen in sync_pending_orders."""
    level = decision.get("level")
    long = setup.side == "long"
    if level is None or not (setup.stop < level < setup.target if long else setup.target < level < setup.stop):
        record_decision(session, state, candle_time, "skipped", "Không có mức chờ hợp lệ", setup)
        return
    if session.scalar(select(PaperPendingOrder.id).where(
            PaperPendingOrder.symbol == current_symbol(), PaperPendingOrder.status == "pending")):
        record_decision(session, state, candle_time, "skipped", "Đang có lệnh chờ của cặp này", setup)
        return
    genome = learning["candidates"][strat_name]["genome"]
    policy = genome.get("entry_policy", "market")
    first = candle_time + 900
    snapshot = {
        "strategy": strat_name, "reason": setup.reason, "exit_policy": genome.get("exit_policy", "fixed"),
        "exit_params": genome.get("exit_params"), "exit_rule": genome.get("rule"),
        "audit": {
            "strategy": strat_name, "reason": setup.reason, "signal_time": candle_time,
            "signal_close": setup.entry, "selector": "futures_learner", "paper_only": True,
            "auto_mode": learning["auto_mode"], "selection_reason": decision.get("selection_reason"),
            "regime": decision.get("regime"), "risk_percent": state.risk_percent,
            "leverage": state.leverage, "margin_mode": "cross",
            "strategy_source": "entry_snapshot", "initial_stop": setup.stop, "initial_target": setup.target,
            "exit_policy": genome.get("exit_policy", "fixed"), "entry_policy": policy,
            "limit_level": level, "genome": genome,
            "generation": learning["candidates"][strat_name].get("generation", 0),
            "parents": learning["candidates"][strat_name].get("parents", []),
        },
    }
    session.add(PaperPendingOrder(
        symbol=current_symbol(), signal_time=candle_time, created_at=int(now), next_bar=first,
        expires_at=first + WAIT_BARS * 900, side=setup.side, entry_policy=policy, level=level,
        stop=setup.stop, target=setup.target, status="pending", snapshot=snapshot))
    record_decision(
        session, state, candle_time, "pending",
        f"LỆNH CHỜ {setup.side.upper()} · {strat_name} · {policy} @ {level:g}", setup,
        audit=snapshot["audit"])

def close_pending(session, order, status, reason):
    order.status, order.reason = status, reason
    decision = session.get(AutoPaperDecision, (order.symbol, order.signal_time))
    if decision and decision.action == "pending":
        decision.action, decision.reason = "skipped", f"Lệnh chờ {status}: {reason}"[:500]
    session.add(Event(kind="paper", message=f"LỆNH CHỜ #{order.id} {order.symbol} {order.side.upper()}: {reason}"))

def fill_pending(session, wallet, state, order, bar, fill_bar, now, limit):
    snap = order.snapshot
    setup = Setup(order.side, order.level, order.stop, order.target, snap["reason"])
    entry = order.level if limit else execution_price(wallet, order.side, opening=True)
    notional, risk_cash, blocked_by = size_order(session, wallet, state, now, order.side, entry, order.stop)
    if blocked_by:
        close_pending(session, order, "cancelled", blocked_by[1])
        if blocked_by[0]:
            notify_blocked(session, order.symbol, order.signal_time, setup, blocked_by[0], blocked_by[1],
                           snap["strategy"])
        return
    try:
        # ponytail: limit fills at the level with taker fee, like the replay; no maker rebate/queue model.
        trade = create_position(
            session, wallet, order.side, notional, order.stop, order.target, origin="auto",
            signal_time=order.signal_time, exit_policy=snap["exit_policy"], leverage=state.leverage,
            exit_params=snap["exit_params"], exit_rule=snap["exit_rule"],
            fill_price=order.level if limit else None)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise
        close_pending(session, order, "cancelled", exc.detail)
        return
    if limit:
        trade.entry_time = bar.time
        trade.monitoring = {**trade.monitoring, "next_bar": bar.time + 900}
    trade.monitoring = {**trade.monitoring, "entry_mode": order.entry_policy, "pending_id": order.id}
    order.status, order.trade_id = "filled", trade.id
    decision = session.get(AutoPaperDecision, (order.symbol, order.signal_time))
    audit = {**snap["audit"], "entry_fill": trade.entry,
             "risk_budget": risk_cash}
    if decision:
        decision.action, decision.trade_id = "opened", trade.id
        decision.reason = f"AUTO PAPER #{trade.id}: {order.side.upper()} · {snap['strategy']} · {order.entry_policy} @ {trade.entry:g}"
        decision.parameters = {**decision.parameters, "audit": audit}
    notify_open(session, trade, audit, master(session).balance, snap["reason"])
    if limit:
        direction = 1 if order.side == "long" else -1
        stopped = bar.low <= order.stop if direction == 1 else bar.high >= order.stop
        targeted = has_target(trade.monitoring) and (
            fill_bar.high >= order.target if direction == 1 else fill_bar.low <= order.target)
        if stopped or targeted:
            gap = stopped and direction * (bar.open - order.stop) <= 0
            base = bar.open if gap else order.stop if stopped else order.target
            price = base * (1 - direction * wallet.slippage_bps / 10000)
            close_position(session, wallet, trade, "recovered_stop_loss" if stopped else "recovered_take_profit",
                           now, price=price, exit_time=bar.time if gap else bar.time + 899)

def sync_pending_orders(now=None):
    """Closed-bar fills for queued orders; same trade-through/stop-first rules as the entry replay."""
    with Session.begin() as session:
        wallet = lock_wallet(session)
        now = time.time() if now is None else now
        end = int(now) // 900 * 900
        orders = session.scalars(select(PaperPendingOrder).where(
            PaperPendingOrder.symbol == current_symbol(), PaperPendingOrder.status == "pending")).all()
        if not orders:
            return
        state = session.get(AutoPaperState, state_id())
        for order in orders:
            if not state or not state.enabled:
                close_pending(session, order, "cancelled", "Auto Paper đã tắt")
                continue
            try:
                if session.get(MarketState, state_id()).source != SOURCE:
                    close_pending(session, order, "cancelled", "Nguồn nến không còn là MEXC")
                    continue
                fresh_quote(wallet, now)
                portfolio(session, require_fresh=True, now=now)
                if wallet.funding_error:
                    raise HTTPException(409, "Funding chưa đồng bộ")
            except HTTPException:
                if end >= order.expires_at:
                    close_pending(session, order, "expired", "hết hạn khi dữ liệu chậm")
                continue
            roll_day(session, wallet, state, now)
            reason = limit_reason(session, wallet, state, now)
            if reason:
                close_pending(session, order, "cancelled", reason)
                continue
            first = order.expires_at - WAIT_BARS * 900
            sample = {"side": order.side, "level": order.level, "stop": order.stop, "target": order.target,
                      "entry_policy": order.entry_policy, "entry_time": first, "first_entry_time": first}
            cursor = order.next_bar
            try:
                for bar in load_candles(session, since=order.next_bar, until=end):
                    if bar.time != order.next_bar:
                        break
                    filled = wait(sample, bar, [])
                    order.next_bar = bar.time + 900
                    if sample.get("status") == "cancelled":
                        close_pending(session, order, "expired" if sample["exit_reason"] == "expired" else "cancelled",
                                      sample["exit_reason"])
                        break
                    if sample.get("confirmed"):
                        if now - (bar.time + 900) > MAX_SIGNAL_AGE:
                            close_pending(session, order, "cancelled", "retest xác nhận quá 60 giây")
                        else:
                            fill_pending(session, wallet, state, order, bar, bar, now, limit=False)
                        break
                    if filled:
                        if now - (bar.time + 900) > MAX_SIGNAL_AGE:
                            close_pending(session, order, "cancelled", "bỏ khớp nến cũ quá 60 giây")
                        else:
                            fill_pending(session, wallet, state, order, bar, filled[0], now, limit=True)
                        break
            except HTTPException:
                order.next_bar = cursor
                continue
            if order.status == "pending" and end >= order.expires_at:
                close_pending(session, order, "expired", "hết 4 nến chờ")

@router.post("/pending/{order_id}/cancel")
def cancel_pending(order_id: int):
    with Session.begin() as session:
        lock_wallet(session)
        order = session.get(PaperPendingOrder, order_id)
        if order is None or order.symbol != current_symbol():
            raise HTTPException(404, "Không tìm thấy lệnh chờ")
        if order.status == "pending":
            close_pending(session, order, "cancelled", "Hủy thủ công")
        return {"order_id": order.id, "status": order.status}

def baseline_auto_on_startup():
    """Run once per worker start, before any automatic entries are evaluated."""
    with Session.begin() as session:
        lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        if state and state.enabled:
            state.last_candle = int(time.time()) // 900 * 900 - 900
            set_status(session, state, "Worker khởi động lại; bỏ qua nến cũ, chờ nến mới")


def auto_tick(now=None):
    try:
        sync_pending_orders(now)
        _auto_tick(now)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        with Session.begin() as session:
            lock_wallet(session)
            state = session.get(AutoPaperState, state_id())
            if state and state.enabled:
                set_status(session, state, exc.detail)

def _auto_tick(now=None):
    with Session.begin() as session:
        wallet = lock_wallet(session)
        now = time.time() if now is None else now
        state = session.get(AutoPaperState, state_id())
        if state is None:
            return
        if not state.enabled:
            note_missed(session, state, now, "Auto Paper đang tắt")
            return
        try:
            quote = fresh_quote(wallet, now)
            portfolio(session, require_fresh=True, now=now)
        except HTTPException as exc:
            blocked(session, state, now, exc.detail)
            return
        roll_day(session, wallet, state, now)
        reason = limit_reason(session, wallet, state, now)
        if reason:
            state.enabled = False
            blocked(session, state, now, reason + "; đã dừng vào lệnh mới, SL/TP vẫn chạy")
            return
        candle_time = int(now) // 900 * 900 - 900
        if state.last_candle is not None and state.last_candle >= candle_time:
            return
        # A crash/retry cannot create a second decision or order for the same bar.
        if session.get(AutoPaperDecision, (current_symbol(), candle_time)):
            state.last_candle = candle_time
            return
        if now - (candle_time + 900) > MAX_SIGNAL_AGE:
            record_decision(
                session,
                state,
                candle_time,
                "skipped",
                "Bỏ qua nến quá 60 giây sau đóng; không vào lệnh bù",
            )
            return
        try:
            candles = market_ready(session, now)
        except HTTPException as exc:
            set_status(session, state, exc.detail)
            return
        if quote["time"] < candle_time + 900:
            set_status(session, state, "Đang chờ quote mới sau thời điểm đóng nến")
            return
        if wallet.funding_error:
            set_status(session, state, "Funding chưa đồng bộ; tạm dừng vào lệnh mới")
            return
        try:
            _, setup, strat_name = select_for_auto(session, candle_time, now)
        except HTTPException as exc:
            set_status(session, state, exc.detail)
            return
        if setup is None:
            record_decision(session, state, candle_time, "no_setup", "Bot học chưa có tín hiệu đủ điều kiện cho chế độ đang chọn")
            return
        learning = read_state(session, int(now))
        learning_decision = learning["last_decision"] or {}
        save_market_signal(session, candle_time, setup)
        market_entry = learning["candidates"][strat_name]["genome"].get("entry_policy", "market") == "market"
        if not market_entry:
            place_pending(session, state, wallet, candle_time, now, setup, strat_name,
                          learning, learning_decision)
            return
        entry = execution_price(wallet, setup.side, opening=True)
        distance = abs(entry - setup.stop)
        if distance <= 0:
            record_decision(
                session, state, candle_time, "skipped", "Khoảng cách SL không hợp lệ", setup
            )
            return
        # ponytail: no position cap; bounded only by available cash, risk budget and daily limits.
        requested_notional, risk_cash, blocked_by = size_order(session, wallet, state, now, setup.side, entry, setup.stop)
        if blocked_by:
            kind, reason = blocked_by
            record_decision(session, state, candle_time, "skipped", reason, setup)
            if kind:
                notify_blocked(session, current_symbol(), candle_time, setup, kind, reason, strat_name)
            return
        try:
            trade = create_position(
                session,
                wallet,
                setup.side,
                requested_notional,
                setup.stop,
                setup.target,
                origin="auto",
                signal_time=candle_time,
                exit_policy=learning["candidates"][strat_name]["genome"].get("exit_policy", "fixed"),
                leverage=state.leverage,
                exit_params=learning["candidates"][strat_name]["genome"].get("exit_params"),
                exit_rule=learning["candidates"][strat_name]["genome"].get("rule"),
            )
        except HTTPException as exc:
            record_decision(session, state, candle_time, "skipped", exc.detail, setup)
            kind = ("margin" if exc.detail.startswith(("Không đủ ký quỹ", "Ký quỹ mỗi lệnh"))
                    else "risk_cap" if exc.detail.startswith("Tổng rủi ro") else None)
            if kind:
                notify_blocked(session, current_symbol(), candle_time, setup, kind, exc.detail, strat_name)
            return
        record_decision(
            session,
            state,
            candle_time,
            "opened",
            f"AUTO PAPER #{trade.id}: {setup.side.upper()} · {strat_name} · {setup.reason}"
            + (" · HỌC THỬ, CHƯA KIỂM CHỨNG" if learning and learning["auto_mode"] == "paper_learning" else ""),
            setup,
            trade,
            audit={
                "strategy": strat_name,
                "reason": setup.reason,
                "signal_time": candle_time,
                "signal_close": candles[-1].close,
                "selector": "futures_learner", "paper_only": True,
                "auto_mode": learning["auto_mode"],
                "selection_reason": learning_decision.get("selection_reason"),
                "regime": learning_decision.get("regime"),
                "risk_percent": state.risk_percent,
                "leverage": state.leverage,
                "margin_mode": "cross",
                "risk_budget": risk_cash,
                "strategy_source": "entry_snapshot",
                "initial_stop": setup.stop,
                "initial_target": setup.target,
                "exit_policy": (learning["candidates"][strat_name]["genome"].get("exit_policy", "fixed")
                                if learning else "fixed"),
                "genome": learning["candidates"][strat_name]["genome"] if learning and strat_name in learning["candidates"] else None,
                "generation": learning["candidates"][strat_name].get("generation", 0) if learning and strat_name in learning["candidates"] else 0,
                "parents": learning["candidates"][strat_name].get("parents", []) if learning and strat_name in learning["candidates"] else [],
            },
        )


def state_response(session, wallet, state, now, learning=None):
    account = session.get(AutoPaperState, 1)
    learning = learning or read_state(session, int(now))
    count, loss, percent = daily_limits(session, wallet, state, now)
    # Journal covers the last 24h of closed 15m candles; quiet "no_setup" rows are only counted.
    last_closed = int(now) // 900 * 900 - 900
    since = last_closed - (JOURNAL_CANDLES - 1) * 900
    decisions = session.scalars(
        select(AutoPaperDecision)
        .where(AutoPaperDecision.symbol == current_symbol(), AutoPaperDecision.candle_time >= since)
        .order_by(AutoPaperDecision.candle_time.desc())
    ).all()
    counts = {action: sum(d.action == action for d in decisions)
              for action in ("opened", "skipped", "no_setup", "missed", "pending")}
    # Only count candles since this pair's first record, so a new pair or DB isn't shown as missed.
    first = session.scalar(select(func.min(AutoPaperDecision.candle_time))
                           .where(AutoPaperDecision.symbol == current_symbol()))
    start = last_closed + 900 if first is None else max(since, first)
    tracked = (last_closed - start) // 900 + 1
    beat = session.get(BotState, 1).heartbeat
    worker_online = bool(beat and now - beat.replace(tzinfo=timezone.utc).timestamp() < 30)
    decision = learning["last_decision"]
    if decision:
        decision = {**decision, "strategies": [
            {**row, "genome": learning["candidates"].get(row["strategy"], {}).get("genome")}
            for row in decision.get("strategies", [])
        ]}
    return dict(
        enabled=state.enabled,
        auto_mode=learning["auto_mode"],
        learner_enabled=learning["enabled"],
        learner_error=learning["error"],
        learner_decision=decision,
        risk_percent=state.risk_percent,
        leverage=state.leverage,
        min_leverage=leverage_limits(wallet)[0],
        max_leverage=leverage_limits(wallet)[1],
        margin_mode="cross",
        max_daily_trades=state.max_daily_trades,
        daily_loss_percent=account.daily_loss_percent,
        max_portfolio_risk_percent=account.max_portfolio_risk_percent,
        portfolio_capital=round(portfolio_risk_limits(session)[0], 2),
        portfolio_used_risk=round(portfolio_risk_limits(session)[1], 2),
        last_candle=state.last_candle,
        day=account.day,
        day_start_equity=account.day_start_equity,
        daily_trades=count,
        daily_loss=loss,
        daily_loss_used_percent=percent,
        status=state.status,
        worker_online=worker_online,
        next_close_time=(int(now) // 900 + 1) * 900,
        coverage=dict(window_candles=JOURNAL_CANDLES, end_time=last_closed,
                      start_time=start, tracked_candles=tracked,
                      evaluated=counts["opened"] + counts["skipped"] + counts["no_setup"] + counts["pending"],
                      unrecorded=tracked - len(decisions), **counts),
        # Every recorded candle of the window; gaps mean the worker never ticked that candle.
        timeline=[
            dict(
                candle_time=d.candle_time,
                action=d.action,
                reason=d.reason,
                trade_id=d.trade_id,
                side=(d.setup or {}).get("side"),
            )
            for d in decisions
        ],
    )


def auto_snapshot(learning=None):
    now = time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    with Session() as session:
        wallet = session.get(PaperWallet, state_id())
        state = session.get(AutoPaperState, state_id())
        if wallet is None or state is None:
            raise HTTPException(503, "Run migrations")
        if session.get(AutoPaperState, 1).day == today and state.day == today:
            # Đường đọc thường xuyên: không lock ví, không ghi.
            return state_response(session, wallet, state, now, learning)
    # Lần đầu của ngày UTC mới: lock một lần để chốt baseline equity (monitoring, không bật trade).
    with Session.begin() as session:
        wallet = lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        now = time.time()
        try:
            fresh_quote(wallet, now)
            portfolio(session, require_fresh=True, now=now)
        except HTTPException:
            pass
        else:
            roll_day(session, wallet, state, now)
        return state_response(session, wallet, state, now, learning)

@router.get("")
def get_auto_state():
    return auto_snapshot()

@router.post("/config")
def configure_auto(payload: AutoConfig):
    with Session.begin() as session:
        wallet = lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        if state is None:
            raise HTTPException(503, "Run migrations")
        if state.enabled:
            raise HTTPException(409, "Dừng Auto Paper trước khi đổi thông số")
        validate_leverage(wallet, state.leverage if payload.leverage is None else payload.leverage)
        account = session.get(AutoPaperState, 1)
        if payload.daily_loss_percent != account.daily_loss_percent or payload.max_portfolio_risk_percent != account.max_portfolio_risk_percent:
            if session.scalar(select(AutoPaperState.id).where(AutoPaperState.enabled.is_(True)).limit(1)):
                raise HTTPException(409, "Dừng Auto Paper trên mọi cặp trước khi đổi cấu hình rủi ro ví chung")
            for pair in session.scalars(select(AutoPaperState)):
                pair.daily_loss_percent = payload.daily_loss_percent
                pair.max_portfolio_risk_percent = payload.max_portfolio_risk_percent
        for key, value in payload.model_dump(exclude_none=True).items():
            setattr(state, key, value)
        session.add(Event(kind="auto_paper", message="Đã lưu thông số Auto Paper"))
        return state_response(session, wallet, state, time.time())


@router.post("/control")
def control_auto(payload: AutoControl):
    with Session.begin() as session:
        wallet = lock_wallet(session)
        now = time.time()
        state = session.get(AutoPaperState, state_id())
        if state is None:
            raise HTTPException(503, "Run migrations")
        if payload.enabled and not state.enabled:
            validate_leverage(wallet, state.leverage)
            fresh_quote(wallet, now)
            portfolio(session, require_fresh=True, now=now)
            market_ready(session, now)
            if not wallet.contract or wallet.funding_error:
                raise HTTPException(409, "Chờ metadata hợp đồng và funding đồng bộ")
            beat = session.get(BotState, 1).heartbeat
            if not beat or now - beat.replace(tzinfo=timezone.utc).timestamp() >= 30:
                raise HTTPException(409, "Worker đang offline; chưa thể bật Auto Paper")
            roll_day(session, wallet, state, now)
            reason = limit_reason(session, wallet, state, now)
            if reason:
                raise HTTPException(409, reason)
            state.last_candle = int(now) // 900 * 900 - 900
            state.enabled = True
            set_status(session, state, "Auto Paper đã bật; chờ nến 15m mới đóng")
        elif not payload.enabled:
            state.enabled = False
            for order in session.scalars(select(PaperPendingOrder).where(
                PaperPendingOrder.symbol == current_symbol(), PaperPendingOrder.status == "pending")):
                close_pending(session, order, "cancelled", "Auto Paper đã tắt")
            set_status(
                session, state, "Auto Paper đã dừng vào lệnh mới; vị thế hiện có vẫn giữ SL/TP"
            )
        return state_response(session, wallet, state, now)


def report_auto_failure():
    """Unexpected processing errors fail closed; quote/SLTP loop keeps running."""
    with Session.begin() as session:
        lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        if state:
            state.enabled = False
            set_status(
                session,
                state,
                "Auto Paper gặp lỗi xử lý; đã dừng vào lệnh mới. Xem log worker; SL/TP vẫn chạy",
            )
