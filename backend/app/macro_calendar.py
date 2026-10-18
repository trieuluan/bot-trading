"""US macro event calendar (FOMC, CPI, NFP) used only as a "stay out" filter.

Only dates verified on official pages are listed: FOMC 2025-2026 (federalreserve.gov), BLS CPI/NFP for
Jan-Mar and Sep-Dec 2025 and Oct-Dec 2026 (bls.gov schedule pages). CPI/NFP for Apr-Aug 2025 and
Jan-Sep 2026 are NOT verified (BLS blocks scripted downloads), so macro_safe is blind there.
ponytail: load the BLS .ics once it is downloadable, then replay results cover the full year.
"""
from bisect import bisect_left
from datetime import datetime, timezone

# Official FOMC decision release times (day 2 of each meeting, 18:00 UTC EDT / 19:00 UTC EST)
FOMC_EVENTS = [
    "2025-01-29 19:00", "2025-03-19 18:00", "2025-05-07 18:00", "2025-06-18 18:00",
    "2025-07-30 18:00", "2025-09-17 18:00", "2025-10-29 18:00", "2025-12-10 19:00",
    "2026-01-28 19:00", "2026-03-18 18:00", "2026-04-29 18:00", "2026-06-17 18:00",
    "2026-07-29 18:00", "2026-09-16 18:00", "2026-10-28 18:00", "2026-12-09 19:00",
]

# BLS CPI, 08:30 ET (verified entries only)
CPI_EVENTS = [
    "2025-01-15 13:30", "2025-02-12 13:30", "2025-03-12 12:30", "2025-09-11 12:30",
    "2025-10-24 12:30", "2025-12-18 13:30",
    "2026-10-14 12:30", "2026-11-10 13:30", "2026-12-10 13:30",
]

# BLS Employment Situation (NFP), 08:30 ET (verified entries only)
NFP_EVENTS = [
    "2025-01-10 13:30", "2025-02-07 13:30", "2025-03-07 13:30", "2025-09-05 12:30",
    "2025-11-20 13:30", "2025-12-16 13:30",
    "2026-10-02 12:30", "2026-11-06 13:30", "2026-12-04 13:30",
]

def _parse_events(strings):
    result = []
    for s in strings:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        result.append(int(dt.timestamp()))
    return sorted(result)

MACRO_TIMESTAMPS = _parse_events(FOMC_EVENTS + CPI_EVENTS + NFP_EVENTS)

def is_macro_safe(timestamp: int, window_seconds: int = 4 * 3600) -> bool:
    """True if timestamp is at least window_seconds away from any major US macro event."""
    if not MACRO_TIMESTAMPS:
        return True
    idx = bisect_left(MACRO_TIMESTAMPS, timestamp)
    if idx < len(MACRO_TIMESTAMPS) and abs(MACRO_TIMESTAMPS[idx] - timestamp) < window_seconds:
        return False
    if idx > 0 and abs(MACRO_TIMESTAMPS[idx - 1] - timestamp) < window_seconds:
        return False
    return True
