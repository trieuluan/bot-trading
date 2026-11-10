"""Read-only frozen population audit; never changes trees or forward labels."""

import argparse
import json
import time

from backend.app.db import Session
from backend.app.feed import load_candles
from backend.app.futures_learner import read_state
from backend.app.futures_population_audit import eligible, freeze, run_audit, write_report
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.market import Candle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', help='Read exported {now, symbols: {symbol: {state, candles}}}')
    parser.add_argument('--output', required=True)
    parser.add_argument('--snapshot-only', action='store_true')
    args = parser.parse_args()
    exported = json.loads(open(args.snapshot).read()) if args.snapshot else None
    now = exported['now'] if exported else int(time.time())
    result = {'created_at': now, 'read_only': True, 'archived': [], 'admitted': [], 'symbols': {}}
    for symbol in SYMBOLS:
        if exported:
            source = exported['symbols'][symbol]
            state = source['state']
            candles = [Candle(**row) for row in source['candles']]
        else:
            with symbol_scope(symbol), Session() as session:
                state = read_state(session, now)
                candles = load_candles(session, limit=100000)
        frozen = freeze(state, now, candles)
        audits = {} if args.snapshot_only else run_audit(symbol, frozen)
        decisions = {name: {'action': 'eligible_for_replacement' if eligible(
            state['candidates'][name], audit, state['cursor']) else 'keep', 'reason': audit['reason']}
            for name, audit in audits.items()}
        result['symbols'][symbol] = {'snapshot': frozen, 'audits': audits, 'decisions': decisions}
        write_report(args.output, result)


if __name__ == '__main__':
    main()
