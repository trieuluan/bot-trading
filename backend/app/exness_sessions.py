"""Standard XAUUSD candle closures, not a complete broker/holiday calendar.

Reference: https://get.exness.help/hc/en-us/articles/4405235684498-Instrument-trading-hours
ponytail: only fully closed candle buckets are recognized; exceptional hours need a dated calendar.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def _closed_bucket(stamp, step):
    def closed(instant):
        local = datetime.fromtimestamp(instant, NEW_YORK)
        weekday, hour = local.weekday(), local.hour
        return (weekday == 5 or weekday == 4 and hour >= 17
                or weekday == 6 and hour < 18
                or weekday < 4 and hour == 17)

    return closed(stamp) and closed(stamp + step - 1)


def session_closed_ahead(stamp, bars=1, step=900):
    """True if any of the next `bars` candle buckets after `stamp` is a scheduled closure."""
    return any(_closed_bucket(stamp + step * n, step) for n in range(1, bars + 1))

def regular_session_gap(after, before, step=900):
    if (step not in (900, 3600, 14400) or after % step or before % step
            or not step < before - after <= 3 * 86400):
        return False
    return (not _closed_bucket(after, step) and not _closed_bucket(before, step)
            and all(_closed_bucket(stamp, step) for stamp in range(after + step, before, step)))


def gap_details(after, before, step=900):
    return {
        "after": after, "before": before,
        "missing_intervals": max(0, (before - after) // step - 1),
        "classification": "regular_session_break" if regular_session_gap(after, before, step)
        else "unverified_gap",
    }
