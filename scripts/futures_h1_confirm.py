"""Local H1-confirmation replay: same seeds, H4 2/3, M15, SL/TP; only H1 gains one extra ALL child.
Research only, no DB writes. Reuses futures_g0_replay so engine/costs stay identical.

python -m scripts.futures_h1_confirm --data research-output/futures-h1 --output research-output/futures-h1/run --workers 10
"""
import argparse
import copy
import hashlib
import json
from multiprocessing import Pool
from pathlib import Path

from backend.app.futures_rule_tree import nodes
from backend.app.futures_strategies import RULE_SEEDS, validate_genome
from backend.app.futures_symbols import SYMBOLS
from scripts import futures_g0_replay as base
from scripts.futures_h4_variants import summarize

EXTRA = {"h1_trend": "trend", "h1_adx": "adx_regime"}
ARMS = ("baseline", *EXTRA)

def with_h1(genome, feature):
    """ALL(H1 setup, extra) or None when that would duplicate the feature / exceed depth (then-chains)."""
    h1 = genome["rule"]["children"][1]
    if any(node.get("feature") == feature for _, node in nodes(h1)) or h1.get("op") == "then":
        return None
    extra = {"feature": feature, "frame": "H1"}
    result = copy.deepcopy(genome)
    result["rule"]["children"][1] = ({"op": "all", "children": [*h1["children"], extra]}
                                     if h1.get("op") == "all" else {"op": "all", "children": [h1, extra]})
    validate_genome(result)
    return result

def candidates():
    result = {}
    for index, seed in enumerate(RULE_SEEDS):
        if "feature" not in seed["rule"]["children"][1]:
            continue
        variants = {arm: with_h1(seed, feature) for arm, feature in EXTRA.items()}
        if any(genome is None for genome in variants.values()):
            continue
        result[f"baseline:{index:02d}"] = {"genome": copy.deepcopy(seed)}
        for arm, genome in variants.items():
            result[f"{arm}:{index:02d}"] = {"genome": genome}
    return result

def run_pair(task):
    original = base.candidates
    base.candidates = candidates
    try:
        return base.run_pair(task)
    finally:
        base.candidates = original

def summary(payloads):
    templates = {name.split(":")[1] for name in candidates()}
    result = {}
    for arm in ARMS:
        result[arm] = {}
        for part in ("train", "validation"):
            rows = [row for payload in payloads for row in payload["rows"]
                    if row[1] == arm and row[2] == part and row[3] in templates]
            drawdowns = [row[part]["max_drawdown_r"]
                         for payload in payloads for row in payload["report"]["strategies"]
                         if row["strategy"] in {f"{arm}:{template}" for template in templates}]
            result[arm][part] = {
                **summarize(rows), "templates": sorted(templates),
                "worst_strategy_drawdown_r": max(drawdowns, default=0),
                "by_symbol": {symbol: summarize([row for row in rows if row[0] == symbol])
                              for symbol in SYMBOLS},
            }
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5, choices=range(1, 11))
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.summarize_only:
        manifest = {"candidates": candidates(), "sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), Path(base.__file__), *Path("backend/app").glob("*.py"),
                         args.data / "mexc_10pairs_340d_compare.pkl")}}
        target = args.output / "matched-manifest.json"
        if target.exists() and json.loads(target.read_text()) != manifest:
            raise ValueError("Replay inputs changed; choose a new output directory")
        target.write_text(json.dumps(manifest, sort_keys=True, indent=2))
        pending = [(symbol, str(args.data), str(args.output)) for symbol in SYMBOLS
                   if not (args.output / f"{symbol}.json").exists()]
        with Pool(args.workers) as pool:
            for _ in pool.imap_unordered(run_pair, pending):
                pass
    payloads = [json.loads((args.output / f"{symbol}.json").read_text()) for symbol in SYMBOLS]
    (args.output / "matched-summary.json").write_text(json.dumps(summary(payloads), indent=2, allow_nan=False))

if __name__ == "__main__":
    main()
