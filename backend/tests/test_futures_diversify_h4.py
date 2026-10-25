from backend.app import futures_learner as learner
from backend.app.futures_rule_tree import consensus_slot
from scripts.futures_diversify_h4 import diversify, default_slot

NOW = 1000000

def test_diversify_keeps_control_and_protected():
    state = learner.new_state(0)
    names = list(state["candidates"].keys())
    # Protect one strategy with a sample
    protected_name = names[0]
    state["samples"].append({
        "strategy": protected_name, "status": "resolved", "net_r": 1.0,
        "entry": 100, "risk": 5, "candle_time": 0, "entry_time": 900, "label_available_at": 1800,
        "side": "long",
    })
    # Active orders from paper
    active = {names[1]}
    
    total_before = len(state["candidates"])
    default_before = sum(default_slot(c["genome"]) for c in state["candidates"].values())
    
    result = diversify(state, active, NOW, "test_seed")
    
    assert protected_name in state["candidates"]
    assert default_slot(state["candidates"][protected_name]["genome"])
    assert names[1] in state["candidates"]
    assert default_slot(state["candidates"][names[1]]["genome"])
    
    assert len(state["candidates"]) == total_before
    assert result["changed"] == result["eligible"] - result["control"] > 30
    assert result["pairs"] == result["changed"]
    assert default_before - result["changed"] == sum(
        default_slot(c["genome"]) for c in state["candidates"].values())
    
    # Check all new candidates have valid consensus slots
    for name, candidate in state["candidates"].items():
        slot = candidate["genome"]["rule"]["children"][0]
        assert consensus_slot(slot)
        assert slot["children"][0] == {"feature": "trend", "frame": "H4"}
        assert slot["children"][1]["feature"] != slot["children"][2]["feature"]
        
    # Check archived trees
    archive = state["rule_archive"]
    assert len(archive) == result["changed"]
    for old_name, old_item in archive.items():
        assert old_item["retirement"]["reason"] == "h4_diversified"

def test_diversify_idempotent_on_second_run():
    state = learner.new_state(0)
    diversify(state, set(), NOW, "test_seed_1")
    again = diversify(state, set(), NOW + 900, "test_seed_2")
    assert again["changed"] == 0  # chỉ còn nhóm đối chứng, và nhóm này tự giữ lại
    for candidate in state["candidates"].values():
        slot = candidate["genome"]["rule"]["children"][0]
        assert consensus_slot(slot)
