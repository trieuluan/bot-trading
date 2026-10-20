from types import SimpleNamespace

from sqlalchemy import select

from backend.app.db import PaperNotification, Session
from backend.app.forex_notifications import notify_reconciled
from backend.app.notifications import deliver


def test_notify_reconciled_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    trade = SimpleNamespace(account="31000117657", signal_time=1700000000, snapshot={"signal": {}})
    with Session.begin() as session:
        notify_reconciled(session, trade, {"status": "submitted"}, {"status": "open", "position_id": "1"}, None, None, [], 1700000010)
    with Session() as session:
        assert session.scalar(select(PaperNotification)) is None


def test_notify_reconciled_full_lifecycle(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    sent = []
    monkeypatch.setattr("backend.app.notifications.send_alert", lambda msg: sent.append(msg) or True)

    trade = SimpleNamespace(
        account="31000117657",
        signal_time=1700000000,
        snapshot={"signal": {"strategy": "hast2_003932570337", "side": "buy", "management_frame": "H1"}},
    )

    with Session.begin() as session:
        # 1. Open
        notify_reconciled(
            session, trade,
            previous={"status": "submitted"},
            updated={"status": "open", "position_id": "100", "role": "tp1", "body": {"stop_loss_price": "2600.0", "take_profit_price": "2620.0"}},
            position={"position_id": "100", "open_price": "2610.0", "volume": "0.01", "direction": "buy"},
            protection={"stop_loss_price": "2600.0", "take_profit_price": "2620.0"},
            deals=[],
            now=1700000010,
        )

        # 2. SL confirmed
        notify_reconciled(
            session, trade,
            previous={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2600.0", "broker_target": "2620.0", "management": {"status": "submitting"}},
            updated={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2610.0", "broker_target": "2620.0", "management": {"status": "confirmed", "stop": "2610.0", "stage": "breakeven", "key": "k1"}},
            position={"position_id": "100", "open_price": "2610.0", "direction": "buy"},
            protection={"stop_loss_price": "2610.0", "take_profit_price": "2620.0"},
            deals=[],
            now=1700000020,
        )

        # 2b. TP updated (e.g. runner extended to H4 barrier)
        notify_reconciled(
            session, trade,
            previous={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2610.0", "broker_target": "2620.0"},
            updated={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2610.0", "broker_target": "2635.0"},
            position={"position_id": "100", "open_price": "2610.0", "direction": "buy"},
            protection={"stop_loss_price": "2610.0", "take_profit_price": "2635.0"},
            deals=[],
            now=1700000025,
        )

        # 3. Closed with deals
        deal_open = SimpleNamespace(position_id="100", event_time=1700000010, deal_id="d1", snapshot={"type": "open", "price": "2610.0", "volume": "0.01", "direction": "buy"})
        deal_close = SimpleNamespace(position_id="100", event_time=1700000030, deal_id="d2", snapshot={"type": "close", "price": "2620.0", "volume": "0.01", "profit": "10.0"})
        notify_reconciled(
            session, trade,
            previous={"status": "open", "position_id": "100"},
            updated={"status": "closed", "position_id": "100", "take_profit_filled": True},
            position=None,
            protection=None,
            deals=[deal_open, deal_close],
            now=1700000030,
        )

    deliver()

    assert len(sent) == 4
    assert "MỞ VỊ THẾ FOREX · BUY XAUUSD" in sent[0]
    assert "hast2_003932570337" in sent[0]
    assert "Chốt 1 (TP1)" in sent[0]
    assert "CẬP NHẬT DỜI SL FOREX · XAUUSD" in sent[1]
    assert "Dời về Hòa Vốn (BE)" in sent[1]
    assert "-1.00R → +0.00R" in sent[1]
    assert "CẬP NHẬT DỜI TP FOREX · XAUUSD" in sent[2]
    assert "+1.00R → +2.50R (Δ +1.50R)" in sent[2]
    assert "ĐÓNG VỊ THẾ FOREX · BUY XAUUSD" in sent[3]
    assert "+10 USD" in sent[3]
    assert "Chạm Take Profit (TP)" in sent[3]
