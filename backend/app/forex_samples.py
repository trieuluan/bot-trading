"""Shared forward sample simulation and measured costs; no strategy selection."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .exness import ExnessError
from .forex_buffer_study import advance_variant, new_variants
from .forex_indicators import ema

REPORT = Path(__file__).resolve().parents[2] / "research-output/exness-learning.json"


def context(candle, hours, ask):
    closed = [row for row in hours if row.time + 3600 <= candle.time + 900][-60:]
    closes = [row.close for row in closed]
    trend = "unknown"
    if len(closes) >= 50:
        trend = "up" if ema(closes, 20) > ema(closes, 50) else "down"
    spread = max(0, ask.close - candle.close)
    return {
        "trend": trend,
        "utc_session": datetime.fromtimestamp(candle.time + 900, timezone.utc).hour // 6,
        "spread": spread,
        "body": abs(candle.close - candle.open),
        "range": candle.high - candle.low,
    }


def slippage_estimate(symbol="XAUUSD"):
    path = REPORT.with_name("exness-slippage-report.json" if symbol == "XAUUSD" else f"exness-slippage-report-{symbol}.json")
    if not path.exists():
        return {"bps": 0.0, "measured": False, "fills": 0}
    try:
        audit = json.loads(path.read_text())
        value = audit.get("per_side_mean_bps")
        if (
            not audit.get("demo_only")
            or audit.get("fills", 0) < 2
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return {"bps": 0.0, "measured": False, "fills": 0}
        return {
            "bps": min(100.0, max(0.0, float(value))),
            "measured": True,
            "fills": audit["fills"],
            "measured_at": audit["as_of"],
        }
    except Exception:
        return {"bps": 0.0, "measured": False, "fills": 0}


def attach_execution(sample, minutes, hours, higher, features, candle, condition):
    if not sample["signal"].get("generated"):
        return
    if not condition or any(
        condition.get(key) is None
        for key in ("volume_min", "volume_max", "volume_step", "contract_size", "point_digits")
    ):
        sample.update(status="excluded", reason="missing_instrument_specs")
        return
    try:
        sample["execution"] = new_variants(
            sample["signal"], minutes, hours, higher, features, candle, condition
        )[0]
    except ExnessError as exc:
        sample.update(status="excluded", reason=str(exc))


def advance_learning_sample(sample, candle, ask, minutes, slippage_bps, commission, hours=()):
    if not sample["signal"].get("generated"):
        return
    row = sample.get("execution")
    if row is None:
        sample.update(status="excluded", reason="execution_model_changed")
        return
    advance_variant(row, candle, ask, minutes, slippage_bps / 10000, commission, hours=hours)
    sample.update(status=row["status"], last_candle=row["last_candle"])
    if row["status"] == "resolved":
        sample.update(
            net_r_ex_swap=row["net_r"],
            label_available_at=row["resolved_at"],
            entry=row["entry"],
            entry_time=row["entry_time"],
            risk=row["risk"],
        )
    if sample["signal"].get("htf_ast_v2"):
        sample["management_history"] = row.get("management_history", [])
        for key in ("entry", "entry_time", "risk", "initial_stop", "partial_split", "legs"):
            if key in row:
                sample[key] = row[key]
    if row["status"] not in ("pending", "open"):
        sample["execution_result"] = {
            key: row[key] for key in ("reason", "tp_reason", "legs") if key in row
        }
        sample.pop("execution", None)
