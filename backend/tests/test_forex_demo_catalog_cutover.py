import gzip
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.forex_detectors import H1_DETECTORS
from backend.app.forex_entry_policy import BASE_14_SETUPS, G0_CATALOG, make_v4_g0_bundle, tree_id
from backend.app.forex_htf_v4 import new_ledger, read_ledger
from backend.app.forex_migration import atomic_json
from scripts.forex_demo_catalog_cutover import replace_ledger


def bar(stamp, close=100, high=101, low=99):
    return SimpleNamespace(time=stamp, open=100, close=close, high=high, low=low, tick_volume=100)


@pytest.mark.parametrize("symbol", ("EURUSD", "USDJPY", "XAUUSD"))
def test_catalog_valid_unique_and_unrestricted_session(symbol):
    trees, bundles = make_v4_g0_bundle(symbol)
    assert len(trees) == len({tree_id(tree) for tree in trees}) == 56
    assert len(bundles) == 14
    assert len(set(BASE_14_SETUPS)) == 14
    assert {"nr7", "asia_break", "london_orb_h1"} <= {row[0] for row in BASE_14_SETUPS}
    assert not {"fvg", "mss"} & {row[0] for row in BASE_14_SETUPS}
    assert all(len(tree["root"]["children"]) == 5 for tree in trees)


def test_cutover_archives_clears_and_is_idempotent(tmp_path):
    path = tmp_path / "ledger.json"
    previous = new_ledger("test", "EURUSD", 10000)
    previous["manifest"].pop("catalog")
    previous["samples"] = [{"status": "resolved", "strategy": "legacy"}]
    atomic_json(path, previous)
    raw = path.read_bytes()
    assert replace_ledger(path, "test", "EURUSD", 20000) == "replaced"
    result = read_ledger("test", "EURUSD", path=path)
    assert result["samples"] == []
    assert result["members"] and len(result["members"]) == 56
    assert "bundles" not in result and "retired_bundles" not in result and result["assignments"] == {}
    assert result["manifest"]["catalog"] == G0_CATALOG
    assert result["cohort_id"] != previous["cohort_id"]
    from pathlib import Path
    assert gzip.decompress(Path(result["migration"]["archive"]).read_bytes()) == raw
    saved = path.read_bytes()
    assert replace_ledger(path, "test", "EURUSD", 30000) == "already_current"
    assert path.read_bytes() == saved


def test_bad_identity_cannot_archive_or_replace(tmp_path):
    path = tmp_path / "ledger.json"
    atomic_json(path, new_ledger("other", "EURUSD", 10000))
    saved = path.read_bytes()
    with pytest.raises(ValueError, match="identity mismatch"):
        replace_ledger(path, "test", "EURUSD", 20000)
    assert path.read_bytes() == saved
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("month,opening_utc", ((1, 8), (7, 7)))
def test_london_breakout_dst_and_no_early_or_repeated_cross(month, opening_utc):
    day = int(datetime(2026, month, 7, tzinfo=timezone.utc).timestamp())
    rows = [bar(day + hour * 3600) for hour in range(opening_utc + 2)]
    assert H1_DETECTORS["london_orb_h1"](rows[:-1], "long") is None
    rows = [bar(day - (20 - index) * 3600) for index in range(20)] + rows
    rows[-1] = bar(rows[-1].time, close=103, high=104)
    setup = H1_DETECTORS["london_orb_h1"](rows, "long")
    assert setup and setup["reference_session"]["start"] == day + opening_utc * 3600
    assert H1_DETECTORS["london_orb_h1"](rows + [bar(rows[-1].time + 3600, close=104, high=105)], "long") is None


def test_asia_break_needs_complete_range_and_no_future_bars():
    rows = [bar(hour * 3600) for hour in range(-20, 10)]
    rows[-1] = bar(9 * 3600, close=103, high=104)
    setup = H1_DETECTORS["asia_break"](rows, "long")
    assert setup and setup["level"] == 101
    assert H1_DETECTORS["asia_break"]([row for row in rows if row.time != 3600], "long") is None
    assert H1_DETECTORS["asia_break"](rows, "short") is None


def test_nr7_reuses_narrowest_range_definition():
    rows = [bar(hour * 3600) for hour in range(15)]
    rows[-1] = bar(14 * 3600, close=100.1, high=100.2, low=99.8)
    assert H1_DETECTORS["nr7"](rows, "long")["level"] == 100.2
    rows[-1] = bar(14 * 3600, close=100.1, high=102, low=98)
    assert H1_DETECTORS["nr7"](rows, "long") is None
