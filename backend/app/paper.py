"""Local paper execution. Public market data only; never calls exchange order APIs.

Wallet-row locks serialize commands, stops and funding. Prices are sampled, not a
matching engine: fills use observed bid/ask plus slippage, stops may gap, and no
unobserved tick path is reconstructed after an outage.
"""

import time
from dataclasses import asdict
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from .db import Event, PaperCommand, PaperFunding, PaperTrade, PaperWallet, Session, utcnow
from .feed import load_candles
from .mexc import MarketDataError, MexcFuturesAdapter

router = APIRouter(prefix="/api/paper", tags=["Paper trading"])
QUOTE_TTL = 20


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


def lock_wallet(session):
    wallet = session.scalar(select(PaperWallet).where(PaperWallet.id == 1).with_for_update())
    if wallet is None:
        raise HTTPException(503, "Chạy migration để khởi tạo ví mô phỏng")
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


def open_position(session):
    return session.scalar(select(PaperTrade).where(PaperTrade.exit_time.is_(None)))


def direction(trade):
    return 1 if trade.side == "long" else -1


def execution_price(wallet, side, opening):
    buying = (side == "long") == opening
    quote = fresh_quote(wallet)
    base = quote["ask1"] if buying else quote["bid1"]
    return base * (1 + (1 if buying else -1) * wallet.slippage_bps / 10000)


def close_position(session, wallet, trade, reason, now):
    price = execution_price(wallet, trade.side, opening=False)
    trade.exit = price
    trade.exit_time = int(now)
    trade.exit_reason = reason
    trade.exit_fee = round(price * trade.quantity * wallet.fee_bps / 10000, 8)
    gross = direction(trade) * trade.quantity * (price - trade.entry)
    wallet.balance = round(wallet.balance + gross - trade.exit_fee, 8)
    trade.net_pnl = round(gross - trade.entry_fee - trade.exit_fee - trade.funding_cost, 8)
    session.add(
        Event(
            kind="paper", message=f"PAPER #{trade.id}: đóng {reason}; PnL {trade.net_pnl:.2f} USDT"
        )
    )


def apply_stops(session, wallet, now):
    trade = open_position(session)
    if trade is None:
        return
    quote = fresh_quote(wallet, now)
    # Executable side of the sampled book, not a candle high/low.
    price = quote["bid1"] if trade.side == "long" else quote["ask1"]
    stopped = price <= trade.stop if trade.side == "long" else price >= trade.stop
    targeted = price >= trade.target if trade.side == "long" else price <= trade.target
    if stopped or targeted:
        close_position(session, wallet, trade, "stop_loss" if stopped else "take_profit", now)


def sync_paper_quote(adapter=None):
    owns_adapter = adapter is None
    adapter = adapter or MexcFuturesAdapter()
    try:
        with Session() as session:
            wallet = session.get(PaperWallet, 1)
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
            if contract:
                wallet.contract = contract
            wallet.error = None
            wallet.retry_at = None
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
        wallet = session.get(PaperWallet, 1)
        if wallet is None or now - wallet.funding_synced_at < 60:
            return
        start = wallet.funding_cursor
        has_trades = session.scalar(
            select(PaperTrade.id)
            .where(or_(PaperTrade.exit_time.is_(None), PaperTrade.exit_time >= start))
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
                        PaperTrade.entry_time < timestamp,
                        or_(PaperTrade.exit_time.is_(None), PaperTrade.exit_time >= timestamp),
                    )
                )
                for trade in trades:
                    if session.get(PaperFunding, (trade.id, timestamp)):
                        continue
                    price = prices[timestamp]
                    cost = round(direction(trade) * trade.quantity * price * rate, 8)
                    wallet.balance = round(wallet.balance - cost, 8)
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


def serialize_trade(trade):
    return {
        name: getattr(trade, name)
        for name in (
            "id",
            "side",
            "entry_time",
            "entry",
            "quantity",
            "contracts",
            "stop",
            "target",
            "entry_fee",
            "exit_time",
            "exit",
            "exit_fee",
            "exit_reason",
            "funding_cost",
            "net_pnl",
        )
    }


@router.get("/wallet")
def get_wallet():
    # Keep balance, position and ledger from the same serialized wallet state.
    with Session.begin() as session:
        wallet = lock_wallet(session)
        trades = session.scalars(select(PaperTrade).order_by(PaperTrade.id.desc()).limit(100)).all()
        position = open_position(session)
        quote = wallet.quote
        mark = quote["fairPrice"] if quote else None
        unrealized = (
            direction(position) * position.quantity * (mark - position.entry)
            if position and mark
            else 0
        )
        margin = position.entry * position.quantity if position else 0
        try:
            fresh_quote(wallet)
            stale = False
        except HTTPException:
            stale = True
        candles = [asdict(candle) for candle in load_candles(session, limit=120)]
        if quote and (not candles or quote["bar"]["time"] > candles[-1]["time"]):
            candles.append(quote["bar"])
        return dict(
            initial_balance=wallet.initial_balance,
            balance=wallet.balance,
            equity=wallet.balance + unrealized,
            unrealized_pnl=unrealized,
            used_margin=margin,
            available=max(0, wallet.balance - margin),
            fee_bps=wallet.fee_bps,
            slippage_bps=wallet.slippage_bps,
            quote=quote,
            stale=stale,
            error=wallet.error,
            funding_error=wallet.funding_error,
            funding_cursor=wallet.funding_cursor,
            position=serialize_trade(position) if position else None,
            trades=[serialize_trade(trade) for trade in trades],
            candles=candles,
        )


def replay_command(session, request_id, payload):
    existing = session.get(PaperCommand, str(request_id))
    if existing:
        if existing.payload != payload:
            raise HTTPException(409, "request_id đã dùng cho một yêu cầu khác")
        return existing.result
    return None


@router.post("/orders")
def open_order(payload: OpenOrder):
    command = payload.model_dump(mode="json")
    with Session.begin() as session:
        wallet = lock_wallet(session)
        previous = replay_command(session, payload.request_id, command)
        if previous:
            return previous
        fresh_quote(wallet)
        apply_stops(session, wallet, time.time())
        if open_position(session):
            raise HTTPException(409, "Đang có vị thế; đóng vị thế trước khi mở lệnh mới")
        if not wallet.contract:
            raise HTTPException(409, "Đang chờ thông số hợp đồng MEXC")
        price = execution_price(wallet, payload.side, opening=True)
        valid = (
            payload.stop < price < payload.target
            if payload.side == "long"
            else payload.target < price < payload.stop
        )
        if not valid:
            raise HTTPException(422, "SL/TP phải nằm hai phía entry theo chiều Long/Short")
        info = wallet.contract
        size, unit = Decimal(str(info["contractSize"])), Decimal(str(info["volUnit"]))
        contracts = (
            Decimal(str(payload.notional)) / Decimal(str(price)) / size / unit
        ).to_integral_value(rounding=ROUND_DOWN) * unit
        if not Decimal(str(info["minVol"])) <= contracts <= Decimal(str(info["maxVol"])):
            raise HTTPException(422, "Khối lượng ngoài giới hạn hợp đồng MEXC")
        quantity = float(contracts * size)
        notional = quantity * price
        fee = round(notional * wallet.fee_bps / 10000, 8)
        if payload.notional > wallet.balance or notional + fee > wallet.balance:
            raise HTTPException(422, "Không đủ USDT ảo; tổng notional + phí tối đa 1× số dư")
        trade = PaperTrade(
            side=payload.side,
            entry_time=int(time.time()),
            entry=price,
            quantity=quantity,
            contracts=float(contracts),
            stop=payload.stop,
            target=payload.target,
            entry_fee=fee,
            exit_fee=0,
            funding_cost=0,
        )
        wallet.balance = round(wallet.balance - fee, 8)
        session.add(trade)
        session.flush()
        result = {"trade_id": trade.id, "status": "opened", "mode": "paper"}
        session.add(PaperCommand(id=str(payload.request_id), payload=command, result=result))
        session.add(
            Event(
                kind="paper",
                message=f"PAPER #{trade.id}: mở {trade.side.upper()} {float(contracts):g} hợp đồng",
            )
        )
        return result


@router.post("/positions/{trade_id}/close")
def close_order(trade_id: int, payload: CloseOrder):
    command = {"action": "close", "trade_id": trade_id, **payload.model_dump(mode="json")}
    with Session.begin() as session:
        wallet = lock_wallet(session)
        previous = replay_command(session, payload.request_id, command)
        if previous:
            return previous
        fresh_quote(wallet)
        trade = session.get(PaperTrade, trade_id)
        if trade is None:
            raise HTTPException(404, "Không tìm thấy vị thế")
        if trade.exit_time is None:
            apply_stops(session, wallet, time.time())
            if trade.exit_time is None:
                close_position(session, wallet, trade, "manual", int(time.time()))
        result = {"trade_id": trade.id, "status": "closed", "mode": "paper"}
        session.add(PaperCommand(id=str(payload.request_id), payload=command, result=result))
        return result
