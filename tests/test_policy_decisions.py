"""Policy boundary tests: real wire shapes, frozen decisions and audit denominators."""
import concurrent.futures
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools/model-policy-ops/model-policy-ops'
POLICY = REPO / 'data/model-choice-policy/operating-points.json'


def run(*args, **kwargs):
    result = subprocess.run([str(TOOL), *map(str, args)], capture_output=True, text=True, **kwargs)
    return result, json.loads(result.stdout) if result.returncode == 0 and result.stdout.startswith('{') else None


@pytest.fixture
def setup(tmp_path):
    policy = json.loads(POLICY.read_text())
    providers = []
    for family in ('codex', 'claude', 'grok'):
        models = sorted({o['expands_to']['model'] for o in policy['operating_points'] if o['expands_to']['provider'] == family})
        providers.append({'instanceId': family + '-one', 'driver': 'claudeAgent' if family == 'claude' else family,
            'enabled': True, 'status': 'ready', 'models': [{'slug': m, 'options': [{'id': 'effort' if family == 'claude' else 'reasoningEffort', 'values': [{'id': e} for e in ('low', 'medium', 'high', 'xhigh', 'max')]}]} for m in models]})
    available = tmp_path / 'available.json'
    available.write_text(json.dumps({'ok': True, 'data': {'runtime': {'secret_path': '/private'}, 'providers': providers}}))
    quota = tmp_path / 'quota.json'
    quota.write_text(json.dumps({'fetched_at': '2026-10-08T00:00:00Z', 'providers': {'claude': {'sources': [{'source': {'id': 'source-real'}, 'usage': {'provider': 'claude', 'source_id': 'source-real', 'fetched_at': '2026-10-08T00:00:00Z', 'windows': [{'name': '7d All', 'utilization': 100}, {'name': '7d Fable', 'utilization': 100}, {'name': 'bonus', 'utilization': 100}, {'name': 'extra', 'utilization': 110, 'currency': 'USD'}]}}]}}}))
    receipts = tmp_path / 'state/decisions.jsonl'
    return policy, available, quota, receipts


def resolve(setup, op='implement.standard', *extra):
    _, available, quota, receipts = setup
    return run('resolve', op, '--available', available, '--quota', quota, '--receipts', receipts, *extra)


def record(setup, decision_id='one', op='implement.standard', *extra):
    return resolve(setup, op, '--record', '--decision-id', decision_id, '--parent-model', 'parent-model', '--parent-provider-instance', 'parent-account', '--parent-thread', 'parent-thread', *extra)


def test_all_ops_and_preserved_show(setup):
    policy, _, _, receipts = setup
    for op in policy['operating_points']:
        result, decision = resolve(setup, op['id'])
        assert result.returncode == 0, result.stderr
        assert decision['selection']['arm'] == op['expands_to']
        assert decision['policy_context'] == op
        assert decision['policy_version'] == policy['policy_version']
        assert len(decision['policy_sha256']) == 64
        assert decision['target']['options'] == {('effort' if op['expands_to']['provider'] == 'claude' else 'reasoningEffort'): op['expands_to']['effort']}
        result, shown = run('show', op['id'])
        assert shown == op
    assert not receipts.exists()


@pytest.mark.parametrize('change,state', [
    ('remove', 'unavailable_model'), ('disabled', 'unavailable_account'),
    ('nonrunnable', 'unavailable_model'), ('ambiguous', 'ambiguous_account'),
    ('wrong_effort', 'unsupported_effort'), ('no_options', 'unknown_effort_surface'),
])
def test_unavailable_has_no_fallback(setup, change, state):
    _, path, _, _ = setup
    value = json.loads(path.read_text())
    p = value['data']['providers'][1]
    if change == 'remove':
        p['models'] = []
    elif change == 'disabled':
        p['enabled'] = False
    elif change == 'nonrunnable':
        for m in p['models']: m['runnable'] = False
    elif change == 'ambiguous':
        value['data']['providers'].append(p | {'instanceId': 'claude-two'})
    elif change == 'wrong_effort':
        for m in p['models']: m['options'][0]['values'] = [{'id': 'low'}]
    elif change == 'no_options':
        for m in p['models']: m['options'] = []
    path.write_text(json.dumps(value))
    result, decision = resolve(setup)
    assert result.returncode == 0
    assert decision['selection']['state'] == state
    assert decision['target'] is None and not decision['launch_ready']
    assert len(decision['alternatives']) == 2
    if change == 'ambiguous':
        _, selected = resolve(setup, 'implement.standard', '--account-hint', 'claude-two')
        assert selected['target']['providerInstanceId'] == 'claude-two'


def test_capabilities_shape_and_disabled(setup):
    _, path, _, _ = setup
    value = json.loads(path.read_text())
    ps = value['data']['providers']
    for p in ps:
        p['providerInstanceId'] = p.pop('instanceId')
        p['driverKind'] = p.pop('driver')
        p['canRunChildTask'] = True
        for m in p['models']:
            m['id'] = m.pop('slug')
            for o in m['options']: o['options'] = o.pop('values')
    path.write_text(json.dumps({'structuredContent': {'providers': ps}}))
    _, decision = resolve(setup)
    assert decision['target']['options'] == {'effort': 'medium'}
    ps[1]['canRunChildTask'] = False
    path.write_text(json.dumps({'providers': ps}))
    _, decision = resolve(setup)
    assert decision['target'] is None


@pytest.mark.parametrize('raw,state', [('bad json', 'error'), ('{}', 'schema_mismatch'), ('{"stale":true}', 'stale'), ('{"ok":false}', 'error')])
def test_bad_sources(setup, raw, state):
    _, path, quota, _ = setup
    path.write_text(raw)
    quota.write_text('bad json')
    _, decision = resolve(setup)
    assert decision['availability']['state'] == state
    assert decision['target'] is None
    assert decision['quota']['state'] == 'error'


def test_missing_source_and_quota_expiry(setup):
    _, available, quota, receipts = setup
    _, decision = run('resolve', 'implement.standard', '--receipts', receipts)
    assert decision['availability']['state'] == 'missing'
    assert decision['target'] is None
    value = json.loads(quota.read_text())
    value['providers']['claude']['sources'][0]['usage']['is_expired'] = True
    quota.write_text(json.dumps(value))
    _, decision = resolve(setup, 'implement.standard', '--quota-provider', 'claude', '--quota-source', 'source-real')
    assert decision['quota']['state'] == 'stale'


def test_nested_quota_preserves_ids_and_relevance(setup):
    _, decision = resolve(setup, 'implement.standard', '--quota-provider', 'claude', '--quota-source', 'source-real')
    q = decision['quota']
    assert q['provider_id'] == 'claude' and q['source_id'] == 'source-real'
    assert [w['state'] for w in q['windows']] == ['exhausted', 'informational', 'informational', 'informational']
    assert decision['target'] is not None  # quota is information, never automatic allocation
    _, decision = resolve(setup, 'implement.standard', '--quota-provider', 'claude')
    assert decision['quota']['state'] == 'source_required'
    _, decision = resolve(setup)
    assert decision['quota']['state'] == 'mapping_required'


@pytest.mark.parametrize('flags', [('--override', 'model=gpt-6.1-sol'), ('--override', 'effort=xhigh'), ('--override', 'account=claude-one'), ('--no-op',)])
def test_override_reason_required(setup, flags):
    op = None if '--no-op' in flags else 'implement.standard'
    result, _ = resolve(setup, op, *flags) if op else run('resolve', '--no-op')
    assert result.returncode != 0
    assert 'reason' in result.stderr


def test_reasoned_override_no_op_and_xhigh(setup):
    _, d = resolve(setup, 'implement.standard', '--override', 'provider=codex', '--override', 'model=gpt-6.1-sol', '--override', 'effort=xhigh', '--reason', 'bounded implementation with independent review')
    assert d['target'] == {'providerInstanceId': 'codex-one', 'model': 'gpt-6.1-sol', 'options': {'reasoningEffort': 'xhigh'}}
    _, a, q, r = setup
    result, d = run('resolve', '--no-op', '--override', 'provider=codex', '--override', 'model=gpt-6.1-sol', '--override', 'effort=medium', '--reason', 'new task shape', '--available', a, '--receipts', r)
    assert result.returncode == 0 and d['policy_context']['id'] is None


def test_frozen_receipt_and_changed_inputs(setup, tmp_path):
    result, first = record(setup)
    assert result.returncode == 0, result.stderr
    assert '/private' not in json.dumps(first)
    _, _, _, receipts = setup
    assert receipts.stat().st_mode & 0o777 == 0o600
    assert receipts.parent.stat().st_mode & 0o777 == 0o700
    policy, _, _, _ = setup
    policy['policy_version'] = 'new-version'
    policy['operating_points'][4]['expands_to']['effort'] = 'high'
    changed = tmp_path / 'policy.json'
    changed.write_text(json.dumps(policy))
    _, pinned = record(setup, 'one', 'implement.standard', '--policy', changed)
    assert pinned == first | {'replayed': True}
    _, new = record(setup, 'two', 'implement.standard', '--policy', changed)
    assert new['policy_version'] == 'new-version'
    assert new['target']['options']['effort'] == 'high'
    result, _ = record(setup, 'one', 'implement.standard', '--reason', 'changed')
    assert result.returncode != 0 and 'changed inputs' in result.stderr
    assert len(receipts.read_text().splitlines()) == 2


def test_concurrent_receipts(setup):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: record(setup), range(16)))
    assert all(r.returncode == 0 for r, _ in results)
    stored = json.loads(setup[3].read_text())
    assert all(d == stored or d == stored | {'replayed': True} for _, d in results)
    assert sum(not d.get('replayed', False) for _, d in results) == 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: record(setup, f'id-{i}'), range(16)))
    assert all(r.returncode == 0 for r, _ in results)
    assert len(setup[3].read_text().splitlines()) == 17


def test_branching_escalation_limits(setup):
    assert record(setup)[0].returncode == 0
    result, _ = record(setup, 'invalid', 'docs.lookup', '--escalate-from', 'one', '--why', 'oracle_failed', '--reason', 'test failed', '--handoff-boundary', 'fresh child')
    assert result.returncode != 0 and 'explicit target op' in result.stderr
    result, d = record(setup, 'up', 'implement.accuracy-first', '--escalate-from', 'one', '--why', 'oracle_failed', '--reason', 'test failed', '--handoff-boundary', 'fresh child with failing oracle')
    assert result.returncode == 0, result.stderr
    assert d['escalation']['to_op'] == 'implement.accuracy-first'
    result, _ = record(setup, 'up-again', 'review.audit', '--escalate-from', 'up', '--why', 'judged_insufficient', '--reason', 'still bad', '--handoff-boundary', 'new child')
    assert result.returncode != 0 and 'one linked escalation' in result.stderr
    result, _ = record(setup, 'sibling', 'review.audit', '--escalate-from', 'one', '--why', 'judged_insufficient', '--reason', 'bad', '--handoff-boundary', 'new child')
    assert result.returncode != 0


def test_audit_export_denominators_failed_outputs_and_scope(setup, tmp_path):
    _, receipt = record(setup, 'one', 'implement.standard', '--reason', 'task fits')
    record(setup, 'unused')
    target = receipt['target']
    base = {'thread_id': 'parent-thread', 'timestamp': '2026-10-08T00:00:00Z', 'parent_provider': 'parent-account', 'parent_model': 'parent-model'}
    delegations = [base | {'call_id': 'direct', 'input': {'clientRequestId': 'one', 'target': target}, 'output': {'childRunId': 'actual-run'}, 'child_status': 'completed'},
                   base | {'call_id': 'structured', 'input': {'clientRequestId': 'one', 'target': target}, 'output': {'structuredContent': {'childRunId': 'structured-run'}}},
                   base | {'call_id': 'failed', 'input': {'clientRequestId': 'unmatched', 'target': {'model': 'other'}}, 'output': 'failed', 'status': 'failed'}]
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': delegations}))
    result, d = run('audit', '--export', export, '--receipts', setup[3], '--thread', 'parent-thread', '--since', '2026-10-01T00:00:00Z')
    assert result.returncode == 0, result.stderr
    assert d['coverage'] == {'count': 2, 'denominator': 3, 'rate': 2/3, 'metric': 'app_owned_receipt_coverage'}
    assert d['effort_explicitness']['count'] == 2
    assert d['unmatched_delegations'] == ['failed']
    assert d['unmatched_receipts'] == ['unused']
    assert d['delegations'][1]['child_run_id'] == 'structured-run'
    assert d['delegations'][0]['outcome'] == 'unknown'
    assert d['delegations'][0]['observed_model'] is None


def test_sql_readonly_actual_child_run_not_first_run(setup, tmp_path):
    _, receipt = record(setup)
    dbpath = tmp_path / 't3.sqlite'
    db = sqlite3.connect(dbpath)
    prefix = 'orchestration_v2_projection_'
    db.execute(f'create table {prefix}runs (run_id text,provider_instance_id text,status text,payload_json text,requested_at text)')
    db.execute(f'create table {prefix}turn_items (turn_item_id text,thread_id text,run_id text,updated_at text,status text,type text,payload_json text)')
    for run_id, provider, model in [('parent', 'parent-account', 'parent-model'), ('first', 'wrong', 'wrong'), ('actual', 'claude-one', 'claude-opus-5-5')]:
        db.execute(f'insert into {prefix}runs values (?,?,?,?,?)', (run_id, provider, 'completed', json.dumps({'modelSelection': {'model': model, 'options': {'effort': 'medium'}}}), '2026-10-08T00:00:00Z'))
    payload = {'startedAt': '2026-10-08T00:00:00Z', 'toolName': 'mcp__t3_code__delegate_task', 'input': {'clientRequestId': 'one', 'target': receipt['target'], 'task': 'SECRET PROMPT'}, 'output': {'structuredContent': {'childRunId': 'actual'}}}
    db.execute(f'insert into {prefix}turn_items values (?,?,?,?,?,?,?)', ('call', 'parent-thread', 'parent', '2026-10-08T00:00:00Z', 'completed', 'dynamic_tool', json.dumps(payload)))
    db.commit(); db.close()
    before = dbpath.read_bytes()
    result, d = run('audit', '--db', dbpath, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    row = d['delegations'][0]
    assert row['child_run_id'] == 'actual' and row['child_request_match'] is True
    assert row['parent_match'] == {'thread': True, 'provider': True, 'model': True}
    assert 'SECRET PROMPT' not in result.stdout
    assert before == dbpath.read_bytes()
    bad = tmp_path / 'bad.sqlite'
    sqlite3.connect(bad).close()
    result, _ = run('audit', '--db', bad)
    assert result.returncode != 0 and 'schema/read mismatch' in result.stderr


def test_record_requires_caller_metadata(setup):
    result, _ = resolve(setup, 'implement.standard', '--record')
    assert result.returncode != 0 and 'explicit' in result.stderr


def test_auto_wire_commands(setup, tmp_path):
    _, available, quota, receipts = setup
    bins = tmp_path / 'bin'
    bins.mkdir()
    for name, content, expected in [('t3code', available.read_text(), '--json models list'), ('clawmeter', quota.read_text(), '--json')]:
        script = bins / name
        script.write_text('#!/usr/bin/env python3\nimport sys\nassert " ".join(sys.argv[1:]) == ' + repr(expected) + '\nprint(' + repr(content) + ')\n')
        script.chmod(0o700)
    result, d = run('resolve', 'implement.standard', '--available', 'auto', '--quota', 'auto', '--receipts', receipts, env=os.environ | {'PATH': str(bins) + os.pathsep + os.environ['PATH']})
    assert result.returncode == 0 and d['target']['options'] == {'effort': 'medium'}
    (bins / 't3code').write_text('#!/usr/bin/env python3\nraise SystemExit(2)\n')
    _, d = run('resolve', 'implement.standard', '--available', 'auto', '--receipts', receipts, env=os.environ | {'PATH': str(bins) + os.pathsep + os.environ['PATH']})
    assert d['availability']['state'] == 'error' and d['target'] is None


def test_old_receipt_survives_removed_op(setup, tmp_path):
    _, old = record(setup)
    policy = dict(setup[0])
    policy['operating_points'] = [o for o in policy['operating_points'] if o['id'] != 'implement.standard']
    changed = tmp_path / 'changed.json'
    changed.write_text(json.dumps(policy))
    _, pinned = record(setup, 'one', 'implement.standard', '--policy', changed)
    assert pinned == old | {'replayed': True}


def test_one_escalation_under_concurrent_writes(setup):
    record(setup)
    def escalate(i):
        return record(setup, f'escalate-{i}', 'implement.accuracy-first', '--escalate-from', 'one', '--why', 'oracle_failed', '--reason', 'oracle failed', '--handoff-boundary', 'new child')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(escalate, range(8)))
    assert sum(r.returncode == 0 for r, _ in results) == 1
    assert len(setup[3].read_text().splitlines()) == 2


def test_same_arm_retry_is_not_escalation(setup, tmp_path):
    policy = setup[0]
    original = next(o for o in policy['operating_points'] if o['id'] == 'implement.standard')
    destination = next(o for o in policy['operating_points'] if o['id'] == 'implement.accuracy-first')
    destination['expands_to'] = dict(original['expands_to'])
    changed = tmp_path / 'same.json'
    changed.write_text(json.dumps(policy))
    record(setup, 'one', 'implement.standard', '--policy', changed)
    result, _ = record(setup, 'retry', 'implement.accuracy-first', '--policy', changed, '--escalate-from', 'one', '--why', 'judged_insufficient', '--reason', 'bad', '--handoff-boundary', 'new child')
    assert result.returncode != 0 and 'same-arm' in result.stderr


def test_no_implicit_parent_or_quota_mapping(setup):
    result, d = resolve(setup)
    assert d['parent'] == {'model': None, 'provider_instance_id': None, 'thread_id': None}
    assert d['quota']['provider_id'] is None
    assert d['selection']['authentication'] == 'not_attested'


def test_xhigh_in_policy_requires_reason(setup, tmp_path):
    policy = setup[0]
    next(o for o in policy['operating_points'] if o['id'] == 'implement.standard')['expands_to']['effort'] = 'max'
    changed = tmp_path / 'max.json'
    changed.write_text(json.dumps(policy))
    result, _ = resolve(setup, 'implement.standard', '--policy', changed)
    assert result.returncode != 0 and 'xhigh/max' in result.stderr


def test_latest_selected_pack_from_env(setup, tmp_path):
    pack = tmp_path / 'packs/model-choice-policy'
    pack.mkdir(parents=True)
    policy = setup[0]
    policy['policy_version'] = 'selected-pack'
    (pack / 'operating-points.json').write_text(json.dumps(policy))
    _, d = run('resolve', 'implement.standard', '--available', setup[1], '--receipts', setup[3], env=os.environ | {'DATA_PACKS_HOME': str(pack.parent)})
    assert d['policy_version'] == 'selected-pack'


def test_invalid_options_and_malformed_receipts(setup):
    for flags in [('--override', 'unknown=a'), ('--override', 'effort=high', '--override', 'effort=low', '--reason', 'x'), ('--decision-id', 'contains space')]:
        result, _ = resolve(setup, 'implement.standard', *flags)
        assert result.returncode != 0 and result.stderr.startswith('FAIL')
    setup[3].parent.mkdir()
    setup[3].write_text('malformed\n')
    result, _ = resolve(setup)
    assert result.returncode != 0 and 'malformed receipt' in result.stderr


@pytest.mark.parametrize('raw', [{'data': []}, {'data': {'providers': [False]}}, {'providers': [{'models': ['invalid']}]}, {'providers': [{'models': [{'options': [{'id': 'effort', 'values': 'bad'}]}]}]}])
def test_malformed_runtime_schema_is_explicit(setup, raw):
    setup[1].write_text(json.dumps(raw))
    result, d = resolve(setup)
    assert result.returncode == 0
    assert d['availability']['state'] == 'schema_mismatch'
    assert d['selection']['state'] == 'schema_mismatch'
    assert d['target'] is None


def test_malformed_quota_is_explicit(setup):
    setup[2].write_text(json.dumps({'providers': {'claude': {'sources': 'bad'}}}))
    result, d = resolve(setup)
    assert result.returncode == 0 and d['quota']['state'] == 'schema_mismatch'


def test_audit_request_mismatch_and_reasoned_override(setup, tmp_path):
    _, receipt = record(setup, 'override', 'implement.standard', '--override', 'effort=high', '--reason', 'hard patch')
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [{'call_id': 'mismatch', 'thread_id': 'parent-thread', 'timestamp': '2026-10-08T00:00:00Z', 'input': {'clientRequestId': 'override', 'target': receipt['target'] | {'model': 'other'}}, 'output': None}]}))
    result, d = run('audit', '--export', export, '--receipts', setup[3])
    assert result.returncode == 0
    assert d['overrides'] == d['override_reasons'] == 1
    assert d['request_match']['count'] == 0
    assert d['delegations'][0]['observed_tier'] == 'unknown_no_native_prefix_evidence'


def test_audit_provider_option_array(setup, tmp_path):
    _, receipt = record(setup)
    target = receipt['target'] | {'options': [{'id': 'effort', 'value': 'medium'}]}
    export = tmp_path / 'array.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [{'call_id': 'array', 'thread_id': 'parent-thread', 'timestamp': '2026-10-08T00:00:00Z', 'input': {'clientRequestId': 'one', 'target': target}, 'output': {'childRunId': 'actual'}, 'child_provider': 'claude-one', 'child_requested_model': 'claude-opus-5-5', 'child_requested_options': {'effort': 'medium'}}]}))
    result, d = run('audit', '--export', export, '--receipts', setup[3])
    assert result.returncode == 0
    assert d['effort_explicitness']['count'] == 1
    assert d['request_match']['count'] == 1
    assert d['delegations'][0]['child_request_match'] is True


@pytest.mark.parametrize('level', ['account', 'model'])
def test_nonrunnable_account_and_disabled_model_status(setup, level):
    value = json.loads(setup[1].read_text())
    provider = value['data']['providers'][1]
    if level == 'account':
        provider['runnable'] = False
    else:
        for model in provider['models']:
            model['status'] = 'disabled'
    setup[1].write_text(json.dumps(value))
    result, d = resolve(setup)
    assert result.returncode == 0 and d['target'] is None


def test_receipt_preserves_existing_parent_permissions(setup):
    receipts = setup[3]
    receipts.parent.mkdir(mode=0o755)
    assert record(setup)[0].returncode == 0
    assert receipts.parent.stat().st_mode & 0o777 == 0o755


def test_replay_keeps_runtime_snapshot_after_refresh(setup):
    _, first = record(setup)
    before = setup[3].read_bytes()
    value = json.loads(setup[1].read_text())
    value['generated_at'] = '2026-10-09T00:00:00Z'
    value['data']['providers'] = []
    setup[1].write_text(json.dumps(value))
    setup[2].write_text('{}')
    result, replay = record(setup)
    assert result.returncode == 0, result.stderr
    assert replay == first | {'replayed': True}
    assert before == setup[3].read_bytes()
    assert replay['availability'] == first['availability']
    assert replay['quota'] == first['quota']


def test_every_command_selects_env_policy_without_changing_show_contract(setup, tmp_path):
    pack = tmp_path / 'packs/model-choice-policy'
    pack.mkdir(parents=True)
    policy = setup[0]
    policy['policy_version'] = '9.9.9-test'
    op = next(o for o in policy['operating_points'] if o['id'] == 'implement.standard')
    op['expands_to']['effort'] = 'high'
    (pack / 'operating-points.json').write_text(json.dumps(policy))
    env = os.environ | {'DATA_PACKS_HOME': str(pack.parent)}
    result, _ = run('check', env=env)
    assert '9.9.9-test' in result.stdout
    result, shown = run('show', 'implement.standard', env=env)
    assert shown == op
    result, _ = run('list', env=env)
    assert 'high' in next(line for line in result.stdout.splitlines() if 'implement.standard' in line)
    result, shown = run('--policy', POLICY, 'show', 'implement.standard', env=env)
    assert shown == next(o for o in json.loads(POLICY.read_text())['operating_points'] if o['id'] == 'implement.standard')


@pytest.mark.parametrize('since', ['2026-10-08T00:00:00Z', '20261008T000000+00:00', '2026-10-07T19:00:00-05:00'])
def test_audit_anchors_on_call_started_at(setup, tmp_path, since):
    dbpath = tmp_path / 'anchor.sqlite'
    db = sqlite3.connect(dbpath)
    prefix = 'orchestration_v2_projection_'
    db.execute(f'create table {prefix}runs (run_id text,provider_instance_id text,status text,payload_json text,requested_at text)')
    db.execute(f'create table {prefix}turn_items (turn_item_id text,thread_id text,run_id text,updated_at text,status text,type text,payload_json text)')
    for name, requested, started in [('long', '2026-10-01T00:00:00Z', '2026-10-08T01:00:00Z'), ('old-call', '2026-10-01T00:00:00Z', '2026-10-07T23:00:00Z'), ('new', '2026-10-08T00:00:00Z', '2026-10-08T02:00:00Z')]:
        db.execute(f'insert into {prefix}runs values (?,?,?,?,?)', (name, 'parent-account', 'completed', '{}', requested))
        db.execute(f'insert into {prefix}turn_items values (?,?,?,?,?,?,?)', (name, 'parent-thread', name, '2026-10-09T00:00:00Z', 'completed', 'dynamic_tool', json.dumps({'startedAt': started, 'toolName': 'delegate_task', 'input': {}, 'output': {}})))
    db.commit(); db.close()
    result, report = run('audit', '--db', dbpath, '--since', since, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert [r['call_id'] for r in report['delegations']] == ['long', 'new']
    assert report['scope']['time_anchor'] == 'delegate_call.startedAt'
    assert report['scope']['since'] == '2026-10-08T00:00:00+00:00'
    assert report['delegations'][0]['timestamp'] == '2026-10-08T01:00:00Z'
    assert report['out_of_scope_delegations']['provider_native'] == {'count': None, 'state': 'unknown_table_absent'}


def test_unavailability_is_not_quality_escalation(setup):
    record(setup)
    before = setup[3].read_bytes()
    result, _ = record(setup, 'up', 'implement.accuracy-first', '--escalate-from', 'one', '--why', 'unavailable', '--reason', 'account unavailable', '--handoff-boundary', 'new child')
    assert result.returncode != 0
    assert 'quality escalation' in result.stderr
    assert before == setup[3].read_bytes()


def test_relaunch_lineage_and_distinct_audit_counts(setup, tmp_path):
    _, first = record(setup)
    before = setup[3].read_bytes()
    result, second = record(setup, 'two', 'implement.standard', '--relaunch-of', 'one', '--why', 'config', '--reason', 'corrected runtime mode')
    assert result.returncode == 0, result.stderr
    assert second['relaunch'] == {'from': 'one', 'why': 'config'}
    assert setup[3].read_bytes().startswith(before)
    _, replay = record(setup, 'two', 'implement.standard', '--relaunch-of', 'one', '--why', 'config', '--reason', 'corrected runtime mode')
    assert replay == second | {'replayed': True}
    export = tmp_path / 'relaunch.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [{'call_id': r['decision_id'], 'thread_id': 'parent-thread', 'timestamp': '2026-10-08T00:00:00Z', 'input': {'clientRequestId': r['decision_id'], 'target': r['target']}, 'output': {}} for r in (first, second)]}))
    result, report = run('audit', '--export', export, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert report['relaunches'] == 1 and report['escalations'] == 0
    assert report['delegations'][1]['relaunch'] == second['relaunch']
    assert report['coverage']['metric'] == 'app_owned_receipt_coverage'
    assert report['out_of_scope_delegations']['provider_native']['count'] is None


@pytest.mark.parametrize('source,why,extra', [('', None, []), ('missing', 'infra', []), ('bad id', 'infra', []), ('two', 'infra', []), ('one', 'oracle_failed', []), ('one', None, []), ('one', 'infra', ['--escalate-from', 'one']), ('one', 'infra', ['--handoff-boundary', 'new child'])])
def test_invalid_relaunch_leaves_receipts_unchanged(setup, source, why, extra):
    record(setup)
    before = setup[3].read_bytes()
    flags = ['--relaunch-of', source, '--reason', 'retry'] + extra
    if why:
        flags += ['--why', why]
    result, _ = record(setup, 'two', 'implement.standard', *flags)
    assert result.returncode != 0
    assert setup[3].read_bytes() == before


def test_auto_replay_uses_original_sources_and_legacy_receipt(setup, tmp_path):
    _, first = record(setup)
    # Existing receipts predate relaunch_of and policy_path.
    first['request'].pop('relaunch_of', None)
    first.pop('policy_path', None)
    setup[3].write_text(json.dumps(first) + '\n')
    before = setup[3].read_bytes()
    bins = tmp_path / 'bin'
    bins.mkdir()
    for name in ('t3code', 'clawmeter'):
        script = bins / name
        script.write_text('#!/bin/sh\nexit 99\n')
        script.chmod(0o700)
    flags = ['resolve', 'implement.standard', '--record', '--decision-id', 'one', '--parent-model', 'parent-model', '--parent-provider-instance', 'parent-account', '--parent-thread', 'parent-thread', '--receipts', setup[3], '--available', 'auto', '--quota', 'auto']
    result, replay = run(*flags, env=os.environ | {'PATH': str(bins) + os.pathsep + os.environ['PATH']})
    assert result.returncode == 0, result.stderr
    assert replay == first | {'replayed': True}
    assert setup[3].read_bytes() == before


@pytest.mark.parametrize('flags', [
    ['--account-hint', 'claude-one'], ['--quota-provider', 'claude'],
    ['--quota-source', 'other'], ['--parent-model', 'changed'],
    ['--parent-provider-instance', 'changed'], ['--parent-thread', 'changed'],
    ['--override', 'effort=high', '--reason', 'changed'],
])
def test_changed_caller_intent_rejected(setup, flags):
    record(setup)
    before = setup[3].read_bytes()
    result, _ = record(setup, 'one', 'implement.standard', *flags)
    assert result.returncode != 0 and 'changed inputs' in result.stderr
    assert setup[3].read_bytes() == before


def test_writable_existing_parent_refused_without_chmod(setup):
    setup[3].parent.mkdir()
    setup[3].parent.chmod(0o777)
    result, _ = record(setup)
    assert result.returncode != 0 and 'writable' in result.stderr
    assert setup[3].parent.stat().st_mode & 0o777 == 0o777
    assert not setup[3].exists()


@pytest.mark.parametrize('why', ['infra', 'config', 'unavailable'])
def test_relaunch_reasons_and_source_id_replay_protection(setup, why):
    record(setup)
    result, receipt = record(setup, 'two', 'implement.standard', '--relaunch-of', 'one', '--why', why, '--reason', 'runtime repair')
    assert result.returncode == 0, result.stderr
    assert receipt['relaunch'] == {'from': 'one', 'why': why}
    before = setup[3].read_bytes()
    result, _ = record(setup, 'two', 'implement.standard', '--relaunch-of', 'missing', '--why', why, '--reason', 'runtime repair')
    assert result.returncode != 0 and 'changed inputs' in result.stderr
    assert setup[3].read_bytes() == before


def test_concurrent_relaunch_records_once(setup):
    record(setup)
    before = setup[3].read_bytes()
    def relaunch(_):
        return record(setup, 'two', 'implement.standard', '--relaunch-of', 'one', '--why', 'infra', '--reason', 'runtime repair')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(relaunch, range(16)))
    assert all(r.returncode == 0 for r, _ in results)
    assert sum(not d.get('replayed', False) for _, d in results) == 1
    assert setup[3].read_bytes().startswith(before)
    assert len(setup[3].read_text().splitlines()) == 2


@pytest.mark.parametrize('relaunch_source,escalation_source', [('up', 'retry'), ('one', 'retry'), ('one', 'one')])
def test_relaunch_does_not_reset_quality_escalation_budget(setup, relaunch_source, escalation_source):
    record(setup)
    if escalation_source == 'one':
        record(setup, 'retry', 'implement.standard', '--relaunch-of', 'one', '--why', 'infra', '--reason', 'runtime repair')
        first_source = 'retry'
    else:
        first_source = 'one'
    result, _ = record(setup, 'up', 'implement.accuracy-first', '--escalate-from', first_source, '--why', 'oracle_failed', '--reason', 'oracle failed', '--handoff-boundary', 'fresh child')
    assert result.returncode == 0, result.stderr
    if escalation_source != 'one':
        result, _ = record(setup, 'retry', 'implement.standard', '--relaunch-of', relaunch_source, '--why', 'infra', '--reason', 'runtime repair')
        assert result.returncode == 0, result.stderr
    before = setup[3].read_bytes()
    result, _ = record(setup, 'second-up', 'review.audit', '--escalate-from', escalation_source, '--why', 'judged_insufficient', '--reason', 'still failing', '--handoff-boundary', 'fresh child')
    assert result.returncode != 0 and 'one linked escalation' in result.stderr
    assert setup[3].read_bytes() == before


def test_escalation_budget_locked_across_relaunch_branch(setup):
    record(setup)
    record(setup, 'retry', 'implement.standard', '--relaunch-of', 'one', '--why', 'infra', '--reason', 'runtime repair')
    def escalate(i):
        return record(setup, f'up-{i}', 'implement.accuracy-first', '--escalate-from', 'one' if i % 2 else 'retry', '--why', 'oracle_failed', '--reason', 'oracle failed', '--handoff-boundary', 'new child')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(escalate, range(16)))
    assert sum(r.returncode == 0 for r, _ in results) == 1
    assert len(setup[3].read_text().splitlines()) == 3


def test_auto_record_replay_after_source_refresh(setup, tmp_path):
    bins = tmp_path / 'bin'
    bins.mkdir()
    for name, source in [('t3code', setup[1]), ('clawmeter', setup[2])]:
        script = bins / name
        script.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nprint(Path(' + repr(str(source)) + ').read_text())\n')
        script.chmod(0o700)
    flags = ['resolve', 'implement.standard', '--record', '--decision-id', 'auto-one', '--parent-model', 'parent-model', '--parent-provider-instance', 'parent-account', '--parent-thread', 'parent-thread', '--receipts', setup[3], '--available', 'auto', '--quota', 'auto']
    env = os.environ | {'PATH': str(bins) + os.pathsep + os.environ['PATH']}
    result, first = run(*flags, env=env)
    assert result.returncode == 0, result.stderr
    before = setup[3].read_bytes()
    setup[1].write_text('{}')
    setup[2].write_text('{"fetched_at":"2026-10-09T00:00:00Z"}')
    result, replay = run(*flags, env=env)
    assert result.returncode == 0, result.stderr
    assert replay == first | {'replayed': True}
    assert setup[3].read_bytes() == before


def audit_export(setup, tmp_path, receipt, rows):
    export = tmp_path / 'audit-regression.json'
    base = {'thread_id': 'parent-thread', 'timestamp': '2026-10-08T00:00:00Z',
            'parent_provider': 'parent-account', 'parent_model': 'parent-model',
            'input': {'clientRequestId': receipt['decision_id'], 'target': receipt['target']}, 'output': {}}
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [base | row for row in rows]}))
    return run('audit', '--export', export, '--receipts', setup[3])


@pytest.mark.parametrize('reverse', [False, True])
def test_audit_rejects_duplicate_receipt_ids(setup, tmp_path, reverse):
    _, receipt = record(setup)
    records = [receipt, receipt | {'target': receipt['target'] | {'model': 'other'}}]
    setup[3].write_text(''.join(json.dumps(r) + '\n' for r in (reversed(records) if reverse else records)))
    result, _ = audit_export(setup, tmp_path, receipt, [{'call_id': 'one'}])
    assert result.returncode != 0 and 'duplicate decision_id' in result.stderr


def test_audit_parent_thread_and_receipt_before_call(setup, tmp_path):
    _, receipt = record(setup)
    receipt['recorded_at'] = '2026-10-08T00:00:00Z'
    setup[3].write_text(json.dumps(receipt) + '\n')
    result, report = audit_export(setup, tmp_path, receipt, [
        {'call_id': 'wrong-thread', 'thread_id': 'other-thread'},
        {'call_id': 'too-late', 'timestamp': '2026-10-07T23:59:59Z'},
        {'call_id': 'after', 'timestamp': '2026-10-08T00:00:01Z'}])
    assert result.returncode == 0, result.stderr
    assert report['delegations'][0]['parent_match'] == {'thread': False, 'provider': True, 'model': True}
    assert [r['receipt_before_call'] for r in report['delegations']] == [True, False, True]


def test_audit_retry_counts_and_multiple_child_flag(setup, tmp_path):
    _, receipt = record(setup, 'one', 'implement.standard', '--override', 'effort=high', '--reason', 'hard patch')
    receipt['escalation'] = {'from': 'source', 'why': 'oracle_failed'}
    receipt['relaunch'] = {'from': 'source', 'why': 'infra'}
    setup[3].write_text(json.dumps(receipt) + '\n')
    result, report = audit_export(setup, tmp_path, receipt, [
        {'call_id': 'a', 'output': {'childRunId': 'child-a'}},
        {'call_id': 'b', 'output': {'childRunId': 'child-a'}},
        {'call_id': 'c', 'output': {'childRunId': 'child-b'}},
        {'call_id': 'failed', 'status': 'failed', 'output': None}])
    assert result.returncode == 0, result.stderr
    assert report['counts'] == {'call_attempts': 4, 'unique_child_runs': 2, 'distinct_receipt_decisions': 1}
    assert report['overrides'] == report['override_reasons'] == report['escalations'] == report['relaunches'] == 1
    assert report['repeated_decision_ids_multiple_children'] == [{'decision_id': 'one', 'child_run_ids': ['child-a', 'child-b']}]
    assert all(r['decision_id_multiple_children'] for r in report['delegations'])


@pytest.mark.parametrize('options', ['x', [], None, [{'id': 'effort', 'value': 'medium'}, {'id': 'effort', 'value': 'medium'}], [{'id': [], 'value': 'medium'}]])
def test_audit_malformed_options_never_match(setup, tmp_path, options):
    _, receipt = record(setup)
    receipt['target']['options'] = options
    setup[3].write_text(json.dumps(receipt) + '\n')
    result, report = audit_export(setup, tmp_path, receipt, [{'call_id': 'malformed', 'output': {'childRunId': 'child'}, 'child_provider': receipt['target']['providerInstanceId'], 'child_requested_model': receipt['target']['model'], 'child_requested_options': options}])
    assert result.returncode == 0, result.stderr
    row = report['delegations'][0]
    assert row['request_match'] is False and row['child_request_match'] is False
    assert row['options_state'] == {'call': 'malformed', 'receipt': 'malformed', 'child': 'malformed'}


def test_audit_observed_out_of_scope_projection_counts(setup, tmp_path):
    dbpath = tmp_path / 'counts.sqlite'
    db = sqlite3.connect(dbpath)
    prefix = 'orchestration_v2_projection_'
    db.execute(f'create table {prefix}runs (run_id text,provider_instance_id text,status text,payload_json text,requested_at text)')
    db.execute(f'create table {prefix}turn_items (turn_item_id text,thread_id text,run_id text,updated_at text,status text,type text,payload_json text)')
    db.execute(f'create table {prefix}subagents (subagent_id text,thread_id text,origin text,started_at text,payload_json text)')
    for name, thread, time, origin in [('yes', 'parent-thread', '2026-10-08T01:00:00Z', 'provider_native'), ('other', 'other-thread', '2026-10-08T01:00:00Z', 'provider_native'), ('old', 'parent-thread', '2026-10-07T23:00:00Z', 'provider_native'), ('app', 'parent-thread', '2026-10-08T01:00:00Z', 'app_owned')]:
        db.execute(f'insert into {prefix}subagents values (?,?,?,?,?)', (name, thread, origin, time, '{"prompt":"SECRET NATIVE PROMPT"}'))
    for name, thread, started, status, tool in [('completed', 'parent-thread', '2026-10-08T01:00:00Z', 'completed', 't3_thread_launch'), ('failed', 'parent-thread', '2026-10-08T02:00:00Z', 'failed', 'mcp__t3_code__t3_thread_launch'), ('dotted', 'parent-thread', '2026-10-08T03:00:00Z', 'failed', 't3-code.t3_thread_launch'), ('old', 'parent-thread', '2026-10-07T23:00:00Z', 'completed', 't3_thread_launch'), ('other', 'other-thread', '2026-10-08T01:00:00Z', 'completed', 't3_thread_launch'), ('unrelated', 'parent-thread', '2026-10-08T01:00:00Z', 'completed', 'different_tool')]:
        db.execute(f'insert into {prefix}turn_items values (?,?,?,?,?,?,?)', (name, thread, 'parent', '2026-10-09T00:00:00Z', status, 'dynamic_tool', json.dumps({'startedAt': started, 'toolName': tool, 'input': {'prompt': 'SECRET LAUNCH PROMPT'}})))
    db.commit(); db.close()
    before = dbpath.read_bytes()
    result, report = run('audit', '--db', dbpath, '--since', '20261008T000000+00:00', '--thread', 'parent-thread', '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert report['out_of_scope_delegations'] == {'provider_native': {'count': 1, 'state': 'observed_t3_projection_records'}, 'top_level_threads': {'count': 3, 'state': 'observed_t3_projection_records'}}
    assert report['coverage']['denominator'] == 0
    assert 'SECRET' not in result.stdout and dbpath.read_bytes() == before


@pytest.mark.parametrize('options', ['x', 7, False])
def test_audit_database_malformed_child_options(setup, tmp_path, options):
    _, receipt = record(setup)
    dbpath = tmp_path / 'malformed.sqlite'
    db = sqlite3.connect(dbpath)
    prefix = 'orchestration_v2_projection_'
    db.execute(f'create table {prefix}runs (run_id text,provider_instance_id text,status text,payload_json text)')
    db.execute(f'create table {prefix}turn_items (turn_item_id text,thread_id text,run_id text,status text,type text,payload_json text)')
    db.execute(f'insert into {prefix}runs values (?,?,?,?)', ('child', receipt['target']['providerInstanceId'], 'completed', json.dumps({'modelSelection': {'model': receipt['target']['model'], 'options': options}})))
    db.execute(f'insert into {prefix}turn_items values (?,?,?,?,?,?)', ('call', 'parent-thread', 'missing-parent', 'completed', 'dynamic_tool', json.dumps({'startedAt': '2026-10-08T00:00:00Z', 'toolName': 'delegate_task', 'input': {'clientRequestId': 'one', 'target': receipt['target']}, 'output': {'childRunId': 'child'}})))
    db.commit(); db.close()
    result, report = run('audit', '--db', dbpath, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert report['delegations'][0]['child_request_match'] is False
    assert report['delegations'][0]['options_state']['child'] == 'malformed'
