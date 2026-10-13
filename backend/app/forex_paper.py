"""Forex paper portfolio simulation using actual Exness bid/ask candles.

Never sends orders to Exness. Completely isolated from MEXC paper state.
"""

import math
import time

from sqlalchemy import select

from .db import (
    ForexObservation,
    ForexPaperAccount,
    ForexPaperTrade,
    Session,
    Workspace,
    WorkspaceCandle,
    WorkspaceFeedState,
)
from .exness_feed import FRAMES, WORKSPACE
from .exness_observation import audit_frames, load_frames
from .forex_costs import lot_size, spec_summary
from .forex_reporting import trade_report

STARTING_BALANCE = 10000.0
RISK_FRACTION = 0.01
SLIPPAGE_BPS = 2
COMMISSION_PER_UNIT = 0.0

def _resolve_trade(trade, price, outcome, stamp, source):
    direction = 1 if trade["side"] == "long" else -1
    slip_rate = trade["slippage_bps"] / 10000
    exit_exec = price * (1 - direction * slip_rate)
    exit_slippage_usd = trade["units"] * price * slip_rate
    total_slippage_usd = trade["entry_slippage_usd"] + exit_slippage_usd if "entry_slippage_usd" in trade else None
    gross_pnl = direction * (price - trade["entry_price_raw"]) * trade["units"] if "entry_price_raw" in trade else None
    pnl = (direction * (exit_exec - trade["entry"]) - trade["commission_per_unit"]) * trade["units"]
    risk_dollars = trade["risk_per_unit"] * trade["units"]
    net_r = pnl / risk_dollars
    trade.update(
        status="resolved",
        outcome=outcome,
        exit_price_raw=price,
        exit=exit_exec,
        exit_slippage_usd=exit_slippage_usd,
        total_slippage_usd=total_slippage_usd,
        gross_pnl=gross_pnl,
        commission_usd=trade["commission_per_unit"] * trade["units"],
        holding_seconds=stamp - trade.get("entry_time", stamp),
        exit_time=stamp,
        exit_source=source,
        exit_time_precision="tick" if source == "live_quote" else "m15_bucket",
        pnl=pnl,
        net_r=net_r,
    )


def _step_trade(trade, bid, ask):
    if trade["status"] not in ("pending", "open"):
        return
    if bid.time != ask.time or bid.time != trade["last_candle"] + 900:
        trade.update(status="censored", reason="unverified_session_gap", last_candle=bid.time)
        return
    trade["last_candle"] = bid.time
    direction = 1 if trade["side"] == "long" else -1
    slip = trade["slippage_bps"] / 10000
    exit_bar = bid if direction == 1 else ask
    if trade["status"] == "pending":
        raw_open = ask.open if direction == 1 else bid.open
        entry = raw_open * (1 + direction * slip)
        risk_per_unit = direction * (entry - trade["stop"])
        target = entry + direction * 2 * risk_per_unit
        if risk_per_unit <= 0 or target <= 0:
            trade.update(status="invalid", reason="next_open_outside_stop")
            return
        units = min(trade["allocated_risk"] / risk_per_unit, trade["capital"] / entry)
        profile = trade.get("cost_profile")
        if profile:
            if profile["account_currency"] != "USD" or profile["quote_currency"] != "USD":
                trade.update(status="invalid", reason="currency_conversion_unverified")
                return
            sized = lot_size(units, profile)
            if sized is None:
                trade.update(status="invalid", reason="below_minimum_lot")
                return
            units, lots = sized
            trade["lots"] = lots
            trade["lot_constraints_verified"] = True
        entry_slippage_usd = units * raw_open * slip
        trade.update(
            status="open",
            entry_price_raw=raw_open,
            entry=entry,
            entry_bid=bid.open,
            entry_ask=ask.open,
            entry_spread=ask.open - bid.open,
            entry_slippage_usd=entry_slippage_usd,
            entry_time=bid.time,
            risk_per_unit=risk_per_unit,
            target=target,
            units=units,
            bars=0,
        )
    trade["bars"] += 1
    stop, target = trade["stop"], trade["target"]
    stop_hit = exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop
    target_hit = exit_bar.high >= target if direction == 1 else exit_bar.low <= target
    if direction * (exit_bar.open - stop) <= 0:
        price, outcome = exit_bar.open, "stop_gap"
    elif direction * (exit_bar.open - target) >= 0:
        price, outcome = target, "target_at_open"
    elif stop_hit:
        price, outcome = stop, "stop"
        trade["ambiguous_bar"] = target_hit
    elif target_hit:
        price, outcome = target, "target"
    else:
        return
    _resolve_trade(trade, price, outcome, bid.time, "closed_candle")
    if outcome in ("stop_gap", "target_at_open"):
        trade["exit_bid"], trade["exit_ask"] = bid.open, ask.open
        trade["exit_spread"] = ask.open - bid.open
    else:
        trade["exit_bid"] = trade["exit_ask"] = trade["exit_spread"] = None

def sync_paper_quote(quote, stream_gap=False, now=None):
    now = time.time() if now is None else now
    if any(
        isinstance(quote.get(key), bool)
        or not isinstance(quote.get(key), (int, float))
        or not math.isfinite(quote[key])
        for key in ("bid", "ask", "time", "received_at")
    ):
        return
    if (
        not 0 <= now - quote["time"] <= 15
        or not 0 <= now - quote["received_at"] <= 15
        or not 0 < quote["bid"] <= quote["ask"]
    ):
        return
    bucket = int(quote["time"] // 900) * 900
    with Session.begin() as session:
        session.scalar(select(Workspace).where(Workspace.id == WORKSPACE).with_for_update())
        account = session.get(ForexPaperAccount, (WORKSPACE, "live"))
        if account is None or account.blocked:
            return
        for row in session.scalars(select(ForexPaperTrade).where(
            ForexPaperTrade.workspace_id == WORKSPACE,
            ForexPaperTrade.channel == "live",
            ForexPaperTrade.snapshot["status"].as_string() == "open",
        )):
            trade = dict(row.snapshot)
            if trade["status"] != "open":
                continue
            previous = trade.get("last_quote_time")
            if previous is not None and quote["time"] <= previous:
                continue
            if stream_gap or previous is None or quote["time"] - previous > 15:
                trade["live_resume_at"] = bucket + 900
                trade.pop("live_trigger", None)
            trade["last_quote_time"] = quote["time"]
            if trade.get("live_trigger", {}).get("time", bucket) < bucket:
                trade.pop("live_trigger", None)
            if quote["time"] >= trade.get("live_resume_at", 0) and "live_trigger" not in trade:
                direction = 1 if trade["side"] == "long" else -1
                price = quote["bid"] if direction == 1 else quote["ask"]
                if direction * (price - trade["stop"]) <= 0:
                    trade["live_trigger"] = {"price": price, "outcome": "stop", "time": quote["time"], "bid": quote["bid"], "ask": quote["ask"]}
                elif direction * (price - trade["target"]) >= 0:
                    trade["live_trigger"] = {"price": trade["target"], "outcome": "target", "time": quote["time"], "bid": quote["bid"], "ask": quote["ask"]}
            if account.last_candle != bucket - 900 or trade["last_candle"] != bucket - 900:
                trade["monitoring"] = "waiting_history"
            elif quote["time"] < trade.get("live_resume_at", 0):
                trade["monitoring"] = "waiting_closed_bar"
            else:
                trade["monitoring"] = "live"
                if trigger := trade.get("live_trigger"):
                    _resolve_trade(trade, trigger["price"], trigger["outcome"], trigger["time"], "live_quote")
                    trade["exit_bid"], trade["exit_ask"] = trigger["bid"], trigger["ask"]
                    trade["exit_spread"] = trigger["ask"] - trigger["bid"]
                    account.balance += trade["pnl"]
            row.snapshot = trade


def _advance_account(session, account, bid, ask):
    rows = session.scalars(
        select(ForexPaperTrade)
        .where(
            ForexPaperTrade.workspace_id == WORKSPACE,
            ForexPaperTrade.channel == account.channel,
        )
        .order_by(ForexPaperTrade.confirm_time)
    ).all()

    active = [row for row in rows if row.snapshot.get("status") in ("pending", "open")]
    for row in active:
        data = dict(row.snapshot)
        _step_trade(data, bid, ask)
        row.snapshot = data
        if data["status"] == "resolved":
            account.balance += data["pnl"]
        if data["status"] == "censored" and "entry" in data:
            account.blocked = True
    account.last_candle = bid.time


def _queue_trade(session, account, snapshot, reason=None, now=None):
    now = int(time.time()) if now is None else now
    specs = spec_summary(session, now)
    profile = snapshot.get("cost_profile")
    if profile is None and specs["status"] == "partial":
        candidate = specs["profile"]
        if candidate["observed_at"] < snapshot["candle_time"] + 1800:
            profile = candidate
    active = session.scalars(
        select(ForexPaperTrade).where(
            ForexPaperTrade.workspace_id == WORKSPACE,
            ForexPaperTrade.channel == account.channel,
        )
    ).all()
    if reason is None:
        reason = (
            "unresolved_gap"
            if account.blocked
            else "insufficient_balance"
            if account.balance <= 0
            else (
                "position_already_active"
                if any(row.snapshot["status"] in ("pending", "open") for row in active)
                else None
            )
        )
    trade_data = {
        "journal_version": 2,
        "execution_model": "bid_ask_lot_v2" if profile else "bid_ask_oz_v1",
        "recorded_at": now,
        "signal": snapshot.get("signal") or {key: snapshot.get(key) for key in (
            "strategy", "parameters", "reason", "trend", "ema_4h", "closed_4h_time",
            "range_high", "range_low", "range_start", "range_end", "breakout_time",
            "level", "touch_time", "retest_extreme", "confirm_time", "available_at",
            "reference_entry", "stop", "target",
        )},
        "cost_profile": profile,
        "commission_verified": bool(profile and profile["commission_per_unit"] is not None),
        "swap_included": False,
        "workspace_id": WORKSPACE,
        "channel": account.channel,
        "status": "pending",
        "side": snapshot["side"],
        "confirm_time": snapshot["candle_time"],
        "last_candle": snapshot["candle_time"],
        "stop": snapshot["stop"],
        "allocated_risk": max(0, account.balance) * RISK_FRACTION,
        "capital": max(0, account.balance),
        "setup_level": snapshot.get("level"),
        "trend_4h": snapshot.get("trend"),
        "ema_4h": round(snapshot["ema_4h"], 3) if snapshot.get("ema_4h") is not None else None,
        "breakout_level": snapshot.get("level"),
        "breakout_time": snapshot.get("breakout_time"),
        "retest_touch_time": snapshot.get("touch_time"),
        "reference_entry": snapshot.get("reference_entry"),
        "provenance": snapshot["provenance"],
        "slippage_bps": SLIPPAGE_BPS,
        "commission_per_unit": profile["commission_per_unit"] if profile and profile["commission_per_unit"] is not None else COMMISSION_PER_UNIT,
    }
    if reason:
        trade_data.update(status="skipped", reason=reason)
    session.add(
        ForexPaperTrade(
            workspace_id=WORKSPACE,
            channel=account.channel,
            confirm_time=snapshot["candle_time"],
            snapshot=trade_data,
        )
    )


def paper_tick(now=None):
    now = int(time.time()) if now is None else now
    with Session.begin() as session:
        session.scalar(select(Workspace).where(Workspace.id == WORKSPACE).with_for_update())
        frames = load_frames(session, now)
        feeds = [session.get(WorkspaceFeedState, (WORKSPACE, frame)) for frame in FRAMES]
        if audit_frames(frames)["status"] == "blocked" or any(
            state is None
            or state.error
            or state.checked_until is None
            or state.checked_until < now // FRAMES[frame][1] * FRAMES[frame][1]
            for frame, state in zip(FRAMES, feeds)
        ):
            return {"status": "data_paused"}
        bids = {row.time: row for row in frames["15m"] if row.price_type == "bid"}
        asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
        accounts = {}
        for channel in ("live", "replay"):
            account = session.get(ForexPaperAccount, (WORKSPACE, channel))
            if account is None:
                account = ForexPaperAccount(
                    workspace_id=WORKSPACE,
                    channel=channel,
                    started_at=now,
                    last_candle=max(bids),
                    balance=STARTING_BALANCE,
                    blocked=False,
                )
                session.add(account)
            accounts[channel] = account
        session.flush()
        observations = {
            row.candle_time: row.snapshot
            for row in session.scalars(
                select(ForexObservation).where(
                    ForexObservation.workspace_id == WORKSPACE,
                    ForexObservation.candle_time
                    > min(account.last_candle for account in accounts.values()),
                )
            )
        }
        latest_observed = max(observations, default=-1)
        pending = sorted(
            stamp
            for stamp in bids
            if min(account.last_candle for account in accounts.values()) < stamp <= latest_observed
        )[:96]
        for stamp in pending:
            snapshot = observations.get(stamp)
            for account in accounts.values():
                if stamp <= account.last_candle:
                    continue
                if account.channel == "live" and stamp + 1800 <= now:
                    for row in session.scalars(
                        select(ForexPaperTrade).where(
                            ForexPaperTrade.workspace_id == WORKSPACE,
                            ForexPaperTrade.channel == "live",
                        )
                    ):
                        if row.snapshot["status"] == "pending":
                            original = dict(row.snapshot)
                            row.snapshot = {
                                **original,
                                "status": "skipped",
                                "reason": "missed_entry_while_offline",
                            }
                            _queue_trade(
                                session,
                                accounts["replay"],
                                {
                                    **original,
                                    "candle_time": original["confirm_time"],
                                    "provenance": "catch_up",
                                },
                                now=now,
                            )
                _advance_account(session, account, bids[stamp], asks[stamp])
            if snapshot and snapshot["status"] == "confirmed":
                channel = (
                    "live"
                    if snapshot.get("provenance") == "live" and stamp + 1800 > now
                    else "replay"
                )
                _queue_trade(
                    session,
                    accounts[channel],
                    {**snapshot, "provenance": "live" if channel == "live" else "catch_up"},
                    now=now,
                )
            session.flush()
        return {"status": "tracking", "processed": len(pending)}


def forex_paper_summary():
    portfolios = []
    with Session() as session:
        for channel in ("live", "replay"):
            account = session.get(ForexPaperAccount, (WORKSPACE, channel))
            trades = session.scalars(
                select(ForexPaperTrade)
                .where(
                    ForexPaperTrade.workspace_id == WORKSPACE,
                    ForexPaperTrade.channel == channel,
                )
                .order_by(ForexPaperTrade.confirm_time.desc())
            ).all()

            raw_list = [row.snapshot for row in trades]
            resolved = [trade for trade in raw_list if trade.get("status") == "resolved"]
            open_trades = [
                trade
                for trade in raw_list
                if trade.get("status") in ("pending", "open")
                or trade.get("status") == "censored"
                and "entry" in trade
            ]
            pnls = [trade.get("pnl", 0.0) for trade in resolved]
            wins = sum(pnl > 0 for pnl in pnls)
            mark = (
                {
                    side: session.get(
                        WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", side, account.last_candle)
                    )
                    for side in ("bid", "ask")
                }
                if account
                else {}
            )
            unrealized = 0.0
            for trade in open_trades:
                if trade["status"] == "open":
                    direction = 1 if trade["side"] == "long" else -1
                    quote = mark.get("bid" if direction == 1 else "ask")
                    if quote is not None:
                        exit_price = quote.close * (1 - direction * trade["slippage_bps"] / 10000)
                        unrealized += (
                            direction * (exit_price - trade["entry"]) - trade["commission_per_unit"]
                        ) * trade["units"]
            uncertain = bool(account and account.blocked)

            portfolios.append(
                {
                    "channel": channel,
                    "status": "blocked"
                    if account and account.blocked
                    else "tracking"
                    if account
                    else "not_started",
                    "started_at": account.started_at if account else None,
                    "balance": round(account.balance, 2) if account else STARTING_BALANCE,
                    "unrealized_pnl": None if uncertain else round(unrealized, 2),
                    "equity": None
                    if uncertain
                    else round((account.balance if account else STARTING_BALANCE) + unrealized, 2),
                    "starting_balance": STARTING_BALANCE,
                    "last_candle": account.last_candle if account else None,
                    "total_trades": sum(
                        row["status"] not in ("invalid", "skipped") for row in raw_list
                    ),
                    "open_trades_count": len(open_trades),
                    "resolved_count": len(resolved),
                    "win_rate": (wins / len(resolved)) if resolved else None,
                    "total_pnl": round(sum(pnls), 2),
                    "report": trade_report(raw_list, STARTING_BALANCE),
                    "open_trades": open_trades,
                    "recent_trades": raw_list[:20],
                }
            )
        calibration = spec_summary(session)
    return {
        "status": "tracking"
        if any(row["started_at"] is not None for row in portfolios)
        else "not_started",
        "message": "Hai ví paper riêng Exness; không gửi lệnh Demo, không dùng ví MEXC",
        "calibration": calibration,
        "assumptions": {
            "risk_percent": RISK_FRACTION * 100,
            "max_notional_equity": 1,
            "slippage_bps_each_side": SLIPPAGE_BPS,
            "round_trip_commission_per_oz": COMMISSION_PER_UNIT,
            "swap_included": False,
            "costs_calibrated": False,
            "same_bar_policy": "stop_first",
            "entry": "next_closed_m15_open_bid_ask",
            "live_exit": "fresh_bid_ask_after_history_catchup",
            "stream_gap_policy": "wait_for_closed_m15",
            "lot_constraints_verified": False,
        },
        "portfolios": portfolios,
    }
