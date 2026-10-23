import threading

from backend.app import forex_learning


def test_analysis_cached_per_symbol_and_m15_candle(monkeypatch):
    calls = []

    def fake(now=None, symbol="XAUUSD"):
        calls.append((symbol, now))
        return {"candle_time": now // 900 * 900 - 900, "status": "ready", "strategies": {}}

    monkeypatch.setattr(forex_learning, "current_analysis", fake)
    monkeypatch.setattr(forex_learning, "_analysis_cache", {})
    base = 1_800_000_000 // 900 * 900
    forex_learning.cached_analysis("EURUSD", now=base + 10)
    forex_learning.cached_analysis("EURUSD", now=base + 40)
    assert len(calls) == 1
    forex_learning.cached_analysis("USDJPY", now=base + 40)
    assert len(calls) == 2
    # Hết TTL trong cùng nến, hoặc sang nến M15 mới, thì tính lại.
    forex_learning.cached_analysis("EURUSD", now=base + 10 + forex_learning.ANALYSIS_TTL)
    forex_learning.cached_analysis("EURUSD", now=base + 900)
    assert len(calls) == 4
    # Trạng thái ready/stale tính theo thời điểm đọc, không theo lúc cache.
    assert forex_learning.cached_analysis("EURUSD", now=base + 900 + 59)["status"] == "ready"
    monkeypatch.setattr(forex_learning, "ANALYSIS_TTL", 10_000)
    assert forex_learning.cached_analysis("EURUSD", now=base + 900 + 500)["status"] == "stale"


def test_concurrent_analysis_requests_compute_once(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(now=None, symbol="XAUUSD"):
        calls.append(symbol)
        started.set()
        release.wait(2)
        return {"status": "unavailable"}

    monkeypatch.setattr(forex_learning, "current_analysis", slow)
    monkeypatch.setattr(forex_learning, "_analysis_cache", {})
    threads = [threading.Thread(target=forex_learning.cached_analysis, args=("GBPUSD",)) for _ in range(3)]
    for thread in threads:
        thread.start()
    started.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
    assert calls == ["GBPUSD"]

def test_worker_analyses_once_per_candle_after_capture_window():
    from bot.exness_worker import tick_due
    done = {}
    assert tick_due(done, "EURUSD", 900 + 10) and tick_due(done, "EURUSD", 900 + 60)
    assert tick_due(done, "EURUSD", 900 + 200)
    assert not tick_due(done, "EURUSD", 900 + 400)
    assert tick_due(done, "EURUSD", 1800 + 5)
    assert tick_due(done, "GBPUSD", 900 + 400)


def test_tick_window_keeps_enough_history():
    from backend.app.exness_observation import TICK_WINDOW
    assert TICK_WINDOW["15m"] >= 10 * 86400 and TICK_WINDOW["4h"] >= 120 * 86400
