"""Auditable paper-only statistics and CSV journal, with separate Live/Replay samples."""

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from .db import ForexPaperTrade, Session
from .exness_feed import WORKSPACE

def trade_report(trades, starting_balance):
    closed = sorted(
        (row for row in trades if row.get("status") == "resolved"),
        key=lambda row: (row.get("exit_time", 0), row.get("confirm_time", 0)),
    )
    pnls = [row.get("pnl", 0.0) for row in closed]
    values_r = [row["net_r"] for row in closed if row.get("net_r") is not None]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    balance = peak = starting_balance
    max_drawdown = max_drawdown_percent = 0.0
    for pnl in pnls:
        balance += pnl
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)
        max_drawdown_percent = max(max_drawdown_percent, (peak - balance) / peak * 100)
    groups = {}
    for field in ("side", "exit_source", "execution_model", "exit_month_utc"):
        buckets = defaultdict(list)
        for row in closed:
            label = datetime.fromtimestamp(row["exit_time"], timezone.utc).strftime("%Y-%m") if field == "exit_month_utc" and row.get("exit_time") is not None else row.get(field) or "unknown"
            buckets[label].append(row)
        groups[field] = {
            label: {
                "count": len(rows), "pnl": sum(row.get("pnl", 0) for row in rows),
                "mean_r": sum(row["net_r"] for row in rows if row.get("net_r") is not None)
                / sum(row.get("net_r") is not None for row in rows)
                if any(row.get("net_r") is not None for row in rows) else None,
            }
            for label, rows in sorted(buckets.items())
        }
    return {
        "resolved_count": len(closed), "r_sample_count": len(values_r),
        "expectancy_r": sum(values_r) / len(values_r) if values_r else None,
        "sum_r": sum(values_r) if values_r else None,
        "profit_factor": gains / losses if losses else None,
        "profit_factor_status": "available" if losses else "no_losses" if closed else "no_sample",
        "max_closed_drawdown_usd": max_drawdown,
        "max_closed_drawdown_percent": max_drawdown_percent,
        "status_counts": dict(Counter(row.get("status", "unknown") for row in trades)),
        "outcome_counts": dict(Counter(row.get("outcome", "unknown") for row in closed)),
        "known_commission_usd": sum(row.get("commission_usd", 0) for row in closed if row.get("commission_verified")),
        "modeled_slippage_usd": sum(row["total_slippage_usd"] for row in closed if row.get("total_slippage_usd") is not None),
        "slippage_coverage_count": sum(row.get("total_slippage_usd") is not None for row in closed),
        "commission_unverified_count": sum(not row.get("commission_verified", False) for row in closed),
        "swap_excluded_count": sum(not row.get("swap_included", False) for row in closed),
        "legacy_journal_count": sum(row.get("journal_version", 0) < 2 for row in trades),
        "first_confirmation": min((row["confirm_time"] for row in trades), default=None),
        "last_exit": max((row["exit_time"] for row in closed if row.get("exit_time") is not None), default=None),
        "groups": groups,
        "basis": "closed_trades_only_excludes_unverified_costs",
    }

CSV_FIELDS = (
    "channel", "journal_version", "execution_model", "confirm_time", "side", "status",
    "signal.strategy", "signal.trend", "signal.ema_4h", "signal.closed_4h_time",
    "signal.breakout_time", "signal.level", "signal.touch_time", "signal.retest_extreme",
    "signal.reason", "entry_time", "entry_bid", "entry_ask", "entry_spread", "entry_price_raw",
    "entry", "units", "lots", "stop", "target", "allocated_risk", "entry_slippage_usd",
    "exit_time", "exit_bid", "exit_ask", "exit_spread", "exit_price_raw", "exit", "exit_source",
    "outcome", "reason", "ambiguous_bar", "exit_time_precision", "slippage_bps", "exit_slippage_usd", "total_slippage_usd",
    "commission_usd", "commission_verified", "swap_included", "gross_pnl", "pnl", "net_r",
    "cost_profile.observed_at", "cost_profile.commission_type", "holding_seconds",
)

def journal_csv(channel):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_FIELDS)
    yield buffer.getvalue()
    with Session() as session:
        rows = session.scalars(select(ForexPaperTrade).where(
            ForexPaperTrade.workspace_id == WORKSPACE, ForexPaperTrade.channel == channel,
        ).order_by(ForexPaperTrade.confirm_time).execution_options(yield_per=100))
        for row in rows:
            values = []
            for field in CSV_FIELDS:
                value = {**row.snapshot, "channel": channel}
                for part in field.split("."):
                    value = value.get(part) if isinstance(value, dict) else None
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                values.append(value)
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(values)
            yield buffer.getvalue()
