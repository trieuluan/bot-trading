"""Offline replay of every captured signal and all entry policies; never contacts broker."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.forex_entry_replay import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must not overwrite input")
    report = compare(json.loads(args.input.read_text()))
    with args.output.open("x") as handle:
        json.dump(report, handle, allow_nan=False)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
