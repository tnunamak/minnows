"""Regressions for routing independence, receipt acceptance and observation notes."""
import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_quota_substitution import NOW, active, block, world  # noqa: F401
from test_route import REPO, stable
from test_route_destinations import capacity, config, entry
import decision_receipts
import directives
import observations
import policy_decisions


def arguments(world, *extra, record=None):
    world.write()
    parser = argparse.ArgumentParser()
    parser.add_argument('--policy', type=Path)
    parser.add_argument('command')
    parser.add_argument('op', nargs='?')
    policy_decisions.add_arguments(parser)
    flags = ['route', 'review.audit', *world.args(), '--destinations', world.tmp / 'missing-destinations.json',
             '--decision-id', record or 'probe', *extra]
    if record:
        flags += ['--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt']
    return parser.parse_args(list(map(str, flags)))


def execute(world, monkeypatch, *extra, record=None):
    monkeypatch.setattr(directives, 'now', lambda: directives.parse_time(NOW, 'test'))
    return policy_decisions.execute(arguments(world, *extra, record=record), world.policy)


@pytest.mark.parametrize('projected', [80, 105.12])
@pytest.mark.parametrize('level', ['vendor', 'family', 'model'])
def test_explicit_independence_without_maker_requires_judgment(world, projected, level):
    world.codex(projected)
    out = world.route('fanout.explore', '--independence', level, '--proof-class', 'oracle')
    reasons = [r for r in out['routing']['judgment_reasons'] if r['code'] == 'independence_without_maker']
    assert reasons and not reasons[0].get('informational')
    assert out['routing']['judgment_required'] is True
    assert block(out)['substituted'] is (projected >= 95)


def test_call_that_becomes_a_replay_cannot_release_winners_state(world, monkeypatch):
    # Pause A after its early ID lookup but before evaluating its 80% quota.
    # B records the same request at 105%, so A's eventual append must replay B.
    loser_args = arguments(world, record='race')
    world.codex(80)
    loser_quota = copy.deepcopy(world.quota)
    winner = {}
    real_read = policy_decisions.read_source

    def interleave(source, command, **kwargs):
        if source == str(world.tmp / 'quota.json'):
            world.codex(105.12)
            winner['receipt'] = world.route('review.audit', '--destinations', world.tmp / 'missing-destinations.json', record='race')
            return loser_quota, real_read(source, command, **kwargs)[1]
        return real_read(source, command, **kwargs)

    monkeypatch.setattr(directives, 'now', lambda: directives.parse_time(NOW, 'test'))
    monkeypatch.setattr(policy_decisions, 'read_source', interleave)
    loser = policy_decisions.execute(loser_args, world.policy)
    assert loser['replayed'] is True
    assert stable(loser) == stable(winner['receipt']) | {'replayed': True}
    assert 'codex' in world.stored(), 'a replay must not persist the losing release'
    assert block(winner['receipt'])['state_written'] is False
    assert block(winner['receipt'])['state_commit'] == 'after_receipt'
    assert len(world.receipts.read_text().splitlines()) == 1
    world.codex(92)
    assert active(world.route('review.audit')) == {'codex': 'held_by_hysteresis'}


@pytest.mark.parametrize('effort', ['max', 'xhigh'])
def test_rejected_effort_does_not_persist_state(world, effort):
    op = next(o for o in world.policy['operating_points'] if o['id'] == 'review.audit')
    for candidate in [op['expands_to'], *op['candidates']]:
        candidate['effort'] = effort
    result, _ = world.cli('route', 'review.audit', *world.args(), '--decision-id', 'rejected')
    assert result.returncode == 1 and 'xhigh/max require --reason' in result.stderr
    assert not world.state.exists()


def test_receipt_permission_failure_does_not_persist_state(world, monkeypatch):
    world.receipts.parent.mkdir(mode=0o700)
    world.receipts.touch(mode=0o400)
    real_open = decision_receipts.os.open

    def reject_receipt(path, flags, *args, **kwargs):
        if Path(path) == world.receipts:
            raise PermissionError(13, 'Permission denied', str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(decision_receipts.os, 'open', reject_receipt)
    with pytest.raises(PermissionError, match='Permission denied'):
        execute(world, monkeypatch, record='rejected')
    assert world.receipts.read_bytes() == b''
    assert not world.state.exists()


def test_receipt_fsync_failure_does_not_persist_state(world, monkeypatch):
    real_fsync = decision_receipts.os.fsync

    def fail(fd):
        if world.receipts.exists() and decision_receipts.os.fstat(fd).st_ino == world.receipts.stat().st_ino:
            raise OSError('receipt fsync failed')
        return real_fsync(fd)

    monkeypatch.setattr(decision_receipts.os, 'fsync', fail)
    with pytest.raises(OSError, match='receipt fsync failed'):
        execute(world, monkeypatch, record='rejected')
    assert not world.state.exists()


def naive_run():
    collected = {'t3': {'state': 'ok', 'reason': None, 'subjects_checked': 0, 'observations': []}}
    return observations.source_run('run', '2026-10-11T00:00:00', '2026-10-11T00:00:00', collected, {})


def test_naive_observation_ledger_routes_with_missing_note(world):
    ledger = world.receipts.with_name('observations.jsonl')
    ledger.parent.mkdir(mode=0o700)
    ledger.write_text(json.dumps(naive_run()) + '\n')
    out = world.route('review.audit')
    assert out['routing']['calibration_notes']['observations'] == [
        {'kind': 'observations_missing', 'last_run_at': None, 'age_hours': None, 'sources': {}}]
    assert block(out)['substituted'] is True


def test_observe_reader_rejects_naive_ledger_row(world):
    ledger = world.receipts.with_name('observations.jsonl')
    ledger.parent.mkdir(mode=0o700)
    ledger.write_text(json.dumps(naive_run()) + '\n')
    rows, state, reason = observations.read_observations(ledger)
    assert rows == [] and state == 'unreadable' and 'timezone' in reason
    result, _ = world.cli('observe', '--receipts', world.receipts, '--destinations', world.tmp / 'missing.json')
    assert result.returncode == 1 and 'timezone' in result.stderr


@pytest.mark.parametrize('field', ['started_at', 'finished_at', 'observed_at', 'source_timestamps'])
def test_observation_writer_requires_aware_timestamps(world, field):
    at = '2026-10-11T00:00:00+00:00'
    naive = '2026-10-11T00:00:00'
    collected = {'t3': {'state': 'ok', 'reason': None, 'subjects_checked': 1, 'observations': []}}
    if field == 'source_timestamps':
        collected['t3']['observations'] = [observations.make_observation(
            't3', 'subject', None, 't3_run_status', {'status': 'completed'}, source_timestamps={'requested_at': naive})]
    if field == 'observed_at':
        row = observations.make_observation('t3', 'subject', None, 't3_run_status', {'status': 'completed'})
        with pytest.raises(ValueError, match='timezone'):
            observations.check_row(row | {'observed_at': naive, 'run_id': 'run'})
        return
    with pytest.raises(ValueError, match='timezone'):
        observations.append_run(world.tmp / 'observations.jsonl', 'run', naive if field == 'started_at' else at,
                                naive if field == 'finished_at' else at, collected)
    assert not (world.tmp / 'observations.jsonl').exists()


@pytest.mark.parametrize('level', [None, 'vendor'])
def test_destination_keeps_missing_maker_at_destination_level(world, level):
    cfg = config(world.tmp, entry(capacity(world.tmp, 'cap')))
    flags = ['--destinations', cfg, '--purpose', 'review', '--inputs', 'github']
    if level:
        flags += ['--independence', level]
    out = world.route('fanout.explore' if level else 'review.audit', *flags)
    code = 'independence_without_maker' if level else 'review_without_maker'
    reason = next(r for r in out['routing']['judgment_reasons'] if r['code'] == code)
    assert reason['applies_to'] == 'destination' and not reason.get('informational')
    assert out['routing']['judgment_required'] is True
    assert out['destination'] == 'worker' and out['target'] is None
    assert block(out)['substituted'] is True and 'codex' in world.stored()


@pytest.mark.parametrize('overridden', [False, True])
@pytest.mark.parametrize('destination', [False, True])
@pytest.mark.parametrize('simulation', [False, True])
def test_active_substitution_with_override_destination_notes_and_simulation(world, overridden, destination, simulation):
    flags = ['--purpose', 'review', '--inputs', 'github']
    if overridden:
        flags += ['--override', 'account=claude-work', '--reason', 'test account']
    if destination:
        flags += ['--destinations', config(world.tmp, entry(capacity(world.tmp, 'cap')))]
    ledger = world.receipts.with_name('observations.jsonl')
    ledger.parent.mkdir(mode=0o700)
    ledger.write_text('{invalid json\n')
    if simulation:
        flags += ['--now', NOW]
        result, out = world.cli('route', 'review.audit', *world.args(), '--decision-id', 'probe', *flags, raw=True)
        assert result.returncode == 0, result.stderr
    else:
        out = world.route('review.audit', *flags)
    assert block(out)['substituted'] is True
    assert out['routing']['routed']['account'] == 'claudeAgent'
    assert out['routing']['final']['account'] == ('claude-work' if overridden else 'claudeAgent')
    assert out['routing']['calibration_notes']['observations'][0]['kind'] == 'observations_missing'
    if destination or simulation:
        assert out['target'] is None and out['launch_ready'] is False
    else:
        assert out['target']['providerInstanceId'] == ('claude-work' if overridden else 'claudeAgent')
    assert world.state.exists() is (not simulation)
    if simulation:
        assert not world.state.with_name(world.state.name + '.lock').exists()


@pytest.mark.parametrize('overridden', [False, True])
def test_audit_matches_final_substituted_or_overridden_target(world, overridden):
    flags = []
    if overridden:
        flags += ['--override', 'account=claude-work', '--reason', 'test account']
    receipt = world.route('review.audit', *flags, record='audit')
    assert block(receipt)['substituted'] is True
    export = world.tmp / 'export.json'
    rows = [{'call_id': name, 'thread_id': 'pt', 'timestamp': '2026-10-11T00:25:00Z',
             'input': {'clientRequestId': 'audit', 'target': target}, 'output': None}
            for name, target in [('match', receipt['target']), ('wrong', receipt['target'] | {'model': 'other'})]]
    export.write_text(json.dumps({'schema_version': 1, 'delegations': rows}))
    result, out = world.cli('audit', '--export', export, '--receipts', world.receipts)
    assert result.returncode == 0, result.stderr
    assert out['request_match']['count'] == 1
    assert [r['request_match'] for r in out['delegations']] == [True, False]
    assert out['overrides'] == (1 if overridden else 0)


def test_quota_enabled_destination_relaunch(world):
    cfg = config(world.tmp, entry(capacity(world.tmp, 'cap')))
    flags = ['--destinations', cfg, '--purpose', 'review', '--inputs', 'github']
    first = world.route('review.audit', *flags, record='first')
    second = world.route('review.audit', *flags, '--relaunch-of', 'first', '--why', 'infra', '--reason', 'test relaunch', record='second')
    assert first['destination'] == second['destination'] == 'worker'
    assert second['relaunch'] == {'from': 'first', 'why': 'infra'}
    assert block(second)['substituted'] is True and second['target'] is None
    assert 'codex' in world.stored()


@pytest.mark.parametrize('flag', ['--receipts', '--directives', '--outcomes', '--observations'])
def test_quota_sidecar_cannot_be_another_ledger(world, flag):
    # Bound the real CLI: a missing guard would wait on its own quota lock.
    world.write()
    lock_path = world.state.with_name(world.state.name + '.lock')
    flags = ['route', 'review.audit', *world.args(), '--decision-id', 'probe', flag, lock_path]
    env = dict(os.environ, XDG_CONFIG_HOME=str(world.tmp / 'xdg'), PINNED_NOW=NOW)
    result = subprocess.run([sys.executable, str(REPO / 'tests/pinned_clock_cli.py'), *map(str, flags)],
                            capture_output=True, text=True, env=env, timeout=5)
    assert result.returncode == 1 and 'same file' in result.stderr
    assert not world.state.exists() and not lock_path.exists()
