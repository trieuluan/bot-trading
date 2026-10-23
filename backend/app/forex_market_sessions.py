"""Read-only broker UTC calendar; independent of execution and gap calendars."""

import math
import re
from datetime import datetime, timezone

from .forex_costs import SPEC_TTL, account_tag

WEEK = 7 * 86400
DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def _week_second(value):
    if not isinstance(value, str):
        raise ValueError("invalid_schedule")
    match = re.fullmatch(r"(Sun|Mon|Tue|Wed|Thu|Fri|Sat), ([0-2][0-9]):([0-5][0-9]):([0-5][0-9])", value)
    if not match or int(match[2]) > 23:
        raise ValueError("invalid_schedule")
    return DAYS.index(match[1]) * 86400 + int(match[2]) * 3600 + int(match[3]) * 60 + int(match[4])


def _intervals(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing_schedule" if rows is None or rows == [] else "invalid_schedule")
    intervals = []
    for row in rows:
        if not isinstance(row, dict) or row.get("trade_mode") not in ("enabled", "trading_disabled"):
            raise ValueError("invalid_schedule")
        start, end = _week_second(row.get("week_start_time")), _week_second(row.get("week_end_time")) + 1
        enabled = row["trade_mode"] == "enabled"
        if end <= start:
            intervals.extend(((start, WEEK, enabled), (0, end, enabled)))
        else:
            intervals.append((start, end, enabled))
    intervals.sort()
    if any(previous[1] > current[0] for previous, current in zip(intervals, intervals[1:])):
        raise ValueError("overlapping_schedule")
    merged = []
    for shift in (-WEEK, 0, WEEK, 2 * WEEK):
        for start, end, enabled in intervals:
            start, end = start + shift, end + shift
            if merged and merged[-1][1] == start and merged[-1][2] == enabled:
                merged[-1] = (merged[-1][0], end, enabled)
            else:
                merged.append((start, end, enabled))
    return merged


def market_session(specs, now, symbol, account_id=None):
    result = {
        "status": "unknown", "reason": "missing_schedule", "as_of": now,
        "observed_at": None, "next_open_at": None, "next_close_at": None,
        "source": "exness_trade_sessions",
    }
    profile = specs.get("profile")
    if not isinstance(profile, dict):
        return result
    observed = profile.get("observed_at")
    valid_stamp = isinstance(observed, (int, float)) and not isinstance(observed, bool) and math.isfinite(observed)
    result["observed_at"] = observed if valid_stamp else None
    if profile.get("instrument") != symbol or account_id and profile.get("account_tag") != account_tag(account_id):
        return {**result, "reason": "identity_mismatch"}
    if not valid_stamp or observed > now:
        return {**result, "reason": "invalid_timestamp"}
    if now - observed >= SPEC_TTL:
        return {**result, "reason": "stale_schedule"}
    if specs.get("error"):
        return {**result, "reason": "sync_error"}
    try:
        intervals = _intervals(profile.get("trade_sessions"))
    except ValueError as exc:
        return {**result, "reason": str(exc)}
    instant = datetime.fromtimestamp(now, timezone.utc)
    offset = (instant.weekday() + 1) % 7 * 86400 + instant.hour * 3600 + instant.minute * 60 + instant.second
    week_start = math.floor(now) - offset
    current_index = next((index for index, (start, end, _) in enumerate(intervals) if start <= offset < end), None)
    if current_index is None:
        return {**result, "reason": "uncovered_time"}
    start, end, enabled = intervals[current_index]
    weekend = any(start <= 6 * 86400 + shift < end for shift in (-WEEK, 0, WEEK))
    result.update(status="open" if enabled else "closed",
                  reason="scheduled_open" if enabled else "weekend" if weekend else "session_break")
    for following_start, following_end, following_enabled in intervals[current_index + 1:]:
        if following_start != end:
            break
        key = "next_open_at" if following_enabled else "next_close_at"
        if result[key] is None:
            result[key] = week_start + following_start
        if all(result[key] is not None for key in ("next_open_at", "next_close_at")):
            break
        end = following_end
    return result
