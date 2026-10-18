"""Reproducible 1x USDT perpetual research; not an exchange execution simulator."""

import math
from dataclasses import asdict, dataclass

from .market import Candle
from .mexc import validate_candles
from .strategy import breakout, retest
from .futures_exits import EXIT_POLICIES, adaptive_target, partial_size, protective_stop


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 10_000
    risk_percent: float = 1
    fee_bps: float = 5
    slippage_bps: float = 2


def simulate(
    candles: list[Candle],
    config: BacktestConfig,
    funding: list[dict],
    fair_candles: list[Candle],
    contract: dict,
    warmup: int = 20,
    signal=breakout,
    window: int = 21,
    exit_policy: str = "fixed",
) -> dict:
    """Signals at close, next-open entries; conservative OHLC exits, funding at open."""
    validate_candles(candles)
    if exit_policy not in EXIT_POLICIES:
        raise ValueError("Invalid futures exit policy")
    if len(candles) <= warmup:
        raise ValueError("Not enough candles after strategy warmup")
    cash = config.initial_capital
    fee = config.fee_bps / 10_000
    slip = config.slippage_bps / 10_000
    size = float(contract["contractSize"])
    lot = float(contract["volUnit"])
    mark = {c.time: c for c in fair_candles}
    rates = {record["time"]: record["rate"] for record in funding}
    pending = None
    position = None
    trades = []
    curve = []
    peak = cash
    max_drawdown = 0.0

    for index in range(warmup, len(candles)):
        candle = candles[index]
        # Settlement is before exits/new entries at this bar's open. New positions do not pay.
        if position is not None and candle.time in rates:
            payment = (
                position["direction"]
                * position["quantity"]
                * mark[candle.time].open
                * rates[candle.time]
            )
            cash -= payment
            position["funding_cost"] += payment

        if pending is not None and cash > 0:
            setup, signal_time = pending
            direction = 1 if setup.side == "long" else -1
            entry = candle.open * (1 + direction * slip)
            if direction * (entry - setup.stop) > 0 and direction * (setup.target - entry) > 0:
                quantity = min(
                    cash * config.risk_percent / 100 / abs(entry - setup.stop),
                    cash / (entry * (1 + fee)),  # Cap notional at 1x available equity.
                )
                contracts = min(
                    math.floor(quantity / size / lot) * lot,
                    math.floor(float(contract["maxVol"]) / lot) * lot,
                )
                quantity = contracts * size
                if contracts >= float(contract["minVol"]):
                    entry_fee = entry * quantity * fee
                    cash -= entry_fee
                    position = {
                        "side": setup.side,
                        "direction": direction,
                        "signal_time": signal_time,
                        "entry_time": candle.time,
                        "entry": entry,
                        "quantity": quantity,
                        "contracts": contracts,
                        "entry_fee": entry_fee,
                        "funding_cost": 0.0,
                        "stop": setup.stop,
                        "target": setup.target,
                        "risk": abs(entry - setup.stop),
                        "exit_policy": exit_policy,
                    }
        pending = None

        if position is not None:
            direction = position["direction"]
            stop = position["stop"]
            target = position["target"]
            exit_price = None
            reason = ""
            gap_stop = candle.open <= stop if direction == 1 else candle.open >= stop
            touch_stop = candle.low <= stop if direction == 1 else candle.high >= stop
            touch_target = candle.high >= target if direction == 1 else candle.low <= target
            if gap_stop:
                exit_price, reason = candle.open, "stop_gap"
            elif touch_stop:
                exit_price, reason = stop, "stop"
            elif touch_target:
                exit_price, reason = target, "target"
            elif index == len(candles) - 1:
                exit_price, reason = candle.close, "end_of_period"
            if exit_price is not None:
                exit_price *= 1 - direction * slip
                gross = direction * (exit_price - position["entry"]) * position["quantity"]
                exit_fee = exit_price * position["quantity"] * fee
                cash += gross - exit_fee
                fees = position["entry_fee"] + exit_fee + position.get("realized_exit_fee", 0)
                trades.append(
                    {
                        **position,
                        "exit_time": candle.time,
                        "exit": exit_price,
                        "exit_reason": reason,
                        "fees": fees,
                        "net_pnl": gross + position.get("realized_gross", 0) - fees - position["funding_cost"],
                    }
                )
                position = None
            else:
                history = candles[max(0, index - 99):index + 1]
                quantity = partial_size(position, history, size * lot, size * float(contract["minVol"]))
                if quantity:
                    fill = candle.close * (1 - direction * slip)
                    gross = direction * (fill - position["entry"]) * quantity
                    exit_fee = fill * quantity * fee
                    cash += gross - exit_fee
                    position.update(original_quantity=position["quantity"],
                                    quantity=position["quantity"] - quantity,
                                    contracts=(position["quantity"] - quantity) / size,
                                    realized_gross=gross, realized_exit_fee=exit_fee,
                                    partial_exits=[dict(time=candle.time + 900, price=fill, quantity=quantity)])
                position["target"] = adaptive_target(position, history)
                position["stop"] = protective_stop(
                    {**position, "entry_fee": position["entry_fee"] * position["quantity"]
                     / position.get("original_quantity", position["quantity"])}, history, fee, slip,
                )

        equity = cash
        if position is not None:
            liquidation_price = mark[candle.time].close * (1 - position["direction"] * slip)
            equity += (
                position["direction"]
                * (liquidation_price - position["entry"])
                * position["quantity"]
            )
            equity -= liquidation_price * position["quantity"] * fee
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        curve.append({"time": candle.time, "equity": round(equity, 6)})
        if position is None and index < len(candles) - 1 and cash > 0:
            setup = signal(candles[max(0, index + 1 - window) : index + 1], allow_short=True)
            if setup:
                pending = (setup, candle.time)

    profit = sum(max(0, trade["net_pnl"]) for trade in trades)
    loss = -sum(min(0, trade["net_pnl"]) for trade in trades)
    return {
        "start_time": candles[warmup].time,
        "end_time": candles[-1].time + 900,
        "bars": len(candles) - warmup,
        "initial_capital": config.initial_capital,
        "final_equity": cash,
        "net_pnl": cash - config.initial_capital,
        "return_percent": (cash / config.initial_capital - 1) * 100,
        "max_drawdown_percent": max_drawdown,
        "trade_count": len(trades),
        "win_rate_percent": sum(t["net_pnl"] > 0 for t in trades) / len(trades) * 100
        if trades
        else 0,
        "profit_factor": profit / loss if loss else None,
        "total_fees": sum(t["fees"] for t in trades),
        "funding_cost": sum(t["funding_cost"] for t in trades),
        "trades": trades,
        "equity_curve": curve,
    }


def run_backtest(
    candles: list[Candle],
    config: BacktestConfig,
    funding: list[dict],
    fair_candles: list[Candle],
    contract: dict,
) -> dict:
    if len(candles) < 100:
        raise ValueError("At least 100 contiguous closed candles are required")
    if (
        not all(math.isfinite(value) for value in asdict(config).values())
        or config.initial_capital <= 0
        or not 0 < config.risk_percent <= 5
        or not 0 <= config.fee_bps <= 100
        or not 0 <= config.slippage_bps <= 100
    ):
        raise ValueError("Invalid backtest parameters")
    validate_candles(candles)
    validate_candles(fair_candles)
    if [c.time for c in candles] != [c.time for c in fair_candles]:
        raise ValueError("Fair price and trade candles must cover the same period")
    split = int(len(candles) * 0.7)
    common = {
        "config": config,
        "funding": funding,
        "fair_candles": fair_candles,
        "contract": contract,
    }
    retest_args = {**common, "signal": retest, "window": 32}
    return {
        "strategy": "structure_breakout_long_short_v1",
        "parameters": asdict(config),
        "split_time": candles[split].time,
        "assumptions": [
            "BTC USDT perpetual, long/short, one position, notional capped at 1x equity",
            "Signal at close; enter at NEXT candle open with adverse slippage",
            "Stop first if stop and target touched in one bar; stop gaps fill at open",
            "Contract quantity rounded down using current contract size and lot step",
            "Fees on both sides; configurable assumptions, not account fee quotes",
            "Historical funding rates with fair-price candle OPEN as settlement-price proxy",
            "Funding charged before exits/entries at settlement bar open; timing approximation",
            "Close positions at each period end; each period starts with fresh capital",
            "70/30 chronological split, fixed rules, no optimization on either period",
            "Candle-close mark equity drawdown; no liquidation, tier changes, ADL or partial fills",
        ],
        "full": simulate(candles, **common),
        "in_sample": simulate(candles[:split], **common),
        "out_of_sample": simulate(candles[split - 20 :], **common),
        "retest": {
            "strategy": "breakout_retest_v1",
            "full": simulate(candles, **retest_args),
            "in_sample": simulate(candles[:split], **retest_args),
            "out_of_sample": simulate(candles[split - 20 :], **retest_args),
        },
    }
