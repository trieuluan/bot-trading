import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.backtest import BacktestConfig, run_backtest, simulate
from backend.app.db import BacktestRun, BotState, MarketState, Session, Signal
from backend.app.feed import sync_market
from backend.app.main import app
from backend.app.market import Candle
from backend.app.mexc import SOURCE, MarketDataError, MexcFuturesAdapter
from backend.app.strategy import breakout

BASE = 1_791_763_200
CONTRACT = {"contractSize": 0.0001, "volUnit": 1, "minVol": 1, "maxVol": 400000}


def candle(index, opening=95, high=100, low=90, close=96):
    return Candle(BASE + index * 900, opening, high, low, close, 10)


def adapter(handler):
    client = httpx.Client(base_url="https://api.mexc.com", transport=httpx.MockTransport(handler))
    return MexcFuturesAdapter(client, sleep=lambda _: None)


def response_data(request, bars):
    start = int(request.url.params["start"])
    end = int(request.url.params["end"])
    selected = [c for c in bars if start <= c.time <= end]
    keys = {
        "time": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "vol": "volume",
    }
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {key: [getattr(c, field) for c in selected] for key, field in keys.items()},
        },
    )


def test_futures_pagination_closed_bars_and_seconds():
    bars = [candle(i) for i in range(2002)]
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and "ApiKey" not in request.headers
        assert request.url.path == "/api/v1/contract/kline/BTC_USDT"
        return response_data(request, bars)

    client = adapter(handler)
    now = BASE + 2001 * 900 + 300
    result = client.history(BASE, now, now)
    assert len(result) == 2001 and result[-1].time == BASE + 2000 * 900
    assert len(requests) == 2
    assert requests[0].url.params["end"] == str(BASE + 2000 * 900 - 1)


def test_gap_invalid_ohlc_and_conflicting_duplicates_are_rejected():
    for bars in (
        [candle(0), candle(2)],
        [candle(0, low=110), candle(1), candle(2)],
        [candle(0), candle(0, close=97), candle(1), candle(2)],
    ):
        client = adapter(lambda request: response_data(request, bars))
        with pytest.raises(MarketDataError):
            client.history(BASE, BASE + 2700, BASE + 2700)


def test_transient_retry_and_rate_limit_cooldown():
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(503)
            if len(calls) < 3
            else httpx.Response(200, json={"success": True, "data": BASE * 1000})
        )

    assert adapter(handler).server_time() == BASE
    assert len(calls) == 3
    calls.clear()

    def limited(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    with pytest.raises(MarketDataError) as exc:
        adapter(limited).server_time()
    assert exc.value.retry_after == 120 and len(calls) == 1


def test_funding_pagination_and_incomplete_history():
    records = [
        {"settleTime": (BASE + i * 28800) * 1000, "fundingRate": 0.0001, "collectCycle": 8}
        for i in range(4)
    ]
    pages = {1: records[2:][::-1], 2: records[:2][::-1]}
    client = adapter(
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "data": {"resultList": pages[int(request.url.params["page_num"])]},
            },
        )
    )
    assert len(client.funding_history(BASE + 900, BASE + 4 * 28800)) == 3
    incomplete = adapter(
        lambda request: httpx.Response(200, json={"success": True, "data": {"resultList": []}})
    )
    with pytest.raises(MarketDataError, match="does not cover"):
        incomplete.funding_history(BASE, BASE + 86400)


def long_history():
    return [candle(i) for i in range(20)] + [candle(20, 96, 106, 95, 105)]


def test_next_open_entry_stop_first_and_cost_accounting():
    bars = long_history() + [candle(21, 106, 140, 89, 110)]
    config = BacktestConfig(fee_bps=8, slippage_bps=2)
    result = simulate(bars, config, [], bars, CONTRACT)
    trade = result["trades"][0]
    assert trade["signal_time"] == bars[20].time
    assert trade["entry_time"] == bars[21].time
    assert trade["entry"] == pytest.approx(106 * 1.0002)
    assert trade["exit_reason"] == "stop"  # Both TP and SL touched: choose stop.
    assert trade["exit"] == pytest.approx(90 * 0.9998)
    assert result["net_pnl"] == pytest.approx(trade["net_pnl"])
    assert trade["fees"] > 0 and trade["contracts"] % CONTRACT["volUnit"] == 0


def test_short_breakout_and_adverse_short_slippage():
    bars = [candle(i) for i in range(20)] + [
        candle(20, 95, 96, 84, 85),
        candle(21, 84, 101, 50, 85),
    ]
    assert breakout(bars[:21]) is None
    assert breakout(bars[:21], allow_short=True).side == "short"
    result = simulate(bars, BacktestConfig(), [], bars, CONTRACT)
    trade = result["trades"][0]
    assert trade["side"] == "short" and trade["entry"] < 84
    assert trade["exit_reason"] == "stop" and trade["exit"] > 100
    assert trade["net_pnl"] < 0


def test_stop_gap_and_funding_only_for_carried_position():
    bars = long_history() + [
        candle(21, 106, 110, 100, 108),
        candle(22, 108, 115, 100, 110),
        candle(23, 80, 85, 79, 82),
    ]
    funding = [{"time": bars[21].time, "rate": 0.1}, {"time": bars[22].time, "rate": 0.001}]
    result = simulate(bars, BacktestConfig(fee_bps=0, slippage_bps=0), funding, bars, CONTRACT)
    trade = result["trades"][0]
    assert trade["funding_cost"] == pytest.approx(trade["quantity"] * 108 * 0.001)
    assert trade["exit_reason"] == "stop_gap" and trade["exit"] == 80
    assert result["net_pnl"] == pytest.approx(sum(t["net_pnl"] for t in result["trades"]))


def test_short_funding_credit_and_oos_warmup():
    bars = [candle(i) for i in range(20)] + [
        candle(20, 95, 96, 84, 85),
        candle(21, 84, 90, 80, 85),
        candle(22, 85, 90, 75, 80),
    ]
    result = simulate(
        bars, BacktestConfig(), [{"time": bars[22].time, "rate": 0.001}], bars, CONTRACT
    )
    assert result["funding_cost"] < 0
    history = [candle(i) for i in range(200)]
    report = run_backtest(history, BacktestConfig(), [], history, CONTRACT)
    assert report["out_of_sample"]["start_time"] == history[140].time
    assert report["in_sample"]["end_time"] == history[140].time
    assert report["out_of_sample"]["trade_count"] == 0
    assert report["out_of_sample"]["final_equity"] == 10000


def test_future_changes_cannot_change_earlier_trades():
    bars = long_history() + [candle(21, 106, 140, 89, 110)] + [candle(i) for i in range(22, 100)]
    first = simulate(bars, BacktestConfig(), [], bars, CONTRACT)
    revised = bars[:90] + [candle(i, 105, 120, 90, 110) for i in range(90, 100)]
    second = simulate(revised, BacktestConfig(), [], revised, CONTRACT)
    assert first["trades"][0] == second["trades"][0]


def test_real_worker_backfills_and_deduplicates(monkeypatch):
    bars = long_history() + [candle(21, 105, 108, 100, 106)]
    with Session.begin() as session:
        state = session.get(MarketState, 1)
        state.source = SOURCE
        state.last_processed = bars[19].time
        session.get(BotState, 1).enabled = True

    class Feed:
        def server_time(self):
            return bars[-1].time + 900

        def history(self, *_):
            return bars

    sync_market(Feed())
    sync_market(Feed())
    with Session() as session:
        signals = list(session.scalars(select(Signal).where(Signal.source == SOURCE)))
        assert len(signals) == 1 and signals[0].candle_time == bars[20].time
        assert session.get(MarketState, 1).last_processed == bars[-1].time


def test_market_failure_persists_cursor_and_records_retry():
    with Session.begin() as session:
        state = session.get(MarketState, 1)
        state.source = SOURCE
        state.last_processed = BASE

    class Offline:
        def server_time(self):
            raise MarketDataError("offline", retry_after=120)

    with pytest.raises(MarketDataError):
        sync_market(Offline())
    with Session() as session:
        state = session.get(MarketState, 1)
        assert state.last_processed == BASE and state.error == "offline" and state.retry_at


def test_research_api_persistence_source_isolation_and_validation(monkeypatch):
    from backend.app import research

    history = [candle(i) for i in range(200)]

    class PublicFeed:
        def server_time(self):
            return history[-1].time + 900

        def history(self, *_args, **_kwargs):
            return history

        def funding_history(self, *_args):
            return []

        def contract_info(self):
            return CONTRACT

        def close(self):
            pass

    monkeypatch.setattr(research, "MexcFuturesAdapter", PublicFeed)
    with TestClient(app) as client:
        assert client.post("/api/market/config", json={"source": "mexc_futures"}).status_code == 200
        snapshot = client.get("/api/dashboard").json()
        assert snapshot["data_source"] == SOURCE and len(snapshot["candles"]) == 120
        report = client.post("/api/backtests", json={"days": 7})
        assert report.status_code == 200, report.text
        data = report.json()
        assert client.get(f"/api/backtests/{data['id']}").json()["result"] == data["result"]
        assert client.get("/api/backtests/latest").json()["id"] == data["id"]
        with Session() as session:
            assert session.get(BacktestRun, data["id"])
            assert not session.get(BotState, 1).enabled
        assert client.post("/api/backtests", json={"days": 91}).status_code == 422
        assert client.post("/api/backtests", json={"fee_bps": -1}).status_code == 422
        assert client.post("/api/market/config", json={"source": "mexc_spot"}).status_code == 422
        assert client.get("/api/backtests/999999").status_code == 404


def test_backfill_uses_analysis_cursor_not_latest_research_cache():
    from backend.app.feed import save_candles

    bars = [candle(i) for i in range(60)]
    with Session.begin() as session:
        state = session.get(MarketState, 1)
        state.source = SOURCE
        state.last_processed = bars[20].time
        session.get(BotState, 1).enabled = True
        save_candles(session, bars[:21] + bars[50:])

    class RecoveredFeed:
        def server_time(self):
            return bars[-1].time + 900

        def history(self, start, end, now):
            assert start == bars[0].time  # Cursor plus 20-bar warmup, despite newer cache.
            return [bar for bar in bars if start <= bar.time < end]

    sync_market(RecoveredFeed())
    with Session() as session:
        assert session.get(MarketState, 1).last_processed == bars[-1].time
        assert session.get(MarketState, 1).error is None


def test_paused_feed_does_not_replay_paused_bars_and_sources_do_not_collide():
    from backend.app.feed import save_candles

    bars = long_history()
    with Session.begin() as session:
        state = session.get(MarketState, 1)
        state.source = SOURCE
        state.last_processed = bars[19].time
        save_candles(session, bars)

    class Feed:
        def server_time(self):
            return bars[-1].time + 900

        def history(self, *_):
            return bars

    sync_market(Feed())
    with Session.begin() as session:
        assert session.get(MarketState, 1).last_processed == bars[-1].time
        session.get(BotState, 1).enabled = True
    sync_market(Feed())
    with Session.begin() as session:
        assert session.scalar(select(Signal.id)) is None
        # Identical timestamps from different sources must coexist after migration 0002.
        for source in ("synthetic", SOURCE):
            session.add(
                Signal(
                    source=source,
                    timeframe="15m",
                    symbol="BTC/USDT",
                    candle_time=BASE,
                    side="long",
                    entry=100,
                    stop=90,
                    target=120,
                    reason="fixture",
                )
            )
    with TestClient(app) as client:
        snapshot = client.get("/api/dashboard").json()
        assert snapshot["signal_count"] == 1
        assert snapshot["signals"][0]["source"] == SOURCE
