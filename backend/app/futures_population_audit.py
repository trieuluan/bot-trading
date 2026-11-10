"""Frozen population audits, separate from forward labels and draft qualification."""

import copy
import hashlib
import json
from pathlib import Path

from .futures_rule_tree import is_htf_rule
from .futures_strategies import genome_fingerprint, genome_name

AUDIT_DAYS = 90
AUDIT_VERSION = 'futures_population_v1'


def engine_digest():
    sources = ('futures_population_audit.py', 'futures_history.py', 'futures_strategies.py',
               'futures_signal_window.py', 'futures_rule_tree.py', 'futures_entries.py',
               'futures_exits.py', 'futures_learner.py', 'strategy.py', 'futures_draft_replay.py',
               'backtest.py', 'futures_retirement.py', 'futures_native.py')
    return hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in sources)).hexdigest()


def freeze(state, now, candles):
    cutoff = min(now // 900 * 900, (state.get('cursor') or 0) + 900)
    sampled = {sample['strategy'] for sample in state['samples'] + state.get('sample_archive', [])}
    candidates = {}
    times = {bar.time for bar in candles}
    for name, candidate in state['candidates'].items():
        if name in sampled:
            continue
        born = candidate['born_at']
        expected = list(range(born, cutoff, 900))
        complete = bool(expected) and all(stamp in times for stamp in expected)
        candidates[name] = {'genome': copy.deepcopy(candidate['genome']), 'born_at': born,
                            'fingerprint': genome_fingerprint(candidate['genome']),
                            'forward_coverage': {'complete': complete, 'start': born, 'end': cutoff,
                                                 'bars': len(expected) if complete else 0}}
    return {'version': AUDIT_VERSION, 'engine': engine_digest(), 'cutoff': cutoff,
            'window': {'start': cutoff - AUDIT_DAYS * 86400, 'end': cutoff},
            'candidates': candidates}


def run_audit(symbol, frozen, emit=lambda **changes: None, cache_dir=None):
    from .futures_draft_replay import ReplayDataError, run_pair

    if frozen['version'] != AUDIT_VERSION or frozen['engine'] != engine_digest():
        raise ValueError('Stale population audit engine')
    results = {}
    for name, candidate in frozen['candidates'].items():
        base = {'version': AUDIT_VERSION, 'engine': frozen['engine'],
                'fingerprint': candidate['fingerprint'], 'born_at': candidate['born_at'],
                'cutoff': frozen['cutoff'], 'forward_coverage': candidate['forward_coverage'],
                'start': frozen['window']['start'], 'end': frozen['window']['end']}
        if genome_fingerprint(candidate['genome']) != candidate['fingerprint']:
            raise ValueError('Frozen population genome changed')
        arms = {name: candidate}
        paired = None
        if (not candidate['genome'].get('signal_frame') and not candidate['genome'].get('signal_version')
                and is_htf_rule(candidate['genome']['rule'])):
            genome = {**copy.deepcopy(candidate['genome']), 'signal_version': 2}
            paired = genome_name(genome)
            arms[paired] = {'genome': genome}
        try:
            emit(phase=f'audit:{name}')
            report = run_pair(symbol, arms, frozen['window'], emit, cache_dir,
                              diagnostics=True)
            rows = {row['strategy']: row for row in report['strategies']}
            original = rows[name]
            resolved = sum(original[part]['resolved'] for part in ('train', 'validation'))
            comparison = {}
            for arm, row in rows.items():
                comparison[arm] = {key: row[key] for key in ('genome', 'train', 'validation', 'funnel')}
            sparse = (not candidate['genome'].get('signal_frame')
                      and is_htf_rule(candidate['genome']['rule']) and resolved < 5)
            results[name] = {**base, 'status': 'sparse' if sparse else 'keep',
                             'reason': ('sparse_signal_not_loss_evidence' if sparse else
                                        'native_protected' if candidate['genome'].get('signal_frame')
                                        else 'sufficient_replay_samples'),
                             'resolved': resolved, 'coverage_complete': True,
                             'dataset_sha256': report['dataset_sha256'], 'comparison': comparison}
        except (ReplayDataError, ValueError) as exc:
            results[name] = {**base, 'status': 'insufficient_data', 'coverage_complete': False,
                             'reason': f'{type(exc).__name__}: {exc}'}
    return results


def eligible(candidate, audit, cursor):
    genome = candidate['genome']
    if not audit or genome.get('signal_frame') or not is_htf_rule(genome['rule']):
        return False
    coverage = audit.get('forward_coverage') or {}
    cutoff = audit.get('cutoff', 0)
    resolved = audit.get('resolved')
    if (not isinstance(coverage, dict) or any(type(value) is not int for value in
            (cutoff, audit.get('start'), candidate.get('born_at'), cursor, coverage.get('bars')))):
        return False
    if cutoff % 900 or candidate['born_at'] % 900:
        return False
    return (audit.get('version') == AUDIT_VERSION and audit.get('engine') == engine_digest()
            and audit.get('status') == 'sparse' and audit.get('coverage_complete') is True
            and audit.get('fingerprint') == genome_fingerprint(genome)
            and audit.get('born_at') == candidate['born_at']
            and isinstance(audit.get('dataset_sha256'), str) and len(audit['dataset_sha256']) == 64
            and type(resolved) is int and 0 <= resolved < 5
            and audit.get('end') == cutoff
            and cutoff - audit.get('start', cutoff) == AUDIT_DAYS * 86400
            and coverage.get('complete') is True and coverage.get('start') == candidate['born_at']
            and coverage.get('end') == cutoff
            and coverage.get('bars', 0) * 900 == cutoff - candidate['born_at']
            and cutoff - candidate['born_at'] >= 5 * 86400
            and 0 <= cursor + 900 - cutoff <= 86400)


def attach(state, results):
    attached = {}
    sampled = {sample['strategy'] for sample in state['samples'] + state.get('sample_archive', [])}
    for name, audit in results.items():
        candidate = state['candidates'].get(name)
        if (candidate and name not in sampled and audit.get('fingerprint') == genome_fingerprint(candidate['genome'])
                and audit.get('born_at') == candidate['born_at']):
            attached[name] = audit
    state.setdefault('population_audit', {}).update(attached)
    return attached


def write_report(path, report):
    target = Path(path)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    temporary.replace(target)
