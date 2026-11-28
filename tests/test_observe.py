"""`observe`: automatic, factual observations of recorded decisions. Facts only; a close or outcome label never changes.

Sources are real: a fixture T3 sqlite file, shell scripts as observe commands, and a temp git repository.
"""
import concurrent.futures
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_policy_decisions import REPO, record, run, setup  # noqa: F401  (setup is a fixture)
from test_policy_outcomes import COMPLETE, PREFIX, close, make_db
from test_route_destinations import config, entry, script

SECRET = 'SECRET-PLANTED-TEXT'
BASELINE = '76a334c'


def paths(setup):
    state = setup[3].parent
    return state / 'outcomes.jsonl', state / 'observations.jsonl'


def observe(setup, *extra, **kwargs):
    return run('observe', '--receipts', setup[3], '--observe-state', setup[3].with_name('observe-state.json'), *extra, **kwargs)


def ok(setup, *extra):
    result, row = observe(setup, *extra)
    assert result.returncode == 0, result.stderr
    return row


def rows(setup):
    return [json.loads(line) for line in paths(setup)[1].read_text().splitlines() if line.strip()]


def facts(setup, source=None, fact_type=None):
    return [r for r in rows(setup) if r['kind'] == 'observation' and (not source or r['source'] == source)
            and (not fact_type or r['fact_type'] == fact_type)]


def new_total(row):
    return sum(entry['new_observations'] for entry in row['sources'].values())


def decision_row(setup, decision_id, chosen=None):
    """Append a receipt that route would have written for a destination (or a copy of a model receipt)."""
    record(setup, decision_id)
    if chosen:
        lines = [json.loads(line) for line in setup[3].read_text().splitlines()]
        for line in lines:
            if line['decision_id'] == decision_id:
                line['routing'] = {'destination': {'chosen': chosen, 'considered': []}}
        setup[3].write_text(''.join(json.dumps(line) + '\n' for line in lines))


def t3_world(setup, tmp_path, status='completed', failure=None):
    _, receipt = record(setup, 'one', 'implement.standard', '--purpose', 'execution')
    db = make_db(tmp_path, receipt, [('a1', COMPLETE)])
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}runs set status=?", (status,))
    c.execute(f"update {PREFIX}runs set payload_json=json_set(payload_json,'$.modelSelection.model',?)", (receipt['target']['model'],))
    if failure:
        payload = {'startedAt': '2026-10-08T00:00:30Z', 'failure': {'class': 'error', 'message': failure}}
        c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  ('item:terminal-failure%3A1', 't', 'child', '2026-10-08T00:00:30Z', 'failed', 'error', json.dumps(payload)))
    c.commit()
    c.close()
    return db


# ---- T3 ---------------------------------------------------------------------------------------------------------------

def test_t3_facts_carry_ids_timestamps_requested_config_and_labels(setup, tmp_path):
    db = t3_world(setup, tmp_path, failure=f'{SECRET} rate limit reached')
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    before = db.read_bytes()
    row = ok(setup, '--db', db)
    assert db.read_bytes() == before
    assert row['sources']['t3'] | {'new_observations': 0} == {'state': 'ok', 'reason': None, 'subjects_checked': 2, 'new_observations': 0}
    assert set(row['sources']) == {'git', 't3'} and row['sources']['git']['subjects_checked'] == 0
    assert row['labels']['completed'] == 'completed ≠ accepted' and row['labels']['consumed'] == 'consumed ≠ correct'
    assert row['labels']['tokens'] == 'tokens ≠ subscription cost'
    by_type = {}
    for r in facts(setup, 't3'):
        by_type.setdefault(r['fact_type'], []).append(r)
        assert r['decision_id'] == 'one' and r['source_ids'] and r['observation_id'] and r['run_id'] == row['run_id']
    assert set(by_type) == {'t3_delegate_call', 't3_run_status', 't3_requested_config', 't3_attempt_status', 't3_terminal_failure', 't3_turn_usage'}
    config_row = by_type['t3_requested_config'][0]
    assert config_row['fact']['requested'] == {'provider_instance': 'claude-one', 'model': receipt['target']['model'], 'effort': 'medium',
                                               'options': {'effort': 'medium'}, 'options_dropped': 0}
    assert config_row['fact']['served_model'] == 'unattested'
    assert by_type['t3_run_status'][0]['fact']['status'] == 'completed'
    assert by_type['t3_run_status'][0]['source_timestamps']['requested_at'] == '2026-10-08T00:00:00+00:00'
    assert by_type['t3_run_status'][0]['unknown']['completed_at'] == 'not_recorded'  # unknown stays null with a reason
    assert by_type['t3_terminal_failure'][0]['fact']['failure_class'] == 'rate_limit'
    usage = by_type['t3_turn_usage']
    assert len(usage) == 1 and usage[0]['fact']['input_tokens'] == 100 and 'not dollars' in usage[0]['fact']['label']
    assert usage[0]['fact']['sums'] == 'never computed; scopes can overlap' and 'sum' not in usage[0]['fact']
    text = paths(setup)[1].read_text()
    for planted in (SECRET, 'SECRET PROMPT', '"SECRET":"prompt"', 'tokenUsage'):
        assert planted not in text


def test_run_details_ignores_malformed_optional_json_timestamps(setup, tmp_path):
    db_path = t3_world(setup, tmp_path)
    c = sqlite3.connect(db_path)
    c.execute(f"update {PREFIX}runs set payload_json='{{' where run_id='child'")
    c.commit()
    module = lib_module('observe')

    details = module.run_details(c, 'child')

    c.close()
    assert details[0][0] == 'completed'


def test_incremental_high_water_refreshes_known_rows_and_reads_new_rows(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    first = ok(setup, '--db', db)
    assert first['incremental']['initial_window'] == 'last 50000 turn_items rowids'
    first_high = first['incremental']['rowid_through']
    second = ok(setup, '--db', db)
    assert second['incremental']['high_water_before'] == first_high
    assert second['incremental']['rows_scanned'] == 0
    assert second['incremental']['rows_refreshed'] == 1
    c = sqlite3.connect(db)
    call = json.loads(c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0])
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
              ('call-later', 'parent-thread', 'parent', '2026-10-08T00:02:00Z', 'completed', 'dynamic_tool', json.dumps(call)))
    c.commit()
    new_high = c.execute(f'select max(rowid) from {PREFIX}turn_items').fetchone()[0]
    c.close()
    third = ok(setup, '--db', db)
    assert third['incremental']['rowid_through'] == new_high > first_high
    assert third['incremental']['rows_scanned'] == 1


def test_incremental_budget_exit_resumes_from_saved_high_water(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  [(f'filler-{i}', 't', 'parent', '2026-10-08T00:00:00Z', 'completed', 'other', '{"toolName":"other"}')
                   for i in range(50_600)])
    c.commit()
    c.close()
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    clock = [0.0]
    real_read = module.read_t3_rowid_batch

    def read_one_batch_then_expire(*args, **kwargs):
        result = real_read(*args, **kwargs)
        clock[0] = 2.0
        return result

    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'read_t3_rowid_batch', read_one_batch_then_expire)
    stopped = module.incremental_t3(db, {'one'}, targets, state, 1)
    monkeypatch.undo()
    assert stopped['state'] == 'partial' and stopped['reason'] == 'time_budget_exhausted'
    assert stopped['incremental']['rowid_through'] == stopped['incremental']['rowid_from'] + 500
    assert stopped['incremental']['rowid_from'] > 0
    assert stopped['incremental']['rows_scanned'] == 500
    module.save_state(state, stopped['_state'])
    resumed = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert resumed['state'] == 'ok'
    assert resumed['incremental']['high_water_before'] == stopped['_state']['rowid']
    assert resumed['incremental']['rowid_through'] == resumed['incremental']['snapshot_high_water']


def test_row_retry_after_one_failed_read_clears_quarantine(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    real_read = module.read_t3_rowid_batch
    failed_batch, failed_row = [False], [False]

    def fail_one_single_row_read(*args, **kwargs):
        if args[3] == 500 and not failed_batch[0]:
            failed_batch[0] = True
            raise ValueError('temporary row payload error')
        if args[3] == 1 and kwargs.get('rowids') is None and not failed_row[0]:
            failed_row[0] = True
            raise ValueError('temporary row payload error')
        return real_read(*args, **kwargs)

    monkeypatch.setattr(module, 'read_t3_rowid_batch', fail_one_single_row_read)
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert first['incremental']['quarantined_rows'] == 1
    module.save_state(state, first['_state'])
    second = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    monkeypatch.undo()
    assert second['incremental']['quarantined_rows'] == 0
    assert any(row['fact_type'] == 't3_delegate_call' for row in second['observations'])


def test_quarantined_slow_read_retries_with_a_larger_single_row_cap(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    real_read = module.read_t3_rowid_batch
    monkeypatch.setattr(module, 'read_t3_rowid_batch',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(module.ReadBatchTooSlow('slow')))
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert first['incremental']['quarantined_rows'] == 1
    module.save_state(state, first['_state'])
    second = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert second['incremental']['quarantined_rows'] == 1
    module.save_state(state, second['_state'])
    monkeypatch.undo()
    read_caps = []

    def record_retry_cap(*args, **kwargs):
        read_caps.append(kwargs.get('max_read_seconds'))
        return real_read(*args, **kwargs)

    monkeypatch.setattr(module, 'read_t3_rowid_batch', record_retry_cap)
    retry = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)

    assert read_caps and read_caps[0] > module.OBSERVE_READ_SECONDS
    assert retry['incremental']['quarantined_rows'] == 0
    assert any(row['fact_type'] == 't3_delegate_call' for row in retry['observations'])


def test_operational_sqlite_error_never_quarantines_or_skips_rows(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    monkeypatch.setattr(module, 'read_t3_rowid_batch',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error')))
    for _ in range(3):
        failed = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
        assert failed['state'] == 'error'
        call_db, _, _ = module.open_call_index(state)
        assert call_db.execute('select count(*) from quarantined_rows').fetchone()[0] == 0
        call_db.close()


def test_incremental_error_keeps_facts_from_completed_batches(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    payload = c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0]
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
              ('call-later', 'parent-thread', 'parent', '2026-10-08T00:01:00Z', 'completed', 'dynamic_tool', payload))
    c.commit()
    c.close()
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    real_read = module.read_t3_rowid_batch
    read_count = 0

    def fail_after_one_batch(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            raise sqlite3.OperationalError('disk I/O error')
        return real_read(*args, **kwargs)

    monkeypatch.setattr(module, 'OBSERVE_BATCH_ROWS', 1)
    monkeypatch.setattr(module, 'read_t3_rowid_batch', fail_after_one_batch)
    failed = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)

    assert failed['state'] == 'error'
    assert any(row['fact_type'] == 't3_delegate_call' for row in failed['observations'])
    assert failed['_state']['rowid'] == 1


def test_budget_limited_t3_batch_keeps_completed_call_observation(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    real_details = module.run_details
    monkeypatch.setattr(module, 'run_details', lambda *_args, **_kwargs: (_ for _ in ()).throw(module.BudgetExhausted()))
    _, calls, _, _, _ = module.read_t3_rowid_batch(db, 0, 10, 500, scope_ids={'one'})
    partial = module.collect_t3(db, {'one'}, {}, calls_override=calls, deadline=time.monotonic() + 10)
    monkeypatch.setattr(module, 'run_details', real_details)
    assert partial['state'] == 'partial'
    assert any(row['fact_type'] == 't3_delegate_call' for row in partial['observations'])
    assert not any(row.get('unknown', {}).get('usage') == 'time_budget_exhausted' for row in partial['observations'])


def test_budget_exit_in_single_row_fallback_does_not_advance_cursor(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    real_read = module.read_t3_rowid_batch
    failed_batch = False

    def force_row_fallback(*args, **kwargs):
        nonlocal failed_batch
        if args[3] == 500 and not failed_batch:
            failed_batch = True
            raise ValueError('fixture batch error')
        return real_read(*args, **kwargs)

    monkeypatch.setattr(module, 'read_t3_rowid_batch', force_row_fallback)
    monkeypatch.setattr(module, 'collect_t3', lambda *_args, **_kwargs: (_ for _ in ()).throw(module.BudgetExhausted()))
    stopped = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)

    assert stopped['state'] == 'partial'
    assert stopped['reason'] == 'time_budget_exhausted'
    assert stopped['_state']['rowid'] == 0


def test_observe_lock_budget_returns_error_without_traceback(setup, tmp_path, monkeypatch):
    from types import SimpleNamespace
    module = lib_module('observe')
    monkeypatch.setattr(module, 'observe_state_lock', lambda *_args, **_kwargs: (_ for _ in ()).throw(module.BudgetExhausted()))
    with pytest.raises(ValueError, match='observe_state_lock_timeout'):
        module.execute(SimpleNamespace(observe_time_budget_seconds=1, observe_state=tmp_path / 'state' / 'observe.json'))


def test_observe_deadline_includes_cli_startup(setup, tmp_path, monkeypatch):
    from types import SimpleNamespace
    module = lib_module('observe')
    deadlines = []
    monkeypatch.setattr(module, 'observe_state_lock', lambda _path, deadline: deadlines.append(deadline) or 123)
    monkeypatch.setattr(module, 'release_observe_state_lock', lambda _fd: None)
    monkeypatch.setattr(module, '_execute_locked', lambda _args: module.result('ok'))
    monkeypatch.setattr(module.time, 'monotonic', lambda: 110.0)
    args = SimpleNamespace(observe_time_budget_seconds=60, observe_state=tmp_path / 'observe.json',
                           _observe_process_started_monotonic=100.0)
    module.execute(args)
    assert deadlines == [160.0]


def test_thread_fact_cursor_resumes_at_the_first_unfinished_call(setup, tmp_path, monkeypatch):
    db_path = t3_world(setup, tmp_path)
    c = sqlite3.connect(db_path)
    call = json.loads(c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0])
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
              ('call-b', 'parent-thread', 'parent', '2026-10-08T00:01:00Z', 'completed', 'dynamic_tool', json.dumps(call)))
    rowids = [row[0] for row in c.execute(f"select rowid from {PREFIX}turn_items where turn_item_id in ('call','call-b') order by rowid")]
    c.close()
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    call_db, _, _ = module.open_call_index(state)
    module.remember_calls(call_db, [{'_source_rowid': rowids[0], 'call_id': 'call', 'child_run_id': 'child', 'decision_id': 'one'},
                                    {'_source_rowid': rowids[1], 'call_id': 'call-b', 'child_run_id': 'child', 'decision_id': 'one'}])
    seen = []

    def facts(_db, _run, _decision, _ids, _owner=None, **_kwargs):
        seen.append(len(seen) + 1)
        if len(seen) == 2:
            raise module.BudgetExhausted([{'fact': 'partial-second'}])
        return [{'fact': 'first'}]

    monkeypatch.setattr(module, 'child_thread_facts', facts)
    first, _, cursor, done = module.collect_indexed_thread_facts(db_path, call_db, {'one'}, {}, time.monotonic() + 10)
    assert first == [{'fact': 'first'}, {'fact': 'partial-second'}] and cursor == rowids[0] and not done
    seen.clear()
    resumed, _, cursor, done = module.collect_indexed_thread_facts(db_path, call_db, {'one'}, {}, time.monotonic() + 10,
                                                                   after_rowid=cursor)
    call_db.close()
    assert resumed == [{'fact': 'first'}] and cursor == rowids[-1] and done


def test_child_followup_fact_survives_budget_between_thread_snapshot_pages(setup, tmp_path, monkeypatch):
    module = lib_module('observe')
    db_path = tmp_path / 'thread-budget.sqlite'
    db = sqlite3.connect(db_path)
    db.execute(f'create table {PREFIX}runs(run_id text,thread_id text,ordinal integer)')
    db.execute(f'create table {PREFIX}threads(thread_id text,payload_json text,updated_at text)')
    db.execute(f'create table {PREFIX}turn_items(thread_id text,type text,updated_at text,ordinal integer)')
    db.execute(f"insert into {PREFIX}runs values ('child','child-thread',1)")
    db.execute(f"insert into {PREFIX}threads values ('child-thread','{{\"pullRequests\":[]}}',NULL)")
    db.commit()

    class Clock:
        readings = iter((0.0, 0.0, 1.0))

        @classmethod
        def monotonic(cls):
            return next(cls.readings, 1.0)

    monkeypatch.setattr(module, 'time', Clock)
    with pytest.raises(module.BudgetExhausted) as error:
        module.child_thread_facts(db, 'child', 'one', {'run_id': 'child'}, include_thread_snapshot=True,
                                  deadline=1.0)
    db.close()

    assert [row['fact_type'] for row in error.value.observations] == ['t3_child_followups']


def test_incremental_facts_match_the_existing_collector_for_the_same_fixture_rows(setup, tmp_path):
    db = t3_world(setup, tmp_path, failure=f'{SECRET} rate limit reached')
    module = lib_module('observe')
    from delegation_audit import read_db
    calls, _ = read_db(db, None, None)
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    baseline = module.collect_t3(db, {'one'}, targets, calls_override=calls)
    incremental = module.incremental_t3(db, {'one'}, targets, tmp_path / 'observe.json', time.monotonic() + 30)
    assert {r['observation_id'] for r in incremental['observations']} == {r['observation_id'] for r in baseline['observations']}


def test_refresh_defers_thread_snapshot_while_main_cursor_is_catching_up(setup, tmp_path, monkeypatch):
    record(setup, 'two', 'implement.standard', '--purpose', 'execution')
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    receipts = [json.loads(line) for line in setup[3].read_text().splitlines()]
    targets = {row['decision_id']: row['target'] for row in receipts}
    c = sqlite3.connect(db)
    c.execute(f'alter table {PREFIX}runs add column thread_id text')
    c.execute(f"update {PREFIX}runs set thread_id='child-thread' where run_id='child'")
    c.execute(f'create index {PREFIX}runs_thread_idx on {PREFIX}runs(thread_id)')
    c.execute(f'create table {PREFIX}threads(thread_id text primary key, payload_json text, updated_at text)')
    c.execute(f"insert into {PREFIX}threads values ('child-thread', '{{\"pullRequests\":[]}}', '2026-10-08T00:00:00Z')")
    c.commit()
    c.close()
    first = module.incremental_t3(db, {'one', 'two'}, targets, state, time.monotonic() + 10)
    module.save_state(state, first['_state'])
    c = sqlite3.connect(db)
    call = json.loads(c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0])
    call['input']['clientRequestId'] = 'two'
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
              ('call-two', 'parent-thread', 'parent', '2026-10-08T00:02:00Z', 'completed', 'dynamic_tool', json.dumps(call)))
    c.commit()
    c.close()
    seen = []
    collect_t3 = module.collect_t3

    def collect(*args, **kwargs):
        seen.append(kwargs['include_thread_snapshot'])
        return collect_t3(*args, **kwargs)

    monkeypatch.setattr(module, 'collect_t3', collect)
    second = module.incremental_t3(db, {'one', 'two'}, targets, state, time.monotonic() + 10)
    assert second['incremental']['rowid_through'] == second['incremental']['snapshot_high_water']
    assert seen[0] is False and True in seen
    snapshots = [row for row in second['observations'] if row['fact_type'] == 't3_child_pull_requests']
    assert any(row['decision_id'] == 'two' and row['fact']['shared_with_decisions'] == 2 for row in snapshots)
    assert not any(row['decision_id'] == 'one' and row['fact']['shared_with_decisions'] == 1 for row in snapshots)


def test_incremental_resets_when_ledger_or_rowid_anchors_change(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30,
                                  {'path': '/ledger-a', 'device': 1, 'inode': 2, 'size': 3,
                                   'mtime_ns': 4, 'tail_sha256': 'a', 'last_run_id': 'run-a'})
    module.save_state(state, first['_state'])
    changed_ledger = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30,
                                           {'path': '/ledger-b', 'device': 1, 'inode': 2, 'size': 3,
                                            'mtime_ns': 4, 'tail_sha256': 'a', 'last_run_id': 'run-a'})
    assert changed_ledger['incremental']['initial_window'] == 'last 50000 turn_items rowids'
    assert changed_ledger['_state']['call_index_id'] != first['_state']['call_index_id']
    module.save_state(state, changed_ledger['_state'])
    changed_ledger['_state']['anchors'] = {'1': 'rowid-was-reused'}
    module.save_state(state, changed_ledger['_state'])
    changed_anchor = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert changed_anchor['incremental']['initial_window'] == 'last 50000 turn_items rowids'


def test_cursor_anchor_detects_rowid_reuse_but_drops_deleted_anchor(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    module.save_state(state, first['_state'])
    c = sqlite3.connect(db)
    original = c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0]
    c.execute(f"delete from {PREFIX}turn_items where turn_item_id='call'")
    c.execute(f'insert into {PREFIX}turn_items(rowid,turn_item_id,thread_id,run_id,updated_at,status,type,payload_json) '
              'values (?,?,?,?,?,?,?,?)', (1, 'call-reused', 'parent-thread', 'parent', '2026-10-08T00:03:00Z',
                                           'completed', 'dynamic_tool', json.dumps(original)))
    c.commit()
    c.close()
    reused = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert reused['incremental']['initial_window'] == 'last 50000 turn_items rowids'
    assert reused['incremental']['reset_reason'] == 'anchor_rowid_reused'

    state = tmp_path / 'observe-deleted-anchor.json'
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    module.save_state(state, first['_state'])
    c = sqlite3.connect(db)
    c.execute(f"delete from {PREFIX}turn_items where turn_item_id='call-reused'")
    c.execute(f'insert into {PREFIX}turn_items(rowid,turn_item_id,thread_id,run_id,updated_at,status,type,payload_json) '
              'values (?,?,?,?,?,?,?,?)', (2, 'later', 'parent-thread', 'parent', '2026-10-08T00:04:00Z',
                                           'completed', 'other', '{}'))
    c.commit()
    c.close()
    deleted = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert deleted['incremental']['initial_window'] is None


def test_missing_database_run_appends_ledger_without_resetting_t3_cursor(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    first = ok(setup, '--db', db)
    first_high = first['incremental']['rowid_through']
    missing = ok(setup, '--db', tmp_path / 'missing.sqlite')
    assert missing['sources']['t3']['reason'] == 'database_missing'
    resumed = ok(setup, '--db', db)
    assert resumed['incremental']['initial_window'] is None
    assert resumed['incremental']['high_water_before'] == first_high


def test_changed_ledger_is_not_rebound_when_database_is_missing(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    ok(setup, '--db', db)
    state_path = setup[3].with_name('observe-state.json')
    state_before = json.loads(state_path.read_text())
    ledger = paths(setup)[1]
    ledger_rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    if ledger_rows[0]['kind'] == 'source_run':
        ledger_rows[0]['started_at'] = '2000-01-01T00:00:00+00:00'
    else:
        ledger_rows[0]['observed_at'] = '2000-01-01T00:00:00+00:00'
    ledger.write_text(''.join(json.dumps(row) + '\n' for row in ledger_rows))

    missing = ok(setup, '--db', tmp_path / 'missing.sqlite')

    assert missing['sources']['t3']['reason'] == 'database_missing'
    after_missing = json.loads(state_path.read_text())
    module = lib_module('observe')
    current_marker = module.read_last_ledger_line(ledger)
    assert not module.ledger_prefix_matches(after_missing['ledger'], current_marker)
    assert after_missing['rowid'] == state_before['rowid']

    healthy = ok(setup, '--db', db)
    assert healthy['incremental']['initial_window'] == 'last 50000 turn_items rowids'
    assert healthy['incremental']['reset_reason'] == 'ledger_prefix_changed'


def test_failed_unsettled_refresh_does_not_commit_settled_status(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}turn_items set status='running' where turn_item_id='call'")
    c.execute(f"update {PREFIX}runs set status='running' where run_id='child'")
    c.execute(f"update {PREFIX}run_attempts set status='running' where run_id='child'")
    c.commit()
    c.close()
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    module.save_state(state, first['_state'])
    call_db, _, _ = module.open_call_index(state)
    assert call_db.execute('select settled from known_calls').fetchone()[0] == 0
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}turn_items set status='completed' where turn_item_id='call'")
    c.execute(f"update {PREFIX}runs set status='completed' where run_id='child'")
    c.execute(f"update {PREFIX}run_attempts set status='completed' where run_id='child'")
    c.commit()
    c.close()
    monkeypatch.setattr(module, 'collect_t3', lambda *_args, **_kwargs: (_ for _ in ()).throw(module.BudgetExhausted()))

    failed = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)

    assert failed['incremental']['rows_refreshed'] == 0
    assert call_db.execute('select settled from known_calls').fetchone()[0] == 0
    call_db.close()


def test_destination_and_git_cursors_save_when_t3_has_no_state(setup, tmp_path, monkeypatch):
    from types import SimpleNamespace
    module = lib_module('observe')
    state = tmp_path / 'state' / 'observe.json'
    state.parent.mkdir()
    state.parent.chmod(0o700)
    state.write_text(json.dumps({'git_cursor': 'old-git', 'destination_cursors': {'destination:foo': 'old-request'}}))
    state.chmod(0o600)
    monkeypatch.setattr(module.store, 'decision_sources', lambda *_args: {
        't3': {'one'}, 'git': {'one'}, 'destination:foo': {'request'}})
    monkeypatch.setattr(module.destination_store, 'read_destinations', lambda _path: (
        [{'name': 'foo', 'observe_command': ['/unused'], 'timeout_seconds': 1}], {}))
    monkeypatch.setattr(module, 'collect_destination', lambda *_args, **_kwargs:
                        module.result('partial', 'time_budget_exhausted', _cursor='next-request', _batch_size=5))
    monkeypatch.setattr(module, 'collect_git', lambda *_args, **_kwargs:
                        module.result('partial', 'time_budget_exhausted', _cursor='next-git'))
    receipts_path = setup[3]
    outcomes_path = paths(setup)[0]
    args = SimpleNamespace(git_window_days=14, since=None, receipts=receipts_path, outcomes=outcomes_path,
                           destinations=tmp_path / 'destinations.json', observations=tmp_path / 'ledger.jsonl',
                           observe_state=state, observe_time_budget_seconds=60,
                           db=tmp_path / 'missing.sqlite')
    module.execute(args)
    saved = json.loads(state.read_text())
    assert saved['git_cursor'] == 'next-git'
    assert saved['destination_cursors']['destination:foo'] == {'after': 'next-request', 'batch_size': 5}


def test_ledger_prefix_validation_accepts_appends_and_rejects_edits(tmp_path):
    module = lib_module('observe')
    ledger = tmp_path / 'observations.jsonl'
    ledger.write_bytes(b'{"run_id":"one"}\n')
    previous = module.read_last_ledger_line(ledger)
    with ledger.open('ab') as stream:
        stream.write(b'{"run_id":"two"}\n')
    appended = module.read_last_ledger_line(ledger, previous_marker=previous)
    assert module.ledger_prefix_matches(previous, appended)
    content = ledger.read_bytes().replace(b'one', b'bad', 1)
    ledger.write_bytes(content)
    edited = module.read_last_ledger_line(ledger, previous_marker=previous)
    assert not module.ledger_prefix_matches(previous, edited)


def test_empty_ledger_prefix_survives_append_but_missing_ledger_does_not(tmp_path):
    module = lib_module('observe')
    ledger = tmp_path / 'observations.jsonl'
    ledger.write_text('{"kind":"source_run"}\n')
    empty_prefix = module.empty_ledger_marker(ledger)
    current = module.read_last_ledger_line(ledger, previous_marker=empty_prefix)
    assert module.ledger_prefix_matches(empty_prefix, current)
    assert not module.ledger_prefix_matches(None, current)
    ledger.unlink()
    assert not module.ledger_prefix_matches(empty_prefix, module.read_last_ledger_line(ledger))


def test_refresh_deadline_leaves_time_to_scan_new_rows(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    c = sqlite3.connect(db)
    call = json.loads(c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0])
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  [(f'call-{i}', 'parent-thread', 'parent', '2026-10-08T00:01:00Z', 'completed', 'dynamic_tool', json.dumps(call))
                   for i in range(499)])
    c.commit()
    c.close()
    real_usage = module.read_child_usage_for_call
    monkeypatch.setattr(module, 'read_child_usage_for_call', lambda *args, **kwargs: real_usage(*args, **kwargs))
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    module.save_state(state, first['_state'])
    c = sqlite3.connect(db)
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  [(f'new-call-{i}', 'parent-thread', 'parent', '2026-10-08T00:02:00Z', 'completed', 'dynamic_tool', json.dumps(call))
                   for i in range(10)])
    c.commit()
    c.close()

    def slow_usage(*args, **kwargs):
        time.sleep(0.05)
        return real_usage(*args, **kwargs)

    monkeypatch.setattr(module, 'OBSERVE_REFRESH_ROWS', 2)
    monkeypatch.setattr(module, 'read_child_usage_for_call', slow_usage)
    second = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 5)
    assert second['incremental']['rows_scanned'] == 10
    assert second['incremental']['refresh_cursor'] > 0
    assert second['reason'] is None  # settled-call rotation may remain, without masking the source as partial
    assert second['incremental']['budget_exhausted'] is False


def test_settled_refresh_backlog_does_not_mark_source_partial(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    c = sqlite3.connect(db)
    payload = c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0]
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  [(f'call-{i}', 'parent-thread', 'parent', '2026-10-08T00:01:00Z', 'completed', 'dynamic_tool', payload)
                   for i in range(5)])
    c.commit()
    c.close()
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 10)
    module.save_state(state, first['_state'])

    monkeypatch.setattr(module, 'OBSERVE_REFRESH_ROWS', 6)
    original_collect = module.collect_t3

    class Clock:
        current = 100.0

        @classmethod
        def monotonic(cls):
            return cls.current

    def slow_collect(*args, **kwargs):
        result = original_collect(*args, **kwargs)
        Clock.current += 0.12
        return result

    monkeypatch.setattr(module, 'time', Clock)
    monkeypatch.setattr(lib_module('delegation_audit'), 'time', Clock)
    monkeypatch.setattr(module, 'collect_t3', slow_collect)
    second = module.incremental_t3(db, {'one'}, targets, state, Clock.current + 1.0)
    assert second['reason'] != 'refresh_pending'
    assert second['state'] == 'ok'
    assert second['incremental']['refresh_cursor'] != 0


def test_refresh_reads_unsettled_calls_before_settled_round_robin(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    c = sqlite3.connect(db)
    payload = c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0]
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
              ('call-later', 'parent-thread', 'parent', '2026-10-08T00:02:00Z', 'completed', 'dynamic_tool', payload))
    c.commit()
    c.close()
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    module.save_state(state, first['_state'])
    call_db, _, _ = module.open_call_index(state)
    earlier, later = [row[0] for row in call_db.execute('select source_rowid from known_calls order by source_rowid')]
    call_db.execute('update known_calls set settled=0 where source_rowid=?', (later,))
    call_db.commit()
    call_db.close()
    read_order = []
    real_read = module.read_t3_rowid_batch

    def record_refresh_order(*args, **kwargs):
        rowids = kwargs.get('rowids') or (args[4] if len(args) > 4 else None)
        if rowids:
            read_order.extend(rowids)
        return real_read(*args, **kwargs)

    monkeypatch.setattr(module, 'read_t3_rowid_batch', record_refresh_order)
    second = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert second['incremental']['rows_scanned'] == 0
    assert read_order[:2] == [later, earlier]


def test_refreshed_running_calls_do_not_make_source_partial(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    targets = {'one': json.loads(setup[3].read_text().splitlines()[0])['target']}
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}turn_items set status='running' where turn_item_id='call'")
    c.execute(f"update {PREFIX}runs set status='running' where run_id='child'")
    c.execute(f"update {PREFIX}run_attempts set status='running' where run_id='child'")
    c.commit()
    c.close()
    first = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)
    assert first['state'] == 'ok'
    module.save_state(state, first['_state'])

    refreshed = module.incremental_t3(db, {'one'}, targets, state, time.monotonic() + 30)

    assert refreshed['state'] == 'ok'
    assert refreshed['reason'] is None
    assert refreshed['incremental']['rows_refreshed'] == 1
    call_db, _, _ = module.open_call_index(state)
    assert call_db.execute('select settled from known_calls').fetchone()[0] == 0
    call_db.close()


def test_incremental_preserves_dynamic_tool_filter_case_insensitively(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)', [
        ('tool-match', 't', 'parent', '2026-10-08T00:00:00Z', 'completed', 'dynamic_tool', '{"toolName":"Dynamic_Delegate_Task"}'),
        ('wrong-type', 't', 'parent', '2026-10-08T00:00:01Z', 'completed', 'other', '{"toolName":"delegate_task"}'),
    ])
    c.commit()
    rowids = [row[0] for row in c.execute(f"select rowid from {PREFIX}turn_items where turn_item_id in ('tool-match','wrong-type') order by rowid")]
    c.close()
    module = lib_module('observe')
    _, calls, _, matched, _ = module.read_t3_rowid_batch(db, 0, 0, 500, rowids=rowids, scope_ids=set())
    assert [call['call_id'] for call in calls] == ['tool-match']
    assert len(matched) == 1


def test_incremental_refuses_missing_followup_index(setup, tmp_path):
    db = child_thread_world(setup, tmp_path, [])
    c = sqlite3.connect(db)
    c.execute(f'drop index {PREFIX}turn_items_thread_id_idx')
    c.commit()
    c.close()
    row = ok(setup, '--db', db)
    assert row['sources']['t3']['state'] == 'unavailable'
    assert row['sources']['t3']['reason'] == 'no_indexed_turn_items_thread_id_type_lookup'


def test_incremental_state_refuses_loose_files_and_symlinks(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    state.write_text('{}')
    state.chmod(0o644)
    loose = module.incremental_t3(db, {'one'}, {}, state, time.monotonic() + 10)
    assert loose['state'] == 'error' and 'not_private' in loose['reason']
    state.unlink()
    target = tmp_path / 'target.json'
    module.save_state(target, {})
    state.symlink_to(target)
    linked = module.incremental_t3(db, {'one'}, {}, state, time.monotonic() + 10)
    assert linked['state'] == 'error' and 'symlink' in linked['reason']


def test_destination_time_budget_returns_a_resume_cursor(setup, tmp_path, monkeypatch):
    module = lib_module('observe')
    clock = [0.0]
    entry = {'name': 'destination', 'observe_command': ['/unused'], 'timeout_seconds': 1}

    def answer(entry, ids, deadline):
        clock[0] = 2.0
        return {request_id: {'found': False} for request_id in ids}, None

    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'ask_destination', answer)
    stopped = module.collect_destination(entry, {f'id-{i:02d}' for i in range(30)}, 1)
    assert stopped['state'] == 'partial' and stopped['_cursor'] == 'id-24'
    resumed = module.collect_destination(entry, {f'id-{i:02d}' for i in range(30)}, time.monotonic() + 10,
                                         stopped['_cursor'])
    monkeypatch.undo()
    assert resumed['state'] == 'ok' and resumed['subjects_checked'] == 5


def test_bad_destination_record_restarts_status_refresh_from_first_id(tmp_path, monkeypatch):
    module = lib_module('observe')
    entry = {'name': 'destination', 'observe_command': ['/unused'], 'timeout_seconds': 1}
    values = {'a': {'found': True, 'status': 'queued'}, 'b': {'found': 'invalid'}}
    monkeypatch.setattr(module, 'ask_destination', lambda _entry, ids, _deadline: ({key: values[key] for key in ids}, None))
    first = module.collect_destination(entry, {'a', 'b'}, time.monotonic() + 10)
    assert first['state'] == 'error' and first['_cursor'] is None
    values['a'] = {'found': True, 'status': 'finished'}
    second = module.collect_destination(entry, {'a', 'b'}, time.monotonic() + 10, first['_cursor'])
    assignment = next(row for row in second['observations'] if row['subject_id'] == 'a')
    assert assignment['fact']['status'] == 'finished'


def test_destination_budget_timeout_retries_with_smaller_batches(tmp_path, monkeypatch):
    module = lib_module('observe')
    entry = {'name': 'destination', 'observe_command': ['/unused'], 'timeout_seconds': 1}
    timed_out = []

    def first_batch_times_out(_entry, ids, _deadline):
        timed_out.append(len(ids))
        return None, 'time_budget_exhausted'

    monkeypatch.setattr(module, 'ask_destination', first_batch_times_out)
    stopped = module.collect_destination(entry, {f'id-{i:02d}' for i in range(30)}, time.monotonic() + 10)
    assert stopped['_cursor'] is None and stopped['_batch_size'] == 12
    assert timed_out == [25]
    monkeypatch.setattr(module, 'ask_destination', lambda _entry, ids, _deadline:
                        ({key: {'found': False} for key in ids}, None))
    resumed = module.collect_destination(entry, {f'id-{i:02d}' for i in range(30)}, time.monotonic() + 10,
                                         stopped['_cursor'], stopped['_batch_size'])
    assert resumed['state'] == 'ok' and resumed['subjects_checked'] == 30


def test_git_time_budget_returns_a_resume_cursor(setup, repo_world, monkeypatch):
    module = lib_module('observe')
    record(setup, 'two')
    close_on(setup, repo_world['repo'], repo_world['sha'], close_id='c2', decision='two')
    from outcomes import read_outcomes
    outcome_rows = read_outcomes(paths(setup)[0])
    clock = [0.0]
    scanned = []

    def scan(close_row, ref, window_days, now, deadline):
        scanned.append(close_row['close_id'])
        clock[0] = 2.0
        return []

    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'scan_commit', scan)
    stopped = module.collect_git(outcome_rows, {'one', 'two'}, 14, NOW, 1)
    assert stopped['state'] == 'partial' and stopped['_cursor']
    scanned.clear()
    resumed = module.collect_git(outcome_rows, {'one', 'two'}, 14, NOW, time.monotonic() + 10, stopped['_cursor'])
    monkeypatch.undo()
    assert resumed['state'] == 'ok' and scanned == ['c2']


def test_incremental_query_plan_uses_rowid_primary_key(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    module = lib_module('observe')
    plan = c.execute('EXPLAIN QUERY PLAN ' + module.t3_batch_sql('i.rowid>? AND i.rowid<=?'), (0, 1, 500)).fetchall()
    assert any('INTEGER PRIMARY KEY (rowid>? AND rowid<?)' in row[-1] for row in plan)
    assert any('SEARCH p USING' in row[-1] for row in plan)
    refresh = c.execute('EXPLAIN QUERY PLAN ' + module.t3_batch_sql('i.rowid IN (?)'), (1, 500)).fetchall()
    assert any('INTEGER PRIMARY KEY (rowid=?)' in row[-1] for row in refresh)
    c.close()


def test_terminal_failure_query_uses_run_ordinal_index(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    c.execute(f'alter table {PREFIX}turn_items add column ordinal integer')
    c.execute(f'update {PREFIX}turn_items set ordinal=rowid')
    c.execute(f'create index {PREFIX}turn_items_run_ordinal_idx on {PREFIX}turn_items(run_id,ordinal)')
    module = lib_module('observe')
    plan = c.execute('EXPLAIN QUERY PLAN ' + module.terminal_failure_page_sql(), ('child', -1, 0)).fetchall()
    assert any('SEARCH' in row[-1] and 'turn_items_run_ordinal_idx' in row[-1] for row in plan)
    assert not any('SCAN orchestration_v2_projection_turn_items' in row[-1] for row in plan)
    c.close()


PRIVATE = ['SECRET-PLANTED-TEXT', 'password=secret123', 'hunter2']


def plant(db, secret):
    """Secret text in every retained T3 field: options, scope, every status, model and instance."""
    c = sqlite3.connect(db)
    options = [{'id': 'effort', 'value': 'high'}, {'id': 'prompt', 'value': secret}, {'id': 'thinking', 'value': secret},
               {'id': 'fastMode', 'value': False}, {'id': 'contextWindow', 'value': {'x': secret}}, {'id': secret, 'value': 'low'}]
    payload = json.dumps({'modelSelection': {'model': secret, 'options': options}})
    c.execute(f"update {PREFIX}runs set payload_json=?, provider_instance_id=?, status=? where run_id='child'", (payload, secret, secret))
    c.execute(f'update {PREFIX}run_attempts set status=?', (secret,))
    c.execute(f'update {PREFIX}provider_turns set status=?', (secret,))
    c.execute(f"update {PREFIX}provider_turns set payload_json=json_set(payload_json,'$.turnTokenUsage.usageScope',?)", (secret,))
    c.execute(f"update {PREFIX}turn_items set status=? where turn_item_id='call'", (secret,))
    c.commit()
    c.close()


@pytest.mark.parametrize('secret', PRIVATE)
def test_t3_retained_fields_never_carry_source_text(setup, tmp_path, secret):
    db = t3_world(setup, tmp_path)
    plant(db, secret)
    row = ok(setup, '--db', db)
    assert row['sources']['t3']['state'] == 'ok'
    assert secret not in paths(setup)[1].read_text()
    by_type = {r['fact_type']: r for r in facts(setup, 't3')}
    requested = by_type['t3_requested_config']
    assert requested['fact']['requested'] == {'provider_instance': 'other', 'model': 'other', 'options': {'effort': 'high', 'fastMode': False},
                                              'options_dropped': 4, 'effort': 'high'}
    assert requested['unknown']['provider_instance'] == requested['unknown']['model'] == 'value_not_in_allowlist'
    usage = by_type['t3_turn_usage']['fact']
    assert usage['usage_scope'] == 'other' and usage['usage_scope_dropped'] is True
    assert usage['turn_status'] == 'other' and usage['turn_status_dropped'] is True
    assert by_type['t3_run_status']['fact']['status'] == 'other' and by_type['t3_attempt_status']['fact']['status'] == 'other'
    assert by_type['t3_delegate_call']['fact']['call_status'] == 'other'
    _, audit = run('audit', '--db', db, '--receipts', setup[3])
    assert secret not in json.dumps(audit['delegations'][0]['observed_child_usage'])  # the audit reader enforces the same enums


BAD_ID = 'password=secret123 "hunter2" x'  # spaces, '=' and quotes: not a T3 id


def break_ids(db, bad):
    """Free text in every retained T3 id and ordinal. Joins stay consistent, so only the grammar can stop it."""
    c = sqlite3.connect(db)
    c.execute(f'update {PREFIX}run_attempts set attempt_id=?, attempt_ordinal=?', (bad + ' attempt', bad + ' ordinal'))
    c.execute(f'update {PREFIX}provider_turns set run_attempt_id=?, provider_turn_id=?, ordinal=?', (bad + ' attempt', bad + ' turn', bad + ' tordinal'))
    c.execute(f"update {PREFIX}turn_items set turn_item_id=?, thread_id=? where turn_item_id='call'", (bad + ' call', bad + ' thread'))
    c.commit()
    c.close()


@pytest.mark.parametrize('bad', [BAD_ID, "tok'en;drop", 'a' * 2000], ids=['spaces-equals-quotes', 'quote', 'too-long'])
def test_t3_ids_and_ordinals_that_break_the_grammar_become_unknown_never_copied(setup, tmp_path, bad):
    db = t3_world(setup, tmp_path, failure=f'{SECRET} rate limit reached')
    break_ids(db, bad)
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}turn_items set turn_item_id=? where turn_item_id like '%terminal-failure%'", (bad + ' terminal-failure',))
    c.commit()
    c.close()
    row = ok(setup, '--db', db)
    assert row['sources']['t3']['state'] == 'ok'
    text = paths(setup)[1].read_text()
    for planted in (bad, 'password', 'hunter2', "tok'en"):
        assert planted not in text
    by_type = {}
    for r in facts(setup, 't3'):
        by_type.setdefault(r['fact_type'], []).append(r)
    call = by_type['t3_delegate_call'][0]  # the row is kept: a bad id is an unknown field, not a dropped fact
    assert call['fact']['child_run_id'] == 'child' and call['unknown']['call_id'] == call['unknown']['thread_id'] == 'invalid_id'
    assert 'call_id' not in call['source_ids'] and 'thread_id' not in call['source_ids']
    attempt = by_type['t3_attempt_status'][0]
    assert attempt['fact']['attempt_id'] is None and attempt['fact']['attempt_ordinal'] is None
    assert attempt['unknown']['attempt_id'] == 'invalid_id' and attempt['unknown']['attempt_ordinal'] == 'invalid_ordinal'
    usage = by_type['t3_turn_usage'][0]
    assert usage['fact']['provider_turn_id'] is None and usage['fact']['ordinal'] is None and usage['fact']['attempt_id'] is None
    assert {'attempt_id', 'attempt_ordinal', 'provider_turn_id', 'ordinal'} <= set(usage['unknown'])
    failure = by_type['t3_terminal_failure'][0]
    assert failure['fact']['turn_item_id'] is None and failure['unknown']['turn_item_id'] == 'invalid_id'
    _, audit = run('audit', '--db', db, '--receipts', setup[3])
    assert bad not in json.dumps(audit['delegations'][0]['observed_child_usage'])


@pytest.mark.parametrize('bad', [BAD_ID, "tok'en;drop"], ids=['spaces-equals-quotes', 'quote'])
def test_a_child_run_id_that_breaks_the_grammar_is_an_unknown_reference_not_a_run(setup, tmp_path, bad):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}turn_items set payload_json=json_set(payload_json,'$.output.structuredContent.childRunId',?) where turn_item_id='call'", (bad,))
    c.commit()
    c.close()
    row = ok(setup, '--db', db)
    assert row['sources']['t3']['state'] == 'ok'
    text = paths(setup)[1].read_text()
    assert bad not in text and 'password' not in text and 'hunter2' not in text
    assert {r['fact_type'] for r in facts(setup, 't3')} == {'t3_delegate_call'}
    call = facts(setup, 't3')[0]
    assert call['fact']['child_run_id'] is None and call['unknown']['child_run_id'] == 'invalid_id'
    assert call['source_ids'].get('run_id') is None


def test_a_valid_t3_id_with_the_live_shape_is_kept(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    live = 'run:thread:mcp%3A0b6bfe5e-aaaa-4bbb-8ccc-123456789abc:ordinal:88'
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}runs set run_id=? where run_id='child'", (live,))
    c.execute(f"update {PREFIX}run_attempts set run_id=?", (live,))
    c.execute(f"update {PREFIX}turn_items set payload_json=json_set(payload_json,'$.output.structuredContent.childRunId',?) where turn_item_id='call'", (live,))
    c.commit()
    c.close()
    ok(setup, '--db', db)
    assert {r['fact']['status'] for r in facts(setup, 't3', 't3_run_status')} == {'completed'}
    assert facts(setup, 't3', 't3_run_status')[0]['source_ids']['run_id'] == live


def test_t3_options_keep_only_documented_ids_with_known_values_and_count_the_rest(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    options = [{'id': 'effort', 'value': 'xhigh'}, {'id': 'fastMode', 'value': True}, {'id': 'serviceTier', 'value': 'priority'},
               {'id': 'contextWindow', 'value': '200k'}, {'id': 'thinking', 'value': False}, {'id': 'effort', 'value': 'low'},
               {'id': 'serviceTier', 'value': 'Priority Tier!'}, {'id': 'reasoningEffort', 'value': 'ultra'}, {'id': 'temperature', 'value': 'low'},
               {'id': 'effort', 'value': 7}, 'not an object', {'id': 'thinking'}]
    c.execute(f"update {PREFIX}runs set payload_json=json_set(payload_json,'$.modelSelection.options',json(?)) where run_id='child'", (json.dumps(options),))
    c.commit()
    c.close()
    ok(setup, '--db', db)
    requested = facts(setup, 't3', 't3_requested_config')[0]['fact']['requested']
    assert requested['options'] == {'effort': 'xhigh', 'fastMode': True, 'serviceTier': 'priority', 'contextWindow': '200k', 'thinking': False}
    assert requested['options_dropped'] == 7  # a repeated id, a bad value, an unknown id, a non-object, a missing value


def test_repeat_run_appends_no_facts_and_a_state_change_appends_new_rows(setup, tmp_path):
    db = t3_world(setup, tmp_path, status='running')
    first = ok(setup, '--db', db)
    assert new_total(first) > 0
    count = len(facts(setup))
    second = ok(setup, '--db', db)
    assert new_total(second) == 0 and len(facts(setup)) == count
    assert len([r for r in rows(setup) if r['kind'] == 'source_run']) == 2  # exactly one source_run per run
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}runs set status='completed'")
    c.commit()
    c.close()
    third = ok(setup, '--db', db)
    assert third['sources']['t3']['new_observations'] == 1
    statuses = [r['fact']['status'] for r in facts(setup, 't3', 't3_run_status')]
    assert statuses == ['running', 'completed']  # the old row stays; append-only


def test_a_source_timestamp_that_becomes_known_or_changes_is_a_new_fact_and_a_quiet_run_appends_none(setup, tmp_path):
    db = t3_world(setup, tmp_path)  # completed, with no completion time recorded
    first = ok(setup, '--db', db)
    assert facts(setup, 't3', 't3_run_status')[0]['unknown']['completed_at'] == 'not_recorded'
    assert new_total(ok(setup, '--db', db)) == 0  # collection time is not part of a fact
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}runs set payload_json=json_set(payload_json,'$.completedAt','2026-10-08T00:05:00Z') where run_id='child'")
    c.commit()
    c.close()
    third = ok(setup, '--db', db)
    assert third['sources']['t3']['new_observations'] == 1 and new_total(first) > 1
    statuses = facts(setup, 't3', 't3_run_status')
    assert [s['source_timestamps'].get('completed_at') for s in statuses] == [None, '2026-10-08T00:05:00+00:00']
    assert 'completed_at' not in statuses[1]['unknown'] and statuses[0]['fact'] == statuses[1]['fact']
    assert new_total(ok(setup, '--db', db)) == 0


def test_a_destination_timestamp_correction_is_a_new_fact(setup, tmp_path):
    absent = RECORD | {'created_at': None} | {'events': [RECORD['events'][0] | {'at': None}]}
    cfg, command = dest_world(setup, tmp_path, reply({'d1': absent}))
    ok(setup, '--destinations', cfg)
    before = facts(setup, 'destination:worker')
    assert new_total(ok(setup, '--destinations', cfg)) == 0
    command.write_text('#!/bin/sh\n' + reply({'d1': RECORD | {'events': [RECORD['events'][0]]}}) + '\n')
    row = ok(setup, '--destinations', cfg)
    assert row['sources']['destination:worker']['new_observations'] == 2  # the assignment and the event, now with their times
    after = facts(setup, 'destination:worker')
    assert len(after) == len(before) + 2 and sum(r['fact_type'] == 'destination_event' for r in after) == 2
    command.write_text('#!/bin/sh\n' + reply({'d1': RECORD | {'events': [RECORD['events'][0] | {'at': '2026-10-10T13:02:00+00:00'}]}}) + '\n')
    assert ok(setup, '--destinations', cfg)['sources']['destination:worker']['new_observations'] == 1  # a corrected event time


def test_a_source_failure_is_not_zero_events(setup, tmp_path):
    record(setup, 'one')
    missing = ok(setup)
    assert missing['sources']['t3'] == {'state': 'unavailable', 'reason': 'no_db_supplied', 'subjects_checked': 0, 'new_observations': 0}
    gone = ok(setup, '--db', tmp_path / 'nope.sqlite')
    assert gone['sources']['t3']['state'] == 'unavailable' and gone['sources']['t3']['reason'] == 'database_missing'
    bad = tmp_path / 'bad.sqlite'
    bad.write_text('not a database')
    broken = observe(setup, '--db', bad)
    assert broken[0].returncode == 0, broken[0].stderr  # a source failure exits 0 and is recorded
    assert broken[1]['sources']['t3']['state'] == 'error' and broken[1]['sources']['t3']['reason']
    empty = tmp_path / 'empty.sqlite'
    sqlite3.connect(empty).close()
    assert ok(setup, '--db', empty)['sources']['t3']['state'] == 'error'  # tables absent: unreadable, not "no events"
    assert facts(setup) == []


def test_zero_matching_events_is_a_healthy_source(setup, tmp_path):
    t3_world(setup, tmp_path)
    decision_row(setup, 'other')
    db = tmp_path / 't3.sqlite'
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}turn_items set payload_json=replace(payload_json,'\"one\"','\"elsewhere\"')")
    c.commit()
    c.close()
    row = ok(setup, '--db', db)
    assert row['sources']['t3'] == {'state': 'ok', 'reason': None, 'subjects_checked': 0, 'new_observations': 0}


# ---- destinations -----------------------------------------------------------------------------------------------------

RECORD = {'found': True, 'status': 'finished', 'task_kind': 'review', 'source': 'agent', 'created_at': '2026-10-10T13:00:00+00:00',
          'cancelled_at': None, 'cancelled_reason': None,
          'reports': [{'outcome': 'completed', 'created_at': '2026-10-10T13:30:00+00:00', 'receipt_id': 'rcpt-1', 'body': SECRET}],
          'events': [{'event_id': 7, 'type': 'queued', 'at': '2026-10-10T13:01:00+00:00', 'actor_kind': 'owner', 'reason': 'new'},
                     {'event_id': 8, 'type': 'consumed', 'at': '2026-10-10T13:40:00+00:00', 'actor_kind': 'agent',
                      'reason': f'{SECRET} free text'}]}


def observer(tmp_path, body, name='obs'):
    return script(tmp_path, name + '.sh', body)


def dest_world(setup, tmp_path, body, ids=('d1',), **entry_extra):
    for decision in ids:
        decision_row(setup, decision, 'worker')
    command = observer(tmp_path, body)
    cap = script(tmp_path, 'cap.sh', 'echo \'{"available": true}\'')
    cfg = config(tmp_path, entry(cap) | {'observe_command': [str(command)]} | entry_extra)
    return cfg, command


def reply(requests):
    return "cat >/dev/null; echo '" + json.dumps({'schema_version': 1, 'requests': requests}) + "'"


def test_destination_facts_status_reports_events_and_no_free_text(setup, tmp_path):
    cfg, _ = dest_world(setup, tmp_path, reply({'d1': RECORD}))
    row = ok(setup, '--destinations', cfg)
    assert row['sources']['destination:worker'] == {'state': 'ok', 'reason': None, 'subjects_checked': 1, 'new_observations': 4}
    found = {r['fact_type']: r for r in facts(setup, 'destination:worker')}
    assert found['destination_assignment']['fact'] == {'type': 'destination_assignment', 'found': True, 'status': 'finished',
                                                       'task_kind': 'review', 'source': 'agent', 'cancelled_reason': None}
    assert found['destination_assignment']['source_timestamps']['created_at'] == '2026-10-10T13:00:00+00:00'
    assert found['destination_report']['fact']['outcome'] == 'completed' and found['destination_report']['source_ids']['receipt_id'] == 'rcpt-1'
    events = facts(setup, 'destination:worker', 'destination_event')
    assert [e['fact']['reason'] for e in events] == ['new', 'redacted']
    assert events[0]['fact']['actor_kind'] == 'owner' and events[0]['source_ids']['request_id'] == 'd1'
    assert SECRET not in paths(setup)[1].read_text()
    assert new_total(ok(setup, '--destinations', cfg)) == 0


@pytest.mark.parametrize('secret', PRIVATE)
def test_destination_strings_are_enum_members_or_redacted_never_copied(setup, tmp_path, secret):
    record = {'found': True, 'status': secret, 'task_kind': secret, 'source': secret, 'created_at': '2026-10-10T13:00:00+00:00',
              'cancelled_at': '2026-10-10T13:05:00+00:00', 'cancelled_reason': secret,
              'reports': [{'outcome': secret, 'created_at': '2026-10-10T13:30:00+00:00', 'receipt_id': secret}],
              'events': [{'event_id': secret, 'type': secret, 'at': '2026-10-10T13:01:00+00:00', 'actor_kind': secret, 'reason': secret},
                         {'event_id': 3, 'type': 'queued', 'at': '2026-10-10T13:02:00+00:00', 'actor_kind': 'owner', 'reason': 'outcome=done'},
                         {'event_id': 4, 'type': 'completed', 'at': '2026-10-10T13:03:00+00:00', 'actor_kind': 'dot', 'reason': 'outcome=completed;late'}]}
    cfg, _ = dest_world(setup, tmp_path, reply({'d1': record}))
    ok(setup, '--destinations', cfg)
    assert secret not in paths(setup)[1].read_text()
    found = {r['fact_type']: r for r in facts(setup, 'destination:worker')}
    assert found['destination_assignment']['fact'] == {'type': 'destination_assignment', 'found': True, 'status': 'redacted', 'task_kind': 'redacted',
                                                       'source': 'redacted', 'cancelled_reason': 'redacted'}
    assert found['destination_report']['fact']['outcome'] == 'redacted' and found['destination_report']['fact']['receipt_id'] is None
    events = facts(setup, 'destination:worker', 'destination_event')
    assert [(e['fact']['event_type'], e['fact']['actor_kind'], e['fact']['reason'], e['fact']['event_id']) for e in events] == [
        ('redacted', 'redacted', 'redacted', None), ('queued', 'owner', 'redacted', 3), ('completed', 'dot', 'redacted', 4)]


def test_destination_enum_members_pass_through(setup, tmp_path):
    types = ['queued', 'claimed', 'working', 'waiting', 'blocked', 'completed', 'consumed', 'accepted', 'integrated', 'abandoned', 'cancelled',
             'delivery_sent', 'delivery_uncertain', 'reconciled', 'check_started', 'check_passed', 'check_failed', 'check_skipped']
    reasons = ['new', 'reclaim', 'resume', 'takeover', 'wait', 'get', 'rejected', 'expired'] + [f'outcome={o}' for o in (
        'completed', 'blocked', 'failed', 'cancelled', 'abandoned')]
    events = [{'event_id': i, 'type': t, 'at': '2026-10-10T13:00:00+00:00', 'actor_kind': a, 'reason': r}
              for i, (t, a, r) in enumerate(zip(types * 2, ['owner', 'dot', 'system', 'operator', 'dot-fix'] * 8, reasons * 3), 1)]
    record = {'found': True, 'status': 'finished', 'task_kind': 'audit-review', 'source': 'drainer', 'created_at': '2026-10-10T13:00:00+00:00',
              'cancelled_at': None, 'cancelled_reason': 'expired', 'reports': [{'outcome': 'failed', 'created_at': '2026-10-10T13:30:00+00:00',
              'receipt_id': 'report_00245e9e-3587-4ec8-9165-6cb45568d66d'}], 'events': events}
    cfg, _ = dest_world(setup, tmp_path, reply({'d1': record}))
    ok(setup, '--destinations', cfg)
    seen = facts(setup, 'destination:worker', 'destination_event')
    assert {e['fact']['event_type'] for e in seen} == set(types) and {e['fact']['reason'] for e in seen} == set(reasons)
    assert {e['fact']['actor_kind'] for e in seen} == {'owner', 'dot', 'system', 'operator', 'dot-fix'}
    assert all('redacted' not in json.dumps(e['fact']) for e in seen)
    assert facts(setup, 'destination:worker', 'destination_assignment')[0]['fact']['cancelled_reason'] == 'expired'
    report = facts(setup, 'destination:worker', 'destination_report')[0]
    assert report['fact']['outcome'] == 'failed' and report['source_ids']['receipt_id'].startswith('report_')


def test_destination_found_false_is_a_fact_and_a_new_event_is_a_new_row(setup, tmp_path):
    cfg, command = dest_world(setup, tmp_path, reply({'d1': {'found': False}}))
    row = ok(setup, '--destinations', cfg)
    assert row['sources']['destination:worker']['new_observations'] == 1
    assert facts(setup, 'destination:worker')[0]['fact'] == {'type': 'destination_assignment', 'found': False}
    command.write_text(reply({'d1': RECORD}).join(['#!/bin/sh\n', '\n']))
    command.chmod(0o755)
    assert ok(setup, '--destinations', cfg)['sources']['destination:worker']['new_observations'] == 4
    longer = RECORD | {'events': RECORD['events'] + [{'event_id': 9, 'type': 'accepted', 'at': '2026-10-10T14:00:00+00:00', 'actor_kind': 'owner', 'reason': None}]}
    command.write_text('#!/bin/sh\n' + reply({'d1': longer}) + '\n')
    assert ok(setup, '--destinations', cfg)['sources']['destination:worker']['new_observations'] == 1


@pytest.mark.parametrize('body,reason', [
    ("cat >/dev/null; echo '{\"error\": \"database unreadable\"}'; exit 1", 'observe_command_reported_error'),
    ("cat >/dev/null; echo 'not json at all'", 'observe_command_invalid_json'),
    ("cat >/dev/null; echo '[1]'", 'observe_command_invalid_shape'),
    ("cat >/dev/null; echo '{\"requests\": {}}'", 'observe_command_invalid_record'),
    ("cat >/dev/null; echo '{}'; exit 3", 'observe_command_nonzero_exit'),
    ("exec sleep 30", 'observe_command_timeout'),
])
def test_destination_failures_are_recorded_not_raised(setup, tmp_path, body, reason):
    cfg, _ = dest_world(setup, tmp_path, body, timeout_seconds=1)
    result, row = observe(setup, '--destinations', cfg)
    assert result.returncode == 0, result.stderr
    found = row['sources']['destination:worker']
    assert found['state'] == 'error' and found['reason'].startswith(reason) and found['new_observations'] == 0
    assert facts(setup, 'destination:worker') == []


def test_destination_without_command_or_config_is_unavailable_and_ids_arrive_on_stdin(setup, tmp_path):
    decision_row(setup, 'd1', 'worker')
    decision_row(setup, 'd2', 'ghost')
    cap = script(tmp_path, 'cap.sh', 'echo \'{"available": true}\'')
    cfg = config(tmp_path, entry(cap))
    row = ok(setup, '--destinations', cfg)
    assert row['sources']['destination:worker']['reason'] == 'no_observe_command'
    assert row['sources']['destination:ghost']['reason'] == 'destination_not_configured'
    seen = tmp_path / 'stdin.txt'
    cfg2, _ = dest_world(setup, tmp_path, f"cat > '{seen}'; echo '{{\"requests\": {{}}}}'", ids=('d3', 'd4'))
    observe(setup, '--destinations', cfg2)
    assert seen.read_text().split() == ['d1', 'd3', 'd4']


def test_observe_command_must_be_an_absolute_argv_and_does_not_change_route_identity(setup, tmp_path):
    cap = script(tmp_path, 'cap.sh', 'echo \'{"available": true}\'')
    bad = config(tmp_path, entry(cap) | {'observe_command': ['relative']}, name='bad.json')
    result, _ = observe(setup, '--destinations', bad)
    assert result.returncode == 1 and 'observe_command must be a list of strings that starts with an absolute path' in result.stderr
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import destinations
    plain = destinations.read_destinations(config(tmp_path, entry(cap), name='a.json'))[1]
    extra = destinations.read_destinations(config(tmp_path, entry(cap) | {'observe_command': [str(cap)]}, name='b.json'))[1]
    assert plain == extra


@pytest.mark.parametrize('name', ['my worker', 'Worker', 'a:b', '-lead', '', 'x' * 33, 'model', 'w\nx'])
def test_a_destination_name_outside_the_shared_contract_is_rejected_when_the_config_is_read(setup, tmp_path, name):
    decision_row(setup, 'd1', 'worker')
    cap = script(tmp_path, 'cap.sh', 'echo \'{"available": true}\'')
    cfg = config(tmp_path, entry(cap, name=name) | {'observe_command': [str(cap)]})
    result, _ = observe(setup, '--destinations', cfg)
    assert result.returncode == 1 and 'name must be a unique name matching ^[a-z0-9][a-z0-9_-]{0,31}$' in result.stderr
    assert not paths(setup)[1].exists()


def test_a_pending_row_that_the_ledger_would_reject_is_refused_before_any_append(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    decision_row(setup, 'poison', 'my worker')  # an old receipt that names a destination outside the contract
    result, _ = observe(setup, '--db', db)
    assert result.returncode == 1 and 'my worker' in result.stderr
    assert not paths(setup)[1].exists()  # the valid T3 facts of the same run were not appended either
    ok_run = ok(setup, '--db', db, '--since', '2999-01-01T00:00:00Z')  # the poisoned receipt is out of scope: the ledger stays readable
    assert ok_run['kind'] == 'source_run' and run('audit', '--db', db, '--receipts', setup[3])[1]['observations']['state'] == 'observed'


def test_append_run_validates_every_pending_row_and_the_source_run_before_writing(tmp_path):
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import observations
    ledger = tmp_path / 'observations.jsonl'
    good = observations.make_observation('git', 's1', 'one', 'git_scan', {'a': 1})
    bad_fact = observations.make_observation('git', 's2', 'one', 'git_scan', {'a': 1}) | {'fact_type': 'other_type'}
    for collected in ({'git': {'state': 'ok', 'reason': None, 'subjects_checked': 2, 'observations': [good, bad_fact]}},
                      {'git': {'state': 'ok', 'reason': None, 'subjects_checked': 1, 'observations': [good]},
                       'destination:my worker': {'state': 'ok', 'reason': None, 'subjects_checked': 0, 'observations': []}},
                      {'git': {'state': 'fine', 'reason': None, 'subjects_checked': 1, 'observations': [good]}}):
        with pytest.raises(ValueError):
            observations.append_run(ledger, 'r1', '2026-10-10T00:00:00+00:00', '2026-10-10T00:00:01+00:00', collected)
        assert not ledger.exists() or ledger.read_text() == ''


def test_append_run_syncs_the_complete_batch_once(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import observations
    ledger = tmp_path / 'observations.jsonl'
    facts = [observations.make_observation('git', f's{n}', 'one', 'git_scan', {'a': n}) for n in range(4)]
    calls = []
    real_fsync = observations.os.fsync
    monkeypatch.setattr(observations.os, 'fsync', lambda fd: calls.append(fd) or real_fsync(fd))
    row = observations.append_run(ledger, 'batch', '2026-10-10T00:00:00+00:00', '2026-10-10T00:00:01+00:00',
                                  {'git': {'state': 'ok', 'reason': None, 'subjects_checked': 4, 'observations': facts}})
    assert row['kind'] == 'source_run'
    assert len(calls) == 1
    assert len(ledger.read_text().splitlines()) == 5


def test_append_run_streams_existing_ledger_for_deduplication(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import observations
    ledger = tmp_path / 'observations.jsonl'
    fact = observations.make_observation('git', 's1', 'one', 'git_scan', {'a': 1})
    collected = {'git': {'state': 'ok', 'reason': None, 'subjects_checked': 1, 'observations': [fact]}}
    observations.append_run(ledger, 'first', '2026-10-10T00:00:00+00:00', '2026-10-10T00:00:01+00:00', collected)
    monkeypatch.setattr(observations, 'locked_private_rows', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('loaded ledger')))
    record = observations.append_run(ledger, 'second', '2026-10-10T00:00:02+00:00', '2026-10-10T00:00:03+00:00', collected)
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert record['sources']['git']['new_observations'] == 0
    assert sum(row['kind'] == 'observation' for row in rows) == 1


# ---- git --------------------------------------------------------------------------------------------------------------

def git(repo, *args, when=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')} | {'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null'}
    if when:
        env |= {'GIT_AUTHOR_DATE': when.isoformat(), 'GIT_COMMITTER_DATE': when.isoformat()}
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', '-C', repo, *args], capture_output=True,
                          text=True, check=True, env=env).stdout.strip()


def commit(repo, files, message, when):
    for name in files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.read_text() + 'x' if path.exists() else 'x')
        git(repo, 'add', name)
    git(repo, 'commit', '-qm', message, when=when)
    return git(repo, 'rev-parse', 'HEAD')


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def days(n):
    return NOW - timedelta(days=n)


@pytest.fixture
def repo_world(setup, tmp_path):
    """E touched a.txt and b.txt 12 days ago; the close was recorded 10 days ago."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    root = commit(repo, ['root.txt'], 'root', days(20))
    git(repo, 'branch', 'side')
    evidenced = commit(repo, ['a.txt', 'b.txt'], 'evidenced', days(12))
    world = {'repo': repo, 'sha': evidenced}
    git(repo, 'checkout', '-q', 'side')
    world['before'] = commit(repo, ['a.txt'], 'older side work', days(15))  # not reachable from E, dated before it
    git(repo, 'checkout', '-q', 'main')
    world['later'] = commit(repo, ['a.txt', 'c.txt'], f'touches a {SECRET}', days(8))
    world['unrelated'] = commit(repo, ['z.txt'], 'unrelated', days(7))
    world['future'] = commit(repo, ['b.txt'], 'after the window', NOW + timedelta(days=2))
    world['root'] = root
    record(setup, 'one')
    evidence = json.dumps({'type': 'commit', 'repo': str(repo), 'sha': evidenced})
    result, _ = close(setup, 'one', 'c1', '--evidence', evidence)
    assert result.returncode == 0, result.stderr
    set_close_time(setup, days(10))
    return world


def set_close_time(setup, when):
    path = paths(setup)[0]
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    for line in lines:
        if line['kind'] == 'close':
            line['recorded_at'] = when.isoformat()
    path.write_text(''.join(json.dumps(line) + '\n' for line in lines))


def test_git_signals_are_candidates_with_window_and_scope_and_no_message_text(setup, repo_world):
    row = ok(setup)
    assert row['sources']['git']['state'] == 'ok' and row['sources']['git']['subjects_checked'] == 1
    signals = facts(setup, 'git', 'same_file_later_commit')
    assert [s['fact']['sha'] for s in signals] == [repo_world['later']]
    assert signals[0]['fact']['overlapping_file_count'] == 1 and signals[0]['fact']['committed_at']
    scope = signals[0]['scope']
    assert scope['repo'] == str(repo_world['repo']) and scope['evidenced_sha'] == repo_world['sha'] and scope['files_touched'] == 2
    assert scope['window']['days'] == 14 and scope['window']['start'] == days(12).isoformat()
    assert scope['window']['end_bound_by'] == 'now'  # the future commit is outside; so is the older side-branch commit
    scan = facts(setup, 'git', 'git_scan')[0]
    assert scan['fact']['files_touched'] == 2 and scan['fact']['meaning'] == 'candidate follow-up signals, not fixes, rework or outcomes'
    shas = {s['fact'].get('sha') for s in facts(setup, 'git')}
    assert repo_world['unrelated'] not in shas and repo_world['future'] not in shas and repo_world['before'] not in shas
    assert SECRET not in paths(setup)[1].read_text()
    assert new_total(ok(setup)) == 0


def test_window_days_bound_the_search_from_the_close_time(setup, repo_world):
    ok(setup, '--git-window-days', '1')  # close + 1 day ends 9 days ago, before the commit made 8 days ago
    assert facts(setup, 'git', 'same_file_later_commit') == []
    assert facts(setup, 'git', 'git_scan')[0]['scope']['window']['end_bound_by'] == 'close_time_plus_days'
    ok(setup, '--git-window-days', '14')
    assert [s['fact']['sha'] for s in facts(setup, 'git', 'same_file_later_commit')] == [repo_world['later']]
    result, _ = observe(setup, '--git-window-days', '0')
    assert result.returncode == 1 and '--git-window-days' in result.stderr


def test_explicit_revert_reference_stores_the_boolean_and_shas_only(setup, repo_world):
    repo = repo_world['repo']
    reverting = commit(repo, ['z.txt'], f"Revert evidenced\n\n{SECRET} body\nThis reverts commit {repo_world['sha']}.", days(6))
    ok(setup)
    found = facts(setup, 'git', 'explicit_revert_reference')
    assert len(found) == 1 and found[0]['fact'] == {'type': 'explicit_revert_reference', 'explicit_revert_reference': True,
                                                    'evidenced_sha': repo_world['sha'], 'reverting_sha': reverting}
    assert found[0]['scope']['window'] and found[0]['decision_id'] == 'one'
    assert SECRET not in paths(setup)[1].read_text()
    # calibrate lists the decision as a candidate for parent review, with no verdict
    _, available, quota, receipts = setup
    result, out = run('calibrate', '--available', available, '--quota', quota, '--receipts', receipts)
    assert result.returncode == 0, result.stderr
    assert out['observations']['candidates_for_parent_followup_review']['decision_ids'] == ['one']
    assert 'no verdict' in out['observations']['candidates_for_parent_followup_review']['meaning']


def test_missing_repo_or_sha_and_nested_paths_are_skipped_never_scanned(setup, repo_world, tmp_path):
    record(setup, 'two')
    record(setup, 'three')
    record(setup, 'four')
    nested = repo_world['repo'] / 'sub'
    nested.mkdir()
    for decision, repo, sha in (('two', tmp_path / 'no-such-repo', repo_world['sha']), ('three', repo_world['repo'], '0' * 40),
                                ('four', nested, repo_world['sha'])):
        result, _ = close(setup, decision, f'c-{decision}', '--evidence', json.dumps({'type': 'commit', 'repo': str(repo), 'sha': sha}))
        assert result.returncode == 0, result.stderr
    row = ok(setup)
    git_source = row['sources']['git']
    assert git_source['state'] == 'ok' and git_source['subjects_checked'] == 4
    assert git_source['skipped'] == {'not_a_repository_root': 1, 'repo_missing': 1, 'sha_missing': 1}
    assert {r['decision_id'] for r in facts(setup, 'git')} == {'one'}


def test_git_is_read_only_ignores_inherited_git_env_and_reads_no_other_repo(setup, repo_world, tmp_path):
    repo = repo_world['repo']
    other = tmp_path / 'other'
    other.mkdir()
    git(other, 'init', '-q')
    refs = git(repo, 'for-each-ref')
    listing = sorted(p.name for p in (repo / '.git').iterdir())
    env = os.environ | {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}
    result, row = observe(setup, env=env)
    assert result.returncode == 0, result.stderr
    assert row['sources']['git']['new_observations'] > 0
    assert git(repo, 'for-each-ref') == refs and sorted(p.name for p in (repo / '.git').iterdir()) == listing
    assert not (repo / '.git/FETCH_HEAD').exists()


# ---- git: no lazy fetch, and operational failures are not health -----------------------------------------------------

LIB = REPO / 'tools/model-policy-ops/lib'


def lib_module(name):
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    return __import__(name)


def git_supports_no_lazy_fetch():
    version = subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.split()[2].split('.')
    return (int(version[0]), int(version[1])) >= (2, 44)


def snapshot(repo):
    """Every byte of the object store (loose objects and packs) plus the object count: any fetch changes it."""
    root = repo / '.git/objects'
    files = {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}
    return files, git(repo, 'count-objects', '-v')


@pytest.fixture
def promisor_world(setup, tmp_path):
    """A partial clone (no trees) of a local repository that has every object: the promisor remote can serve what is missing."""
    if not git_supports_no_lazy_fetch():
        pytest.skip('git older than 2.44 has no GIT_NO_LAZY_FETCH')
    origin = tmp_path / 'origin'
    origin.mkdir()
    git(origin, 'init', '-q', '-b', 'main')
    git(origin, 'config', 'uploadpack.allowFilter', 'true')
    git(origin, 'config', 'uploadpack.allowAnySHA1InWant', 'true')
    first = commit(origin, ['a.txt', 'dir/b.txt'], 'first', days(12))
    subprocess.run(['git', 'clone', '-q', '--no-checkout', '--filter=tree:0', f'file://{origin}', str(tmp_path / 'partial')],
                   check=True, capture_output=True, env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})
    partial = tmp_path / 'partial'
    later = commit(origin, ['a.txt'], 'only in origin', days(11))  # the partial clone has never seen this commit
    record(setup, 'one')
    return {'origin': origin, 'partial': partial, 'present': first, 'absent': later}


def close_on(setup, repo, sha, close_id='c1', decision='one'):
    result, _ = close(setup, decision, close_id, '--evidence', json.dumps({'type': 'commit', 'repo': str(repo), 'sha': sha}))
    assert result.returncode == 0, result.stderr
    set_close_time(setup, days(10))


def test_git_never_lazy_fetches_a_missing_tree_from_a_promisor_remote(setup, promisor_world):
    partial = promisor_world['partial']
    close_on(setup, partial, promisor_world['present'])
    before = snapshot(partial)
    row = ok(setup)
    assert snapshot(partial) == before  # no object or pack was fetched into the source repository
    git_source = row['sources']['git']
    assert facts(setup, 'git') == []  # the subject reads as unavailable
    assert git_source['state'] == 'error' and git_source['failed'] == {'git_missing_local_object': 1} and git_source['subjects_checked'] == 1


def test_git_never_lazy_fetches_a_missing_commit_and_close_does_not_either(setup, promisor_world):
    partial = promisor_world['partial']
    before = snapshot(partial)
    close_on(setup, partial, promisor_world['absent'])  # close verifies the commit through commit_exists
    assert snapshot(partial) == before
    closes = [r for r in rows_of(paths(setup)[0]) if r['kind'] == 'close']
    assert [e['verified_state'] for e in closes[0]['evidence']] == ['unverified_missing']
    row = ok(setup)
    assert snapshot(partial) == before
    assert row['sources']['git']['state'] == 'ok' and row['sources']['git']['skipped'] == {'sha_missing': 1}
    assert facts(setup, 'git') == []


def rows_of(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def stub_git(tmp_path, monkeypatch, behavior, command='log'):
    """A `git` ahead on PATH that misbehaves for `command` in repositories whose path contains `bad`, and is the real git otherwise."""
    import shutil
    real = shutil.which('git')
    stub = tmp_path / 'stub-bin'
    stub.mkdir(exist_ok=True)
    script(stub, 'git', f'case "$*" in *bad*) case "$*" in *" {command} "*) {behavior};; esac;; esac\nexec {real} "$@"')
    monkeypatch.setenv('PATH', f'{stub}:{os.environ["PATH"]}')


def bad_clone(repo_world, tmp_path):
    bad = tmp_path / 'bad-repo'
    subprocess.run(['git', 'clone', '-q', str(repo_world['repo']), str(bad)], check=True, capture_output=True)
    return bad


@pytest.mark.parametrize('behavior,reason,patch', [
    ('exit 3', 'git_failed', {}),
    ('exec sleep 30', 'git_timeout', {'GIT_TIMEOUT': 1}),
    ('printf "%05000d" 0', 'git_output_too_large', {'GIT_OUTPUT_LIMIT': 2000}),
])
def test_an_operational_git_failure_is_an_error_or_partial_never_healthy(setup, repo_world, tmp_path, monkeypatch, behavior, reason, patch):
    observe_module = lib_module('observe')
    for name, value in patch.items():
        monkeypatch.setattr(observe_module, name, value)
    stub_git(tmp_path, monkeypatch, behavior)
    bad = bad_clone(repo_world, tmp_path)
    only_bad = observe_module.collect_git(
        [{'kind': 'close', 'close_id': 'cb', 'decision_id': 'one', 'recorded_at': days(10).isoformat(), 'evidence': [{'type': 'commit', 'repo': str(bad), 'sha': repo_world['sha']}]}],
        {'one'}, 14, NOW)
    assert only_bad['state'] == 'error' and only_bad['failed'] == {reason: 1} and only_bad['skipped'] == {}
    assert only_bad['observations'] == [] and reason in only_bad['reason']
    both = observe_module.collect_git(
        [{'kind': 'close', 'close_id': 'cg', 'decision_id': 'one', 'recorded_at': days(10).isoformat(), 'evidence': [{'type': 'commit', 'repo': str(repo_world['repo']), 'sha': repo_world['sha']}]},
         {'kind': 'close', 'close_id': 'cb', 'decision_id': 'one', 'recorded_at': days(10).isoformat(), 'evidence': [{'type': 'commit', 'repo': str(bad), 'sha': repo_world['sha']}]}],
        {'one'}, 14, NOW)
    assert both['state'] == 'partial' and both['failed'] == {reason: 1} and both['subjects_checked'] == 2
    assert both['observations'] and all(o['subject_id'].startswith('cg:') for o in both['observations'])  # no half facts from the failed subject


def collect_one(repo, sha):
    return lib_module('observe').collect_git(
        [{'kind': 'close', 'close_id': 'cr', 'decision_id': 'one', 'recorded_at': days(10).isoformat(),
          'evidence': [{'type': 'commit', 'repo': str(repo), 'sha': sha}]}], {'one'}, 14, NOW)


def test_cat_file_failing_operationally_is_not_a_missing_commit(setup, repo_world, tmp_path, monkeypatch):
    stub_git(tmp_path, monkeypatch, 'echo "error: object file is corrupt" >&2; exit 3', command='cat-file')
    source = collect_one(bad_clone(repo_world, tmp_path), repo_world['sha'])
    assert source['state'] == 'error' and source['failed'] == {'git_failed': 1} and source['skipped'] == {}
    assert source['observations'] == []


@pytest.mark.parametrize('behavior,reason,patch', [
    ('exec sleep 30', 'git_timeout', {'COMMIT_TIMEOUT': 1}),
    ('printf "garbage\\n"', 'git_failed', {}),  # exit 0 but not a cat-file answer
    ('kill -9 $$', 'git_failed', {}),  # terminated by a signal
])
def test_cat_file_timeouts_signals_and_malformed_output_are_operational(setup, repo_world, tmp_path, monkeypatch, behavior, reason, patch):
    for name, value in patch.items():
        monkeypatch.setattr(lib_module('outcomes'), name, value)
    stub_git(tmp_path, monkeypatch, behavior, command='cat-file')
    bad = bad_clone(repo_world, tmp_path)
    assert lib_module('outcomes').commit_state(str(bad), repo_world['sha']) in ('timeout', 'error')
    source = collect_one(bad, repo_world['sha'])
    assert source['state'] == 'error' and source['failed'] == {reason: 1} and source['skipped'] == {}


def test_cat_file_reports_confirmed_absence_only_from_an_explicit_missing_line(repo_world):
    outcomes = lib_module('outcomes')
    repo = str(repo_world['repo'])
    assert outcomes.commit_state(repo, repo_world['sha']) == 'exists'
    assert outcomes.commit_state(repo, '1' * 40) == 'missing'
    assert outcomes.commit_state(str(repo_world['repo'] / 'no-such-dir'), repo_world['sha']) == 'error'


@pytest.mark.parametrize('error', ['exit 3', 'echo "fatal: detected dubious ownership in repository at x" >&2; exit 128',
                                   'echo "fatal: bad config line 1 in file .git/config" >&2; exit 128'])
def test_a_root_probe_failing_in_a_worktree_repository_is_operational_not_a_non_root(setup, repo_world, tmp_path, monkeypatch, error):
    stub_git(tmp_path, monkeypatch, error, command='rev-parse')
    source = collect_one(bad_clone(repo_world, tmp_path), repo_world['sha'])
    assert source['state'] == 'error' and source['failed'] == {'git_failed': 1} and source['skipped'] == {}


@pytest.mark.parametrize('error', ['exit 3', 'echo "fatal: detected dubious ownership in repository at x" >&2; exit 128'])
def test_a_root_probe_failing_in_a_bare_repository_is_operational_and_a_healthy_bare_one_is_scanned(setup, repo_world, tmp_path, monkeypatch, error):
    bare = tmp_path / 'bad-bare.git'
    subprocess.run(['git', 'clone', '-q', '--bare', str(repo_world['repo']), str(bare)], check=True, capture_output=True)
    healthy = collect_one(bare, repo_world['sha'])
    assert healthy['state'] == 'ok' and healthy['skipped'] == {} and healthy['observations']
    stub_git(tmp_path, monkeypatch, error, command='rev-parse')
    source = collect_one(bare, repo_world['sha'])
    assert source['state'] == 'error' and source['failed'] == {'git_failed': 1} and source['skipped'] == {}


def test_a_genuine_non_repository_or_non_root_is_a_skip_not_a_failure(setup, repo_world, tmp_path):
    plain = tmp_path / 'plain'
    plain.mkdir()
    nested = repo_world['repo'] / 'deep'
    nested.mkdir()
    for path in (plain, nested):
        source = collect_one(path, repo_world['sha'])
        assert source['state'] == 'ok' and source['skipped'] == {'not_a_repository_root': 1} and source['failed'] == {}


def test_operational_failures_reach_the_source_run_and_audit(setup, repo_world, tmp_path, monkeypatch):
    stub_git(tmp_path, monkeypatch, 'exit 3')
    bad = bad_clone(repo_world, tmp_path)
    record(setup, 'two')
    close_on(setup, bad, repo_world['sha'], close_id='c2', decision='two')
    set_close_time(setup, days(10))
    row = ok(setup)
    git_source = row['sources']['git']
    assert git_source['state'] == 'partial' and git_source['failed'] == {'git_failed': 1} and 'git_failed' in git_source['reason']
    assert {r['decision_id'] for r in facts(setup, 'git')} == {'one'}
    _, available, quota, receipts = setup
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': []}))
    result, audit = run('audit', '--export', export, '--receipts', receipts)
    assert result.returncode == 0, result.stderr
    latest = audit['observations']['latest_source_runs']['git']
    assert latest['state'] == 'partial' and latest['failed'] == {'git_failed': 1} and latest['skipped'] == {}


def test_scope_filters_decisions_by_since(setup, repo_world):
    row = ok(setup, '--since', (NOW + timedelta(days=1)).isoformat())
    assert row['sources']['git']['subjects_checked'] == 0 and facts(setup) == []


# ---- storage ----------------------------------------------------------------------------------------------------------

def test_file_mode_parent_symlink_and_alias_refusals(setup, tmp_path):
    record(setup, 'one')
    ok(setup)
    assert paths(setup)[1].stat().st_mode & 0o777 == 0o600
    state = setup[3].parent
    state.chmod(0o770)
    result, _ = observe(setup)
    assert result.returncode == 1 and 'group- or world-accessible' in result.stderr
    state.chmod(0o700)
    link = tmp_path / 'link.jsonl'
    link.symlink_to(paths(setup)[1])
    result, _ = observe(setup, '--observations', link)
    assert result.returncode == 1
    for flag, other in (('--observations', setup[3]), ('--outcomes', paths(setup)[1])):
        before = paths(setup)[1].read_bytes()
        args = ['--observations', setup[3]] if flag == '--observations' else ['--outcomes', paths(setup)[1]]
        result, _ = observe(setup, *args)
        assert result.returncode == 1 and 'name the same file' in result.stderr
        assert paths(setup)[1].read_bytes() == before
    result, _ = run('audit', '--export', tmp_path / 'x.json', '--receipts', setup[3], '--observations', setup[3])
    assert result.returncode == 1 and 'name the same file' in result.stderr


@pytest.mark.parametrize('ledger_name', ('receipts', 'outcomes', 'observations', 'directives'))
def test_observe_state_refuses_every_ledger_path_without_writing(setup, tmp_path, ledger_name):
    record(setup, 'one')
    receipts = setup[3]
    ledgers = {
        'receipts': receipts,
        'outcomes': receipts.with_name('outcomes.jsonl'),
        'observations': receipts.with_name('observations.jsonl'),
        'directives': receipts.with_name('directives.jsonl'),
    }
    target = ledgers[ledger_name]
    if ledger_name != 'receipts':
        target.write_bytes(b'ledger sentinel\n')
    before = {name: path.read_bytes() for name, path in ledgers.items() if path.exists()}
    args = ['observe', '--receipts', receipts, '--observe-state', target]
    if ledger_name == 'outcomes':
        args += ['--outcomes', target]
    elif ledger_name == 'observations':
        args += ['--observations', target]
    elif ledger_name == 'directives':
        args += ['--directives', target]

    result, _ = run(*args)

    assert result.returncode == 1 and 'observe state' in result.stderr
    assert {name: path.read_bytes() for name, path in ledgers.items() if path.exists()} == before


@pytest.mark.parametrize('artifact', ('state', 'lock', 'call index', 'call index WAL', 'call index shared memory', 'call index journal'))
def test_observe_state_refuses_ledger_aliases_for_all_sidecars(setup, tmp_path, artifact):
    record(setup, 'one')
    module = lib_module('observe')
    state = tmp_path / 'observe.json'
    call_index = module.call_index_path(state)
    artifacts = {
        'state': state,
        'lock': state.with_name(state.name + '.lock'),
        'call index': call_index,
        'call index WAL': Path(str(call_index) + '-wal'),
        'call index shared memory': Path(str(call_index) + '-shm'),
        'call index journal': Path(str(call_index) + '-journal'),
    }
    ledger = artifacts[artifact]
    ledger.write_bytes(b'ledger sentinel\n')
    before = ledger.read_bytes()

    result, _ = run('observe', '--receipts', setup[3], '--observations', ledger, '--observe-state', state)

    assert result.returncode == 1 and 'observe state' in result.stderr
    assert ledger.read_bytes() == before
    assert not state.exists() or state == ledger
    assert not call_index.exists() or call_index == ledger
    state_lock = state.with_name(state.name + '.lock')
    assert not state_lock.exists() or state_lock == ledger


@pytest.mark.parametrize('input_option', ('--db', '--destinations'))
def test_observe_state_refuses_input_file_aliases_without_writing(setup, tmp_path, input_option):
    if input_option == '--db':
        target = t3_world(setup, tmp_path)
    else:
        record(setup, 'one')
        target = tmp_path / 'destinations.json'
    if input_option == '--destinations':
        target.write_bytes(b'{"sentinel":true}\n')
    before = target.read_bytes()

    result, _ = run('observe', '--receipts', setup[3], input_option, target, '--observe-state', target)

    assert result.returncode == 1 and input_option in result.stderr and 'observe state' in result.stderr
    assert target.read_bytes() == before


@pytest.mark.parametrize('use_symlink', (False, True))
def test_observe_state_refuses_t3_sidecar_alias_without_writing(setup, tmp_path, use_symlink):
    db = t3_world(setup, tmp_path)
    db_arg = db
    if use_symlink:
        db_arg = tmp_path / 'db-link.sqlite'
        db_arg.symlink_to(db)
    state = Path(str(db) + '-wal')
    before = db.read_bytes()

    result, _ = run('observe', '--receipts', setup[3], '--db', db_arg, '--observe-state', state)

    assert result.returncode == 1 and '--db' in result.stderr and 'observe state' in result.stderr
    assert db.read_bytes() == before and not state.exists()


@pytest.mark.parametrize('input_kind', ('directives', 'destinations', 'db', 'symlinked_db_wal'))
def test_observe_ledger_refuses_input_aliases_without_writing(setup, tmp_path, input_kind):
    if input_kind in ('db', 'symlinked_db_wal'):
        db = t3_world(setup, tmp_path)
        mode = db.stat().st_mode
        if input_kind == 'db':
            ledger = db
            args = ['--db', db]
        else:
            db_alias = tmp_path / 'db-link.sqlite'
            db_alias.symlink_to(db)
            ledger = Path(str(db) + '-wal')
            args = ['--db', db_alias]
    else:
        record(setup, 'one')
        ledger = tmp_path / f'{input_kind}.jsonl'
        if input_kind == 'destinations':
            ledger.write_bytes(b'{"sentinel":true}\n')
        args = ['--directives' if input_kind == 'directives' else '--destinations', ledger]
    before = ledger.read_bytes() if ledger.exists() else None

    result, _ = run('observe', '--receipts', setup[3], '--observations', ledger, *args)

    assert result.returncode == 1 and '--observations' in result.stderr
    assert (ledger.read_bytes() if ledger.exists() else None) == before
    if input_kind in ('db', 'symlinked_db_wal'):
        assert db.stat().st_mode == mode


def test_now_is_refused(setup):
    record(setup, 'one')
    result, _ = observe(setup, '--now', '2026-10-10T00:00:00Z')
    assert result.returncode == 1 and '--now is not allowed for observe' in result.stderr
    assert not paths(setup)[1].exists()


def test_concurrent_runs_append_each_fact_once(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: observe(setup, '--db', db), range(4)))
    assert all(r[0].returncode == 0 for r in results), [r[0].stderr for r in results]
    assert sum(new_total(r[1]) for r in results) == len(facts(setup))
    ids = [r['observation_id'] for r in facts(setup)]
    assert len(ids) == len(set(ids)) and len([r for r in rows(setup) if r['kind'] == 'source_run']) == 4


def test_a_tampered_row_fails_closed_for_observe_and_degrades_audit(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    ok(setup, '--db', db)
    lines = paths(setup)[1].read_text().splitlines()
    first = json.loads(lines[0])
    first['fact']['status'] = 'accepted'
    paths(setup)[1].write_text('\n'.join([json.dumps(first)] + lines[1:]) + '\n')
    result, _ = observe(setup, '--db', db)
    assert result.returncode == 1 and 'observation_id does not match' in result.stderr
    result, audit = run('audit', '--db', db, '--receipts', setup[3])
    assert result.returncode == 0 and audit['observations']['state'] == 'unreadable'
    assert 'observation_id does not match' in audit['observations']['reason']


# ---- audit and calibrate ----------------------------------------------------------------------------------------------

def test_audit_reports_coverage_without_changing_any_other_key(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    decision_row(setup, 'd1', 'worker')
    decision_row(setup, 'd2', 'worker')
    close(setup, 'one', 'c1', outcome='accepted')
    cfg, _ = dest_world(setup, tmp_path, reply({'d1': RECORD, 'd2': {'found': False}}), ids=())
    _, before = run('audit', '--db', db, '--receipts', setup[3])
    assert before['observations']['state'] == 'absent'
    ok(setup, '--db', db, '--destinations', cfg)
    result, after = run('audit', '--db', db, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert {k: v for k, v in after.items() if k != 'observations'} == {k: v for k, v in before.items() if k != 'observations'}
    section = after['observations']
    assert section['state'] == 'observed'
    assert section['coverage']['t3'] == {'decisions_with_observation': 1, 'decisions_in_scope': 1}
    assert section['coverage']['destination:worker'] == {'decisions_with_observation': 2, 'decisions_in_scope': 2}
    assert 'git' not in section['coverage']
    assert section['latest_source_runs']['t3']['state'] == 'ok' and section['latest_source_runs']['destination:worker']['state'] == 'ok'
    assert section['fact_type_counts']['t3_run_status'] == 1 and section['fact_type_counts']['destination_event'] == 2
    assert section['labels']['completed'] == 'completed ≠ accepted'
    assert after['outcomes']['outcome_distribution'] == before['outcomes']['outcome_distribution'] == {'closed': {'accepted': 1}, 'unclosed_or_unreadable': 0}
    assert not any(r['kind'] == 'close' for r in rows(setup))  # observe never writes a close
    assert paths(setup)[0].read_text().count('\n') == 1


def test_coverage_counts_decisions_not_facts_and_reflects_latest_failure(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    decision_row(setup, 'two')
    ok(setup, '--db', db)
    ok(setup)  # no database this time: the latest t3 state is unavailable
    _, audit = run('audit', '--db', db, '--receipts', setup[3])
    assert audit['observations']['coverage']['t3'] == {'decisions_with_observation': 1, 'decisions_in_scope': 2}
    assert audit['observations']['latest_source_runs']['t3']['state'] == 'unavailable'
    assert audit['observations']['latest_source_runs']['t3']['reason'] == 'no_db_supplied'


# ---- old CLI compatibility --------------------------------------------------------------------------------------------

def test_the_previous_cli_still_works_on_a_state_dir_with_observations(setup, tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    archive = subprocess.run(['git', '-C', REPO, 'archive', BASELINE], capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', old], input=archive, check=True)
    tool = old / 'tools/model-policy-ops/model-policy-ops'
    if not tool.exists():
        pytest.skip('baseline archive unavailable')

    def old_cli(*args):
        env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / 'xdg'))  # no real destinations, accounts or user policy leaks in
        result = subprocess.run([str(tool), *map(str, args)], capture_output=True, text=True, env=env)
        return result, json.loads(result.stdout) if result.returncode == 0 and result.stdout.startswith('{') else None

    db = t3_world(setup, tmp_path)
    ok(setup, '--db', db)
    assert paths(setup)[1].exists()
    _, available, quota, receipts = setup
    common = ('--available', available, '--quota', quota, '--receipts', receipts)
    assert old_cli('check')[0].returncode == 0
    result, resolved = old_cli('resolve', 'implement.standard', *common, '--record', '--decision-id', 'old-one', '--parent-model', 'pm',
                               '--parent-provider-instance', 'pp', '--parent-thread', 'pt')
    assert result.returncode == 0, result.stderr
    result, routed = old_cli('route', 'implement.standard', *common)
    assert result.returncode == 0, result.stderr
    result, closed = old_cli('close', 'old-one', '--receipts', receipts, '--close-id', 'oc1', '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none')
    assert result.returncode == 0, result.stderr
    result, _ = old_cli('followup', 'old-one', '--receipts', receipts, '--followup-id', 'of1', '--finding', 'no_rework_found', '--checked-scope', 'x')
    assert result.returncode == 0, result.stderr
    result, audit = old_cli('audit', '--db', db, '--receipts', receipts)
    assert result.returncode == 0, result.stderr
    assert 'observations' not in audit and audit['counts']['call_attempts'] == 1
    assert any(r['close_id'] == 'oc1' for r in map(json.loads, paths(setup)[0].read_text().splitlines()))
    assert stat.S_IMODE(paths(setup)[1].stat().st_mode) == 0o600


@pytest.mark.parametrize('target', ['repo', '.git', 'HEAD'])
def test_a_permission_error_on_the_repository_probe_is_operational_not_a_healthy_skip(setup, repo_world, monkeypatch, target):
    """EACCES while probing the repo, its .git or a bare HEAD is filesystem_unreadable, never repo_missing or not_a_repository_root."""
    observe_module = lib_module('observe')
    repo_path = repo_world['repo']
    if target == 'HEAD':
        repo_path = repo_world['repo'].parent / 'probe-bare.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(repo_world['repo']), str(repo_path)], check=True, capture_output=True)
    repo = str(repo_path)
    real_lstat = os.lstat
    blocked = repo if target == 'repo' else os.path.join(repo, target)

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == blocked:
            raise PermissionError(13, 'Permission denied', os.fspath(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(observe_module.os, 'lstat', lstat)
    source = collect_one(repo_path, repo_world['sha'])
    assert source['state'] == 'error' and source['failed'] == {'filesystem_unreadable': 1} and source['skipped'] == {}


def test_a_genuinely_absent_repository_is_still_a_skip(setup, repo_world):
    source = collect_one(repo_world['repo'] / 'does-not-exist', repo_world['sha'])
    assert source['state'] == 'ok' and source['skipped'] == {'repo_missing': 1} and source['failed'] == {}


def child_thread_world(setup, tmp_path, pull_requests, messages=3, raw=None):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    c.execute(f'alter table {PREFIX}runs add column thread_id text')
    c.execute(f'create index {PREFIX}runs_thread_id_idx on {PREFIX}runs(thread_id)')
    c.execute(f"update {PREFIX}runs set thread_id='child-thread' where run_id='child'")
    c.execute(f'create table {PREFIX}threads (thread_id text, payload_json text, updated_at text)')
    c.execute(f'create index {PREFIX}threads_thread_id_idx on {PREFIX}threads(thread_id)')
    payload = raw if raw is not None else json.dumps({'pullRequests': pull_requests, 'title': SECRET})
    c.execute(f'insert into {PREFIX}threads values (?,?,?)', ('child-thread', payload, '2026-10-08T01:00:00Z'))
    for i in range(messages):
        c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  (f'msg{i}', 'child-thread', 'child', f'2026-10-08T00:0{i}:00Z', 'completed', 'user_message', json.dumps({'text': SECRET})))
    c.commit()
    c.close()
    return db


def set_thread(db, payload, updated_at):
    c = sqlite3.connect(db)
    c.execute(f"update {PREFIX}threads set payload_json=?, updated_at=? where thread_id='child-thread'", (payload, updated_at))
    c.commit()
    c.close()


def pr(state, number=7, repo='owner/repo', **snap):
    return {'repository': repo, 'number': number, 'url': f'https://github.com/{repo}/pull/{number}',
            'snapshot': {'state': state, 'title': SECRET, 'syncedAt': '2026-10-08T01:00:00Z', **snap}}


def test_child_thread_followups_and_pull_requests_are_counts_and_enums_only(setup, tmp_path):
    db = child_thread_world(setup, tmp_path, [pr('merged', mergedAt='2026-10-08T00:50:00Z'), pr('weird', 8), {'repository': 'bad repo', 'number': 9}])
    ok(setup, '--db', db)
    followups = facts(setup, 't3', 't3_child_followups')
    assert [f['fact'] for f in followups] == [{'type': 't3_child_followups', 'child_thread_id': 'child-thread',
                                              'user_messages': 3, 'followup_messages': 2}]
    assert followups[0]['source_timestamps'] == {'last_user_message_at': '2026-10-08T00:02:00+00:00'}
    prs = facts(setup, 't3', 't3_child_pull_requests')
    assert prs[0]['fact']['pull_requests'] == [
        {'repository': 'owner/repo', 'number': 7, 'state': 'merged', 'merged_at': '2026-10-08T00:50:00+00:00',
         'synced_at': '2026-10-08T01:00:00+00:00'},
        {'repository': 'owner/repo', 'number': 8, 'state': 'redacted', 'synced_at': '2026-10-08T01:00:00+00:00'}]
    assert prs[0]['fact']['dropped'] == 1 and prs[0]['fact']['state'] == 'listed' and prs[0]['fact']['shared_with_decisions'] == 1
    assert prs[0]['source_timestamps'] == {'thread_updated_at': '2026-10-08T01:00:00+00:00'}
    assert SECRET not in paths(setup)[1].read_text(), 'no titles or message text are copied'
    again = ok(setup, '--db', db)
    assert again['sources']['t3']['new_observations'] == 0, 'unchanged facts append nothing'


def test_a_removed_or_unreadable_link_replaces_the_merged_snapshot(setup, tmp_path):
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import derived_outcomes
    db = child_thread_world(setup, tmp_path, [pr('merged')])
    ok(setup, '--db', db)
    receipts = [json.loads(line) for line in setup[3].read_text().splitlines()]
    assert derived_outcomes.derive(['one'], receipts, rows(setup))['one']['outcome'] == 'accepted'
    set_thread(db, json.dumps({'pullRequests': []}), '2026-10-08T02:00:00Z')
    ok(setup, '--db', db)
    assert derived_outcomes.derive(['one'], receipts, rows(setup))['one']['outcome'] == 'unknown', 'the link was removed'
    set_thread(db, 'not json', '2026-10-08T03:00:00Z')
    ok(setup, '--db', db)
    assert derived_outcomes.derive(['one'], receipts, rows(setup))['one']['reason'] == 'pull_requests_unreadable'


@pytest.mark.parametrize('raw', [json.dumps({'pullRequests': [1, 'x', None]}), json.dumps({'pullRequests': [{'repository': 'o/r', 'number': 3, 'snapshot': 'text'}]}),
                                 json.dumps({'pullRequests': [pr('open', syncedAt=5), pr('open', 2, syncedAt='2026-10-08T00:00:00Z')]}),
                                 json.dumps({'pullRequests': {'not': 'a list'}})])
def test_malformed_pull_request_shapes_never_abort_collection(setup, tmp_path, raw):
    db = child_thread_world(setup, tmp_path, None, raw=raw)
    row = ok(setup, '--db', db)
    assert row['sources']['t3']['state'] == 'ok'
    assert facts(setup, 't3', 't3_run_status'), 'other facts are still recorded'
    assert len(facts(setup, 't3', 't3_child_pull_requests')) == 1


def test_a_thread_shared_by_two_decisions_is_flagged(setup, tmp_path):
    db = child_thread_world(setup, tmp_path, [pr('merged')])
    c = sqlite3.connect(db)
    c.execute(f'insert into {PREFIX}runs (run_id, provider_instance_id, status, payload_json, requested_at, thread_id) values (?,?,?,?,?,?)',
              ('child2', 'claude-one', 'completed', '{}', '2026-10-08T00:10:00Z', 'child-thread'))
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  [(f'filler-{i}', 'parent-thread', 'parent', '2026-10-08T00:09:00Z', 'completed', 'other', '{"toolName":"other"}')
                   for i in range(501)])
    call = json.loads(c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0])
    call['input']['clientRequestId'] = 'other-decision'
    call['output']['structuredContent']['childRunId'] = 'child2'
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)', ('call2', 'parent-thread', 'parent', '2026-10-08T00:10:00Z',
                                                                       'completed', 'dynamic_tool', json.dumps(call)))
    c.commit()
    c.close()
    ok(setup, '--db', db)
    assert facts(setup, 't3', 't3_child_pull_requests')[0]['fact']['shared_with_decisions'] == 2


def test_shared_thread_reports_unknown_owner_outside_initial_window(setup, tmp_path, monkeypatch):
    db = child_thread_world(setup, tmp_path, [pr('merged')])
    _, receipt = record(setup, 'two', 'implement.standard', '--purpose', 'execution')
    c = sqlite3.connect(db)
    c.execute(f'insert into {PREFIX}runs values (?,?,?,?,?,?)',
              ('child2', 'claude-one', 'completed', json.dumps({'modelSelection': {'model': receipt['target']['model']}}),
               '2026-10-08T00:10:00Z', 'child-thread'))
    c.executemany(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
                  [(f'filler-{i}', 'parent-thread', 'parent', '2026-10-08T00:09:00Z', 'completed', 'other', '{}')
                   for i in range(11)])
    call = json.loads(c.execute(f"select payload_json from {PREFIX}turn_items where turn_item_id='call'").fetchone()[0])
    call['input']['clientRequestId'] = 'two'
    call['input']['target'] = receipt['target']
    call['output']['structuredContent']['childRunId'] = 'child2'
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)',
              ('call2', 'parent-thread', 'parent', '2026-10-08T00:10:00Z', 'completed', 'dynamic_tool', json.dumps(call)))
    c.commit()
    c.close()
    module = lib_module('observe')
    monkeypatch.setattr(module, 'OBSERVE_INITIAL_ROWS', 10)
    receipts = [json.loads(line) for line in setup[3].read_text().splitlines()]
    targets = {row['decision_id']: row['target'] for row in receipts}
    result = module.incremental_t3(db, {'one', 'two'}, targets, tmp_path / 'state' / 'observe.json', time.monotonic() + 30)
    fact = next(row for row in result['observations'] if row['fact_type'] == 't3_child_pull_requests')
    assert fact['fact']['shared_with_decisions'] is None
    assert fact['unknown']['shared_with_decisions'] == 'outside_observed_window'


def test_unowned_followup_run_does_not_change_legacy_sharing_count(setup, tmp_path):
    db_path = child_thread_world(setup, tmp_path, [pr('merged')])
    c = sqlite3.connect(db_path)
    c.execute(f'insert into {PREFIX}runs values (?,?,?,?,?,?)',
              ('followup-run', 'claude-one', 'completed', '{}', '2026-10-08T02:00:00Z', 'child-thread'))
    c.commit()
    c.close()
    module = lib_module('observe')
    call_db, _, _ = module.open_call_index(tmp_path / 'state' / 'observe.json')
    module.remember_calls(call_db, [{'_source_rowid': 1, 'call_id': 'call', 'child_run_id': 'child', 'decision_id': 'one'}])
    db = sqlite3.connect(db_path)
    found = module.pull_request_facts(db, 'child-thread', 'child', 'one', {'run_id': 'child'}, {}, call_db=call_db)
    db.close()
    call_db.close()
    assert found[0]['fact']['shared_with_decisions'] == 1
    assert 'shared_with_decisions' not in found[0]['unknown']


def test_incremental_thread_owner_count_includes_unreceipted_calls(setup, tmp_path):
    db_path = child_thread_world(setup, tmp_path, [pr('merged')])
    c = sqlite3.connect(db_path)
    c.execute(f'insert into {PREFIX}runs values (?,?,?,?,?,?)',
              ('child2', 'claude-one', 'completed', '{}', '2026-10-08T02:00:00Z', 'child-thread'))
    c.commit()
    c.close()
    module = lib_module('observe')
    call_db, _, _ = module.open_call_index(tmp_path / 'state' / 'observe.json')
    module.remember_calls(call_db, [
        {'_source_rowid': 1, 'call_id': 'call1', 'child_run_id': 'child', 'decision_id': 'one'},
        {'_source_rowid': 2, 'call_id': 'call2', 'child_run_id': 'child2', 'decision_id': 'outside'},
    ])
    db = sqlite3.connect(db_path)
    found = module.pull_request_facts(db, 'child-thread', 'child', 'one', {'run_id': 'child'}, {},
                                      call_db=call_db)
    db.close()
    call_db.close()
    assert found[0]['fact']['shared_with_decisions'] == 2


def test_thread_run_paging_keeps_tied_and_null_ordinal_owners(setup, tmp_path):
    db_path = child_thread_world(setup, tmp_path, [pr('merged')])
    c = sqlite3.connect(db_path)
    c.execute(f'alter table {PREFIX}runs add column ordinal integer')
    c.execute(f'create index {PREFIX}runs_thread_ordinal_idx on {PREFIX}runs(thread_id,ordinal)')
    c.execute(f"update {PREFIX}runs set ordinal=1 where thread_id='child-thread'")
    c.executemany(f'insert into {PREFIX}runs(run_id,provider_instance_id,status,payload_json,requested_at,thread_id,ordinal) '
                  'values (?,?,?,?,?,?,?)',
                  [(f'run-{i}', 'claude-one', 'completed', '{}', '2026-10-08T02:00:00Z', 'child-thread', 1)
                   for i in range(501)])
    c.execute(f'insert into {PREFIX}runs(run_id,provider_instance_id,status,payload_json,requested_at,thread_id,ordinal) '
              "values ('null-run','claude-one','completed','{}','2026-10-08T02:00:00Z','child-thread',NULL)")
    c.commit()
    c.close()
    module = lib_module('observe')
    call_db, _, _ = module.open_call_index(tmp_path / 'state' / 'observe.json')
    module.remember_calls(call_db, [
        {'_source_rowid': 1, 'call_id': 'call1', 'child_run_id': 'child', 'decision_id': 'one'},
        {'_source_rowid': 2, 'call_id': 'call2', 'child_run_id': 'run-500', 'decision_id': 'two'},
        {'_source_rowid': 3, 'call_id': 'call3', 'child_run_id': 'null-run', 'decision_id': 'three'},
    ])
    db = sqlite3.connect(db_path)
    assert module.indexed_thread_owners(db, call_db, 'child-thread', 'one') == (3, False)
    found = module.pull_request_facts(db, 'child-thread', 'child', 'one', {'run_id': 'child'},
                                      {'child': 'one', 'run-500': 'two', 'null-run': 'three'})
    db.close()
    call_db.close()
    assert found[0]['fact']['shared_with_decisions'] == 3


def test_partial_refresh_keeps_unsettled_update_inside_savepoint(setup, tmp_path):
    module = lib_module('observe')
    call_db, _, _ = module.open_call_index(tmp_path / 'state' / 'observe.json')
    call = {'_source_rowid': 7, 'call_id': 'call', 'child_run_id': 'child', 'decision_id': 'one'}
    module.remember_calls(call_db, [call])
    call_db.execute('SAVEPOINT refresh_row')
    module.mark_calls_unsettled(call_db, [call], commit=False)
    call_db.execute('RELEASE refresh_row')
    call_db.commit()
    assert call_db.execute('SELECT settled FROM known_calls WHERE source_rowid=7').fetchone() == (0,)
    call_db.close()


def test_partial_refresh_commits_facts_and_advances_its_cursor(setup, tmp_path, monkeypatch):
    db_path = t3_world(setup, tmp_path)
    module = lib_module('observe')
    state_path = tmp_path / 'state' / 'observe.json'
    target = json.loads(setup[3].read_text().splitlines()[0])['target']
    initial = module.incremental_t3(db_path, {'one'}, {'one': target}, state_path, time.monotonic() + 30)
    module.save_state(state_path, initial['_state'])
    call_db, _, _ = module.open_call_index(state_path)
    source_rowid = call_db.execute('SELECT source_rowid FROM known_calls').fetchone()[0]
    call_db.close()

    def partial_refresh(_db_path, _scope, _targets, calls_override=None, **_kwargs):
        call = calls_override[0]
        fact = module.store.make_observation('t3', call['call_id'], 'one', 't3_run_status', {'status': 'completed'})
        return module.result('partial', 'time_budget_exhausted', 1, [fact])

    monkeypatch.setattr(module, 'collect_t3', partial_refresh)
    resumed = module.incremental_t3(db_path, {'one'}, {'one': target}, state_path, time.monotonic() + 30)
    call_db, _, _ = module.open_call_index(state_path)
    settled = call_db.execute('SELECT settled FROM known_calls WHERE source_rowid=?', (source_rowid,)).fetchone()[0]
    call_db.close()
    assert any(row['fact_type'] == 't3_run_status' for row in resumed['observations'])
    assert resumed['incremental']['refresh_cursor'] == source_rowid
    assert settled == 0


def test_invalid_call_observation_id_uses_source_rowid_not_batch_position(setup, tmp_path):
    db_path = t3_world(setup, tmp_path)
    module = lib_module('observe')
    invalid = {'_source_rowid': 7, 'call_id': None, 'decision_id': 'one', 'timestamp': '2026-10-08T00:00:00Z',
               'status': 'completed', 'thread_id': None, 'child_run_id': None}
    leading = {'_source_rowid': 6, 'call_id': 'valid', 'decision_id': 'one', 'timestamp': '2026-10-08T00:00:00Z',
               'status': 'completed', 'thread_id': None, 'child_run_id': None}
    first = module.collect_t3(db_path, {'one'}, {}, calls_override=[invalid])
    second = module.collect_t3(db_path, {'one'}, {}, calls_override=[leading, invalid])
    first_row = next(row for row in first['observations'] if row['unknown'].get('call_id') == 'invalid_id')
    second_row = next(row for row in second['observations'] if row['unknown'].get('call_id') == 'invalid_id')
    assert first_row['observation_id'] == second_row['observation_id']


def test_detail_readers_keep_null_ordinal_rows(setup, tmp_path):
    db_path = child_thread_world(setup, tmp_path, [], messages=2)
    c = sqlite3.connect(db_path)
    c.execute(f'alter table {PREFIX}turn_items add column ordinal integer')
    c.execute(f"update {PREFIX}turn_items set ordinal=NULL where type='user_message'")
    c.execute(f'create index {PREFIX}turn_items_user_message_idx on {PREFIX}turn_items(thread_id,type,ordinal)')
    c.execute(f'update {PREFIX}run_attempts set attempt_ordinal=NULL')
    c.commit()
    c.close()
    module = lib_module('observe')
    db = sqlite3.connect(db_path)
    _, attempts, _ = module.run_details(db, 'child')
    facts = module.child_thread_facts(db, 'child', 'one', {'run_id': 'child'})
    usage = lib_module('delegation_audit').read_child_usage(db, 'child')
    db.close()
    assert len(attempts) == 1
    assert len(usage['attempts']) == 1 and usage['attempts'][0]['attempt_ordinal'] is None
    followups = next(row for row in facts if row['fact_type'] == 't3_child_followups')
    assert followups['fact']['user_messages'] == 2


def test_usage_reader_keeps_attempts_when_all_turn_indexes_are_absent(setup, tmp_path):
    db_path = t3_world(setup, tmp_path)
    c = sqlite3.connect(db_path)
    c.execute(f'DROP INDEX {PREFIX}provider_turns_thread_ordinal_idx')
    c.commit()
    c.close()
    module = lib_module('delegation_audit')
    db = sqlite3.connect(db_path)
    usage = module.read_child_usage(db, 'child', close_batches=False)
    db.close()
    assert usage['state'] == 'complete'
    assert usage['attempts'][0]['turns'][0]['provider_turn_id'] == 'turn0'


def test_audit_usage_falls_back_for_attempts_without_thread_ids(setup, tmp_path):
    _, receipt = record(setup)
    db_path = make_db(tmp_path, receipt, [('a1', COMPLETE)])
    c = sqlite3.connect(db_path)
    c.execute(f'create index {PREFIX}provider_turns_thread_id_idx on {PREFIX}provider_turns(thread_id)')
    c.execute(f'update {PREFIX}run_attempts set thread_id=NULL where attempt_id="a1"')
    c.commit()
    c.close()
    module = lib_module('delegation_audit')
    db = sqlite3.connect(db_path)
    usage = module.read_child_usage(db, 'child', close_batches=False)
    db.close()
    assert usage['state'] == 'complete'
    assert usage['attempts'][0]['turns'][0]['provider_turn_id'] == 'turn0'


def test_usage_attempt_order_keeps_null_ordinals_first(setup, tmp_path):
    _, receipt = record(setup)
    db_path = make_db(tmp_path, receipt, [('a1', COMPLETE), ('a2', COMPLETE)], extra_attempt=True)
    c = sqlite3.connect(db_path)
    c.execute(f'update {PREFIX}run_attempts set attempt_ordinal=NULL where attempt_id="a1"')
    c.commit()
    c.close()
    module = lib_module('delegation_audit')
    db = sqlite3.connect(db_path)
    usage = module.read_child_usage(db, 'child')
    db.close()
    assert [attempt['attempt_id'] for attempt in usage['attempts']] == ['a1', 'a2']


def test_completed_thread_snapshot_cursor_wraps(setup, tmp_path):
    db_path = t3_world(setup, tmp_path)
    module = lib_module('observe')
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    result = module.incremental_t3(db_path, {'one'}, {'one': receipt['target']}, tmp_path / 'state.json', time.monotonic() + 30)
    assert result['incremental']['thread_cursor'] == 0


def test_last_ledger_marker_handles_trimmed_tail_and_invalid_json(tmp_path):
    module = lib_module('observe')
    ledger = tmp_path / 'observations.jsonl'
    ledger.write_bytes(b'x' * (16 * 1024 * 1024 + 10) + b'\n{bad json}\n')
    assert module.read_last_ledger_line(ledger)['last_run_id'] is None


def test_audit_usage_does_not_apply_observe_read_slice(setup, tmp_path, monkeypatch):
    db_path = t3_world(setup, tmp_path)
    module = lib_module('delegation_audit')
    monkeypatch.setattr(module, 'USAGE_READ_SLICE_SECONDS', 0)
    db = sqlite3.connect(db_path)
    usage = module.read_child_usage(db, 'child', close_batches=False)
    db.close()
    assert usage['state'] == 'complete'


def test_first_incremental_run_does_not_report_a_reset(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    ledger = tmp_path / 'existing.jsonl'
    ledger.write_text('{}\n')
    marker = module.read_last_ledger_line(ledger)
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    result = module.incremental_t3(db, {'one'}, {'one': receipt['target']}, tmp_path / 'state.json',
                                   time.monotonic() + 30, ledger_marker=marker)
    assert result['incremental']['initial_window'] == 'last 50000 turn_items rowids'
    assert result['incremental']['reset_reason'] is None


def test_last_ledger_marker_tolerates_a_row_larger_than_one_megabyte(tmp_path):
    module = lib_module('observe')
    ledger = tmp_path / 'observations.jsonl'
    ledger.write_text(json.dumps({'kind': 'source_run', 'run_id': 'large', 'padding': 'x' * (1024 * 1024 + 10)}) + '\n')
    assert module.read_last_ledger_line(ledger)['last_run_id'] == 'large'


def test_stuck_unsettled_refresh_cursor_completes_a_cycle(setup, tmp_path, monkeypatch):
    db = t3_world(setup, tmp_path)
    module = lib_module('observe')
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    state_path = tmp_path / 'observe.json'
    initial = module.incremental_t3(db, {'one'}, {'one': receipt['target']}, state_path, time.monotonic() + 30)
    initial['_state']['rowid'] = initial['incremental']['snapshot_high_water']
    module.save_state(state_path, initial['_state'])
    call_db, _, _ = module.open_call_index(state_path)
    call_db.execute('DELETE FROM known_calls')
    call_db.executemany('INSERT INTO known_calls(source_rowid,turn_item_id,child_run_id,decision_id,settled) VALUES(?,?,?,?,0)',
                        [(rowid, f'call-{rowid}', f'child-{rowid}', 'one') for rowid in range(1, 252)])
    call_db.commit()
    call_db.close()

    def read_batch(db_path, after, through, limit, rowids=None, scope_ids=None, deadline=None, **kwargs):
        rowid = rowids[0]
        call = {'_source_rowid': rowid, 'call_id': f'call-{rowid}', 'child_run_id': f'child-{rowid}',
                'decision_id': 'one', 'status': 'running', 'child_status': 'running'}
        return after, [call], 1, [rowid], None

    monkeypatch.setattr(module, 'read_t3_rowid_batch', read_batch)
    monkeypatch.setattr(module, 'collect_t3', lambda *args, **kwargs: {'state': 'ok', 'observations': [], 'subjects_checked': 0})
    first = module.incremental_t3(db, {'one'}, {'one': receipt['target']}, state_path, time.monotonic() + 30)
    module.save_state(state_path, first['_state'])
    second = module.incremental_t3(db, {'one'}, {'one': receipt['target']}, state_path, time.monotonic() + 30)
    assert first['reason'] == 'refresh_pending'
    assert second['reason'] is None, second['incremental']
    assert second['incremental']['refresh_unsettled_cursor'] == 0


def test_transient_usage_slice_keeps_t3_source_pending(setup, tmp_path, monkeypatch):
    db_path = t3_world(setup, tmp_path)
    module = lib_module('observe')
    from delegation_audit import usage_unknown
    monkeypatch.setattr(module, 'read_child_usage_for_call', lambda *args, **kwargs: usage_unknown('unavailable', 'read_slice_exceeded'))
    _, calls, _, _, _ = module.read_t3_rowid_batch(db_path, 0, 100, 500)
    call = calls[0] | {'status': 'completed', 'child_status': 'completed'}
    collected = module.collect_t3(db_path, {'one'}, {}, calls_override=[call])
    assert collected['state'] == 'partial'
    assert collected['reason'] == 'usage_refresh_pending'


def test_default_observe_state_is_keyed_by_ledger_and_respects_xdg_state_home(tmp_path, monkeypatch):
    module = lib_module('observe')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    first = module.default_observe_state(tmp_path / 'one.jsonl')
    second = module.default_observe_state(tmp_path / 'two.jsonl')
    assert first.parent == tmp_path / 'state/model-policy'
    assert first != second


def test_terminal_failure_message_matches_sqlite_json_text_semantics():
    module = lib_module('observe')
    import json
    empty = module.terminal_failure((1, 'item', None, json.dumps({'failure': {'message': ''}, 'title': 'rate limit reached'}), 'failed'))
    object_message = module.terminal_failure((1, 'item', None, json.dumps({'failure': {'message': {'text': 'rate limit reached'}}}), 'failed'))
    assert empty[-1] == ''
    assert object_message[-1] == '{"text":"rate limit reached"}'
    assert module.classify_failure(empty[-1]) == 'unknown'
    assert module.classify_failure(object_message[-1]) == 'rate_limit'


def test_thread_followup_paging_keeps_duplicate_boundary_ordinals(setup, tmp_path):
    db = t3_world(setup, tmp_path)
    c = sqlite3.connect(db)
    c.execute(f'alter table {PREFIX}turn_items add column ordinal integer')
    c.execute(f'create table {PREFIX}threads (thread_id text primary key,payload_json text,updated_at text)')
    c.execute(f'alter table {PREFIX}runs add column thread_id text')
    c.execute(f"update {PREFIX}runs set thread_id='child-thread' where run_id='child'")
    c.execute(f"insert into {PREFIX}threads values ('child-thread','{{}}',NULL)")
    c.execute(f'create index {PREFIX}turn_items_thread_id_type_ordinal_idx on {PREFIX}turn_items(thread_id,type,ordinal)')
    c.executemany(f'insert into {PREFIX}turn_items(turn_item_id,thread_id,run_id,updated_at,status,type,payload_json,ordinal) values (?,?,?,?,?,?,?,?)',
                  [(f'user-{n:03}', 'child-thread', 'child', f'2026-10-08T00:{n // 60:02}:{n % 60:02}Z',
                    'completed', 'user_message', '{}', 7) for n in range(501)])
    c.commit()
    c.close()
    module = lib_module('observe')
    c = sqlite3.connect(db)
    facts = module.child_thread_facts(c, 'child', 'one', {'run_id': 'child'}, include_thread_snapshot=False)
    c.close()
    followup = next(row for row in facts if row['fact_type'] == 't3_child_followups')
    assert followup['fact']['user_messages'] == 501


def test_provider_turn_paging_seeks_duplicate_non_null_ordinals_by_rowid(tmp_path):
    module = lib_module('delegation_audit')
    db = sqlite3.connect(tmp_path / 'turn-pages.sqlite')
    db.execute('CREATE TABLE turns (provider_turn_id TEXT, run_attempt_id TEXT, thread_id TEXT, provider_thread_id TEXT, ordinal INTEGER NOT NULL, status TEXT)')
    db.execute('CREATE INDEX provider_turns_thread_idx ON turns(thread_id)')
    db.executemany('INSERT INTO turns VALUES (?,?,?,?,?,?)',
                   [(f'turn-{n:03}', 'attempt', 'thread', 'provider-thread', 7, 'completed') for n in range(501)])
    pages = list(module.paged_turn_rows(db, 'SELECT provider_turn_id,run_attempt_id,ordinal,status,rowid FROM turns',
                                        'WHERE thread_id=? AND run_attempt_id IN (?)', ('thread', 'attempt'),
                                        False, time.monotonic() + 10))
    assert [len(page) for page in pages] == [500, 1]
    assert len({row[0] for page in pages for row in page}) == 501
    plan = db.execute('EXPLAIN QUERY PLAN SELECT provider_turn_id,run_attempt_id,ordinal,status,rowid FROM turns '
                      'WHERE thread_id=? AND run_attempt_id IN (?) AND rowid>? ORDER BY rowid LIMIT 500',
                      ('thread', 'attempt', 0)).fetchall()
    assert any('provider_turns_thread_idx' in row[3] and 'rowid>?' in row[3] for row in plan)
    assert not any('TEMP B-TREE' in row[3] for row in plan)
    db.close()


def test_usage_reader_keeps_attempt_linked_turns_and_null_ordinals(setup, tmp_path):
    db_path = t3_world(setup, tmp_path)
    c = sqlite3.connect(db_path)
    c.execute(f'create index {PREFIX}provider_turns_thread_id_idx on {PREFIX}provider_turns(thread_id,ordinal)')
    c.execute(f"update {PREFIX}run_attempts set provider_turn_id='turn-null' where attempt_id='a1'")
    payload = json.dumps({'turnTokenUsage': COMPLETE})
    c.execute(f'insert into {PREFIX}provider_turns values (?,?,?,?,?,?,?,?,?,?)',
              ('turn-null', 't', 'other-thread', 'node', 'a1', None, 'completed', None, None, payload))
    c.execute(f'insert into {PREFIX}provider_turns values (?,?,?,?,?,?,?,?,?,?)',
              ('turn-other', 't', 'other-thread', 'node', 'a1', 2, 'completed', None, None, payload))
    c.executemany(f'insert into {PREFIX}provider_turns values (?,?,?,?,?,?,?,?,?,?)',
                  [(f'turn-extra-{i:03}', 't', 't', 'node', 'a1', None, 'completed', None, None, payload)
                   for i in range(501)])
    c.commit()
    c.close()
    module = lib_module('delegation_audit')
    db = sqlite3.connect(db_path)
    assert not module.has_index_prefix(db, PREFIX + 'provider_turns', 'run_attempt_id')
    usage = module.read_child_usage(db, 'child', close_batches=True)
    db.close()
    turns = usage['attempts'][0]['turns']
    ids = {turn['provider_turn_id'] for turn in turns}
    assert {'turn0', 'turn-null', 'turn-other'} <= ids
    assert len(ids) == 504 and 'turn-extra-500' in ids
    assert next(turn for turn in turns if turn['provider_turn_id'] == 'turn-null')['ordinal'] is None


def test_usage_read_slice_exits_with_unknown_and_releases_transaction(setup, tmp_path, monkeypatch):
    db_path = t3_world(setup, tmp_path)
    module = lib_module('delegation_audit')
    db = sqlite3.connect(db_path)
    db.execute('BEGIN')
    monkeypatch.setattr(module, 'usage_page', lambda *_args, **_kwargs: (_ for _ in ()).throw(module.UsageReadSliceExceeded()))

    usage = module.read_child_usage(db, 'child', close_batches=True)

    assert usage['state'] == 'unavailable'
    assert usage['reason'] == 'read_slice_exceeded'
    assert not db.in_transaction
    db.close()


def test_usage_reader_marks_unindexable_attempt_links_unknown(setup, tmp_path):
    db_path = t3_world(setup, tmp_path)
    c = sqlite3.connect(db_path)
    c.execute("update orchestration_v2_projection_run_attempts set thread_id=NULL,provider_thread_id=NULL,provider_turn_id='turn0' where attempt_id='a1'")
    c.commit()
    c.close()
    module = lib_module('delegation_audit')
    db = sqlite3.connect(db_path)

    usage = module.read_child_usage(db, 'child', close_batches=True)

    db.close()
    assert usage['state'] == 'unavailable'
    assert usage['reason'] == 'attempt_thread_unavailable'


def test_a_thread_without_pull_requests_records_an_empty_snapshot(setup, tmp_path):
    db = child_thread_world(setup, tmp_path, [], messages=1)
    ok(setup, '--db', db)
    assert facts(setup, 't3', 't3_child_followups')[0]['fact']['followup_messages'] == 0
    assert facts(setup, 't3', 't3_child_pull_requests')[0]['fact']['pull_requests'] == []


def test_followup_timestamp_uses_maximum_across_all_pages(setup, tmp_path):
    db_path = child_thread_world(setup, tmp_path, [], messages=501)
    c = sqlite3.connect(db_path)
    c.execute(f'alter table {PREFIX}turn_items add column ordinal integer')
    c.execute(f'update {PREFIX}turn_items set ordinal=rowid')
    c.execute(f'create index {PREFIX}turn_items_user_message_idx on {PREFIX}turn_items(thread_id,type,ordinal)')
    c.execute(f"update {PREFIX}turn_items set updated_at='2999-01-01T00:00:00Z' where turn_item_id='msg0'")
    c.commit()
    c.close()
    module = lib_module('observe')
    db = sqlite3.connect(db_path)
    found = module.child_thread_facts(db, 'child', 'one', {'run_id': 'child'}, include_thread_snapshot=False)
    db.close()
    followup = next(row for row in found if row['fact_type'] == 't3_child_followups')
    assert followup['source_timestamps']['last_user_message_at'] == '2999-01-01T00:00:00+00:00'
