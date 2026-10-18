import copy

from scripts.futures_evolve_adaptive import sound, top_candidates, training_parents


def test_training_selection_ignores_validation_and_handles_zero():
    rows = [{"genome": {"id": str(index)},
             "train": {"resolved": count, "mean_r": mean_r},
             "validation": {"mean_r": validation}}
            for index, (count, mean_r, validation) in enumerate([
                (30, -0.1, 100), (30, 0, -100), (29, 100, 100), (30, None, 100)])]
    expected = [{"id": "1"}, {"id": "0"}]
    assert training_parents(rows) == expected
    changed = copy.deepcopy(rows)
    for row in changed:
        row["validation"] = {"mean_r": -1000}
    assert training_parents(changed) == expected


def test_screen_requires_samples_positive_expectancy_and_profit_factor():
    stat = {"resolved": 30, "mean_r": 0.1, "profit_factor": 1.11}
    assert sound({"validation": stat}, "validation", 1.1)
    for change in ({"resolved": 29}, {"mean_r": None}, {"mean_r": -0.1},
                   {"profit_factor": None}, {"profit_factor": 1.1}):
        assert not sound({"validation": {**stat, **change}}, "validation", 1.1)
    assert sound({"validation": {**stat, "profit_factor": None, "no_losses": True}},
                 "validation", 1.1)
def test_top_candidates_extracts_highest_r_with_min_sample_cutoff():
    rows = [
        {"strategy": "a", "train": {"resolved": 20, "mean_r": 0.25}, "validation": {"resolved": 10, "mean_r": 0.9}},
        {"strategy": "b", "train": {"resolved": 5, "mean_r": 0.50}, "validation": {"resolved": 20, "mean_r": 0.1}},
        {"strategy": "c", "train": {"resolved": 30, "mean_r": -0.1}, "validation": {"resolved": 25, "mean_r": 0.4}},
    ]
    res = top_candidates(rows, n=2)
    assert [r["strategy"] for r in res["by_train"]] == ["a", "c"]
    assert [r["strategy"] for r in res["by_validation"]] == ["c", "b"]
