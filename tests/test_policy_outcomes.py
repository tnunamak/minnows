"""Outcome ledger tests: append-only closes, typed evidence, launch facts and audit joins."""
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess

import pytest

from test_policy_decisions import record, run, setup  # noqa: F401  (setup is a fixture)

PREFIX = 'orchestration_v2_projection_'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outcomes_path(setup):
    return setup[3].with_name('outcomes.jsonl')


def close(setup, decision='one', close_id='c1', *extra, outcome='accepted'):
    return run('close', decision, '--receipts', setup[3], '--close-id', close_id, '--outcome', outcome,
               '--judged-by', 'parent', '--check', 'none', *extra)


def follow(setup, decision='one', followup_id='f1', *extra):
    return run('followup', decision, '--receipts', setup[3], '--followup-id', followup_id,
               '--finding', 'no_rework_found', '--checked-scope', 'git log of touched files', *extra)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    env = ['-c', 'user.name=t', '-c', 'user.email=t@example.com']
    subprocess.run(['git', '-C', repo, 'init', '-q'], check=True)
    (repo / 'a').write_text('x')
    subprocess.run(['git', '-C', repo, 'add', 'a'], check=True)
    subprocess.run(['git', '-C', repo, *env, 'commit', '-qm', 'one'], check=True)
    sha_ = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    return repo, sha_


def test_unknown_decision_refused_and_nothing_written(setup):
    result, _ = close(setup, 'missing')
    assert result.returncode != 0 and 'receipt not found' in result.stderr
    assert not outcomes_path(setup).exists()


def test_close_defaults_replay_and_privacy(setup):
    record(setup)
    result, first = close(setup, 'one', 'c1', '--note', 'merged')
    assert result.returncode == 0, result.stderr
    assert first['repairs'] is None and first['owner_input'] == 'unknown'
    assert first['assertion'] == 'caller_claim' and first['policy_sha256']
    path = outcomes_path(setup)
    assert path.stat().st_mode & 0o777 == 0o600
    _, again = close(setup, 'one', 'c1', '--note', 'merged')
    assert again == first | {'replayed': True}
    assert len(path.read_text().splitlines()) == 1
    result, _ = close(setup, 'one', 'c1', '--note', 'different')
    assert result.returncode != 0 and 'changed inputs' in result.stderr


def test_close_id_required_and_second_close_needs_supersedes(setup):
    record(setup)
    result, _ = run('close', 'one', '--receipts', setup[3], '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none')
    assert result.returncode != 0 and '--close-id is required' in result.stderr
    assert close(setup)[0].returncode == 0
    result, _ = close(setup, 'one', 'c2', outcome='rejected')
    assert result.returncode != 0 and '--supersedes' in result.stderr
    result, _ = close(setup, 'one', 'c2', '--supersedes', 'c1', outcome='rejected')
    assert result.returncode != 0 and '--reason' in result.stderr
    result, second = close(setup, 'one', 'c2', '--supersedes', 'c1', '--reason', 'found regression', outcome='rejected')
    assert result.returncode == 0, result.stderr
    # c1 is superseded; a sibling correction of it is refused.
    result, _ = close(setup, 'one', 'c3', '--supersedes', 'c1', '--reason', 'again', outcome='unknown')
    assert result.returncode != 0 and 'current close' in result.stderr
    result, _ = close(setup, 'one', 'c2', '--supersedes', 'c1', '--reason', 'found regression', outcome='rejected')
    assert result.returncode == 0
    assert len(outcomes_path(setup).read_text().splitlines()) == 2


def test_concurrent_siblings_record_once(setup):
    record(setup)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: close(setup, 'one', f'c{i}'), range(8)))
    assert sum(r.returncode == 0 for r, _ in results) == 1
    assert len(outcomes_path(setup).read_text().splitlines()) == 1


def test_evidence_verification_states(setup, tmp_path, git_repo):
    record(setup)
    repo, head = git_repo
    log = tmp_path / 'log.txt'
    log.write_text('ok')
    good = [json.dumps({'type': 'commit', 'repo': str(repo), 'sha': head}),
            json.dumps({'type': 'check', 'label': 'unit', 'exit_code': 0, 'log': str(log), 'sha256': sha(log)}),
            json.dumps({'type': 'pr', 'repo': 'owner/name', 'number': 7})]
    args = ['--check', 'oracle']
    result, record_ = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted',
                          '--judged-by', 'parent', *args, *sum((['--evidence', e] for e in good), []))
    assert result.returncode == 0, result.stderr
    assert [e['verified_state'] for e in record_['evidence']] == ['exists', 'log_hash_matches', 'claim_only']
    assert record_['evidence_summary']['accepted_with_hash_matched_check_log'] is True
    assert 'never the truth' in record_['evidence'][0]['verification_scope']
    # The log changes after recording; replay keeps the original observation.
    log.write_text('changed')
    result, replay = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted',
                         '--judged-by', 'parent', *args, *sum((['--evidence', e] for e in good), []))
    assert replay == record_ | {'replayed': True}


def test_bad_evidence_states_visible_not_counted(setup, tmp_path, git_repo):
    record(setup)
    repo, _ = git_repo
    log = tmp_path / 'log.txt'
    log.write_text('ok')
    refs = [json.dumps({'type': 'commit', 'repo': str(repo), 'sha': 'a' * 40}),
            json.dumps({'type': 'file', 'path': str(log), 'sha256': '0' * 64}),
            json.dumps({'type': 'file', 'path': str(tmp_path / 'nope'), 'sha256': '0' * 64}),
            json.dumps({'type': 'check', 'label': 'unit', 'exit_code': 0, 'log': str(log), 'sha256': '1' * 64})]
    result, rec = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted',
                      '--judged-by', 'parent', '--check', 'oracle', *sum((['--evidence', e] for e in refs), []))
    assert result.returncode == 0, result.stderr
    assert [e['verified_state'] for e in rec['evidence']] == ['unverified_missing', 'mismatch', 'missing', 'mismatch']
    assert rec['evidence_summary']['accepted_with_hash_matched_check_log'] is False


@pytest.mark.parametrize('evidence,message', [
    ({'type': 'pr', 'number': 7}, 'exactly'),
    ({'type': 'pr', 'repo': 'no-owner', 'number': 7}, 'owner/name'),
    ({'type': 'commit', 'repo': 'relative', 'sha': 'a' * 40}, 'absolute'),
    ({'type': 'commit', 'repo': '/x', 'sha': '--output=x'}, 'hex'),
    ({'type': 'bogus'}, 'type'),
    ({'type': 'check', 'label': 'u', 'exit_code': True, 'log': '/x', 'sha256': 'a' * 64}, 'exit_code'),
])
def test_malformed_evidence_refused(setup, evidence, message):
    record(setup)
    result, _ = close(setup, 'one', 'c1', '--evidence', json.dumps(evidence))
    assert result.returncode != 0 and message in result.stderr
    assert not outcomes_path(setup).exists()
    result, _ = close(setup, 'one', 'c1', '--evidence', 'not json')
    assert result.returncode != 0


def test_nonzero_check_is_not_passing_oracle(setup, tmp_path):
    record(setup)
    log = tmp_path / 'log.txt'
    log.write_text('fail')
    ref = json.dumps({'type': 'check', 'label': 'unit', 'exit_code': 1, 'log': str(log), 'sha256': sha(log)})
    result, _ = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted',
                    '--judged-by', 'parent', '--check', 'oracle', '--evidence', ref)
    assert result.returncode != 0 and 'nonzero check' in result.stderr
    result, rec = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'rejected',
                      '--judged-by', 'parent', '--check', 'oracle', '--evidence', ref)
    assert result.returncode == 0 and rec['evidence_summary']['accepted_with_hash_matched_check_log'] is False


@pytest.mark.parametrize('extra,outcome,message', [
    (['--check', 'oracle'], 'accepted', 'requires a check evidence'),
    (['--repairs', '2'], 'accepted', 'accepted_after_repair'),
    (['--repairs', '0'], 'accepted_after_repair', '>= 1'),
    (['--note', 'a' * 281], 'accepted', '--note'),
    (['--note', 'two\nlines'], 'accepted', '--note'),
])
def test_close_consistency_rules(setup, extra, outcome, message):
    record(setup)
    args = ['close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', outcome, '--judged-by', 'parent']
    if '--check' not in extra:
        args += ['--check', 'none']
    result, _ = run(*args, *extra)
    assert result.returncode != 0 and message in result.stderr


def test_followup_requires_close_scope_and_is_idempotent(setup):
    record(setup)
    result, _ = follow(setup)
    assert result.returncode != 0 and 'close the decision first' in result.stderr
    close(setup)
    result, rec = follow(setup)
    assert result.returncode == 0, result.stderr
    assert rec['close_id'] == 'c1' and rec['lag_seconds_since_close'] >= 0 and 'not checked' in rec['semantics']
    assert follow(setup)[1] == rec | {'replayed': True}
    result, _ = run('followup', 'one', '--receipts', setup[3], '--followup-id', 'f2', '--finding', 'no_rework_found')
    assert result.returncode != 0 and '--checked-scope' in result.stderr
    result, _ = run('followup', 'one', '--receipts', setup[3], '--followup-id', 'f3', '--finding', 'revert', '--checked-scope', 's')
    assert result.returncode != 0 and 'requires evidence' in result.stderr
    assert follow(setup, 'one', 'c1')[0].returncode != 0


def test_launch_facts_live_outside_request_and_replay(setup):
    result, first = record(setup, 'one', 'implement.standard', '--purpose', 'execution', '--proof-class', 'oracle', '--urgent')
    assert result.returncode == 0, result.stderr
    assert first['launch_facts'] == {'schema_version': 1, 'purpose': 'execution', 'proof_class': 'oracle', 'urgent': True}
    assert not {'purpose', 'proof_class', 'urgent', 'irreversible', 'launch_facts'} & set(first['request'])
    _, plain = record(setup, 'two')
    assert 'launch_facts' not in plain and set(plain['request']) == set(first['request'])
    same = record(setup, 'one', 'implement.standard', '--purpose', 'execution', '--proof-class', 'oracle', '--urgent')[1]
    assert same == first | {'replayed': True}
    for extra in (['--purpose', 'review'], [], ['--purpose', 'execution', '--proof-class', 'oracle']):
        result, _ = record(setup, 'one', 'implement.standard', *extra)
        assert result.returncode != 0 and 'changed inputs' in result.stderr
    # A legacy decision (no launch_facts) replays with no flags, and rejects added facts.
    assert record(setup, 'two')[1] == plain | {'replayed': True}
    result, _ = record(setup, 'two', 'implement.standard', '--irreversible')
    assert result.returncode != 0 and 'changed inputs' in result.stderr


def make_db(tmp_path, receipt, usage_by_turn, extra_attempt=False):
    path = tmp_path / 't3.sqlite'
    db = sqlite3.connect(path)
    db.execute(f'create table {PREFIX}runs (run_id text,provider_instance_id text,status text,payload_json text,requested_at text)')
    db.execute(f'create table {PREFIX}turn_items (turn_item_id text,thread_id text,run_id text,updated_at text,status text,type text,payload_json text)')
    db.execute(f'create table {PREFIX}run_attempts (attempt_id text,thread_id text,run_id text,attempt_ordinal int,root_node_id text,provider text,provider_thread_id text,provider_turn_id text,status text,payload_json text,provider_instance_id text)')
    db.execute(f'create table {PREFIX}provider_turns (provider_turn_id text,thread_id text,provider_thread_id text,node_id text,run_attempt_id text,ordinal int,status text,started_at text,completed_at text,payload_json text)')
    for run_id, provider in (('parent', 'parent-account'), ('child', 'claude-one')):
        db.execute(f'insert into {PREFIX}runs values (?,?,?,?,?)', (run_id, provider, 'completed', json.dumps({'modelSelection': {'model': 'm', 'options': {'effort': 'medium'}}}), '2026-10-08T00:00:00Z'))
    attempts = ['a1'] + (['a2'] if extra_attempt else [])
    for i, attempt in enumerate(attempts):
        db.execute(f'insert into {PREFIX}run_attempts values (?,?,?,?,?,?,?,?,?,?,?)', (attempt, 't', 'child', i + 1, 'n', 'claude', 'pt', None, 'completed', '{"SECRET":"prompt"}', 'claude-one'))
    for i, (attempt, usage) in enumerate(usage_by_turn):
        payload = {'turnTokenUsage': usage, 'tokenUsage': {'SECRET': 'x'}}
        db.execute(f'insert into {PREFIX}provider_turns values (?,?,?,?,?,?,?,?,?,?)', (f'turn{i}', 't', 'pt', 'n', attempt, i, 'completed', None, None, json.dumps(payload)))
    call = {'startedAt': '2026-10-08T00:00:00Z', 'toolName': 'mcp__t3_code__delegate_task', 'input': {'clientRequestId': 'one', 'target': receipt['target'], 'task': 'SECRET PROMPT'}, 'output': {'structuredContent': {'childRunId': 'child'}}}
    db.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)', ('call', 'parent-thread', 'parent', '2026-10-08T00:00:00Z', 'completed', 'dynamic_tool', json.dumps(call)))
    db.commit()
    db.close()
    return path


COMPLETE = {'usageScope': 'main_agent', 'cachedInputTokens': 10, 'cacheCreationTokens': 5, 'reasoningTokens': 3, 'hasSubagents': False, 'usageStatus': 'complete', 'inputTokens': 100, 'outputTokens': 20}


def test_audit_joins_closes_followups_usage_and_denominators(setup, tmp_path):
    _, receipt = record(setup, 'one', 'implement.standard', '--purpose', 'execution')
    record(setup, 'two', 'review.audit')
    close(setup, 'one', 'c1', '--note', 'merged')
    close(setup, 'one', 'c2', '--supersedes', 'c1', '--reason', 'regressed', outcome='rejected')
    follow(setup)
    db = make_db(tmp_path, receipt, [('a1', COMPLETE)])
    # A retried call on the same decision must not inflate the distribution.
    c = sqlite3.connect(db)
    payload = json.loads(c.execute(f'select payload_json from {PREFIX}turn_items').fetchone()[0])
    c.execute(f'insert into {PREFIX}turn_items values (?,?,?,?,?,?,?)', ('call2', 'parent-thread', 'parent', '2026-10-08T00:01:00Z', 'completed', 'dynamic_tool', json.dumps(payload | {'startedAt': '2026-10-08T00:01:00Z'})))
    c.commit(); c.close()
    before = db.read_bytes()
    result, d = run('audit', '--db', db, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert db.read_bytes() == before
    for secret in ('SECRET', 'prompt'):
        assert secret not in result.stdout
    o = d['outcomes']
    assert o['descriptive_only'] is True and o['causal_claims'] == 'none'
    assert o['denominators'] == {'call_attempts': 2, 'distinct_decisions': 1, 'calls_without_receipt': 0}
    assert o['close_coverage']['decisions_closed'] == 1 and o['close_coverage']['calls_on_closed_decisions'] == 2
    assert o['outcome_distribution'] == {'closed': {'rejected': 1}, 'unclosed_or_unreadable': 0}
    assert o['superseded_closes'] == 1
    assert o['followup_coverage']['closed_decisions_with_followup'] == 1
    assert o['strata']['purpose'] == {'execution': {'decisions': 1, 'closed': {'rejected': 1}, 'unclosed_or_unreadable': 0}}
    assert o['child_usage_coverage']['by_state'] == {'complete': 1}
    row = d['delegations'][0]
    assert row['decision_outcome']['chain'] == ['c1', 'c2'] and row['decision_outcome']['current']['close_id'] == 'c2'
    assert row['followups']['state'] == 'observed' and row['launch_facts']['purpose'] == 'execution'
    assert row['parent_overhead_usage']['state'] == 'unknown'
    assert 'unattributed_account_state' in row['quota']['state']
    turn = row['observed_child_usage']['attempts'][0]['turns'][0]
    assert turn['input_tokens'] == 100 and turn['nested_usage'] == 'none_reported' and 'sum' not in json.dumps(turn)
    assert 'tokenUsage' not in json.dumps(row['observed_child_usage'])
    assert 'not dollars' in row['observed_child_usage']['label']


def test_audit_unclosed_and_legacy_output_keys(setup, tmp_path):
    _, receipt = record(setup)
    result, d = run('audit', '--db', make_db(tmp_path, receipt, []), '--receipts', setup[3])
    assert result.returncode == 0
    assert d['outcomes']['outcome_distribution'] == {'closed': {}, 'unclosed_or_unreadable': 1}
    assert d['delegations'][0]['followups']['state'] == 'not_checked'
    assert d['delegations'][0]['observed_child_usage']['state'] == 'no_turn_rows'
    assert d['delegations'][0]['outcome'] == 'unknown'
    assert not outcomes_path(setup).exists()


@pytest.mark.parametrize('usage,state,tokens', [
    ({'usageStatus': 'partial', 'usageScope': 'main_agent', 'hasSubagents': False, 'inputTokens': 7}, 'partial', 7),
    ({'usageStatus': 'unavailable', 'usageScope': 'main_agent', 'inputTokens': 7}, 'unavailable', None),
    ({'usageScope': 'main_agent', 'inputTokens': 7}, 'unavailable', None),
    ({'usageStatus': 'complete', 'usageScope': 'main_agent', 'hasSubagents': True, 'inputTokens': -1}, 'complete', None),
])
def test_usage_partial_unavailable_and_subagents_stay_unknown(setup, tmp_path, usage, state, tokens):
    _, receipt = record(setup)
    result, d = run('audit', '--db', make_db(tmp_path, receipt, [('a1', usage)]), '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    usage_view = d['delegations'][0]['observed_child_usage']
    turn = usage_view['attempts'][0]['turns'][0]
    assert usage_view['state'] == state and turn['input_tokens'] == tokens
    assert turn['nested_usage'] == ('none_reported' if usage.get('hasSubagents') is False else 'unknown')


def test_usage_multiple_attempts_and_turns_are_not_summed(setup, tmp_path):
    _, receipt = record(setup)
    db = make_db(tmp_path, receipt, [('a1', COMPLETE), ('a2', COMPLETE | {'usageScope': 'other'})], extra_attempt=True)
    _, d = run('audit', '--db', db, '--receipts', setup[3])
    view = d['delegations'][0]['observed_child_usage']
    assert view['multiple_attempts'] is True and len(view['attempts']) == 2
    assert [a['turns'][0]['usage_scope'] for a in view['attempts']] == ['main_agent', 'other']
    assert view['sums'].startswith('never computed')


def test_usage_tables_absent_or_malformed_do_not_break_audit(setup, tmp_path):
    _, receipt = record(setup)
    db = make_db(tmp_path, receipt, [])
    c = sqlite3.connect(db)
    c.execute(f'drop table {PREFIX}provider_turns')
    c.commit()
    _, d = run('audit', '--db', db, '--receipts', setup[3])
    assert d['delegations'][0]['observed_child_usage']['state'] == 'unknown_table_absent'
    c.execute(f'drop table {PREFIX}run_attempts')
    c.execute(f'create table {PREFIX}run_attempts (other text)')
    c.commit(); c.close()
    result, d = run('audit', '--db', db, '--receipts', setup[3])
    assert result.returncode == 0
    assert d['delegations'][0]['observed_child_usage']['reason'] == 'schema_mismatch'


def test_outcomes_file_permissions_and_parent_refusal(setup):
    record(setup)
    setup[3].parent.chmod(0o770)
    result, _ = close(setup)
    assert result.returncode != 0 and 'group- or world-writable' in result.stderr
    assert not outcomes_path(setup).exists()


def test_receipts_file_has_no_outcome_rows(setup):
    record(setup)
    close(setup)
    rows = [json.loads(line) for line in setup[3].read_text().splitlines()]
    assert all('kind' not in r for r in rows)


# --- repair r1: ledger aliases, evidence hardening, row validation, names, docs ---

import os
import re
import sys

from test_policy_decisions import REPO

sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
import outcomes as outcomes_lib  # noqa: E402

README = (REPO / 'tools/model-policy-ops/README.md').read_text()


def readme_example(name):
    return json.loads(re.search(rf'<!-- example:{name} -->\n```json\n(.*?)\n```', README, re.S).group(1))


def audit_export(setup, tmp_path, *extra):
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [{
        'call_id': 'call-1', 'thread_id': 'parent-thread', 'timestamp': '2026-10-08T12:00:00Z',
        'input': {'clientRequestId': receipt['decision_id'], 'target': receipt['target']}, 'output': {'childRunId': 'child'}}]}))
    return run('audit', '--export', export, '--receipts', setup[3], *extra)


def alias_paths(setup, tmp_path, how):
    receipts = setup[3]
    if how == 'same':
        return receipts
    alias = tmp_path / f'alias-{how}.jsonl'
    (os.symlink if how == 'symlink' else os.link)(receipts, alias)
    return alias


@pytest.mark.parametrize('how', ['same', 'symlink', 'hardlink'])
@pytest.mark.parametrize('command', ['close', 'followup', 'audit'])
def test_receipts_outcomes_alias_is_refused_before_any_write(setup, tmp_path, how, command):
    record(setup)
    close(setup)
    before = setup[3].read_bytes()
    alias = alias_paths(setup, tmp_path, how)
    export = tmp_path / 'e.json'
    export.write_text('{"schema_version":1,"delegations":[]}')
    commands = {'close': ['close', 'one', '--close-id', 'cx', '--outcome', 'rejected', '--judged-by', 'parent', '--check', 'none'],
                'followup': ['followup', 'one', '--followup-id', 'fx', '--finding', 'no_rework_found', '--checked-scope', 's'],
                'audit': ['audit', '--export', export]}
    result, _ = run(*commands[command], '--receipts', setup[3], '--outcomes', alias)
    assert result.returncode != 0 and 'same file' in result.stderr
    assert setup[3].read_bytes() == before
    assert all('kind' not in json.loads(line) for line in setup[3].read_text().splitlines())


def test_default_outcomes_name_cannot_alias_receipts(setup):
    named = setup[3].with_name('outcomes.jsonl')
    result, _ = run('close', 'one', '--receipts', named, '--close-id', 'c', '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none')
    assert result.returncode != 0 and 'same file' in result.stderr


def test_fifo_and_directory_evidence_do_not_hang(setup, tmp_path):
    record(setup)
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    refs = [{'type': 'file', 'path': str(fifo), 'sha256': '0' * 64}, {'type': 'file', 'path': str(tmp_path), 'sha256': '0' * 64}]
    result, rec = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'unknown', '--judged-by', 'parent',
                      '--check', 'none', *sum((['--evidence', json.dumps(r)] for r in refs), []), timeout=8)
    assert result.returncode == 0, result.stderr
    assert [e['verified_state'] for e in rec['evidence']] == ['unreadable', 'unreadable']


def test_hash_cap_counts_bytes_read(tmp_path, monkeypatch):
    big = tmp_path / 'big'
    big.write_bytes(b'x' * 2048)
    monkeypatch.setattr(outcomes_lib, 'MAX_HASH_BYTES', 1024)
    assert outcomes_lib.file_hash_state(str(big), sha(big)) == 'too_large'
    monkeypatch.setattr(outcomes_lib, 'MAX_HASH_BYTES', 4096)
    assert outcomes_lib.file_hash_state(str(big), sha(big)) == 'hash_matches'


GOOD_CLOSE = {'kind': 'close', 'schema_version': 1, 'close_id': 'c1', 'decision_id': 'one', 'outcome': 'accepted', 'judged_by': 'parent',
              'check': 'none', 'repairs': None, 'owner_input': 'unknown', 'evidence': [], 'note': None, 'supersedes': None,
              'reason': None, 'closer_thread': None, 'recorded_at': '2026-10-08T00:00:00+00:00',
              'evidence_summary': {'states': {}, 'accepted_with_hash_matched_check_log': False, 'exit_codes_claimed': []}}
GOOD_FOLLOWUP = {'kind': 'followup', 'schema_version': 1, 'followup_id': 'f1', 'decision_id': 'one', 'finding': 'no_rework_found',
                 'checked_scope': 's', 'evidence': [], 'close_id': 'c1', 'observed_at': '2026-10-08T01:00:00+00:00',
                 'lag_seconds_since_close': 3600.0}


def without(row, key):
    return {k: v for k, v in row.items() if k != key}


@pytest.mark.parametrize('rows,reason', [
    ([without(GOOD_CLOSE, 'close_id')], "missing close_id"),
    ([GOOD_CLOSE | {'kind': 'closed'}], 'kind must be'),
    ([GOOD_CLOSE | {'kind': None}], 'kind must be'),
    ([GOOD_CLOSE | {'schema_version': 2}], 'schema_version'),
    ([GOOD_CLOSE | {'outcome': 'great'}], 'outcome'),
    ([GOOD_CLOSE | {'close_id': 'bad id!'}], 'ID'),
    ([GOOD_CLOSE | {'evidence': [{'type': 'file'}]}], 'evidence'),
    ([GOOD_CLOSE | {'evidence_summary': {}}], 'evidence_summary'),
    ([GOOD_CLOSE, GOOD_CLOSE], 'already used'),
    ([GOOD_CLOSE, GOOD_CLOSE | {'close_id': 'c2'}], 'already has a close'),
    ([GOOD_CLOSE | {'supersedes': 'ghost', 'reason': 'r'}], 'not an earlier close'),
    ([GOOD_CLOSE, GOOD_CLOSE | {'close_id': 'c2', 'supersedes': 'c1', 'reason': 'r'},
      GOOD_CLOSE | {'close_id': 'c3', 'supersedes': 'c1', 'reason': 'r'}], 'already superseded'),
    ([GOOD_FOLLOWUP], 'not an earlier close'),
    ([GOOD_CLOSE, GOOD_FOLLOWUP | {'decision_id': 'other'}], 'not an earlier close'),
    ([GOOD_CLOSE, GOOD_FOLLOWUP | {'finding': 'fine'}], 'finding'),
    ([GOOD_CLOSE, GOOD_FOLLOWUP | {'lag_seconds_since_close': 'soon'}], 'lag_seconds'),
    (['not json at all'], 'not valid JSON'),
])
@pytest.mark.parametrize('command', ['close', 'followup', 'audit'])
def test_bad_outcome_rows_fail_closed_with_file_and_line(setup, tmp_path, rows, reason, command):
    record(setup)
    path = outcomes_path(setup)
    path.write_text(''.join((r if isinstance(r, str) else json.dumps(r)) + '\n' for r in rows))
    path.chmod(0o600)
    before = path.read_bytes()
    lines = {'close': ['close', 'one', '--close-id', 'new', '--outcome', 'rejected', '--judged-by', 'parent', '--check', 'none', '--supersedes', 'c1', '--reason', 'r'],
             'followup': ['followup', 'one', '--followup-id', 'newf', '--finding', 'no_rework_found', '--checked-scope', 's'],
             'audit': ['audit', '--export', tmp_path / 'e.json']}
    (tmp_path / 'e.json').write_text('{"schema_version":1,"delegations":[]}')
    result, _ = run(*lines[command], '--receipts', setup[3])
    last = len(rows)
    assert result.returncode != 0, result.stdout
    assert re.search(rf'FAIL outcomes\.jsonl line {last}: .*{re.escape(reason)}', result.stderr), result.stderr
    assert path.read_bytes() == before


def test_valid_hand_built_rows_are_accepted(setup, tmp_path):
    record(setup)
    outcomes_path(setup).write_text(json.dumps(GOOD_CLOSE) + '\n\n' + json.dumps(GOOD_FOLLOWUP) + '\n')
    outcomes_path(setup).chmod(0o600)
    result, d = audit_export(setup, tmp_path)
    assert result.returncode == 0, result.stderr
    assert d['outcomes']['followup_coverage']['closed_decisions_with_followup'] == 1


def test_oracle_summary_names_only_what_is_observed(setup, tmp_path):
    record(setup)
    log = tmp_path / 'log.txt'
    log.write_text('ok')
    ref = {'type': 'check', 'label': 'unit', 'exit_code': 0, 'log': str(log), 'sha256': sha(log)}
    _, rec = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted', '--judged-by', 'parent',
                 '--check', 'oracle', '--evidence', json.dumps(ref))
    assert rec['evidence_summary'] == {'states': {'log_hash_matches': 1}, 'accepted_with_hash_matched_check_log': True,
                                       'exit_codes_claimed': [0]}
    assert 'verified_oracle' not in json.dumps(rec)
    result, d = audit_export(setup, tmp_path)
    assert d['outcomes']['accepted_with_hash_matched_check_log'] == 1
    assert 'verified_oracle' not in result.stdout
    # A rejected close with a matching log is not counted.
    record(setup, 'two')
    _, rej = run('close', 'two', '--receipts', setup[3], '--close-id', 'c2', '--outcome', 'rejected', '--judged-by', 'parent',
                 '--check', 'oracle', '--evidence', json.dumps(ref | {'exit_code': 1}))
    assert rej['evidence_summary']['accepted_with_hash_matched_check_log'] is False
    assert rej['evidence_summary']['exit_codes_claimed'] == [1]


def test_commit_check_ignores_inherited_git_environment(setup, tmp_path, git_repo):
    record(setup)
    repo, head = git_repo
    other = tmp_path / 'other'
    subprocess.run(['git', 'init', '-q', other], check=True)
    env = os.environ | {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}
    _, rec = run('close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted', '--judged-by', 'parent',
                 '--check', 'none', '--evidence', json.dumps({'type': 'commit', 'repo': str(repo), 'sha': head}), env=env)
    assert rec['evidence'][0]['verified_state'] == 'exists'


def test_closer_thread_is_optional_a_flag_and_part_of_replay_identity(setup, tmp_path):
    record(setup)
    record(setup, 'two')
    record(setup, 'three')
    _, plain = close(setup, 'one', 'c1')
    assert plain['closer_thread'] is None
    _, same = close(setup, 'two', 'c2', '--closer-thread', 'parent-thread')
    _, other = close(setup, 'three', 'c3', '--closer-thread', 'stranger-thread')
    assert same['closer_thread'] == 'parent-thread' and other['closer_thread'] == 'stranger-thread'
    assert close(setup, 'two', 'c2', '--closer-thread', 'stranger-thread')[0].returncode != 0
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    export = tmp_path / 'e.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [
        {'call_id': f'call-{d}', 'thread_id': 'parent-thread', 'timestamp': '2026-10-08T12:00:00Z',
         'input': {'clientRequestId': d, 'target': receipt['target']}, 'output': {}} for d in ('one', 'two', 'three')]}))
    _, audit_ = run('audit', '--export', export, '--receipts', setup[3])
    flags = {r['decision_id']: r['decision_outcome']['current']['closer_thread_differs_from_parent'] for r in audit_['delegations']}
    assert flags == {'one': None, 'two': False, 'three': True}
    assert audit_['outcomes']['closer_thread'] == {'differs_from_parent': 1, 'not_recorded': 1,
                                                    'meaning': 'a review flag; the tool does not decide who may close a decision'}


def test_superseded_outcome_is_defined_apart_from_the_supersedes_flag():
    text = re.sub(r'\s+', ' ', README)
    assert 'The outcome `superseded` means the delegated result was not used because other work replaced it' in text
    assert 'no link to `--supersedes`' in text


def test_public_guidance_leaves_the_objective_to_the_owner():
    guidance = (REPO / 'tools/model-policy-ops/routing-guidance.md').read_text()
    assert 'cheapest option' not in guidance and 'belongs to the owner' in guidance


def test_readme_offline_catalog_example_resolves_to_a_target(setup, tmp_path):
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps(readme_example('catalog')))
    result, decision = run('resolve', 'fanout.dollar-tight', '--available', catalog)
    assert result.returncode == 0, result.stderr
    assert decision['target'] == {'providerInstanceId': 'codex-main', 'model': 'gpt-6-luna', 'options': {'reasoningEffort': 'medium'}}
    # The documented wrong shapes really fail.
    text = catalog.read_text()
    for bad, state in ((text.replace('{"id": "medium"}, {"id": "high"}', '"medium", "high"'), 'schema_mismatch'),
                       (text.replace('reasoningEffort', 'reasoning_effort'), 'unknown_effort_surface')):
        catalog.write_text(bad)
        _, decision = run('resolve', 'fanout.dollar-tight', '--available', catalog)
        assert decision['target'] is None and decision['selection']['state'] == state


def test_readme_journey_and_audit_reference_match_real_output(setup, tmp_path):
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps(readme_example('catalog')))
    receipts = tmp_path / 'j/decisions.jsonl'
    result, decision = run('resolve', 'fanout.dollar-tight', '--available', catalog, '--purpose', 'execution', '--proof-class', 'oracle',
                           '--record', '--decision-id', 'DEC', '--parent-model', 'm', '--parent-provider-instance', 'p',
                           '--parent-thread', 'THREAD', '--receipts', receipts)
    assert result.returncode == 0, result.stderr
    export = readme_example('export')
    export['delegations'][0] |= {'thread_id': 'THREAD'}
    export['delegations'][0]['input']['clientRequestId'] = 'DEC'
    path = tmp_path / 'export.json'
    path.write_text(json.dumps(export))
    assert run('close', 'DEC', '--receipts', receipts, '--close-id', 'C1', '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none')[0].returncode == 0
    result, d = run('audit', '--export', path, '--receipts', receipts, '--thread', 'THREAD')
    assert result.returncode == 0, result.stderr
    assert d['counts']['call_attempts'] == 1 and d['delegations'][0]['matched_receipt'] is True
    section = README[README.index('### Audit JSON reference'):]
    documented = lambda key: f'`{key}`' in section  # noqa: E731
    assert all(documented(k) for k in d), [k for k in d if not documented(k)]
    assert all(documented(k) for k in d['outcomes']), [k for k in d['outcomes'] if not documented(k)]
    for key in ('decision_outcome', 'followups', 'launch_facts', 'observed_child_usage', 'parent_overhead_usage', 'quota'):
        assert key in d['delegations'][0] and documented(key)
    for key in d['delegations'][0]['decision_outcome']['current']:
        assert documented(key), key
