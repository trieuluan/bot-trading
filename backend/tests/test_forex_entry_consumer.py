import json
import threading

from backend.app import forex_entry_consumer as consumer


class Stream:
    def __init__(self, lines):
        self.lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


def test_slow_local_processing_is_not_stream_silence(monkeypatch):
    quote = {"bid": 1.0, "ask": 1.1, "time": 1000.0, "received_at": 1000.0}
    line = b"data: " + json.dumps({"status": "live", "symbol": "XAUUSD", "quote": quote}).encode() + b"\n"
    clock = iter([0.0, 0.1, 1.0, 7.0, 7.0, 7.1, 7.2, 7.3])
    cancelled = []
    entries = consumer.EntryConsumers("acct", threading.Event(), lambda *_: None)
    monkeypatch.setattr(consumer.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(consumer.time, "time", lambda: 1000.0)
    monkeypatch.setattr(consumer, "urlopen", lambda *_args, **_kwargs: Stream([line, b": heartbeat\n"]))
    monkeypatch.setattr(consumer, "consume_quote", lambda *_args: [])
    monkeypatch.setattr(entries, "waiting", lambda _symbol: True)
    monkeypatch.setattr(entries, "cancel", lambda _symbol, reason, **detail: cancelled.append((reason, detail)))

    entries.stream("XAUUSD")

    assert cancelled == [("stream_disconnected", {
        "cause": "eof", "read_wait": 0.1, "since_tick": 0.0, "consume_ms": 6000, "lock_wait_ms": 0})]
