"""US macro event calendar (FOMC, CPI, NFP) used only as a "stay out" filter.

Verified against official pages: FOMC 2025-2026 (federalreserve.gov) and CPI/NFP from Nov 2026
(bls.gov). UNVERIFIED (entered from memory, BLS blocks scripted downloads): CPI/NFP for 2025 and
Jan-Oct 2026. ponytail: replace with a BLS .ics export before trusting replay results on those dates.
"""
from bisect import bisect_left
from datetime import datetime, timezone

# Official FOMC decision release times (day 2 of each meeting, 18:00 UTC EDT / 19:00 UTC EST)
FOMC_EVENTS = [
    # 2025
    "2025-01-29 19:00", "2025-03-19 18:00", "2025-05-07 18:00", "2025-06-18 18:00",
    "2025-07-30 18:00", "2025-09-17 18:00", "2025-10-29 18:00", "2025-12-10 19:00",
    # 2026
    "2026-01-28 19:00", "2026-03-18 18:00", "2026-04-29 18:00", "2026-06-17 18:00",
    "2026-07-29 18:00", "2026-09-16 18:00", "2026-10-28 18:00", "2026-12-09 19:00",
]

# US CPI release dates (08:30 AM ET -> 12:30 UTC EDT / 13:30 UTC EST)
CPI_EVENTS = [
    # 2025
    "2025-01-15 13:30", "2025-02-12 13:30", "2025-03-12 12:30", "2025-04-10 12:30",
    "2025-05-13 12:30", "2025-06-11 12:30", "2025-07-11 12:30", "2025-08-12 12:30",
    "2025-09-11 12:30", "2025-10-15 12:30", "2025-11-13 13:30", "2025-12-10 13:30",
    # 2026
    "2026-01-14 13:30", "2026-02-11 13:30", "2026-03-11 12:30", "2026-04-14 12:30",
    "2026-05-12 12:30", "2026-06-10 12:30", "2026-07-14 12:30", "2026-08-12 12:30",
    "2026-09-15 12:30", "2026-10-14 12:30", "2026-11-10 13:30", "2026-12-10 13:30",
]

# US Employment Situation / NFP (First Friday of month, 08:30 AM ET -> 12:30 / 13:30 UTC)
NFP_EVENTS = [
    # 2025
    "2025-01-10 13:30", "2025-02-07 13:30", "2025-03-07 13:30", "2025-04-04 12:30",
    "2025-05-02 12:30", "2025-06-06 12:30", "2025-07-03 12:30", "2025-08-01 12:30",
    "2025-09-05 12:30", "2025-10-03 12:30", "2025-11-07 13:30", "2025-12-05 13:30",
    # 2026
    "2026-01-09 13:30", "2026-02-06 13:30", "2026-03-06 13:30", "2026-04-03 12:30",
    "2026-05-08 12:30", "2026-06-05 12:30", "2026-07-02 12:30", "2026-08-07 12:30",
    "2026-09-04 12:30", "2026-10-02 12:30", "2026-11-06 13:30", "2026-12-04 13:30",
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
