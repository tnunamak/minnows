"""End-to-end joins across route, dispatch, close, observe and audit."""
import json
import sqlite3

import pytest

from test_observe import facts, ok, paths
from test_policy_decisions import run, setup  # noqa: F401
from test_policy_outcomes import COMPLETE, close, git_repo, make_db, outcomes_path
from test_quota_substitution import QuotaWorld
from test_route import World as RouteWorld
from test_route_destinations import capacity, config, entry, script


@pytest.fixture
def world(tmp_path):
    return RouteWorld(tmp_path)


def export_for(receipt, *, target=None, thread='pt'):
    return {'schema_version': 1, 'delegations': [{
        'call_id': f"call-{receipt['decision_id']}",
        'thread_id': thread,
        'timestamp': '2026-10-10T00:20:00Z',
        'input': {'clientRequestId': receipt['decision_id'], 'target': target or receipt['target']},
        'output': {'childRunId': 'child'},
    }]}


def write_export(tmp_path, receipt, *, target=None, thread='pt'):
    path = tmp_path / 'export.json'
    path.write_text(json.dumps(export_for(receipt, target=target, thread=thread)))
    return path


def child_db(tmp_path, receipt, *, target=None):
    """Use the existing T3 fixture builder, then align its child run to this route receipt."""
    target = target or receipt['target']
    fixture_dir = tmp_path / 'child-fixture'
    fixture_dir.mkdir(exist_ok=True)
    db_path = make_db(fixture_dir, receipt, [('a1', COMPLETE)])
    db = sqlite3.connect(db_path)
    row = db.execute('select payload_json from orchestration_v2_projection_runs where run_id = ?', ('child',)).fetchone()
    payload = json.loads(row[0])
    payload['modelSelection'] = {'model': target['model'], 'options': target['options']}
    db.execute('update orchestration_v2_projection_runs set provider_instance_id=?, payload_json=? where run_id=?',
               (target['providerInstanceId'], json.dumps(payload), 'child'))
    if target != receipt['target']:
        call_row = db.execute('select payload_json from orchestration_v2_projection_turn_items where type = ?', ('dynamic_tool',)).fetchone()
        call = json.loads(call_row[0])
        call['input']['target'] = target
        db.execute('update orchestration_v2_projection_turn_items set payload_json=? where type=?', (json.dumps(call), 'dynamic_tool'))
    db.commit()
    db.close()
    return db_path


def audit_export(setup, tmp_path, export, *extra):
    return run('audit', '--export', export, '--receipts', setup[3], *extra)


def test_model_route_dispatch_close_observe_audit_join(setup, world, tmp_path, git_repo):
    world.route('implement.quota-tight', record='one')
    receipt = json.loads(world.receipts.read_text().splitlines()[0])
    export = write_export(tmp_path, receipt)
    repo, head = git_repo
    result, closed = close(setup, 'one', 'c1', '--evidence', json.dumps({'type': 'commit', 'repo': str(repo), 'sha': head}))
    assert result.returncode == 0, result.stderr
    assert closed['evidence_verification'][0]['state'] == 'exists'

    call_result, before = audit_export(setup, tmp_path, export)
    assert call_result.returncode == 0, call_result.stderr
    assert before['route_coverage']['route'] == 1
    assert before['request_match']['count'] == 1
    assert before['delegations'][0]['request_match'] is True
    assert before['outcomes']['close_coverage']['decisions_closed'] == 1
    assert before['outcomes']['commit_evidence_verification']['exists'] == 1

    db = child_db(tmp_path, receipt)
    observation = ok(setup, '--db', db)
    assert observation['sources']['t3']['state'] == 'ok'
    assert observation['sources']['git']['state'] == 'ok'
    result, after = audit_export(setup, tmp_path, export, '--observations', paths(setup)[1])
    assert result.returncode == 0, result.stderr
    assert after['observations']['coverage']['t3'] == {'decisions_with_observation': 1, 'decisions_in_scope': 1}
    assert after['observations']['coverage']['git'] == {'decisions_with_observation': 1, 'decisions_in_scope': 1}
    assert after['outcomes']['outcome_distribution'] == before['outcomes']['outcome_distribution']
    assert facts(setup, 't3', 't3_requested_config')[0]['fact']['requested']['model'] == receipt['target']['model']
    assert outcomes_path(setup).exists() and paths(setup)[1].exists()


def test_destination_route_submission_observation_and_audit_join(setup, world, tmp_path):
    cap = capacity(tmp_path, 'cap')
    lifecycle = {'found': True, 'status': 'finished', 'task_kind': 'review', 'source': 'agent',
                 'created_at': '2026-10-10T13:00:00+00:00', 'cancelled_at': None, 'cancelled_reason': None,
                 'reports': [], 'events': [{'event_id': 1, 'type': 'queued', 'at': '2026-10-10T13:01:00+00:00',
                                            'actor_kind': 'owner', 'reason': 'accepted'}]}
    response = json.dumps({'schema_version': 1, 'requests': {'dest1': lifecycle}})
    observer = script(tmp_path, 'observe.sh', f"cat >/dev/null; echo '{response}'")
    cfg = config(tmp_path, entry(cap) | {'observe_command': [str(observer)]})
    routed = world.route('review.audit', '--destinations', cfg, '--purpose', 'review', '--inputs', 'github', record='dest1')
    receipt = json.loads(world.receipts.read_text().splitlines()[0])
    assert routed['destination'] == 'worker' and receipt['decision_id'] == 'dest1'

    result, observed = run('observe', '--receipts', setup[3], '--destinations', cfg)
    assert result.returncode == 0, result.stderr
    assert observed['sources']['destination:worker']['state'] == 'ok'
    export = tmp_path / 'empty-export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': []}))
    result, audit = run('audit', '--export', export, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert audit['destination_routes']['worker']['decisions'] == 1
    assert audit['spend_first']['offered'] == 1
    assert 'dest1' not in audit['unmatched_receipts']
    assert audit['observations']['coverage']['destination:worker'] == {'decisions_with_observation': 1, 'decisions_in_scope': 1}
    assert any(row['source_ids'].get('request_id') == 'dest1' for row in facts(setup, 'destination:worker', 'destination_event'))


def test_mismatched_delegate_target_does_not_hide_observed_child_config(setup, world, tmp_path):
    world.route('implement.quota-tight', record='one')
    receipt = json.loads(world.receipts.read_text().splitlines()[0])
    changed_model = 'gpt-6.1-sol' if receipt['target']['model'] != 'gpt-6.1-sol' else 'claude-opus-5-5'
    changed_target = dict(receipt['target'], model=changed_model)
    export = write_export(tmp_path, receipt, target=changed_target)
    db = child_db(tmp_path, receipt, target=changed_target)

    result, audit = audit_export(setup, tmp_path, export)
    assert result.returncode == 0, result.stderr
    assert audit['delegations'][0]['request_match'] is False
    observed = ok(setup, '--db', db)
    assert observed['sources']['t3']['state'] == 'ok'
    requested = facts(setup, 't3', 't3_requested_config')[0]['fact']['requested']
    assert requested['model'] == changed_target['model']
    assert requested['provider_instance'] == changed_target['providerInstanceId']


def test_superseded_decision_relaunch_joins_audit(setup, world, tmp_path):
    world.route('implement.quota-tight', record='one')
    result, _ = close(setup, 'one', 'c1', outcome='superseded')
    assert result.returncode == 0, result.stderr
    second, relaunch = world.cli('route', 'implement.quota-tight', *world.args(), '--relaunch-of', 'one', '--why', 'config', '--reason', 'repair runtime',
                                 '--decision-id', 'two', '--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt')
    assert second.returncode == 0, second.stderr
    result, _ = close(setup, 'two', 'c2', outcome='accepted')
    assert result.returncode == 0, result.stderr
    export = tmp_path / 'relaunch-export.json'
    export.write_text(json.dumps(export_for(relaunch)))

    result, audit = audit_export(setup, tmp_path, export)
    assert result.returncode == 0, result.stderr
    # Only the relaunch was dispatched; the superseded first decision stays visible as an unmatched receipt.
    assert audit['counts']['distinct_receipt_decisions'] == 1 and audit['relaunches'] == 1
    assert audit['unmatched_receipts'] == ['one']
    assert audit['outcomes']['outcome_distribution']['closed'] == {'accepted': 1}
    # superseded_closes counts close corrections (--supersedes), not the outcome `superseded`.
    assert audit['outcomes']['superseded_closes'] == 0
    assert relaunch['relaunch'] == {'from': 'one', 'why': 'config'}


def test_quota_substituted_receipt_joins_audit_and_observe(setup, tmp_path):
    world = QuotaWorld(tmp_path)
    receipt_out = world.route('review.audit', record='one')
    receipt = json.loads(world.receipts.read_text().splitlines()[0])
    assert receipt_out['routing']['quota_substitution']['substituted'] is True
    assert receipt['routing']['quota_substitution']['substituted'] is True
    export = write_export(tmp_path, receipt)
    db = child_db(tmp_path, receipt)

    result, audit = audit_export(setup, tmp_path, export)
    assert result.returncode == 0, result.stderr
    assert audit['route_coverage']['route'] == 1
    assert audit['request_match']['count'] == 1 and audit['delegations'][0]['request_match'] is True
    observe_result = ok(setup, '--db', db)
    assert observe_result['sources']['t3']['state'] == 'ok'
    assert facts(setup, 't3', 't3_requested_config')[0]['fact']['requested']['model'] == receipt['target']['model']
    assert receipt['routing']['quota_substitution']['thresholds'] == {'activate_projected_pct': 95, 'release_projected_pct': 90}
