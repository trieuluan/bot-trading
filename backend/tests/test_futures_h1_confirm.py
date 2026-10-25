import copy

from backend.app.futures_strategies import RULE_SEEDS, validate_genome
from scripts import futures_g0_replay
from scripts.futures_h1_confirm import ARMS, EXTRA, candidates, summary, with_h1


def test_h1_comparison_keeps_matched_templates_and_changes_only_h1():
    original = copy.deepcopy(RULE_SEEDS)
    factory = futures_g0_replay.candidates
    population = candidates()
    templates = {name.split(":")[1] for name in population}
    assert len(templates) == 15
    assert len(population) == 3 * len(templates)
    for template in templates:
        control = population[f"baseline:{template}"]["genome"]
        assert control == RULE_SEEDS[int(template)]
        for arm, feature in EXTRA.items():
            changed = copy.deepcopy(population[f"{arm}:{template}"]["genome"])
            validate_genome(changed)
            assert changed["rule"]["children"][1] == {
                "op": "all", "children": [control["rule"]["children"][1],
                                          {"feature": feature, "frame": "H1"}],
            }
            changed["rule"]["children"][1] = control["rule"]["children"][1]
            assert changed == control
    assert RULE_SEEDS == original
    assert futures_g0_replay.candidates is factory
    assert with_h1(RULE_SEEDS[8], "trend") is None
    assert with_h1(RULE_SEEDS[3], "trend") is None


def test_summary_does_not_select_templates_by_observed_trades():
    payload = {"rows": [
        ["BTC_USDT", "baseline", "validation", "07", 900, "long", -1, "stop"],
        ["BTC_USDT", "h1_trend", "validation", "07", 900, "long", 2, "target"],
        ["BTC_USDT", "baseline", "validation", "08", 900, "long", 5, "target"],
    ], "report": {"strategies": []}}
    result = summary([payload])
    assert result["baseline"]["validation"]["trades"] == 1
    assert result["baseline"]["validation"]["mean_r"] == -1
    assert result["h1_adx"]["validation"]["trades"] == 0
    for arm in ARMS:
        assert "07" in result[arm]["validation"]["templates"]
        assert "08" not in result[arm]["validation"]["templates"]
