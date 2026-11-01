from backend.app.strategy import Candle
from scripts.futures_confirmation_replay import advance_confirmation, gate_flags, value


def pending(arm="retest", side="long"):
    return dict(arm=arm, side=side, status="open", entry=None, candle_time=0,
                entry_time=900, next_confirmation=900, stop=90 if side == "long" else 110,
                target=120 if side == "long" else 80, zone=(99, 101))


def test_retest_only_enters_after_confirmation_close():
    sample = pending()
    bar = Candle(900, 100, 104, 99, 103, 1)
    advance_confirmation(sample, bar, bar, [], [bar])
    assert sample["confirmed"] and sample["entry_time"] == 1800
    assert sample["entry"] is None


def test_sweep_needs_strict_far_zone_breach_in_both_directions():
    for side in ("long", "short"):
        sample = pending("sweep", side)
        bar = (Candle(900, 100, 104, 99, 103, 1) if side == "long"
               else Candle(900, 100, 101, 96, 97, 1))
        advance_confirmation(sample, bar, bar, [], [bar])
        assert not sample.get("confirmed")
        bar = (Candle(1800, 100, 104, 98, 103, 1) if side == "long"
               else Candle(1800, 100, 102, 96, 97, 1))
        advance_confirmation(sample, bar, bar, [], [bar])
        assert sample["confirmed"] and sample["entry"] is None


def test_stop_and_target_before_confirmation_cancel():
    for bar in (Candle(900, 100, 104, 89, 103, 1), Candle(900, 100, 121, 99, 103, 1)):
        sample = pending()
        advance_confirmation(sample, bar, bar, [], [bar])
        assert sample["status"] == "cancelled" and value(sample) == 0


def test_expiry_gap_and_close_through_zone():
    for stamp, expected in ((1800, "excluded"), (900, "cancelled")):
        sample = pending()
        bar = Candle(stamp, 100, 101, 97, 98, 1)
        advance_confirmation(sample, bar, bar, [], [bar])
        assert sample["status"] == expected
    sample = pending()
    sample["next_confirmation"] = 4500
    bar = Candle(4500, 100, 104, 99, 103, 1)
    advance_confirmation(sample, bar, bar, [], [bar])
    assert sample["exit_reason"] == "confirmation_expired"


def test_three_gates_require_ema_and_distinguish_confirmation_count(monkeypatch):
    import scripts.futures_confirmation_replay as study

    monkeypatch.setattr(study, "matches", lambda node, *args, **kwargs: node["pass"])
    for flags, expected in (((True, True, False), (True, True, False)),
                            ((True, True, True), (True, True, True)),
                            ((True, False, False), (True, False, False)),
                            ((False, True, True), (False, False, False))):
        assert gate_flags({"children": [{"pass": flag} for flag in flags]}, [], "long", {}) == expected
