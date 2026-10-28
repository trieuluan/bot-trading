from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.db import PaperNotification, Session
from backend.app.forex_notifications import (
    notify_reconciled,
    profit_text,
    setup_progress,
    strategy_text,
)
from backend.app.notifications import deliver


def test_notify_reconciled_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    trade = SimpleNamespace(account="31000117657", signal_time=1700000000, snapshot={"signal": {}})
    with Session.begin() as session:
        notify_reconciled(session, trade, {"status": "submitted"}, {"status": "open", "position_id": "1"}, None, None, [], 1700000010)
    with Session() as session:
        assert session.scalar(select(PaperNotification)) is None


@pytest.mark.parametrize("fraction", [0, 0.325541])
def test_notify_reconciled_full_lifecycle(monkeypatch, fraction):
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
            now=1700000010 + fraction,
        )

        # 2. SL confirmed
        notify_reconciled(
            session, trade,
            previous={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2600.0", "broker_target": "2620.0", "management": {"status": "submitting"}},
            updated={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2610.0", "broker_target": "2620.0", "management": {"status": "confirmed", "stop": "2610.0", "stage": "breakeven", "key": "k1"}},
            position={"position_id": "100", "open_price": "2610.0", "direction": "buy"},
            protection={"stop_loss_price": "2610.0", "take_profit_price": "2620.0"},
            deals=[],
            now=1700000020 + fraction,
        )

        # 2b. TP updated (e.g. runner extended to H4 barrier)
        notify_reconciled(
            session, trade,
            previous={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2610.0", "broker_target": "2620.0"},
            updated={"status": "open", "position_id": "100", "body": {"stop_loss_price": "2600.0"}, "broker_stop": "2610.0", "broker_target": "2635.0"},
            position={"position_id": "100", "open_price": "2610.0", "direction": "buy"},
            protection={"stop_loss_price": "2610.0", "take_profit_price": "2635.0"},
            deals=[],
            now=1700000025 + fraction,
        )

        # 3. Closed with deals
        deal_open = SimpleNamespace(position_id="100", event_time=1700000010, deal_id="d1", snapshot={"type": "open", "price": "2610.0", "volume": "0.01", "direction": "buy"})
        deal_close = SimpleNamespace(position_id="100", event_time=1700000030, deal_id="d2", snapshot={"type": "close", "price": "2620.0", "volume": "0.01", "profit": {"amount": "10.0", "currency": "USD"}})
        notify_reconciled(
            session, trade,
            previous={"status": "open", "position_id": "100"},
            updated={"status": "closed", "position_id": "100", "take_profit_filled": True},
            position=None,
            protection=None,
            deals=[deal_open, deal_close],
            now=1700000030 + fraction,
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


def deal(identifier, kind, volume, profit=None, pid="100", at=1700000010):
    return SimpleNamespace(position_id=pid, event_time=at, deal_id=identifier,
                           snapshot={"type": kind, "price": "2620", "volume": volume,
                                     "profit": profit, "direction": "buy"})


def test_partial_close_uses_actual_volume_and_does_not_repeat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    trade = SimpleNamespace(account="demo", symbol="EURUSD", signal_time=1700000000,
                            snapshot={"signal": {"side": "long"}, "position_id": "100"})
    deals = [deal("open", "open", "0.10"),
             deal("partial", "close", "0.03", {"amount": "12.50", "currency": "EUR"}),
             deal("future", "close", "0.07", {"amount": "80", "currency": "EUR"}, at=1700000050)]
    previous = {"position_id": "100", "status": "open"}
    updated = dict(previous)
    with Session.begin() as session:
        notify_reconciled(session, trade, previous, updated,
                          {"direction": "buy", "volume": "0.07"}, None, deals, 1700000020)
    with Session.begin() as session:
        notify_reconciled(session, trade, updated, dict(updated),
                          {"direction": "buy", "volume": "0.07"}, None, deals, 1700000030)
    with Session() as session:
        notes = session.scalars(select(PaperNotification)).all()
        assert len(notes) == 1
        message = notes[0].message
    assert "CHỐT MỘT PHẦN FOREX · BUY EURUSD" in message
    assert "30.0%" in message and "70.0%" in message
    assert "+12.5 EUR" in message and "USD" not in message.replace("EURUSD", "")
    assert "92.5" not in message
    assert "không gồm PnL thả nổi" in message
    assert updated["notified_close_deals"] == ["partial"]


def test_setup_progress_excludes_unfilled_legs_and_other_positions():
    trade = SimpleNamespace(snapshot={"legs": [{"position_id": "100"}, {"position_id": "200"},
                                              {"status": "skipped", "body": {"volume": "1"}}]})
    deals = [deal("o1", "open", "0.03"), deal("o2", "open", "0.07", pid="200"),
             deal("c1", "close", "0.03", {"amount": "12.50", "currency": "USD"}),
             deal("unrelated", "close", "5", pid="999")]
    result = "\n".join(setup_progress(trade, {"position_id": "100"}, deals))
    assert "30.0%" in result and "70.0%" in result
    assert "+12.5 USD" in result


@pytest.mark.parametrize("deals", [[], [{"profit": None}], [{"profit": "5"}],
    [{"profit": {"amount": "nan", "currency": "USD"}}],
    [{"profit": {"amount": "5", "currency": "USD"}}, {"profit": {"amount": "5", "currency": "EUR"}}]])
def test_profit_unknown_is_not_reported_as_usd(deals):
    assert profit_text(deals) == "chưa xác minh"


def test_labels_v3_v4_and_legacy():
    from backend.app.forex_entry_policy import with_entry
    from backend.app.forex_htf_v2 import g0_trees, tree_id
    for tree in (g0_trees()[0], with_entry(g0_trees()[0])):
        result = strategy_text({"tree": tree, "strategy": tree_id(tree)})
        assert "H1" in result and "M15" in result
        assert tree_id(tree) in result
        assert "limit_or_stop" not in result
    assert strategy_text({"strategy": "old<&"}) == "<code>old&lt;&amp;</code>"


def test_compose_injects_telegram_into_forex_worker():
    from pathlib import Path
    compose = (Path(__file__).parents[2] / "compose.yaml").read_text()
    worker = compose.split("  exness-feed:\n", 1)[1].split("  frontend:\n", 1)[0]
    assert "TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}" in worker
    assert "TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID:-}" in worker



def test_stop_exit_reason_only_when_exit_reached_stop():
    from backend.app.forex_notifications import stop_exit_reason
    assert stop_exit_reason("sell", "157.574", "157.574", "157.574") == "breakeven_stop"
    assert stop_exit_reason("sell", "157.700", "157.574", "157.100") == "stop_loss"
    assert stop_exit_reason("sell", "157.500", "157.574", "157.100") is None
    assert stop_exit_reason("buy", "1.0890", "1.0900", "1.0950") == "stop_loss"
    assert stop_exit_reason("buy", "1.0950", "1.0900", "1.0850") is None
    assert stop_exit_reason("buy", "1.0", None, None) is None


def test_notify_reconciled_accepts_float_timestamp(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    trade = SimpleNamespace(
        account="31000117657",
        symbol="GBPJPY",
        signal_time=1793170800,
        snapshot={"signal": {"strategy": "hast4_d2c9fb7a3d2f", "side": "short"}},
    )
    with Session.begin() as session:
        notify_reconciled(
            session,
            trade,
            previous={"status": "submitted"},
            updated={"status": "open", "position_id": "999", "role": "runner"},
            position={"position_id": "999", "open_price": "209.68", "volume": "0.1", "direction": "sell"},
            protection=None,
            deals=[],
            now=1793171779.325541,
        )
    with Session() as session:
        row = session.scalar(select(PaperNotification).where(PaperNotification.id.like("%forex%")))
        assert row is not None
        assert "GBPJPY" in row.message
