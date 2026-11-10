import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import futures_history as history
from backend.app import futures_population_audit as audit
from backend.app.db import FuturesLearningState, Session
from backend.app.futures_research_lock import worker_status
from backend.app.futures_strategies import RULE_SEEDS, genome_fingerprint, genome_name
from backend.app.market import Candle
from backend.app.strategy import aggregate, aggregation_cache
from bot import draft_replay_worker as worker


def state_with_candidates():
    first = copy.deepcopy(RULE_SEEDS[0])
    second = {**copy.deepcopy(first), 'target_r': 3.0}
    return {'enabled': True, 'cursor': 8999100, 'samples': [], 'candidates': {
        genome_name(genome): {'genome': genome, 'born_at': 8000100}
        for genome in (first, second)}}


def failed_job(state, name, *, attempts=1, finished=9000000, retryable=True):
    frozen = audit.freeze(state, finished, [])
    frozen.update(purpose='population_audit', candidates={name: frozen['candidates'][name]})
    job = {'strategy': name, 'snapshot': frozen, 'attempts': attempts, 'phase': 'replay',
           'completed': 6240, 'total': 8640}
    audit.finish_audit(state, job, 'Replay exceeded 60 minutes', retryable, finished)
    return job


def test_timeout_budget_separates_audit_pair_and_universe():
    assert worker.replay_timeout({'purpose': 'population_audit', 'window': {}}) == 3600
    assert worker.replay_timeout({'window': {}}) == 1200
    assert worker.replay_timeout({'window': {'scope': worker.UNIVERSE_SCOPE}}) == 3600


@pytest.mark.parametrize('attempts,delay', [(1, 300), (2, 1800), (3, 86400)])
def test_retry_delay_and_frozen_identity(attempts, delay):
    state = state_with_candidates()
    name, other = sorted(state['candidates'])
    job = failed_job(state, name, attempts=attempts)
    assert job['next_retry_at'] == 9000000 + delay
    assert audit.next_audit(state, 9000001)[2] == other
    state['candidates'].pop(other)
    assert audit.next_audit(state, job['next_retry_at'] - 1) is None
    chosen = audit.start_audit(state, job['next_retry_at'], [])
    assert chosen[1] == job['snapshot']
    assert chosen[1] is not job['snapshot']
    assert chosen[2] == (attempts + 1 if attempts < 3 else 1)
    assert state['population_audit_retries'][name]['completed'] == 6240


def test_failed_legacy_job_does_not_lock_symbol_or_drop_history():
    state = state_with_candidates()
    name, other = sorted(state['candidates'])
    job = failed_job(state, name)
    state.pop('population_audit_retries')
    state['population_audit_job'] = job
    before = copy.deepcopy(state['candidates'])
    assert audit.next_audit(state, 9000001)[2] == other
    assert state['population_audit_retries'][name]['snapshot'] == job['snapshot']
    assert state['candidates'] == before
    assert state['samples'] == []


@pytest.mark.parametrize('change', ['engine', 'genome', 'born_at'])
def test_changed_identity_never_reuses_frozen_retry(change):
    state = state_with_candidates()
    name = sorted(state['candidates'])[0]
    state['candidates'] = {name: state['candidates'][name]}
    failed_job(state, name)
    if change == 'engine':
        state['population_audit_retries'][name]['snapshot']['engine'] = 'old-engine'
    elif change == 'genome':
        state['candidates'][name]['genome']['target_r'] = 4.0
    else:
        state['candidates'][name]['born_at'] += 900
    state['cursor'] += 900
    chosen = audit.start_audit(state, 9000900, [])
    assert chosen[1]['cutoff'] != 9000000
    assert chosen[2] == 1
    assert chosen[1]['candidates'][name]['fingerprint'] == genome_fingerprint(state['candidates'][name]['genome'])


def test_success_clears_retry_and_nonretryable_failure_waits_one_day():
    state = state_with_candidates()
    name = sorted(state['candidates'])[0]
    job = failed_job(state, name, retryable=False)
    assert job['next_retry_at'] == 9000000 + 86400
    audit.finish_audit(state, job, None, True, 9001000)
    assert job['status'] == 'completed'
    assert name not in state['population_audit_retries']


@pytest.mark.parametrize('seconds', [3600, 14400, 86400])
def test_slice_cache_preserves_frames_gaps_sources_and_cutoffs(seconds):
    bars = [Candle(index * 900, 100, 102, 98, 101, 10) for index in range(512)]
    other = list(bars)
    other[123] = Candle(bars[123].time, 200, 210, 190, 205, 20)
    histories = [bars, list(bars), bars[1:], bars[:127], bars[:128], bars[3:257],
                 bars[:124] + bars[125:], other]
    cases = [(rows, cutoff, aggregate(rows, seconds, until=cutoff)) for rows in histories
             for cutoff in (None, 127 * 900, 128 * 900, 239 * 900)]
    with aggregation_cache(reuse_slices=True):
        first = aggregate(bars, seconds)
        assert aggregate(list(bars), seconds) is first
        for rows, cutoff, expected in cases:
            assert aggregate(rows, seconds, until=cutoff) == expected
        assert aggregate(bars[:128], seconds) == aggregate(bars, seconds, until=127 * 900)
    assert aggregate(bars, seconds) is not first


def test_replay_report_is_identical_with_slice_reuse_disabled(monkeypatch):
    bars = [Candle(i * 900, 100, 101, 99, 100, 10) for i in range(history.WARMUP + 192)]
    candidates = {genome_name(RULE_SEEDS[0]): {'genome': RULE_SEEDS[0]}}
    kwargs = dict(diagnostics=True, strict_samples=True, include_samples=True)
    start, end = bars[history.WARMUP].time, bars[-1].time + 900
    expected = history.replay(candidates, bars, bars, [], start, end, **kwargs)
    monkeypatch.setattr(history, 'aggregation_cache', lambda **_: aggregation_cache())
    assert history.replay(candidates, bars, bars, [], start, end, **kwargs) == expected


def supervisor_context(monkeypatch, messages):
    clock = [0.0]
    receive, send, child = Mock(), Mock(), Mock()
    def poll(_):
        clock[0] += 16
        return bool(messages)
    receive.poll.side_effect = poll
    receive.recv.side_effect = lambda: messages.pop(0)
    child.is_alive.return_value = True
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(worker, 'stop', SimpleNamespace(is_set=lambda: False))
    monkeypatch.setattr(worker.multiprocessing, 'get_context', lambda _: SimpleNamespace(
        Pipe=lambda **_: (receive, send), Process=lambda **_: child))
    return child


def test_timeout_retains_latest_progress_and_reports_timings(monkeypatch):
    child = supervisor_context(monkeypatch, [
        ('progress', {'phase': 'replay', 'completed': 96, 'total': 8640}),
        ('progress', {'phase': 'replay', 'completed': 192, 'total': 8640})])
    monkeypatch.setattr(worker, 'AUDIT_TIMEOUT_SECONDS', 30)
    updates = []
    result = worker.supervise(Mock(), 'BTC_USDT', {'purpose': 'population_audit', 'window': {}},
                              lambda **changes: updates.append(changes))
    assert result[0] is None and result[2] is True
    assert updates[-1]['completed'] == 192
    assert updates[-1]['error_kind'] == 'timeout'
    assert updates[-1]['elapsed_seconds'] == 32
    assert updates[-1]['data_seconds'] + updates[-1]['replay_seconds'] == 32
    child.terminate.assert_called_once()


def test_dead_process_and_success_not_masked_by_heartbeat(monkeypatch):
    child = supervisor_context(monkeypatch, [])
    child.is_alive.return_value = False
    assert 'Replay process exited' in worker.supervise(Mock(), 'BTC_USDT', {'window': {}}, lambda **_: None)[1]
    supervisor_context(monkeypatch, [('result', {'complete': True})])
    assert worker.supervise(Mock(), 'BTC_USDT', {'window': {}}, lambda **_: None)[0] == {'complete': True}


def test_scheduler_prioritizes_untried_tree_across_symbols_and_persists_progress(monkeypatch):
    from backend.app.futures_symbols import current_symbol

    states = {symbol: state_with_candidates() for symbol in ('BTC_USDT', 'ETH_USDT')}
    for state in states.values():
        name = sorted(state['candidates'])[0]
        state['candidates'] = {name: state['candidates'][name]}
    name = next(iter(states['BTC_USDT']['candidates']))
    failed_job(states['BTC_USDT'], name, finished=9000000)
    monkeypatch.setattr(worker, 'SYMBOLS', list(states))
    monkeypatch.setattr(worker, 'read_state', lambda *_: copy.deepcopy(states[current_symbol()]))
    monkeypatch.setattr(worker, 'save_state', lambda _, state: states.update({current_symbol(): state}))
    monkeypatch.setattr(worker, 'load_candles', lambda *_, **__: [])
    monkeypatch.setattr(worker, 'lock_wallet', lambda _: None)
    monkeypatch.setattr(worker, 'touch', lambda *_, **__: None)
    monkeypatch.setattr(worker, 'admit_drafts', lambda *_: 0)
    monkeypatch.setattr(worker, 'active_strategies', lambda *_: set())
    monkeypatch.setattr(worker.time, 'time', lambda: 9000400)
    visited = []
    def supervise(_, symbol, frozen, emit):
        visited.append(symbol)
        emit(phase='replay', completed=192, total=8640, error_kind='timeout', elapsed_seconds=3600)
        return None, 'Replay exceeded 60 minutes', True
    monkeypatch.setattr(worker, 'supervise', supervise)
    assert worker.audit_population(Mock(), 9000400)
    assert visited == ['ETH_USDT']
    job = states['ETH_USDT']['population_audit_job']
    assert job['completed'] == 192 and job['error_kind'] == 'timeout'
    assert job['next_retry_at'] == 9000700
    assert states['BTC_USDT']['population_audit_retries'][name]['attempts'] == 1


def test_interrupted_audit_keeps_snapshot_without_spending_attempt():
    state = state_with_candidates()
    name = sorted(state['candidates'])[0]
    state['candidates'] = {name: state['candidates'][name]}
    job = failed_job(state, name, attempts=2)
    job['status'] = 'running'
    state['population_audit_retries'][name] = job
    chosen = audit.start_audit(state, 9000100, [])
    assert chosen[1] == job['snapshot'] and chosen[2] == 2


@pytest.mark.parametrize('purpose', ['audit', 'draft', 'parents'])
def test_worker_status_enriches_active_job(purpose):
    job = {'status': 'running', 'strategy': 'rule_tree:target', 'phase': 'replay',
           'completed': 4200, 'total': 8640, 'updated_at': 1794334550,
           'token': 'private', 'snapshot': {'private': True}}
    phase = {'audit': 'audit:replay', 'draft': 'BTC_USDT:replay', 'parents': 'parents:replay'}[purpose]
    with Session.begin() as session:
        session.merge(FuturesLearningState(id=1, payload={
            'draft_worker': {'heartbeat_at': 1794334550, 'phase': phase, 'symbol': 'ETH_USDT', 'draft_id': 'draft-1'},
            'universe_parent_job': job,
            'population_audit_job': {**job, 'strategy': 'wrong-symbol'},
        }))
        session.merge(FuturesLearningState(id=2, payload={
            'population_audit_job': job, 'drafts': {'draft-1': job},
        }))
    with Session() as session:
        status = worker_status(session)
        assert status['symbol'] == 'ETH_USDT'
        assert status['active_job']['purpose'] == purpose
        assert status['active_job']['strategy'] == 'rule_tree:target'
        assert status['active_job']['completed'] == 4200
        assert 'token' not in status['active_job'] and 'snapshot' not in status['active_job']


@pytest.mark.parametrize('phase,job_status', [('idle', 'running'), ('audit:replay', 'completed')])
def test_worker_status_does_not_present_old_job_as_active(phase, job_status):
    with Session.begin() as session:
        session.merge(FuturesLearningState(id=1, payload={
            'draft_worker': {'heartbeat_at': 1794334550, 'phase': phase, 'symbol': 'BTC_USDT'},
            'population_audit_job': {'status': job_status},
        }))
    with Session() as session:
        assert 'active_job' not in worker_status(session)

def test_worker_status_universe_draft_reports_replay_pair():
    job = {'status': 'running', 'phase': 'AVAX_USDT:replay', 'completed': 1, 'total': 2}
    with Session.begin() as session:
        session.merge(FuturesLearningState(id=1, payload={'draft_worker': {
            'heartbeat_at': 1794334550, 'phase': 'AVAX_USDT:replay', 'symbol': 'DOGE_USDT', 'draft_id': 'd'}}))
        session.merge(FuturesLearningState(id=5, payload={'drafts': {'d': job}}))
    with Session() as session:
        active = worker_status(session)['active_job']
    assert (active['symbol'], active['replay_symbol'], active['universe']) == ('DOGE_USDT', 'AVAX_USDT', True)

def test_worker_status_universe_draft_before_pair_prefix_does_not_guess():
    from backend.app.futures_drafts import UNIVERSE_SCOPE
    job = {'status': 'running', 'phase': 'data', 'window': {'scope': UNIVERSE_SCOPE}}
    with Session.begin() as session:
        session.merge(FuturesLearningState(id=1, payload={'draft_worker': {
            'heartbeat_at': 1794334550, 'phase': 'data', 'symbol': 'DOGE_USDT', 'draft_id': 'd'}}))
        session.merge(FuturesLearningState(id=5, payload={'drafts': {'d': job}}))
    with Session() as session:
        active = worker_status(session)['active_job']
    assert (active['symbol'], active['replay_symbol'], active['universe']) == ('DOGE_USDT', None, True)
