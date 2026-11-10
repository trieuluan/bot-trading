"""Offline replay of a frozen audit using real cached inputs; never reads learner DB."""

import argparse
import copy
import cProfile
import importlib.util
import json
import time
from pathlib import Path

from backend.app.futures_draft_replay import read_cache
from backend.app.futures_history import WARMUP, replay
from backend.app.futures_native import NATIVE_WARMUP
from backend.app.futures_rule_tree import is_htf_rule, nodes
from backend.app.futures_strategies import genome_name
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.market import Candle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--symbol', choices=SYMBOLS, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history-source', type=Path)
    parser.add_argument('--bars', type=int)
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    frozen = json.loads(args.snapshot.read_text())
    candidates = copy.deepcopy(frozen['candidates'])
    for candidate in list(candidates.values()):
        genome = candidate['genome']
        if not genome.get('signal_frame') and not genome.get('signal_version') and is_htf_rule(genome['rule']):
            paired = {**copy.deepcopy(genome), 'signal_version': 2}
            candidates[genome_name(paired)] = {'genome': paired}
    start, end = frozen['window']['start'], frozen['window']['end']
    if args.bars is not None:
        if not 1 <= args.bars <= (end - start) // 900:
            parser.error('--bars must fit the frozen window')
        end = start + args.bars * 900
    warmup = max(NATIVE_WARMUP if row['genome'].get('signal_frame') else WARMUP
                 for row in candidates.values())
    since = start - warmup * 900
    loaded = time.perf_counter()

    def candles(symbol, kind):
        rows = read_cache(args.cache / f'{symbol}-{kind}.json.gz') or []
        result = [Candle(**row) for row in rows if since <= row['time'] < end]
        if [bar.time for bar in result] != list(range(since, end, 900)):
            raise ValueError(f'Incomplete offline cache: {symbol}-{kind}')
        return result

    trade, fair = candles(args.symbol, 'trade'), candles(args.symbol, 'fair')
    stored = read_cache(args.cache / f'{args.symbol}-funding.json.gz') or {}
    if stored.get('start', end) > since - 2 * 86400 or stored.get('end', 0) < end:
        raise ValueError('Incomplete offline funding cache')
    funding = [row for row in stored['rows'] if since - 2 * 86400 <= row['time'] < end]
    features = {node.get('feature') for row in candidates.values() for _, node in nodes(row['genome']['rule'])}
    if 'oi_surge' in features:
        raise ValueError('OI requires a separate real observation export; cannot synthesize it')
    benchmarks = ({symbol: candles(symbol, 'trade') for symbol in SYMBOLS if symbol != args.symbol}
                  if features & {'market_alignment', 'relative_strength'} else None)
    data_seconds = time.perf_counter() - loaded
    execute = replay
    if args.history_source:
        spec = importlib.util.spec_from_file_location('backend.app._benchmark_history', args.history_source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        execute = module.replay
    profiler = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    if profiler:
        profiler.enable()
    with symbol_scope(args.symbol):
        report = execute(candidates, trade, fair, funding, start, end,
                         benchmarks=benchmarks, strict_samples=True, diagnostics=True, include_samples=True)
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(args.profile))
    seconds = time.perf_counter() - started
    args.output.write_text(json.dumps({'data_seconds': data_seconds, 'replay_seconds': seconds,
                                       'report': report}, allow_nan=False, indent=2))
    print(json.dumps({'data_seconds': data_seconds, 'replay_seconds': seconds,
                      'dataset_sha256': report['dataset_sha256']}), flush=True)


if __name__ == '__main__':
    main()
