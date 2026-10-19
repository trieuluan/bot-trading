"""Local paper execution. Public market data only; never calls exchange order APIs.

Wallet-row locks serialize commands, stops and funding. Prices are sampled, not a
matching engine: live fills use bid/ask; recovery simulates historical levels and
stop gaps with adverse slippage. Exact intrabar execution times are unavailable.
"""

import time
import math
from dataclasses import asdict
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from .db import (
    FuturesLearningState,
    FuturesOpenInterest,
    AutoPaperDecision,
    AutoPaperState,
    Event,
    PaperCommand,
    PaperFunding,
    PaperTrade,
    PaperWallet,
    Session,
    utcnow,
)
from .futures_symbols import SYMBOLS, current_symbol, state_id, symbol_scope
from .futures_exits import (EXIT_POLICIES, adaptive_target, partial_size, protective_stop,
                           discretionary_exit, has_target, exit_parameters)
from .feed import load_candles
from .notifications import notify_close, notify_open, notify_partial
from .mexc import MarketDataError, MexcFuturesAdapter

router = APIRouter(prefix="/api/paper", tags=["Paper trading"])
QUOTE_TTL = 20
MAX_ORDER_MARGIN_PERCENT = 10

def stop_risk_per_unit(side, entry, stop, fee_bps, slippage_bps):
    direction = 1 if side == "long" else -1
    fill = stop * (1 - direction * slippage_bps / 10000)
    return max(0, direction * (entry - fill) + (entry + fill) * fee_bps / 10000)

def portfolio_risk_limits(session):
    cash, unrealized, _ = portfolio(session)
    capital = max(0, min(cash, cash + unrealized))
    used = 0.0
    for position in session.scalars(select(PaperTrade).where(PaperTrade.exit_time.is_(None))):
        market = session.get(PaperWallet, SYMBOLS.index(position.symbol) + 1)
        direction = 1 if position.side == "long" else -1
        fill = position.stop * (1 - direction * market.slippage_bps / 10000)
        used += max(0, direction * (position.entry - fill) * position.quantity
                    + position.entry_fee + fill * position.quantity * market.fee_bps / 10000
                    + position.funding_cost)
    return capital, used


class OpenOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: UUID
    side: Literal["long", "short"]
    notional: float = Field(gt=0, le=100000)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)


class CloseOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


def master(session):
    """Single shared cash ledger (row 1); other rows keep only per-pair quote/contract/funding cursor."""
    wallet = session.get(PaperWallet, 1)
    if wallet is None:
        raise HTTPException(503, "Chạy migration để khởi tạo ví chung mô phỏng")
    return wallet

def portfolio_stale_symbols(session, now=None):
    symbols = set(session.scalars(select(PaperTrade.symbol).where(PaperTrade.exit_time.is_(None))))
    stale = []
    for symbol in sorted(symbols):
        wallet = session.get(PaperWallet, SYMBOLS.index(symbol) + 1)
        try:
            if wallet is None:
                raise HTTPException(409, "Missing market state")
            fresh_quote(wallet, now)
        except HTTPException:
            stale.append(symbol)
    return stale

def portfolio(session, require_fresh=False, now=None):
    """(cash, unrealized, margin) across all pairs."""
    if require_fresh:
        stale = portfolio_stale_symbols(session, now)
        if stale:
            raise HTTPException(409, "Ví chung chờ giá vị thế: " + ", ".join(stale))
    positions = session.scalars(select(PaperTrade).where(PaperTrade.exit_time.is_(None))).all()
    marks = {
        SYMBOLS[w.id - 1]: w.quote["fairPrice"]
        for w in session.scalars(select(PaperWallet)) if w.quote and 1 <= w.id <= len(SYMBOLS)
    }
    unrealized = sum(
        direction(t) * t.quantity * (marks[t.symbol] - t.entry) for t in positions if t.symbol in marks
    )
    return master(session).balance, unrealized, used_margin(positions)

def lock_wallet(session):
    account = session.scalar(select(PaperWallet).where(PaperWallet.id == 1).with_for_update())
    if account is None:
        raise HTTPException(503, "Chạy migration để khởi tạo ví chung mô phỏng")
    wallet = session.scalar(select(PaperWallet).where(PaperWallet.id == state_id()).with_for_update())
    if wallet is None:
        raise HTTPException(503, "Chạy migration để khởi tạo ví mô phỏng")
    wallet.fee_bps = account.fee_bps
    wallet.slippage_bps = account.slippage_bps
    return wallet


def fresh_quote(wallet, now=None):
    now = time.time() if now is None else now
    quote = wallet.quote
    if (
        not quote
        or wallet.error
        or not 0 <= now - quote["received_at"] <= QUOTE_TTL
        or abs(now - quote["time"]) > 30
    ):
        raise HTTPException(409, "Giá MEXC đã chậm; chờ worker cập nhật trước khi giao dịch")
    return quote


def open_positions(session):
    return session.scalars(
        select(PaperTrade).where(PaperTrade.symbol == current_symbol(), PaperTrade.exit_time.is_(None)).order_by(PaperTrade.id)
    ).all()

def unrealized_pnl(positions, mark):
    return sum(direction(t) * t.quantity * (mark - t.entry) for t in positions) if mark else 0

def used_margin(positions):
    return sum(t.entry * t.quantity / (t.leverage or 1) for t in positions)

def leverage_limits(wallet):
    info = wallet.contract or {}
    try:
        minimum = int(info.get("minLeverage", 1))
        maximum = int(info["maxLeverage"])
        country_maximum = int(info.get("countryConfigContractMaxLeverage") or 0)
        if country_maximum > 0:
            maximum = min(maximum, country_maximum)
        if not 1 <= minimum <= maximum:
            raise ValueError("Invalid leverage range")
        return minimum, maximum
    except (KeyError, ValueError, TypeError, OverflowError):
        return 1, None

def validate_leverage(wallet, leverage):
    minimum, maximum = leverage_limits(wallet)
    if type(leverage) is not int or leverage < minimum or leverage > (maximum or 1):
        raise HTTPException(422, f"Đòn bẩy phải là số nguyên {minimum}–{maximum or 1}x theo cặp")
    if leverage > 1:
        try:
            rate = float(wallet.contract["maintenanceMarginRate"])
            if not 0 < rate < 1 / leverage:
                raise ValueError("Invalid maintenance margin rate")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(409, "Chờ thông số ký quỹ duy trì trước khi dùng đòn bẩy") from exc

def liquidate_cross(session, now):
    """Conservative full-portfolio paper liquidation on fresh sampled marks, not exchange execution."""
    positions = session.scalars(select(PaperTrade).where(PaperTrade.exit_time.is_(None))).all()
    if not any((trade.leverage or 1) > 1 for trade in positions):
        return
    if portfolio_stale_symbols(session, now):
        return
    maintenance = 0.0
    for trade in positions:
        market = session.get(PaperWallet, SYMBOLS.index(trade.symbol) + 1)
        rate = (trade.monitoring or {}).get("maintenance_margin_rate")
        if rate is None:
            rate = float((market.contract or {}).get("maintenanceMarginRate") or 0)
        maintenance += trade.quantity * market.quote["fairPrice"] * (rate + master(session).fee_bps / 10000)
    cash, unrealized, _ = portfolio(session)
    if cash + unrealized > maintenance:
        return
    for trade in positions:
        with symbol_scope(trade.symbol):
            market = lock_wallet(session)
            close_position(session, market, trade, "liquidation", now)
    for state in session.scalars(select(AutoPaperState)):
        state.enabled = False
        state.status = "Đã dừng Auto Paper: thanh lý Cross giả lập; kiểm tra ví chung"
    session.add(Event(kind="paper", message="Thanh lý Cross giả lập toàn danh mục theo giá mark lấy mẫu"))


def direction(trade):
    return 1 if trade.side == "long" else -1


def execution_price(wallet, side, opening):
    buying = (side == "long") == opening
    quote = fresh_quote(wallet)
    base = quote["ask1"] if buying else quote["bid1"]
    return base * (1 + (1 if buying else -1) * wallet.slippage_bps / 10000)


def close_position(session, wallet, trade, reason, now, price=None, exit_time=None):
    price = price if price is not None else execution_price(wallet, trade.side, opening=False)
    trade.exit = price
    trade.exit_time = int(exit_time if exit_time is not None else now)
    trade.exit_reason = reason
    trade.exit_fee = round(price * trade.quantity * wallet.fee_bps / 10000, 8)
    gross = direction(trade) * trade.quantity * (price - trade.entry)
    cash = master(session)
    cash.balance = round(cash.balance + gross - trade.exit_fee, 8)
    trade.net_pnl = round(gross - trade.entry_fee - trade.exit_fee - trade.funding_cost
                          + (trade.monitoring or {}).get("realized_net", 0), 8)
    session.add(
        Event(
            kind="paper", message=f"PAPER #{trade.id}: đóng {reason}; PnL {trade.net_pnl:.2f} USDT"
        )
    )
    notify_close(session, trade)


def apply_stops(session, wallet, now):
    positions = open_positions(session)
    if not positions:
        return
    quote = fresh_quote(wallet, now)
    for trade in positions:
        reconcile_stops(session, wallet, trade, now)
        if trade.exit_time is not None:
            continue
        # Executable side of the sampled book, not a candle high/low.
        price = quote["bid1"] if trade.side == "long" else quote["ask1"]
        stopped = price <= trade.stop if trade.side == "long" else price >= trade.stop
        targeted = has_target(trade.monitoring or {}) and (
            price >= trade.target if trade.side == "long" else price <= trade.target)
        if stopped or targeted:
            close_position(session, wallet, trade, "stop_loss" if stopped else "take_profit", now)

def resolve_with_m1(trade, candle):
    """Order a 15m candle that crossed both SL and TP using 1m bars; None keeps the SL-first rule.

    ponytail: blocking REST call under the wallet lock; only on rare double-cross, 15 bars.
    Upgrade path: persist 1m bars or fetch outside the transaction.
    """
    try:
        adapter = MexcFuturesAdapter(symbol=trade.symbol)
        try:
            bars = adapter.history(candle.time, candle.time + 900, interval="Min1")
        finally:
            adapter.close()
    except (MarketDataError, ValueError):
        return None
    for bar in bars:
        stop = bar.low <= trade.stop if trade.side == "long" else bar.high >= trade.stop
        target = bar.high >= trade.target if trade.side == "long" else bar.low <= trade.target
        if stop or target:
            return ("stop" if stop else "target"), bar, stop and target
    return None

def reconcile_stops(session, wallet, trade, now):
    """Simulated candle crossing closes conservatively at historical SL/TP, not present quote."""
    monitoring = trade.monitoring or {}
    cursor = monitoring.get("next_bar", (trade.entry_time // 900 + 1) * 900)
    end = int(now) // 900 * 900
    for candle in load_candles(session, since=cursor, until=end):
        if candle.time != cursor:
            break
        stopped = candle.low <= trade.stop if trade.side == "long" else candle.high >= trade.stop
        targeted = has_target(monitoring) and (
            candle.high >= trade.target if trade.side == "long" else candle.low <= trade.target)
        if stopped or targeted:
            hit, span, ambiguous, resolution = candle, 899, bool(stopped and targeted), "15m"
            if stopped and targeted:
                found = resolve_with_m1(trade, candle)
                if found:
                    kind, hit, ambiguous = found
                    stopped, targeted, span, resolution = kind == "stop", kind == "target", 59, "1m"
            reason = "recovered_stop_loss" if stopped else "recovered_take_profit"
            level = trade.stop if stopped else trade.target
            gap = stopped and (hit.open <= level if trade.side == "long" else hit.open >= level)
            base = hit.open if gap else level
            fill = base * (1 + (-1 if trade.side == "long" else 1) * wallet.slippage_bps / 10000)
            exit_time = hit.time if gap else hit.time + span
            reversed_funding = []
            for funding in session.scalars(select(PaperFunding).where(
                PaperFunding.trade_id == trade.id, PaperFunding.time > exit_time,
            )):
                reversed_funding.append(dict(time=funding.time, cost=funding.cost))
                master(session).balance = round(master(session).balance + funding.cost, 8)
                trade.funding_cost = round(trade.funding_cost - funding.cost, 8)
                funding.cost = 0
            trade.monitoring = {
                **monitoring,
                "next_bar": cursor,
                "status": "recovered",
                "trigger_bar": hit.time,
                "ambiguous": ambiguous,
                "resolution": resolution,
                "fill_basis": "gap_open" if gap else "level",
                "execution_model": "historical_levels_v1",
                "recovered_at": int(now),
                "time_basis": "bar_open" if gap else "bar_end_proxy",
                "reversed_funding": reversed_funding,
            }
            close_position(session, wallet, trade, reason, now, price=fill, exit_time=exit_time)
            state = session.get(AutoPaperState, state_id())
            if state:
                state.enabled = False
                state.status = "Đã dừng Auto Paper: đối soát SL/TP lịch sử; kiểm tra ví trước khi bật lại"
            session.add(Event(kind="paper", message=(
                f"PAPER #{trade.id}: nến {resolution} {hit.time} chạm {'SL' if stopped else 'TP'}; "
                f"khớp giả lập {fill:.2f} USDT; thời gian trong nến là ước lượng. "
                "Nếu vẫn cùng chạm cả SL và TP thì ưu tiên SL. Auto Paper đã dừng."
            )))
            return
        policy = monitoring.get("exit_policy", "fixed")
        if policy != "fixed":
            history = load_candles(session, limit=100, until=candle.time + 900)
            settled_funding = sum(session.scalars(select(PaperFunding.cost).where(
                PaperFunding.trade_id == trade.id, PaperFunding.time <= candle.time,
            )))
            risk = monitoring["risk"]
            info = wallet.contract or {}
            unit = float(info.get("contractSize") or 0) * float(info.get("volUnit") or 0)
            position = dict(side=trade.side, entry=trade.entry, risk=risk, stop=trade.stop,
                            target=trade.target, exit_policy=policy, quantity=trade.quantity,
                            entry_fee=trade.entry_fee,
                            funding_cost=settled_funding,
                            partial_exits=monitoring.get("partial_exits"),
                            exit_params=monitoring.get("exit_params"), exit_rule=monitoring.get("exit_rule"),
                            entry_time=trade.entry_time,
                            original_quantity=monitoring.get("original_quantity", trade.quantity))
            why = discretionary_exit(position, history)
            if why:
                fill = candle.close * (1 + (-1 if trade.side == "long" else 1) * wallet.slippage_bps / 10000)
                trade.monitoring = {**monitoring, "next_bar": cursor, "status": "recovered",
                                    "trigger_bar": candle.time, "fill_basis": "bar_close",
                                    "execution_model": "historical_levels_v1", "recovered_at": int(now),
                                    "time_basis": "bar_end_proxy"}
                close_position(session, wallet, trade, why, now, price=fill, exit_time=candle.time + 899)
                return
            minimum = float(info.get("contractSize") or 0) * float(info.get("minVol") or 0)
            quantity = partial_size(position, history, unit, minimum) if unit > 0 else 0
            if quantity:
                original = trade.quantity
                fill = candle.close * (1 - direction(trade) * wallet.slippage_bps / 10000)
                fee = round(fill * quantity * wallet.fee_bps / 10000, 8)
                gross = direction(trade) * quantity * (fill - trade.entry)
                master(session).balance = round(master(session).balance + gross - fee, 8)
                entry_fee = trade.entry_fee * quantity / trade.quantity
                trade.entry_fee = round(trade.entry_fee - entry_fee, 8)
                trade.quantity = float(Decimal(str(trade.quantity)) - Decimal(str(quantity)))
                trade.contracts = float(Decimal(str(trade.quantity)) / Decimal(str(info["contractSize"])))
                for funding in session.scalars(select(PaperFunding).where(
                    PaperFunding.trade_id == trade.id, PaperFunding.time >= candle.time + 900,
                )):
                    refund = round(funding.cost * quantity / original, 8)
                    funding.cost = round(funding.cost - refund, 8)
                    trade.funding_cost = round(trade.funding_cost - refund, 8)
                    master(session).balance = round(master(session).balance + refund, 8)
                monitoring = {**monitoring, "realized_net": round(
                    monitoring.get("realized_net", 0) + gross - fee - entry_fee, 8),
                    "original_quantity": monitoring.get("original_quantity", original),
                    "original_entry_fee": monitoring.get("original_entry_fee", trade.entry_fee + entry_fee),
                    "partial_exits": [*(monitoring.get("partial_exits") or []),
                                      dict(time=candle.time + 900, price=fill, quantity=quantity,
                                           gross=gross, exit_fee=fee, entry_fee=entry_fee)]}
                position.update(quantity=trade.quantity, entry_fee=trade.entry_fee,
                                partial_exits=monitoring["partial_exits"])
                session.add(Event(kind="paper", message=f"PAPER #{trade.id}: chốt {quantity:g} tại {fill:.4f}"))
                notify_partial(session, trade, fill, quantity, gross - fee - entry_fee,
                               trade.quantity, candle.time)
            if has_target(position):
                trade.target = adaptive_target(position, history)
            next_stop = protective_stop(
                position, history, wallet.fee_bps / 10000, wallet.slippage_bps / 10000)
            if next_stop != trade.stop:
                monitoring = {**monitoring, "last_stop_update": candle.time + 900,
                              "previous_stop": trade.stop}
                trade.stop = next_stop
        cursor += 900
    status = "history_pending" if cursor < end else "watching"
    if status == "history_pending" and monitoring.get("status") != status:
        session.add(Event(kind="paper", message=(
            f"PAPER #{trade.id}: thiếu nến từ {cursor}; chờ đối soát SL/TP, "
            "SL/TP theo quote hiện tại vẫn chạy"
        )))
    trade.monitoring = {**monitoring, "next_bar": cursor, "status": status}


def sync_paper_stops(now=None):
    now = time.time() if now is None else now
    with Session.begin() as session:
        wallet = lock_wallet(session)
        for trade in open_positions(session):
            reconcile_stops(session, wallet, trade, now)

def sync_paper_quote(adapter=None):
    owns_adapter = adapter is None
    adapter = adapter or MexcFuturesAdapter()
    try:
        with Session() as session:
            wallet = session.get(PaperWallet, state_id())
            if wallet is None:
                return  # Worker can start while migrations are being applied.
            if wallet.retry_at and wallet.retry_at.replace(tzinfo=utcnow().tzinfo) > utcnow():
                return
            needs_contract = wallet.contract is None
        contract = adapter.contract_info() if needs_contract else None
        quote = adapter.ticker()
        with Session.begin() as session:
            wallet = lock_wallet(session)
            if wallet.quote and quote["time"] < wallet.quote["time"]:
                return
            # The live bar contains observed ticks only; cached closed bars stay authoritative.
            bucket = int(quote["time"]) // 900 * 900
            previous = (wallet.quote or {}).get("bar")
            last = quote["lastPrice"]
            if previous and previous["time"] == bucket:
                bar = {
                    **previous,
                    "high": max(previous["high"], last),
                    "low": min(previous["low"], last),
                    "close": last,
                }
            else:
                bar = dict(time=bucket, open=last, high=last, low=last, close=last, volume=0)
            wallet.quote = {**quote, "bar": bar}
            if quote.get("holdVol"):
                observed_at = math.ceil(max(quote["time"], quote["received_at"]))
                latest = session.scalar(select(FuturesOpenInterest)
                    .where(FuturesOpenInterest.symbol == current_symbol())
                    .order_by(FuturesOpenInterest.time.desc()).limit(1))
                if latest is None or observed_at // 300 > latest.time // 300:
                    session.add(FuturesOpenInterest(symbol=current_symbol(), time=observed_at,
                                                    hold_vol=quote["holdVol"]))
            if contract:
                wallet.contract = contract
            wallet.error = None
            wallet.retry_at = None
            liquidate_cross(session, time.time())
            apply_stops(session, wallet, time.time())
    except MarketDataError as exc:
        with Session.begin() as session:
            wallet = lock_wallet(session)
            wallet.error = str(exc)
            wallet.retry_at = utcnow() + timedelta(seconds=exc.retry_after)
    finally:
        if owns_adapter:
            adapter.close()


def sync_paper_funding(adapter=None, now=None):
    """Post actual settlements once, delayed until their 15m fair-price candle closes."""
    now = int(time.time()) if now is None else now
    end = now // 900 * 900
    with Session() as session:
        wallet = session.get(PaperWallet, state_id())
        if wallet is None or now - wallet.funding_synced_at < 60:
            return
        start = wallet.funding_cursor
        has_trades = session.scalar(
            select(PaperTrade.id)
            .where(PaperTrade.symbol == current_symbol(), or_(PaperTrade.exit_time.is_(None), PaperTrade.exit_time >= start))
            .limit(1)
        )
        if end <= start:
            return
    if not has_trades:
        with Session.begin() as session:
            wallet = lock_wallet(session)
            wallet.funding_cursor = end
            wallet.funding_synced_at = now
        return
    owns_adapter = adapter is None
    adapter = adapter or MexcFuturesAdapter()
    try:
        records = [
            record
            for record in adapter.funding_history(start - 86400, end)
            if start <= record["time"] < end
        ]
        prices = {
            record["time"]: adapter.history(record["time"], record["time"] + 900, now, fair=True)[
                0
            ].open
            for record in records
        }
        with Session.begin() as session:
            wallet = lock_wallet(session)
            for record in records:
                timestamp, rate = record["time"], record["rate"]
                trades = session.scalars(
                    select(PaperTrade).where(
                        PaperTrade.symbol == current_symbol(),
                        PaperTrade.entry_time < timestamp,
                        or_(PaperTrade.exit_time.is_(None), PaperTrade.exit_time >= timestamp),
                    )
                )
                for trade in trades:
                    if session.get(PaperFunding, (trade.id, timestamp)):
                        continue
                    price = prices[timestamp]
                    quantity = trade.quantity + sum(
                        leg["quantity"] for leg in (trade.monitoring or {}).get("partial_exits", [])
                        if timestamp < leg["time"])
                    cost = round(direction(trade) * quantity * price * rate, 8)
                    master(session).balance = round(master(session).balance - cost, 8)
                    trade.funding_cost = round(trade.funding_cost + cost, 8)
                    if trade.net_pnl is not None:
                        trade.net_pnl = round(trade.net_pnl - cost, 8)
                    session.add(
                        PaperFunding(
                            trade_id=trade.id, time=timestamp, rate=rate, price=price, cost=cost
                        )
                    )
                    session.add(
                        Event(kind="paper", message=f"PAPER #{trade.id}: funding {cost:.4f} USDT")
                    )
            wallet.funding_cursor = max(wallet.funding_cursor, end)
            wallet.funding_synced_at = now
            wallet.funding_error = None
    except MarketDataError as exc:
        with Session.begin() as session:
            wallet = lock_wallet(session)
            wallet.funding_error = str(exc)
            wallet.funding_synced_at = now + int(max(0, exc.retry_after - 60))
    finally:
        if owns_adapter:
            adapter.close()


def serialize_trade(trade, decision=None, candidates=None):
    result = {
        name: getattr(trade, name)
        for name in (
            "id",
            "symbol",
            "origin",
            "signal_time",
            "side",
            "entry_time",
            "entry",
            "quantity",
            "contracts",
            "leverage",
            "stop",
            "target",
            "entry_fee",
            "exit_time",
            "exit",
            "exit_fee",
            "exit_reason",
            "funding_cost",
            "net_pnl",
            "monitoring",
        )
    }

    raw_audit = decision.parameters.get("audit") if decision and decision.parameters else None
    audit = dict(raw_audit) if raw_audit else None
    if audit:
        audit["strategy_source"] = "entry_snapshot" if audit.get("genome") else "missing"
    if audit and candidates:
        name = audit.get("strategy")
        if name and name in candidates:
            cand = candidates[name]
            if not audit.get("genome"):
                audit["genome"] = cand.get("genome")
                audit["strategy_source"] = "registry"
            if "generation" not in audit:
                audit["generation"] = cand.get("generation", 0)
            if "parents" not in audit:
                audit["parents"] = cand.get("parents", [])
            if "exit_policy" not in audit:
                audit["exit_policy"] = cand.get("genome", {}).get("exit_policy", "fixed")
    result["audit"] = audit
    result["margin_mode"] = "cross"
    result["initial_margin"] = round(trade.entry * trade.quantity / (trade.leverage or 1), 8)
    result["entry_reason"] = decision.setup.get("reason") if decision and decision.setup else None
    return result


@router.get("/trades/{trade_id}/candles")
def get_trade_candles(trade_id: int):
    """Read cached history only; inspecting a trade never changes trading state."""
    with Session() as session:
        trade = session.get(PaperTrade, trade_id)
        if not trade or trade.symbol != current_symbol():
            raise HTTPException(404, "Không tìm thấy lệnh mô phỏng")
        anchor = trade.signal_time or int(trade.entry_time) // 900 * 900
        end = int(trade.exit_time or time.time()) // 900 * 900 + 4 * 900
        # Bound history from the signal forward so entry context is never truncated.
        candles = load_candles(
            session, since=anchor - 24 * 900, until=min(end, anchor + 1976 * 900)
        )
        result = [asdict(candle) for candle in candles]
        wallet = session.get(PaperWallet, state_id())
        bar = wallet.quote.get("bar") if wallet and wallet.quote else None
        if (
            bar
            and anchor - 24 * 900 <= bar["time"] < min(end, anchor + 1976 * 900)
            and (not result or bar["time"] > result[-1]["time"])
        ):
            result.append(bar)
        return result


@router.get("/wallet")
def get_wallet():
    # Keep balance, position and ledger from the same serialized wallet state.
    with Session.begin() as session:
        wallet = lock_wallet(session)
        learning_state = session.get(FuturesLearningState, state_id())
        learning_payload = (learning_state.payload or {}) if learning_state else {}
        candidates = {**learning_payload.get("rule_archive", {}), **learning_payload.get("candidates", {})}
        trades = session.scalars(select(PaperTrade).where(PaperTrade.symbol == current_symbol()).order_by(PaperTrade.id.desc()).limit(100)).all()
        positions = open_positions(session)
        decisions = {
            d.trade_id: d
            for d in session.scalars(
                select(AutoPaperDecision).where(
                    AutoPaperDecision.trade_id.in_(
                        {t.id for t in trades} | {t.id for t in positions}
                    )
                )
            )
        }
        quote = wallet.quote
        mark = quote["fairPrice"] if quote else None
        cash, unrealized, margin = portfolio(session)
        pair_unrealized = unrealized_pnl(positions, mark)
        try:
            fresh_quote(wallet)
            stale = False
        except HTTPException:
            stale = True
        candles = [asdict(candle) for candle in load_candles(session, limit=120)]
        if quote and (not candles or quote["bar"]["time"] > candles[-1]["time"]):
            candles.append(quote["bar"])
        return dict(
            symbol=current_symbol(),
            initial_balance=master(session).initial_balance,
            balance=cash,
            equity=cash + unrealized,
            unrealized_pnl=unrealized,
            pair_unrealized_pnl=pair_unrealized,
            portfolio_stale_symbols=portfolio_stale_symbols(session),
            wallet_scope="shared",
            margin_mode="cross",
            min_leverage=leverage_limits(wallet)[0],
            max_leverage=leverage_limits(wallet)[1],
            used_margin=margin,
            available=max(0, cash - margin),
            fee_bps=wallet.fee_bps,
            slippage_bps=wallet.slippage_bps,
            quote=quote,
            stale=stale,
            error=wallet.error,
            funding_error=wallet.funding_error,
            funding_cursor=wallet.funding_cursor,
            position=(
                serialize_trade(positions[-1], decisions.get(positions[-1].id), candidates) if positions else None
            ),
            positions=[serialize_trade(t, decisions.get(t.id), candidates) for t in positions],
            trades=[serialize_trade(trade, decisions.get(trade.id), candidates) for trade in trades],
            candles=candles,
        )


def replay_command(session, request_id, payload):
    existing = session.get(PaperCommand, str(request_id))
    if existing:
        if existing.payload != payload:
            raise HTTPException(409, "request_id đã dùng cho một yêu cầu khác")
        return existing.result
    return None


def create_position(
    session, wallet, side, requested_notional, stop, target, origin="manual", signal_time=None,
    exit_policy="fixed", leverage=1, exit_params=None, exit_rule=None,
):
    """Shared sizing/fill/accounting for manual and automatic paper entries.

    Caller holds the wallet lock. No network or independent commit is allowed here.
    """
    if exit_policy not in EXIT_POLICIES:
        raise HTTPException(422, "Invalid futures exit policy")
    try:
        exit_parameters(exit_policy, exit_params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    validate_leverage(wallet, leverage)
    fresh_quote(wallet)
    cash, _, margin = portfolio(session, require_fresh=True)
    if not wallet.contract:
        raise HTTPException(409, "Đang chờ thông số hợp đồng MEXC")
    price = execution_price(wallet, side, opening=True)
    valid = stop < price < target if side == "long" else target < price < stop
    if not valid:
        raise HTTPException(422, "SL/TP phải nằm hai phía entry theo chiều Long/Short")
    info = wallet.contract
    size, unit = Decimal(str(info["contractSize"])), Decimal(str(info["volUnit"]))
    contracts = (
        Decimal(str(requested_notional)) / Decimal(str(price)) / size / unit
    ).to_integral_value(rounding=ROUND_DOWN) * unit
    if not Decimal(str(info["minVol"])) <= contracts <= Decimal(str(info["maxVol"])):
        raise HTTPException(422, "Khối lượng ngoài giới hạn hợp đồng MEXC")
    quantity = float(contracts * size)
    notional = quantity * price
    fee = round(notional * wallet.fee_bps / 10000, 8)
    free = cash - margin
    needed = notional / leverage + fee
    if needed > free:
        raise HTTPException(422, f"Không đủ ký quỹ khả dụng: cần {needed:.2f}, còn {free:.2f} USDT")
    if origin == "auto":
        capital, used_risk = portfolio_risk_limits(session)
        if notional / leverage > capital * MAX_ORDER_MARGIN_PERCENT / 100 + 1e-8:
            raise HTTPException(422, "Ký quỹ mỗi lệnh Auto Paper vượt 10% vốn ví chung")
        risk = quantity * stop_risk_per_unit(side, price, stop, wallet.fee_bps, wallet.slippage_bps)
        cap = session.get(AutoPaperState, 1).max_portfolio_risk_percent
        if cap is not None and (
            used_risk + risk > capital * cap / 100 + 1e-8
        ):
            raise HTTPException(422, f"Tổng rủi ro tới SL vượt {cap:g}% vốn ví chung; bỏ lệnh mới")
    trade = PaperTrade(
        symbol=current_symbol(),
        side=side,
        entry_time=int(time.time()),
        monitoring=dict(
            next_bar=(int(time.time()) // 900 + 1) * 900,
            status="watching",
            maintenance_margin_rate=float(info.get("maintenanceMarginRate") or 0),
            **({} if exit_policy == "fixed" else {"exit_policy": exit_policy, "risk": abs(price - stop),
                                                      "exit_params": exit_params, "exit_rule": exit_rule}),
        ),
        origin=origin,
        signal_time=signal_time,
        entry=price,
        quantity=quantity,
        contracts=float(contracts),
        leverage=leverage,
        stop=stop,
        target=target,
        entry_fee=fee,
        exit_fee=0,
        funding_cost=0,
    )
    master(session).balance = round(cash - fee, 8)
    session.add(trade)
    session.flush()
    return trade


@router.post("/orders")
def open_order(payload: OpenOrder):
    command = payload.model_dump(mode="json")
    if current_symbol() != "BTC_USDT":
        command["symbol"] = current_symbol()
    with Session.begin() as session:
        wallet = lock_wallet(session)
        previous = replay_command(session, payload.request_id, command)
        if previous:
            return previous
        fresh_quote(wallet)
        apply_stops(session, wallet, time.time())
        auto = session.get(AutoPaperState, state_id())
        if auto and auto.enabled:
            raise HTTPException(409, "Dừng Auto Paper trước khi mở lệnh thủ công")
        trade = create_position(
            session, wallet, payload.side, payload.notional, payload.stop, payload.target
        )
        notify_open(session, trade, None, master(session).balance)
        result = {"trade_id": trade.id, "status": "opened", "mode": "paper"}
        session.add(PaperCommand(id=str(payload.request_id), payload=command, result=result))
        session.add(
            Event(
                kind="paper",
                message=f"PAPER #{trade.id}: mở {trade.side.upper()} {trade.contracts:g} hợp đồng",
            )
        )
        return result


@router.post("/positions/{trade_id}/close")
def close_order(trade_id: int, payload: CloseOrder):
    command = {"action": "close", "trade_id": trade_id, **payload.model_dump(mode="json")}
    if current_symbol() != "BTC_USDT":
        command["symbol"] = current_symbol()
    with Session.begin() as session:
        wallet = lock_wallet(session)
        previous = replay_command(session, payload.request_id, command)
        if previous:
            return previous
        fresh_quote(wallet)
        trade = session.get(PaperTrade, trade_id)
        if trade is None or trade.symbol != current_symbol():
            raise HTTPException(404, "Không tìm thấy vị thế")
        if trade.exit_time is None:
            apply_stops(session, wallet, time.time())
            if trade.exit_time is None:
                close_position(session, wallet, trade, "manual", int(time.time()))
        result = {"trade_id": trade.id, "status": "closed", "mode": "paper"}
        session.add(PaperCommand(id=str(payload.request_id), payload=command, result=result))
        return result
