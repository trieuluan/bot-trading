from unittest.mock import Mock

import pytest

from backend.app.futures_symbols import current_symbol
from bot import worker


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
    assert len(threads) == 2 + 7 * len(worker.SYMBOLS)
    assert threads[0]["target"] is worker.run_loop
    assert threads[0]["args"][0] == "heartbeat"
    for index, symbol in enumerate(worker.SYMBOLS):
        streams = [item for item in threads if item["target"] is worker.stream_paper_quotes
                   and item["args"][0] == symbol]
        assert len(streams) == 1
        assert streams[0]["args"][1] is worker.stop
        jobs = [item for item in threads if item["args"][0] == symbol
                and item["target"] is worker.run_symbol_loop]
        assert len(jobs) == 6
        assert all(item["args"][-1] == index * 0.4 for item in jobs)
        assert {item["args"][1]: item["args"][3] for item in jobs} == {
            "historical-stops": 5, "quotes-and-stops": 5, "market-history": 15,
            "funding": 60, "auto-paper": 5, "futures-learning": 30,
        }
