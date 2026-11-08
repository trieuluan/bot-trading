"""Automated machine learning rule discovery (Shallow Decision Trees & KNN).

Trains on Fold 1 (Train 50%), validates on Fold 2 (25%), evaluates on Fold 3 (Holdout 25%).
Tests all 4 timeframes: D1, H4, H1, M15.
Discovers exact non-linear numerical boundaries (feature splits) without human naming.
Accounts for 0.08% taker fees, 0.02% slippage per side, and settled funding.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

import numpy as np
from sklearn.tree import DecisionTreeRegressor, export_text

from scripts.candle_discovery_mtf import TIMEFRAMES, build_folds, extract_features_and_trades
from scripts.htf_portfolio_replay import load_data

FEATURE_NAMES = [
    "bar_dir_match",
    "body_ratio",
    "wick_entry",
    "wick_contra",
    "norm_range",
    "range_compression",
    "pos_in_range",
    "dist_ema20",
    "ema_trend_match",
    "d1_trend_match",
]

OUTPUT_FILE = Path("research-output/candle-ml-tree-discovery.json")


def run_tree_discovery():
    data, first, end, digest = load_data()
    folds = build_folds(first, end)
    print(f"Loaded {len(data)} pairs. Folds: Train to {datetime.fromtimestamp(folds[0]['end'], timezone.utc)}, Val to {datetime.fromtimestamp(folds[1]['end'], timezone.utc)}, Holdout to {datetime.fromtimestamp(folds[2]['end'], timezone.utc)}")

    results = {}

    for tf_name in TIMEFRAMES:
        all_samples = []
        for sym, pack in data.items():
            s = extract_features_and_trades(sym, pack[2], pack[3], pack[4], tf_name)
            all_samples.extend(s)

        train_s = [s for s in all_samples if folds[0]["start"] <= s["time"] < folds[0]["end"]]
        val_s = [s for s in all_samples if folds[1]["start"] <= s["time"] < folds[1]["end"]]
        hold_s = [s for s in all_samples if folds[2]["start"] <= s["time"] < folds[2]["end"]]

        X_train = np.array([[s[k] for k in FEATURE_NAMES] for s in train_s], dtype=np.float32)
        y_train = np.array([s["net_r"] for s in train_s], dtype=np.float32)

        X_val = np.array([[s[k] for k in FEATURE_NAMES] for s in val_s], dtype=np.float32)
        y_val = np.array([s["net_r"] for s in val_s], dtype=np.float32)

        X_hold = np.array([[s[k] for k in FEATURE_NAMES] for s in hold_s], dtype=np.float32)
        y_hold = np.array([s["net_r"] for s in hold_s], dtype=np.float32)

        print(f"\n=======================================================")
        print(f" MACHINE DISCOVERY - TIMEFRAME {tf_name}")
        print(f" Samples: Train={len(train_s)}, Val={len(val_s)}, Holdout={len(hold_s)}")
        print(f" Baseline Net R: Train={y_train.mean():+.3f}, Val={y_val.mean():+.3f}, Holdout={y_hold.mean():+.3f}")
        print(f"=======================================================")

        # Train shallow decision tree regressor (depth 3 = max 8 rules)
        # min_samples_leaf enforces statistical significance
        min_leaf = max(30, int(len(train_s) * 0.02))
        tree = DecisionTreeRegressor(max_depth=3, min_samples_leaf=min_leaf, random_state=42)
        tree.fit(X_train, y_train)

        # Predict leaf node assignments
        leaf_train = tree.apply(X_train)
        leaf_val = tree.apply(X_val)
        leaf_hold = tree.apply(X_hold)

        unique_leaves = sorted(set(leaf_train))
        leaf_reports = []

        for leaf_id in unique_leaves:
            m_tr = y_train[leaf_train == leaf_id]
            m_va = y_val[leaf_val == leaf_id]
            m_ho = y_hold[leaf_hold == leaf_id]

            if len(m_tr) < 20 or len(m_va) < 10 or len(m_ho) < 10:
                continue

            r_tr = float(m_tr.mean())
            r_va = float(m_va.mean())
            r_ho = float(m_ho.mean())
            win_ho = float((m_ho > 0).mean())

            leaf_reports.append({
                "leaf_id": int(leaf_id),
                "n_train": len(m_tr), "r_train": r_tr,
                "n_val": len(m_va), "r_val": r_va,
                "n_hold": len(m_ho), "r_hold": r_ho,
                "win_hold": win_ho,
            })

            flag = "PASS" if (r_tr > 0 and r_va > 0 and r_ho > 0) else "FAIL"
            print(f" Leaf {leaf_id:2d} [{flag:4s}]: Train(N={len(m_tr):4d}, R={r_tr:+.3f}) | Val(N={len(m_va):4d}, R={r_va:+.3f}) | Hold(N={len(m_ho):4d}, R={r_ho:+.3f}, Win={win_ho*100:4.1f}%)")

        tree_rules_text = export_text(tree, feature_names=FEATURE_NAMES)
        results[tf_name] = {
            "baseline": {"train": float(y_train.mean()), "val": float(y_val.mean()), "holdout": float(y_hold.mean())},
            "leaves": leaf_reports,
            "tree_structure": tree_rules_text,
        }

    OUTPUT_FILE.write_text(json.dumps(results, indent=2))
    print(f"\nDiscovery report written to {OUTPUT_FILE}")


if __name__ == "__main__":
    run_tree_discovery()
