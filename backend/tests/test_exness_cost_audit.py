import io
import json
import math
import time

import pytest

from scripts import exness_cost_audit
from scripts.exness_cost_audit import spread_sample, summarize


def test_spread_uses_midpoint_not_slippage():
    sample = spread_sample(4000, 4000.26)
    assert sample[0] == pytest.approx(0.26)
    assert sample[1] == pytest.approx(0.26 / 4000.13 * 10000)
    assert spread_sample(4000, 4000) == (0, 0)


@pytest.mark.parametrize("bid,ask", [(0, 1), (2, 1), (True, 2), (1, math.nan),
                                    (1, math.inf), (None, 2), (1, "2")])
def test_rejects_invalid_quotes(bid, ask):
    assert spread_sample(bid, ask) is None


def test_empty_and_single_sample_statistics():
    assert summarize([]) == {"samples": 0, "usd_per_oz": None, "bps_of_mid": None}
    assert summarize([(0.2, 0.5)])["bps_of_mid"] == {
        "mean": 0.5, "median": 0.5, "p95": 0.5, "max": 0.5}


def test_live_rejects_stale_quotes_and_deduplicates_snapshots(monkeypatch):
    now = time.time()
    live = {"status": "live", "symbol": "XAUUSD", "quote": {
        "time": now, "received_at": now, "bid": 4000, "ask": 4000.26}}
    stale = {**live, "quote": {**live["quote"], "time": now - 30}}
    payload = b"".join(b"data: " + json.dumps(row).encode() + b"\n\n"
                       for row in (live, live, stale))
    monkeypatch.setattr(exness_cost_audit, "urlopen", lambda *args, **kwargs: io.BytesIO(payload))
    result = exness_cost_audit.live_spreads(1)
    assert result["samples"] == 1
    assert result["rejected_snapshots"] == 1
    assert result["error"] == "ValueError"
