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

from .db import AutoPaperDecision, AutoPaperState, BotState, Event, MarketState, PaperTrade, Session
from .futures_symbols import current_symbol, market_symbol, state_id
from .feed import load_candles, save_market_signal
from .futures_learner import read_state, select_for_auto
from .mexc import SOURCE, MarketDataError, validate_candles
from .paper import (
    MAX_ORDER_MARGIN_PERCENT,
    MAX_PORTFOLIO_RISK_PERCENT,
    create_position,
    execution_price,
    fresh_quote,
    leverage_limits,
    lock_wallet,
    master,
    open_positions,
    portfolio,
    portfolio_risk_limits,
    stop_risk_per_unit,
    validate_leverage,
)
from .strategy import breakout

router = APIRouter(prefix="/api/paper/auto", tags=["Auto Paper"])
MAX_SIGNAL_AGE = 60


class AutoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    risk_percent: float = Field(default=1, gt=0, le=5)
    leverage: int | None = Field(default=None, ge=1, strict=True)
    max_daily_trades: int = Field(default=100, ge=1, le=100)
    daily_loss_percent: float = Field(default=3, gt=0, le=20)


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


def baseline_auto_on_startup():
    """Run once per worker start, before any automatic entries are evaluated."""
    with Session.begin() as session:
        lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        if state and state.enabled:
            state.last_candle = int(time.time()) // 900 * 900 - 900
            set_status(session, state, "Worker khởi động lại; bỏ qua nến cũ, chờ nến mới")


def auto_tick(now=None):
    now = time.time() if now is None else now
    with Session.begin() as session:
        wallet = lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        if state is None or not state.enabled:
            return
        try:
            quote = fresh_quote(wallet, now)
            portfolio(session, require_fresh=True, now=now)
        except HTTPException as exc:
            set_status(session, state, exc.detail)
            return
        roll_day(session, wallet, state, now)
        positions = open_positions(session)
        if any((p.monitoring or {}).get("status") == "history_pending" for p in positions):
            set_status(session, state, "Vị thế đang thiếu dữ liệu đối soát SL/TP; chờ đồng bộ nến")
            return
        reason = limit_reason(session, wallet, state, now)
        if reason:
            state.enabled = False
            set_status(session, state, reason + "; đã dừng vào lệnh mới, SL/TP vẫn chạy")
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
            used_learner, setup, strat_name = select_for_auto(session, candle_time, now)
        except HTTPException as exc:
            set_status(session, state, exc.detail)
            return
        if not used_learner:
            setup = breakout(candles, allow_short=True)
            strat_name = "breakout_20_v1"
            if setup is None:
                record_decision(
                    session, state, candle_time, "no_setup", "Nến vừa đóng chưa có breakout hợp lệ"
                )
                return
        if setup is None:
            record_decision(session, state, candle_time, "no_setup", "Bot học chưa có tín hiệu đủ điều kiện cho chế độ đang chọn")
            return
        learning = read_state(session, int(now)) if used_learner else None
        learning_decision = (learning["last_decision"] or {}) if learning else {}
        save_market_signal(session, candle_time, setup)
# ponytail: no position cap; bounded only by available cash, risk budget and daily limits.
        entry = execution_price(wallet, setup.side, opening=True)
        distance = abs(entry - setup.stop)
        if distance <= 0:
            record_decision(
                session, state, candle_time, "skipped", "Khoảng cách SL không hợp lệ", setup
            )
            return
        _, daily_loss, _ = daily_limits(session, wallet, state, now)
        remaining_budget = max(
            0, state.day_start_equity * session.get(AutoPaperState, 1).daily_loss_percent / 100 - daily_loss
        )
        capital, used_risk = portfolio_risk_limits(session)
        headroom = max(0.0, capital * MAX_PORTFOLIO_RISK_PERCENT / 100 - used_risk)
        risk_cash = min(
            max(0, equity(session, wallet)) * state.risk_percent / 100, remaining_budget, headroom
        )
        if risk_cash <= 0:
            record_decision(
                session, state, candle_time, "skipped",
                f"Tổng rủi ro SL ({used_risk:.2f} USDT) chạm trần {MAX_PORTFOLIO_RISK_PERCENT}% ví chung",
                setup,
            )
            return
        cash, _, margin = portfolio(session)
        free = cash - margin
        fee_rate = wallet.fee_bps / 10000
        max_order_notional = capital * (MAX_ORDER_MARGIN_PERCENT / 100) * state.leverage
        unit_risk = stop_risk_per_unit(setup.side, entry, setup.stop, wallet.fee_bps, wallet.slippage_bps)
        requested_notional = min(
            risk_cash / unit_risk * entry,
            free / (1 / state.leverage + fee_rate),
            max_order_notional,
        )
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
                exit_policy=(learning["candidates"][strat_name]["genome"].get("exit_policy", "fixed")
                             if learning else "fixed"),
                leverage=state.leverage,
            )
        except HTTPException as exc:
            record_decision(session, state, candle_time, "skipped", exc.detail, setup)
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
                **({
                    "range_start": candles[-21].time,
                    "range_end": candles[-2].time,
                    "range_high": max(c.high for c in candles[-21:-1]),
                    "range_low": min(c.low for c in candles[-21:-1]),
                } if not used_learner else {
                    "selector": "futures_learner", "paper_only": True,
                    "auto_mode": learning["auto_mode"],
                    "selection_reason": learning_decision.get("selection_reason"),
                    "regime": learning_decision.get("regime"),
                }),
                "risk_percent": state.risk_percent,
                "leverage": state.leverage,
                "margin_mode": "cross",
                "risk_budget": risk_cash,
                "exit_policy": (learning["candidates"][strat_name]["genome"].get("exit_policy", "fixed")
                                if learning else "fixed"),
            },
        )


def state_response(session, wallet, state, now):
    account = session.get(AutoPaperState, 1)
    learning = read_state(session, int(now))
    count, loss, percent = daily_limits(session, wallet, state, now)
    decisions = session.scalars(
        select(AutoPaperDecision).where(AutoPaperDecision.symbol == current_symbol()).order_by(AutoPaperDecision.candle_time.desc()).limit(12)
    ).all()
    beat = session.get(BotState, 1).heartbeat
    worker_online = bool(beat and now - beat.replace(tzinfo=timezone.utc).timestamp() < 30)
    return dict(
        enabled=state.enabled,
        auto_mode=learning["auto_mode"],
        learner_enabled=learning["enabled"],
        learner_error=learning["error"],
        learner_decision=learning["last_decision"],
        risk_percent=state.risk_percent,
        leverage=state.leverage,
        min_leverage=leverage_limits(wallet)[0],
        max_leverage=leverage_limits(wallet)[1],
        margin_mode="cross",
        max_daily_trades=state.max_daily_trades,
        daily_loss_percent=account.daily_loss_percent,
        last_candle=state.last_candle,
        day=account.day,
        day_start_equity=account.day_start_equity,
        daily_trades=count,
        daily_loss=loss,
        daily_loss_used_percent=percent,
        status=state.status,
        worker_online=worker_online,
        next_close_time=(int(now) // 900 + 1) * 900,
        decisions=[
            dict(
                candle_time=d.candle_time,
                evaluated_at=d.evaluated_at,
                action=d.action,
                reason=d.reason,
                setup=d.setup,
                trade_id=d.trade_id,
                parameters=d.parameters,
            )
            for d in decisions
        ],
    )


@router.get("")
def get_auto_state():
    now = time.time()
    with Session.begin() as session:
        wallet = lock_wallet(session)
        state = session.get(AutoPaperState, state_id())
        if not state:
            raise HTTPException(503, "Run migrations")
        # Monitoring may initialize a new UTC day's baseline, but never enables trading.
        try:
            fresh_quote(wallet, now)
            portfolio(session, require_fresh=True, now=now)
        except HTTPException:
            pass
        else:
            roll_day(session, wallet, state, now)
        return state_response(session, wallet, state, now)


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
        if payload.daily_loss_percent != account.daily_loss_percent:
            if session.scalar(select(AutoPaperState.id).where(AutoPaperState.enabled.is_(True)).limit(1)):
                raise HTTPException(409, "Dừng Auto Paper trên mọi cặp trước khi đổi giới hạn lỗ ví chung")
            for pair in session.scalars(select(AutoPaperState)):
                pair.daily_loss_percent = payload.daily_loss_percent
        for key, value in payload.model_dump(exclude_none=True).items():
            setattr(state, key, value)
        session.add(Event(kind="auto_paper", message="Đã lưu thông số Auto Paper"))
        return state_response(session, wallet, state, time.time())


@router.post("/control")
def control_auto(payload: AutoControl):
    now = time.time()
    with Session.begin() as session:
        wallet = lock_wallet(session)
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
