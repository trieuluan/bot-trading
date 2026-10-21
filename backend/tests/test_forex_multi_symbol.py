import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.db import Session, WorkspaceCandle
from backend.app.exness_execution import capacity_available, max_positions, max_positions_per_symbol
from backend.app.exness_management import size_lot
from backend.app.forex_conversion import conversion_to_usd
from backend.app.forex_htf_v2 import ledger_path
from backend.app.forex_migration import migrate_symbol_ledger
from backend.app.forex_symbols import SYMBOLS, checked_symbol, configured_symbols

NOW = 1791900000


def test_eleven_symbols_configured(monkeypatch):
    assert len(SYMBOLS) == 11
    assert "XAUUSD" in SYMBOLS and "EURUSD" in SYMBOLS and "GBPJPY" in SYMBOLS
    monkeypatch.delenv("EXNESS_SYMBOLS", raising=False)
    monkeypatch.setenv("EXNESS_SYMBOL", "EURUSD")
    assert configured_symbols() == ("EURUSD",)
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD, EURUSD, GBPUSD")
    assert configured_symbols() == ("XAUUSD", "EURUSD", "GBPUSD")


def test_checked_symbol_rejects_unsupported():
    assert checked_symbol("EURUSD") == "EURUSD"
    with pytest.raises(ValueError, match="Unsupported Forex symbol"):
        checked_symbol("BTCUSDT")


def test_conversion_direct_usd():
    conv = conversion_to_usd("USD", now=NOW)
    assert conv["verified"] is True
    assert conv["rate"] == Decimal("1")
    assert conv["direction"] == "direct"


def test_conversion_divide_jpy():
    with Session.begin() as s:
        s.add(WorkspaceCandle(workspace_id="exness_forex", symbol="USDJPY", timeframe="15m",
                              price_type="bid", time=NOW - 900, open=150.0, high=150.5, low=149.8, close=150.0))
        s.add(WorkspaceCandle(workspace_id="exness_forex", symbol="USDJPY", timeframe="15m",
                              price_type="ask", time=NOW - 900, open=150.02, high=150.52, low=149.82, close=150.02))
    conv = conversion_to_usd("JPY", now=NOW)
    assert conv["verified"] is True
    assert conv["pair"] == "USDJPY"
    assert conv["direction"] == "divide_usdjpy"
    assert conv["rate"] == Decimal("1") / Decimal("150.0")


def test_conversion_stale_candle_fails():
    with Session.begin() as s:
        s.add(WorkspaceCandle(workspace_id="exness_forex", symbol="USDJPY", timeframe="15m",
                              price_type="bid", time=NOW - 5000, open=150.0, high=150.5, low=149.8, close=150.0))
        s.add(WorkspaceCandle(workspace_id="exness_forex", symbol="USDJPY", timeframe="15m",
                              price_type="ask", time=NOW - 5000, open=150.02, high=150.52, low=149.82, close=150.02))
    conv = conversion_to_usd("JPY", now=NOW)
    assert conv["verified"] is False


def test_sizing_respects_conversion_usd_risk(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    condition = {
        "instrument": "USDJPY", "contract_size": "100000", "volume_min": "0.01",
        "volume_max": "100.0", "volume_step": "0.01", "point_digits": 3,
        "quote_to_account_loss": str(Decimal("1") / Decimal("150.0")),
    }
    lot = size_lot(Decimal("0.50"), condition)
    assert lot == Decimal("0.07")
    actual_usd_risk = Decimal("0.50") * Decimal("100000") * lot * (Decimal("1") / Decimal("150.0"))
    assert actual_usd_risk <= Decimal("25")


def test_ledger_path_per_symbol():
    p_xau = ledger_path("acc123", "XAUUSD")
    p_eur = ledger_path("acc123", "EURUSD")
    assert p_xau.name.endswith("-XAUUSD.json")
    assert p_eur.name.endswith("-EURUSD.json")
    assert p_xau != p_eur


def test_independent_g0_population_per_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        dir_path = Path(tmp)
        legacy = dir_path / "exness-htf-ast2-shadow-acc.json"
        p_xau = dir_path / "exness-htf-ast2-shadow-acc-XAUUSD.json"
        p_eur = dir_path / "exness-htf-ast2-shadow-acc-EURUSD.json"

        led_xau = migrate_symbol_ledger(p_xau, legacy, "acc", "XAUUSD", NOW)
        led_eur = migrate_symbol_ledger(p_eur, legacy, "acc", "EURUSD", NOW)

        assert led_xau["symbol"] == "XAUUSD"
        assert led_eur["symbol"] == "EURUSD"
        assert len(led_xau["population"]) == 56
        assert len(led_eur["population"]) == 56
        assert led_xau["samples"] == []
        assert led_eur["samples"] == []


def test_capacity_limits_66_total_and_6_per_symbol(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "66")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL", "6")
    assert max_positions() == 66
    assert max_positions_per_symbol() == 6

    broker = {
        "positions": [{"position_id": str(i), "instrument": "EURUSD"} for i in range(5)],
        "orders": [],
    }
    assert capacity_available(broker, "acc", "EURUSD", additional=1) is True
    assert capacity_available(broker, "acc", "EURUSD", additional=2) is False
    assert capacity_available(broker, "acc", "EURGBP", additional=1) is True

    full_broker = {
        "positions": [{"position_id": str(i), "instrument": f"PAIR{i%10}"} for i in range(66)],
        "orders": [],
    }
    assert capacity_available(full_broker, "acc", "EURUSD", additional=1) is False


def test_migration_preserves_xau_population_samples_and_restart(tmp_path):
    from backend.app.forex_htf_v2 import RUNTIME, SCHEMA, g0_trees
    legacy = tmp_path / "legacy.json"
    path = tmp_path / "legacy-XAUUSD.json"
    previous = {"runtime": RUNTIME, "schema": SCHEMA, "account": "acc", "population": g0_trees(),
                "samples": [{"strategy": "saved", "status": "open"}], "generation": 2, "since": 123}
    raw = json.dumps(previous).encode()
    legacy.write_bytes(raw)
    migrated = migrate_symbol_ledger(path, legacy, "acc", "XAUUSD", NOW)
    assert migrated == {**previous, "symbol": "XAUUSD"}
    assert legacy.read_bytes() == raw
    assert migrate_symbol_ledger(path, legacy, "acc", "XAUUSD", NOW + 999) == migrated
    manifest = json.loads(path.with_suffix(".migration.json").read_text())
    assert Path(manifest["archive"]).read_bytes() == raw


def test_failed_archive_never_overwrites_source_or_creates_ledger(tmp_path, monkeypatch):
    import backend.app.forex_migration as migration
    from backend.app.forex_htf_v2 import RUNTIME, SCHEMA
    legacy, path = tmp_path / "legacy.json", tmp_path / "XAUUSD.json"
    raw = json.dumps({"runtime": RUNTIME, "schema": SCHEMA, "account": "acc"}).encode()
    legacy.write_bytes(raw)
    original = migration.atomic_json
    monkeypatch.setattr(migration, "atomic_json", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        migrate_symbol_ledger(path, legacy, "acc", "XAUUSD", NOW)
    assert legacy.read_bytes() == raw and not path.exists()
    monkeypatch.setattr(migration, "atomic_json", original)
    assert migrate_symbol_ledger(path, legacy, "acc", "XAUUSD", NOW)["account"] == "acc"


def test_migration_wrong_identity_blocks_only_affected_symbol(tmp_path):
    from backend.app.forex_htf_v2 import RUNTIME, SCHEMA
    legacy, path = tmp_path / "legacy.json", tmp_path / "XAUUSD.json"
    raw = json.dumps({"runtime": RUNTIME, "schema": SCHEMA, "account": "wrong"}).encode()
    legacy.write_bytes(raw)
    with pytest.raises(ValueError, match="identity"):
        migrate_symbol_ledger(path, legacy, "acc", "XAUUSD", NOW)
    assert legacy.read_bytes() == raw and not path.exists()
    assert len(migrate_symbol_ledger(tmp_path / "EURUSD.json", legacy, "acc", "EURUSD", NOW)["population"]) == 56


@pytest.mark.parametrize("currency,pair,bid,ask,loss,gain", [
    ("JPY", "USDJPY", 150, 151, Decimal(1) / 150, Decimal(1) / 151),
    ("CAD", "USDCAD", 1.3, 1.31, Decimal(1) / Decimal("1.3"), Decimal(1) / Decimal("1.31")),
    ("CHF", "USDCHF", 0.9, 0.91, Decimal(1) / Decimal("0.9"), Decimal(1) / Decimal("0.91")),
    ("GBP", "GBPUSD", 1.3, 1.31, Decimal("1.31"), Decimal("1.3")),
])
def test_bid_ask_conversion_and_future_quotes(currency, pair, bid, ask, loss, gain):
    with Session.begin() as session:
        for stamp, value in ((NOW - 900, bid), (NOW, 999)):
            for side, price in (("bid", value), ("ask", ask if stamp < NOW else 1000)):
                session.add(WorkspaceCandle(workspace_id="exness_forex", symbol=pair, timeframe="15m",
                    price_type=side, time=stamp, open=price, high=price, low=price, close=price))
    result = conversion_to_usd(currency, now=NOW)
    assert result["rate"] == loss and result["gain_rate"] == gain
    assert result["observed_at"] == NOW


def test_missing_conversion_or_commission_cannot_be_verified():
    from backend.app.forex_conversion import cost_context
    with pytest.raises(ValueError, match="conversion"):
        cost_context({"quote_currency": "JPY", "account_currency": "USD", "commission_per_unit": 0}, NOW)
    result = cost_context({"quote_currency": "USD", "account_currency": "USD", "commission_per_unit": None}, NOW)
    assert result["commission_per_unit"] is None


def test_reservations_count_each_leg_and_do_not_double_count_reconciled_positions(monkeypatch):
    from backend.app.db import ExnessExecutionTrade
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "66")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL", "6")
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(account="acc", symbol="EURUSD", signal_time=NOW,
            snapshot={"status": "submitted", "legs": [
                {"status": "open", "position_id": "1"}, {"status": "submitted"}, {"status": "planned"},
            ]}))
    broker = {"positions": [{"position_id": "1", "instrument": "EURUSD"}], "orders": []}
    assert capacity_available(broker, "acc", "EURUSD", 3)
    assert not capacity_available(broker, "acc", "EURUSD", 4)
    assert capacity_available(broker, "acc", "GBPUSD", 6)
    broker["positions"] += [{"position_id": str(i), "instrument": "external"} for i in range(2, 63)]
    assert capacity_available(broker, "acc", "GBPUSD", 2)
    assert not capacity_available(broker, "acc", "GBPUSD", 3)


def test_feed_observation_journal_and_execution_partition_same_timestamp():
    from backend.app.db import (
        ExnessExecutionState,
        ExnessExecutionTrade,
        ForexObservation,
        WorkspaceFeedState,
    )
    from backend.app.exness_execution import record_journal
    with Session.begin() as session:
        state = ExnessExecutionState(account="acc", enabled=False, started_at=NOW, snapshot={})
        session.add(state)
        for symbol in ("XAUUSD", "EURUSD"):
            session.add(ExnessExecutionTrade(account="acc", symbol=symbol, signal_time=NOW, snapshot={"symbol": symbol}))
            session.add(ForexObservation(workspace_id="exness_forex", symbol=symbol, candle_time=NOW, snapshot={"symbol": symbol}))
            feed = session.get(WorkspaceFeedState, ("exness_forex", symbol, "15m"))
            if not feed:
                session.add(WorkspaceFeedState(workspace_id="exness_forex", symbol=symbol, timeframe="15m", checked_until=NOW))
            record_journal(state, {"symbol": symbol, "candle_time": NOW, "evaluated": True}, "no_signal", NOW)
        assert len(state.snapshot["journal"]) == 2
    with Session() as session:
        for symbol in ("XAUUSD", "EURUSD"):
            assert session.get(ExnessExecutionTrade, ("acc", symbol, NOW)).snapshot["symbol"] == symbol
            assert session.get(ForexObservation, ("exness_forex", symbol, NOW)).snapshot["symbol"] == symbol


def test_forex_does_not_inherit_gold_daily_break():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.app.exness_sessions import session_closed_ahead
    stamp = int(datetime(2026, 10, 20, 16, 45, tzinfo=ZoneInfo("America/New_York")).timestamp())
    assert session_closed_ahead(stamp, symbol="XAUUSD")
    assert not session_closed_ahead(stamp, symbol="EURUSD")


def test_api_symbol_validation_and_isolated_candles(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.main import app
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    with Session.begin() as session:
        for symbol, price in (("XAUUSD", 4000), ("EURUSD", 1.15)):
            session.add(WorkspaceCandle(workspace_id="exness_forex", symbol=symbol, timeframe="15m",
                price_type="bid", time=NOW, open=price, high=price, low=price, close=price))
    client = TestClient(app)
    for symbol, price in (("XAUUSD", 4000), ("EURUSD", 1.15)):
        response = client.get(f"/api/workspaces/exness_forex/candles?symbol={symbol}")
        assert response.status_code == 200
        assert response.json()["workspace"]["symbol"] == symbol
        assert response.json()["candles"][0]["close"] == price
    for endpoint in ("dashboard", "candles", "learning", "learning/analysis", "execution", "quotes/stream"):
        assert client.get(f"/api/workspaces/exness_forex/{endpoint}?symbol=GBPUSD").status_code == 422

def test_database_migration_preserves_populated_xau_and_allows_same_keys(tmp_path):
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("forex_symbols_migration", Path("migrations/versions/0024_forex_symbols.py"))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    old_keys = {
        "workspace_feed_state": ["workspace_id", "timeframe"],
        "forex_observations": ["workspace_id", "candle_time"],
        "forex_gap_audits": ["workspace_id", "after", "before"],
        "forex_instrument_state": ["workspace_id"],
        "exness_execution_trades": ["account", "signal_time"],
    }
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        for name, keys in old_keys.items():
            table = sa.Table(name, metadata, *[sa.Column(key, sa.String, primary_key=True) for key in keys],
                             sa.Column("payload", sa.String))
            table.create(connection)
            connection.execute(table.insert().values(**dict.fromkeys(keys, "same"), payload="original"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migrated = sa.MetaData()
        migrated.reflect(connection)
        for name, keys in old_keys.items():
            table = migrated.tables[name]
            rows = connection.execute(sa.select(table)).mappings().all()
            assert len(rows) == 1 and rows[0]["symbol"] == "XAUUSD" and rows[0]["payload"] == "original"
            connection.execute(table.insert().values(**dict.fromkeys(keys, "same"), symbol="EURUSD", payload="new"))
            assert connection.scalar(sa.select(sa.func.count()).select_from(table)) == 2
    engine.dispose()

def test_live_sync_prioritizes_latest_after_sleep_without_skipping_cursor():
    from types import SimpleNamespace

    from backend.app.db import WorkspaceFeedState
    from backend.app.exness_feed import sync_page
    calls = []
    api = SimpleNamespace(candle_limit=2, candles=lambda *args, **kwargs: calls.append(args) or [])
    with Session.begin() as session:
        state = session.get(WorkspaceFeedState, ("exness_forex", "XAUUSD", "15m"))
        state.started_at = state.checked_until = NOW - 20 * 900
        state.error = state.retry_at = None
    sync_page(api, "15m", now=NOW, live_first=True)
    assert calls[0][3] == NOW
    assert calls[1][2] == NOW - 20 * 900
    with Session() as session:
        assert session.get(WorkspaceFeedState, ("exness_forex", "XAUUSD", "15m")).checked_until == NOW - 18 * 900

def test_cost_identity_and_staleness_fail_closed():
    from backend.app.forex_conversion import cost_context
    from backend.app.forex_costs import account_tag
    profile = {"instrument": "EURUSD", "account_tag": account_tag("acc"), "observed_at": NOW,
               "account_currency": "USD", "quote_currency": "USD", "commission_per_unit": 0}
    assert cost_context(profile, NOW, account="acc", symbol="EURUSD")["quote_to_account_loss"] == "1"
    for account, symbol, now in (("other", "EURUSD", NOW), ("acc", "XAUUSD", NOW),
                                 ("acc", "EURUSD", NOW + 3600), ("acc", "EURUSD", NOW - 1)):
        with pytest.raises(ValueError):
            cost_context(profile, now, account=account, symbol=symbol)
    with pytest.raises(ValueError):
        cost_context({**profile, "quote_currency": None}, NOW)

def test_legacy_csv_symbol_filter_preserves_gold_history(monkeypatch):
    import csv
    import io

    from backend.app.db import ForexPaperTrade
    from backend.app.forex_reporting import journal_csv
    with Session.begin() as session:
        session.add(ForexPaperTrade(workspace_id="exness_forex", channel="live", confirm_time=NOW,
                                   snapshot={"status": "resolved"}))
    gold = list(csv.DictReader(io.StringIO("".join(journal_csv("live", "XAUUSD")))))
    assert gold[0]["symbol"] == "XAUUSD"
    assert not list(csv.DictReader(io.StringIO("".join(journal_csv("live", "EURUSD")))))

def test_broker_csv_filters_symbol_and_has_price_precision(monkeypatch):
    import csv
    import io

    from fastapi.testclient import TestClient

    from backend.app.db import ExnessDeal
    from backend.app.main import app

    monkeypatch.setenv("EXNESS_ACCOUNT_ID", "acc")
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    with Session.begin() as session:
        for symbol, pid, price in (("XAUUSD", "1", "4000.001"), ("EURUSD", "2", "1.12345")):
            session.add(ExnessDeal(account="acc", deal_id=pid, position_id=pid, event_time=NOW,
                snapshot={"instrument": symbol, "type": "open", "direction": "buy", "price": price,
                          "volume": "0.01", "event_time": NOW}))
    client = TestClient(app)
    response = client.get("/api/workspaces/exness_forex/execution/trades.csv?symbol=EURUSD")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1 and rows[0]["symbol"] == "EURUSD" and rows[0]["open_price"] == "1.12345"
    assert client.get("/api/workspaces/exness_forex/execution/trades.csv?symbol=GBPUSD").status_code == 422
