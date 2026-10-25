import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from backend.app import futures_wakeup
from backend.app.futures_symbols import current_symbol, symbol_scope
from bot import learning_worker, worker


@pytest.mark.parametrize("cancelled", [False, True])
def test_pair_start_delay_respects_shutdown_and_symbol_scope(monkeypatch, cancelled):
    wait = Mock(return_value=cancelled)
    monkeypatch.setattr(worker, "stop", Mock(wait=wait))
    calls = []
    monkeypatch.setattr(worker, "run_loop", lambda *args: calls.append(current_symbol()))
    previous = current_symbol()
    worker.run_symbol_loop("ETH_USDT", "quote", lambda: None, 5, 0.4)
    wait.assert_called_once_with(0.4)
    assert calls == ([] if cancelled else ["ETH_USDT"])
    assert current_symbol() == previous


def test_main_staggers_pairs_without_delaying_heartbeat(monkeypatch):
    threads = []
    monkeypatch.setattr(worker, "validate_mode", lambda: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(worker, "baseline_auto_on_startup", lambda: None)
    monkeypatch.setattr(worker, "stop", Mock(is_set=Mock(return_value=False)))

    def thread(**kwargs):
        threads.append(kwargs)
        return Mock()

    monkeypatch.setattr(worker.threading, "Thread", thread)
    worker.main()
    assert len(threads) == 3 + 6 * len(worker.SYMBOLS)
    assert threads[0]["target"] is worker.run_loop
    assert threads[0]["args"][0] == "heartbeat"
    assert threads[2]["target"] is worker.listen
    assert threads[2]["args"] == (worker.stop,)
    for index, symbol in enumerate(worker.SYMBOLS):
        streams = [item for item in threads if item["target"] is worker.stream_paper_quotes
                   and item["args"][0] == symbol]
        assert len(streams) == 1
        assert streams[0]["args"][1] is worker.stop
        jobs = [item for item in threads if item["args"][0] == symbol
                and item["target"] is worker.run_symbol_loop]
        assert len(jobs) == 5
        assert all(item["args"][-1] == index * 0.4 for item in jobs)
        assert {item["args"][1]: item["args"][3] for item in jobs} == {
            "historical-stops": 5, "quotes-and-stops": 5, "market-history": 15,
            "funding": 60, "auto-paper": 5,
        }

def test_learning_service_only_starts_learning_for_each_symbol(monkeypatch):
    threads = []
    validated = Mock()
    monkeypatch.setattr(learning_worker, "validate_mode", validated)
    monkeypatch.setattr(learning_worker.signal, "signal", lambda *args: None)

    def thread(**kwargs):
        threads.append(kwargs)
        return Mock()

    monkeypatch.setattr(learning_worker.threading, "Thread", thread)
    learning_worker.main()
    validated.assert_called_once_with()
    assert len(threads) == 1 + len(worker.SYMBOLS)
    assert threads[0]["target"] is learning_worker.listen
    assert threads[0]["args"] == (learning_worker.stop,)
    for index, item in enumerate(threads[1:]):
        assert item["target"] is worker.run_symbol_loop
        assert item["args"] == (worker.SYMBOLS[index], "futures-learning",
                                learning_worker.learning_tick, 30, index * 0.4)

@pytest.mark.parametrize("symbol,now,expected", [
    ("BTC_USDT", 899, 3), ("BTC_USDT", 900, 2), ("BTC_USDT", 901, 1),
    ("BTC_USDT", 902, 15), ("BTC_USDT", 870, 15),
    ("LINK_USDT", 900, 3.8),
])
def test_market_poll_aligns_with_close_without_changing_normal_interval(symbol, now, expected):
    with symbol_scope(symbol):
        assert worker.loop_delay("market-history", 15, 0, now) == pytest.approx(expected)
        assert worker.loop_delay("quotes-and-stops", 5, 1, now) == 4
        assert worker.loop_delay("futures-learning", 30, 2, now) == 28

def test_wakeup_routing_coalesces_and_isolates_symbols_and_jobs(monkeypatch):
    events = {(job, symbol): threading.Event()
              for job in ("futures-learning", "auto-paper")
              for symbol in ("BTC_USDT", "ETH_USDT")}
    monkeypatch.setattr(futures_wakeup, "WAKEUPS", events)
    for payload in ("futures-learning:ETH_USDT", "futures-learning:ETH_USDT",
                    "forex:ETH_USDT", "auto-paper:unknown", "bad", "auto-paper:BTC_USDT:bad"):
        futures_wakeup.dispatch(payload)
    assert [key for key, event in events.items() if event.is_set()] == [
        ("futures-learning", "ETH_USDT")]

@pytest.mark.parametrize("job", ["futures-learning", "auto-paper"])
def test_wakeup_during_callback_is_not_lost_and_never_runs_callback_in_parallel(monkeypatch, job):
    stop, wakeup = threading.Event(), threading.Event()
    monkeypatch.setattr(worker, "stop", stop)
    monkeypatch.setattr(worker, "WAKEUPS", {(job, "BTC_USDT"): wakeup})
    wait = Mock(wraps=wakeup.wait)
    monkeypatch.setattr(wakeup, "wait", wait)
    calls = []

    def callback():
        calls.append(current_symbol())
        assert not wakeup.is_set()
        if len(calls) == 1:
            wakeup.set()
            wakeup.set()
        else:
            stop.set()

    with symbol_scope("BTC_USDT"):
        worker.run_loop(job, callback, 30)
    assert calls == ["BTC_USDT", "BTC_USDT"]
    wait.assert_called_once()

def test_wakeup_wait_expires_without_notification_and_respects_shutdown(monkeypatch):
    monkeypatch.setattr(worker, "stop", threading.Event())
    wakeup = Mock(wait=Mock(return_value=False))
    clock = iter((0, 0, 0.5, 1))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    worker.wait_for_wakeup(wakeup, 1)
    assert wakeup.wait.call_count == 2
    worker.stop.set()
    monkeypatch.setattr(worker.time, "monotonic", lambda: 1)
    worker.wait_for_wakeup(wakeup, 30)
    assert wakeup.wait.call_count == 2

@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_notify_uses_transaction_and_current_symbol(monkeypatch, dialect):
    session = Mock()
    session.get_bind.return_value.dialect.name = dialect
    with symbol_scope("ETH_USDT"):
        futures_wakeup.notify(session, "auto-paper")
    if dialect == "sqlite":
        session.execute.assert_not_called()
    else:
        statement, parameters = session.execute.call_args.args
        assert str(statement) == "SELECT pg_notify(:channel, :payload)"
        assert parameters == {"channel": futures_wakeup.CHANNEL, "payload": "auto-paper:ETH_USDT"}
    session.commit.assert_not_called()

def test_listener_subscribes_and_dispatches_without_running_strategy(monkeypatch):
    stop = threading.Event()
    engine = Mock()
    engine.dialect.name = "postgresql"
    engine.url.set.return_value.render_as_string.return_value = "postgresql://localhost/test"
    monkeypatch.setattr(futures_wakeup, "engine", engine)
    connection = MagicMock()

    def notifications(**kwargs):
        assert kwargs == {"timeout": 1, "stop_after": 1}
        stop.set()
        yield SimpleNamespace(payload="auto-paper:BTC_USDT")

    connection.notifies.side_effect = notifications
    connect = Mock(return_value=MagicMock(__enter__=Mock(return_value=connection)))
    monkeypatch.setattr(futures_wakeup.psycopg, "connect", connect)
    dispatch = Mock()
    monkeypatch.setattr(futures_wakeup, "dispatch", dispatch)
    futures_wakeup.listen(stop)
    connect.assert_called_once_with("postgresql://localhost/test", autocommit=True, connect_timeout=5)
    connection.execute.assert_called_once_with(f"LISTEN {futures_wakeup.CHANNEL}")
    dispatch.assert_called_once_with("auto-paper:BTC_USDT")

def test_listener_reconnects_and_leaves_polling_available(monkeypatch):
    stop = Mock(is_set=Mock(side_effect=[False, True]))
    engine = Mock()
    engine.dialect.name = "postgresql"
    monkeypatch.setattr(futures_wakeup, "engine", engine)
    monkeypatch.setattr(futures_wakeup.psycopg, "connect",
                        Mock(side_effect=futures_wakeup.psycopg.OperationalError("offline")))
    futures_wakeup.listen(stop)
    stop.wait.assert_called_once_with(5)
