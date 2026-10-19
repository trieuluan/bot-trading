from sqlalchemy import select
from types import SimpleNamespace
import json
import pytest

from backend.app.db import PaperNotification, PaperTrade, PaperWallet, Session
from backend.app.notifications import deliver, notify_open, notify_partial, notify_close, queue
from backend.app.paper import close_position

def test_notifications_queue_and_deliver(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    monkeypatch.setattr("backend.app.notifications.send_alert", lambda msg: sent.append(msg) or True)

    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.initial_balance = wallet.balance = 10000
        trade = PaperTrade(
            symbol="BTC_USDT", side="long", entry_time=1700000000,
            origin="auto", entry=80000, quantity=0.1, contracts=100, leverage=50,
            stop=79000, target=82000, entry_fee=2.0, exit_fee=0, funding_cost=0,
            monitoring={"exit_policy": "trail", "risk": 1000},
        )
        session.add(trade)
        session.flush()

        notify_open(session, trade, {
            "strategy": "rule_tree:137edf99aa",
            "generation": 1,
            "genome": {"exit_profile": "htf_breakout", "exit_policy": "trail"},
            "exit_policy": "trail",
        }, 10000, "Breakout H1 + nến xác nhận")

        notify_partial(session, trade, 81000, 0.05, 48.0, 0.05, 1700000900)

        close_position(session, wallet, trade, "take_profit", 1700001800, price=82000)

    deliver()

    assert len(sent) == 3
    assert "MỞ LỆNH" in sent[0]
    assert "Breakout HTF #137edf · G1" in sent[0]
    assert "CHỐT MỘT PHẦN" in sent[1]
    assert "ĐÓNG LỆNH" in sent[2]
    assert "Chạm TP" in sent[2]
    assert "<b>MỞ LỆNH LONG</b>" in sent[0]
    assert "<blockquote expandable>" in sent[0]
    assert "✅ <b>ĐÓNG LỆNH LONG · LÃI</b>" in sent[2]

    with Session() as session:
        pending = session.scalars(select(PaperNotification).where(PaperNotification.sent_at.is_(None))).all()
        assert len(pending) == 0
    deliver()
    assert len(sent) == 3

def test_rollback_never_sends_and_duplicates_only_queue_once():
    with pytest.raises(RuntimeError), Session.begin() as session:
        queue(session, "rollback", "Must not send")
        raise RuntimeError("Transaction failed")
    with Session() as session:
        assert session.get(PaperNotification, "rollback") is None
    with Session.begin() as session:
        queue(session, "unique", "Message")
        queue(session, "unique", "Duplicate")
    with Session() as session:
        assert [item.message for item in session.scalars(select(PaperNotification))] == ["Message"]

def test_failure_retries_after_backoff(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock")
    attempts = []
    monkeypatch.setattr("backend.app.notifications.send_alert", lambda msg: attempts.append(msg) or False)
    with Session.begin() as session:
        queue(session, "retry", "Retry me")
    deliver(100)
    deliver(101)
    assert attempts == ["Retry me"]
    monkeypatch.setattr("backend.app.notifications.send_alert", lambda msg: attempts.append(msg) or True)
    deliver(110)
    deliver(111)
    assert attempts == ["Retry me", "Retry me"]
    with Session() as session:
        item = session.get(PaperNotification, "retry")
        assert item.attempts == 2
        assert item.sent_at == 110

def test_alert_html_and_legacy_payload(monkeypatch):
    from backend.app.alerts import send_alert
    payloads = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok":true}'

    def urlopen(request, **kwargs):
        payloads.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("backend.app.alerts.urlopen", urlopen)
    assert send_alert("✅ <b>TEST</b>")
    assert send_alert("Legacy text with < 10 and > 5")
    assert payloads[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in payloads[1]


@pytest.mark.parametrize("net, label", [(12, "✅"), (-12, "❌"), (0, "➖")])
def test_close_color_uses_pnl_not_sl_reason(monkeypatch, net, label):
    sent = []
    monkeypatch.setattr("backend.app.notifications.queue", lambda session, key, msg: sent.append(msg))
    monkeypatch.setattr("backend.app.notifications.audit_of", lambda *args: None)
    trade = SimpleNamespace(
        id=1, symbol="ETH_USDT", side="long", origin="manual", entry_time=1700000000,
        exit_time=1700001800, entry=100, exit=101, quantity=1, net_pnl=net,
        exit_reason="stop_loss", exit_fee=1, funding_cost=0, monitoring={},
    )
    notify_close(None, trade)
    assert sent[0].startswith(label)
    assert "🛑 Chạm SL" in sent[0]


def test_open_escapes_strategy_and_reason(monkeypatch):
    sent = []
    monkeypatch.setattr("backend.app.notifications.queue", lambda session, key, msg: sent.append(msg))
    trade = SimpleNamespace(
        id=1, symbol="ETH_USDT", side="short", origin="auto", entry_time=1700000000,
        entry=100, stop=101, target=98, quantity=1, contracts=1, leverage=10, entry_fee=1,
    )
    notify_open(None, trade, {"strategy": "<script>", "genome": {"family": "A&B"}}, 1000, "H1 < H4 & <b>unsafe</b>")
    assert "A&amp;B" in sent[0]
    assert "H1 &lt; H4 &amp; &lt;b&gt;unsafe&lt;/b&gt;" in sent[0]

def test_strategy_line_uses_readable_profile():
    from backend.app.notifications import strategy_line
    rule = {"op": "all", "children": [{"feature": "trend"}, {"feature": "wick_sweep"}, {"feature": "engulfing"}]}
    audit = {"strategy": "rule_tree:3a1709ff", "generation": 1, "genome": {"family": "rule_tree", "rule": rule}}
    assert strategy_line(audit) == "Quét thanh khoản HTF #3a1709 · G1"
    audit["genome"] = {"family": "rule_tree", "rule": {"feature": "trend"}}
    assert strategy_line(audit) == "Cây tự sinh #3a1709 · G1"
    audit["genome"]["exit_profile"] = "htf_fib"
    assert strategy_line(audit) == "Fibonacci thoái lui HTF #3a1709 · G1"
