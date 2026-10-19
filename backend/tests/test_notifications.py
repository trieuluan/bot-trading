from sqlalchemy import select
import pytest

from backend.app.db import PaperNotification, PaperTrade, PaperWallet, Session
from backend.app.notifications import deliver, notify_open, notify_partial, queue
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
