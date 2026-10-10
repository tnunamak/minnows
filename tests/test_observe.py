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
from datetime import datetime, timedelta, timezone

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
    return run('observe', '--receipts', setup[3], *extra, **kwargs)


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
    before = db.read_bytes()
    row = ok(setup, '--db', db)
    assert db.read_bytes() == before
    assert row['sources']['t3'] | {'new_observations': 0} == {'state': 'ok', 'reason': None, 'subjects_checked': 1, 'new_observations': 0}
    assert set(row['sources']) == {'git', 't3'} and row['sources']['git']['subjects_checked'] == 0
    assert row['labels']['completed'] == 'completed ≠ accepted' and row['labels']['consumed'] == 'consumed ≠ correct'
    assert row['labels']['tokens'] == 'tokens ≠ subscription cost'
    by_type = {}
    for r in facts(setup, 't3'):
        by_type.setdefault(r['fact_type'], []).append(r)
        assert r['decision_id'] == 'one' and r['source_ids'] and r['observation_id'] and r['run_id'] == row['run_id']
    assert set(by_type) == {'t3_delegate_call', 't3_run_status', 't3_requested_config', 't3_attempt_status', 't3_terminal_failure', 't3_turn_usage'}
    config_row = by_type['t3_requested_config'][0]
    assert config_row['fact']['requested'] == {'provider_instance': 'claude-one', 'model': 'm', 'effort': 'medium',
                                               'options': {'effort': 'medium'}}
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

RECORD = {'found': True, 'status': 'completed', 'task_kind': 'review', 'source': 'agent', 'created_at': '2026-10-10T13:00:00+00:00',
          'cancelled_at': None, 'cancelled_reason': None,
          'reports': [{'outcome': 'done', 'created_at': '2026-10-10T13:30:00+00:00', 'receipt_id': 'rcpt-1', 'body': SECRET}],
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
    assert found['destination_assignment']['fact'] == {'type': 'destination_assignment', 'found': True, 'status': 'completed',
                                                       'task_kind': 'review', 'source': 'agent', 'cancelled_reason': None}
    assert found['destination_assignment']['source_timestamps']['created_at'] == '2026-10-10T13:00:00+00:00'
    assert found['destination_report']['fact']['outcome'] == 'done' and found['destination_report']['source_ids']['receipt_id'] == 'rcpt-1'
    events = facts(setup, 'destination:worker', 'destination_event')
    assert [e['fact']['reason'] for e in events] == ['new', 'redacted']
    assert events[0]['fact']['actor_kind'] == 'owner' and events[0]['source_ids']['request_id'] == 'd1'
    assert SECRET not in paths(setup)[1].read_text()
    assert new_total(ok(setup, '--destinations', cfg)) == 0


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
    assert result.returncode == 1 and 'group- or world-writable' in result.stderr
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
        result = subprocess.run([str(tool), *map(str, args)], capture_output=True, text=True)
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
