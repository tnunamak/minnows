"""Owner-authorized free-route trial: closes, rework observation, and the retrospective signal.

The trial changes no primary arm. What it adds is an obligation: every free-route result needs an
explicit close, an explicit repairs count and a rework observation, and the expansion's threshold is
measured over unique consumer tasks. These tests pin that machinery, including the cases where the
honest answer is `unknown` rather than zero.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

from test_route import DEEPSEEK_FREE, PROVENANCE, add_opencode_fixture, world  # noqa: F401
from test_route_authorization import CLOCK, run
from test_route_governed_destinations import no_submission


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools' / 'model-policy-ops' / 'lib'))
import decision_receipts as receipts  # noqa: E402
import outcomes  # noqa: E402


POLICY = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
TASK_LINK = '{"v":1,"consumer_task_id":"task-a","workflow_id":"wf","stage_id":"make","role":"maker"}'


def task_link(task='t1', stage='make', role='maker', workflow='w1'):
    return {'v': 1, 'consumer_task_id': task, 'workflow_id': workflow, 'stage_id': stage, 'role': role}


def task_result(task='t1', supersedes=None, evidence=(0,)):
    return {'v': 1, 'consumer_task_id': task, 'supersedes_task_close_id': supersedes,
            'standard_evidence': list(evidence)}


def evidence_file(tmp_path):
    standard = tmp_path / 'standard.txt'
    standard.write_text('Synthetic acceptance-standard fixture. Each close is a caller claim.\n')
    return {'type': 'file', 'path': str(standard), 'sha256': hashlib.sha256(standard.read_bytes()).hexdigest()}


@pytest.fixture
def ledger(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path / 'receipts.jsonl', tmp_path / 'outcomes.jsonl', evidence_file(tmp_path)


def free_receipt(decision, task='t1', role='maker', stage=None, op='docs.lookup', effort='high', purpose='research',
                 proof_class=None):
    """A stored free-route decision, shaped the way the router stores one."""
    facts = {'purpose': purpose, **({'proof_class': proof_class} if proof_class else {})}
    value = {'schema_version': 1, 'decision_id': decision, 'recorded_at': '2026-10-15T00:00:00+00:00',
             'request': {'op': op},
             'selection': {'arm': {'effort': effort}},
             'launch_facts': facts,
             'data_policy': {'state': 'allowed_public_experimental', 'route': DEEPSEEK_FREE, 'account': 'opencode'}}
    if task is not None:
        value['task_link'] = task_link(task, stage=(stage or role), role=role)
    return value

def paid_receipt(decision, task='t1', role='maker'):
    return {'schema_version': 1, 'decision_id': decision, 'recorded_at': '2026-10-15T00:00:00+00:00',
            'request': {'op': 'docs.lookup'},
            'data_policy': {'state': 'allowed', 'route': 'claude/claude-haiku-5-5', 'account': 'claudeAgent'},
            'task_link': task_link(task, role=role)}


def put(ledger, value):
    return receipts.append_receipt(ledger[0], value)


def close_args(ledger, decision, close_id, result=None, supersedes=None, outcome='accepted', repairs=0, evidence=None,
               check='independent_judged', judged_by='parent'):
    refs = [json.dumps(ledger[2])] if evidence is None else evidence
    return argparse.Namespace(
        receipts=ledger[0], outcomes=ledger[1], op=decision, close_id=close_id, outcome=outcome,
        judged_by=judged_by, check=check, owner_input=None, repairs=repairs, evidence=refs,
        note=None, reason='Synthetic correction' if supersedes else None, closer_thread=None, supersedes=supersedes,
        task_result=json.dumps(result) if result is not None else None)


def close(ledger, decision, close_id, **kwargs):
    return outcomes.write_close(close_args(ledger, decision, close_id, **kwargs),
                                receipts.find_receipt(ledger[0], decision))


def followup(ledger, decision, followup_id, finding='no_rework_found', scope='whole change', evidence=None):
    args = argparse.Namespace(receipts=ledger[0], outcomes=ledger[1], op=decision, followup_id=followup_id,
                              finding=finding, checked_scope=scope, evidence=evidence or [])
    return outcomes.write_followup(args, receipts.find_receipt(ledger[0], decision))


def retro(ledger, threshold=None):
    return outcomes.free_route_trial_retro(receipts.read_receipts(ledger[0]), outcomes.read_outcomes(ledger[1]),
                                           POLICY, threshold)


def pending(ledger, consumer_task_id=None):
    return outcomes.free_route_pending(receipts.read_receipts(ledger[0]), outcomes.read_outcomes(ledger[1]),
                                       consumer_task_id)


def completion(ledger, consumer_task_id):
    return outcomes.free_route_completion(receipts.read_receipts(ledger[0]), outcomes.read_outcomes(ledger[1]),
                                          consumer_task_id)


def decision_summary(ledger, decision_id):
    return next(d for d in outcomes.free_route_decisions(receipts.read_receipts(ledger[0]))
                if d['decision_id'] == decision_id)


def test_retro_counts_one_task_across_maker_checker_and_repair_receipts(ledger):
    put(ledger, free_receipt('maker', role='maker'))
    put(ledger, free_receipt('checker', role='checker'))
    put(ledger, free_receipt('repair', role='repair', stage='repair'))
    close(ledger, 'maker', 'm', outcome='rejected')
    close(ledger, 'checker', 'c')
    close(ledger, 'repair', 'r', result=task_result(), outcome='accepted_after_repair', repairs=1)
    report = retro(ledger)
    assert report['unique_closed_tasks'] == 1
    assert report['accepted_after_repair'] == 1 and report['accepted'] == 0 and report['rejected'] == 0
    assert report['by_op_effort']['docs.lookup@high']['tasks'] == 1
    assert report['counted_tasks'][0]['decisions'] == ['checker', 'maker', 'repair']
    assert report['legacy_decision_level']['decisions'] == 0


def test_retro_does_not_count_an_unclosed_task(ledger):
    put(ledger, free_receipt('d1'))
    report = retro(ledger)
    assert report['unique_closed_tasks'] == 0 and report['unclosed_tasks'] == 1
    assert report['due'] is False


def test_retro_reports_a_conflicting_metadata_task_as_broken_not_unclosed(ledger):
    put(ledger, free_receipt('d1'))
    put(ledger, free_receipt('d2'))
    put(ledger, free_receipt('d3', task='t2'))
    # The live writer refuses a whole-task claim while the task still owes a close, so the conflicting
    # pair -- two free-route receipts claiming one task -- enters only through history. Both carry the
    # claim, so the task is broken: never counted and never called merely unclosed.
    close(ledger, 'd1', 'a')
    rows = [json.loads(line) for line in ledger[1].read_text().splitlines()]
    rows[0]['task_result'] = task_result()
    rows.append({**rows[0], 'close_id': 'b', 'decision_id': 'd2'})
    ledger[1].write_text(''.join(json.dumps(row) + '\n' for row in rows))
    report = retro(ledger)
    assert report['unique_closed_tasks'] == 0
    assert report['broken_tasks'] == 1 and report['unclosed_tasks'] == 1
    assert report['by_op_effort']['docs.lookup@high'] == {
        'tasks': 0, 'accepted': 0, 'accepted_after_repair': 0, 'rejected': 0,
        'other_closed_tasks': 0, 'unclosed_tasks': 1, 'broken_tasks': 1}
    assert retro(ledger) == report


def test_retro_keeps_a_superseded_revision_out_of_the_task_count(ledger):
    put(ledger, free_receipt('d1'))
    close(ledger, 'd1', 'a', result=task_result())
    close(ledger, 'd1', 'b', result=task_result(supersedes='a'), outcome='rejected', supersedes='a')
    report = retro(ledger)
    assert report['unique_closed_tasks'] == 1 and report['rejected'] == 1 and report['accepted'] == 0
    assert report['counted_tasks'][0]['current_close_id'] == 'b'
    assert report['counted_tasks'][0]['decisions'] == ['d1']


def test_retro_reports_missing_rework_as_unknown_never_zero(ledger):
    put(ledger, free_receipt('d1'))
    close(ledger, 'd1', 'a', result=task_result())
    report = retro(ledger)
    assert report['rework_observed'] == 0 and report['rework_unknown'] == 1
    followup(ledger, 'd1', 'f1')
    after = retro(ledger)
    assert after['rework_observed'] == 1 and after['rework_unknown'] == 0
    assert after['unique_closed_tasks'] == 1


def test_a_free_route_close_requires_an_explicit_repairs_count(ledger):
    put(ledger, free_receipt('d1'))
    with pytest.raises(ValueError, match='explicit --repairs'):
        close(ledger, 'd1', 'a', result=task_result(), repairs=None)
    # 0 is a value, not an omission.
    close(ledger, 'd1', 'a', result=task_result(), repairs=0)


def test_retro_reports_a_missing_repairs_count_without_inventing_zero(ledger):
    put(ledger, free_receipt('d1'))
    # A legacy row can lack the count, but the live writer refuses to write a new one, so the gap enters
    # only through history and is reported, never filled with a zero the caller never stated.
    close(ledger, 'd1', 'a', result=task_result(), repairs=0)
    row = json.loads(ledger[1].read_text().strip())
    row['repairs'] = None
    ledger[1].write_text(json.dumps(row) + '\n')
    assert retro(ledger)['repairs_missing'] == 1
    put(ledger, free_receipt('d2', task='t2'))
    close(ledger, 'd2', 'b', result=task_result('t2'), outcome='accepted_after_repair', repairs=2)
    assert retro(ledger)['repairs_missing'] == 1


def test_a_judged_free_route_close_requires_an_independent_judge(ledger):
    put(ledger, free_receipt('d1', proof_class='judged'))
    with pytest.raises(ValueError, match='independent_judged'):
        close(ledger, 'd1', 'a', result=task_result(), repairs=0, check='maker_judged', judged_by='parent')
    close(ledger, 'd1', 'a', result=task_result(), repairs=0, check='independent_judged', judged_by='independent')


def test_a_whole_task_completion_claim_is_refused_until_every_obligation_is_closed(ledger):
    put(ledger, free_receipt('maker', role='maker'))
    put(ledger, free_receipt('checker', role='checker'))
    put(ledger, free_receipt('repair', role='repair', stage='repair'))
    # A whole-task claim is refused while a sibling decision of the same consumer task still owes a close.
    with pytest.raises(ValueError, match='completion claim refused'):
        close(ledger, 'maker', 'm', result=task_result(), repairs=0)
    # A stage close makes no whole-task claim, so it is always allowed.
    stage = close(ledger, 'maker', 'm', outcome='rejected', repairs=1)
    assert stage['free_route']['consumer_task_id'] == 't1'
    assert stage['free_route']['completion']['state'] == 'incomplete'
    close(ledger, 'checker', 'c', repairs=0)
    # Once every obligation is closed, the repair carries the claim and records completion as-of-close.
    record = close(ledger, 'repair', 'r', result=task_result(), outcome='accepted_after_repair', repairs=1)
    assert record['free_route']['completion']['state'] == 'complete'
    assert retro(ledger)['unique_closed_tasks'] == 1


def test_legacy_receipts_without_a_task_link_are_a_separate_cohort(ledger):
    put(ledger, free_receipt('legacy', task=None))
    report = retro(ledger)
    assert report['unique_closed_tasks'] == 0
    assert report['legacy_decision_level'] == {'decisions': 1,
                                               'cohort': 'free-route receipts without a task link; excluded from the task count'}


def test_matched_and_unmatched_comparator_cohorts_stay_distinct(ledger):
    put(ledger, free_receipt('free-matched', task='t1'))
    put(ledger, paid_receipt('paid-matched', task='t1'))
    put(ledger, free_receipt('free-alone', task='t2'))
    close(ledger, 'free-matched', 'a', result=task_result('t1'))
    close(ledger, 'free-alone', 'b', result=task_result('t2'), outcome='rejected')
    report = retro(ledger)
    assert report['comparator']['matched_task_ids'] == ['t1']
    assert report['comparator']['unmatched_task_ids'] == ['t2']
    assert 'Haiku' in report['comparator']['note'] and 'paid cheap arm' in report['comparator']['note']


def test_the_threshold_signal_is_deterministic_idempotent_and_flips_once(ledger):
    put(ledger, free_receipt('d1', task='t1'))
    close(ledger, 'd1', 'a', result=task_result('t1'))
    first = retro(ledger, threshold=2)
    assert first['due'] is False and first['unique_closed_tasks'] == 1
    put(ledger, free_receipt('d2', task='t2'))
    close(ledger, 'd2', 'b', result=task_result('t2'))
    second = retro(ledger, threshold=2)
    assert second['due'] is True and second['threshold'] == 2
    before = (ledger[0].read_bytes(), ledger[1].read_bytes())
    assert retro(ledger, threshold=2) == second
    assert (ledger[0].read_bytes(), ledger[1].read_bytes()) == before


def test_the_threshold_defaults_to_the_pack_authority_record(ledger):
    assert retro(ledger)['threshold'] == POLICY['experimental_free_routes'][0]['selection']['threshold'] == 50
    assert retro(ledger)['authorization'] == 'owner_authorized_trial_2026-10-15'
    assert 'Owner authorized' in retro(ledger)['authority_note']


def test_pending_closes_names_the_missing_explicit_close_and_rework_observation(ledger):
    put(ledger, free_receipt('d1', task='t1'))
    assert pending(ledger) == {'count': 1, 'decisions': [{'decision_id': 'd1', 'op': 'docs.lookup', 'effort': 'high',
                                                         'purpose': 'research', 'consumer_task_id': 't1',
                                                         'missing': ['close', 'rework_observation']}]}
    close(ledger, 'd1', 'a', result=task_result('t1'))
    assert pending(ledger)['decisions'][0]['missing'] == ['rework_observation']
    followup(ledger, 'd1', 'f1')
    assert pending(ledger) == {'count': 0, 'decisions': []}


def test_a_paid_decision_does_not_need_a_task_link(world):
    add_opencode_fixture(world)
    out = world.route('implement.quota-tight', record='paid')
    assert out['launch_ready'] is True and 'task_link' not in json.dumps(out['request'])


def test_a_recorded_free_route_decision_requires_a_task_link(world):
    add_opencode_fixture(world)
    argv = ['route', 'docs.lookup', *world.args(), '--input-provenance', PROVENANCE, '--data-classification', 'public',
            '--proof-class', 'oracle', '--record', '--decision-id', 'free-record', '--parent-model', 'pm',
            '--parent-provider-instance', 'pp', '--parent-thread', 'pt']
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    assert process.returncode == 1 and out is None and '--task-link' in process.stderr
    assert not world.receipts.exists() or b'free-record' not in world.receipts.read_bytes()
    process, out = world.cli(*argv, '--task-link', TASK_LINK, evaluation_time=CLOCK)
    assert process.returncode == 0, process.stderr
    assert out['task_link']['consumer_task_id'] == 'task-a'


def test_an_allowed_free_route_states_the_close_obligation(world):
    add_opencode_fixture(world)
    out = run(world, 'route', 'docs.lookup', *world.args(), '--input-provenance', PROVENANCE,
              '--data-classification', 'public', '--proof-class', 'oracle', '--reason', 'public task')
    assert out['data_policy']['state'] == 'allowed_public_experimental'
    assert out['data_policy']['close_required'] == {
        'close_required': True, 'task_link_required': True, 'outcomes': ['accepted', 'accepted_after_repair', 'rejected'],
        'repairs_required': True, 'rework_observation': 'required_when_known',
        'missing_rework_semantics': 'absent follow-up is reported unknown, never zero',
        'reason': 'owner-authorized trial: each free-route result needs an explicit close, an explicit repairs count and a rework observation'}


def test_an_open_close_is_surfaced_at_the_route_boundary(world):
    add_opencode_fixture(world)
    shared = ['docs.lookup', *world.args(), '--input-provenance', PROVENANCE, '--data-classification', 'public',
              '--proof-class', 'oracle', '--reason', 'public task']
    record, first = world.cli('route', *shared, '--record', '--decision-id', 'free-open', '--task-link', TASK_LINK,
                              '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt',
                              evaluation_time=CLOCK)
    assert record.returncode == 0, record.stderr
    assert first['routing']['trial']['pending_closes'] == {'count': 0, 'decisions': []}
    second = run(world, 'route', *shared)
    assert second['data_policy']['state'] == 'allowed_public_experimental'
    surfaced = second['routing']['trial']['pending_closes']
    assert surfaced['count'] == 1 and surfaced['decisions'][0]['decision_id'] == 'free-open'
    assert surfaced['decisions'][0]['missing'] == ['close', 'rework_observation']


def test_the_route_boundary_scopes_open_obligations_to_the_requests_consumer_task(world):
    add_opencode_fixture(world)
    shared = ['docs.lookup', *world.args(), '--input-provenance', PROVENANCE, '--data-classification', 'public',
              '--proof-class', 'oracle', '--reason', 'public task']
    link_a = json.dumps(task_link('task-a'))
    link_b = json.dumps(task_link('task-b'))
    record, first = world.cli('route', *shared, '--record', '--decision-id', 'free-a', '--task-link', link_a,
                              '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt',
                              evaluation_time=CLOCK)
    assert record.returncode == 0, record.stderr
    # The same consumer task's request sees exactly its own open obligation...
    same_task = run(world, 'route', *shared, '--task-link', link_a)
    surfaced = same_task['routing']['trial']['pending_closes']
    assert surfaced['count'] == 1 and surfaced['decisions'][0]['decision_id'] == 'free-a'
    assert surfaced['decisions'][0]['consumer_task_id'] == 'task-a'
    assert surfaced['decisions'][0]['missing'] == ['close', 'rework_observation']
    # ...and a different consumer task's request never inherits it.
    other_task = run(world, 'route', *shared, '--task-link', link_b)
    assert other_task['routing']['trial']['pending_closes'] == {'count': 0, 'decisions': []}


def test_the_scoped_completion_contract_tracks_one_tasks_obligations(ledger):
    put(ledger, free_receipt('d1', task='t1'))
    # No close: the hard obligation is the close; the soft one is a later rework observation.
    assert outcomes.close_obligations(decision_summary(ledger, 'd1'), outcomes.read_outcomes(ledger[1])) == ['close']
    assert pending(ledger, 't1')['decisions'][0]['missing'] == ['close', 'rework_observation']
    assert completion(ledger, 't1')['state'] == 'incomplete'
    assert completion(ledger, 't1')['open'][0]['missing'] == ['close']
    close(ledger, 'd1', 'a')
    # One head without a follow-up: the completion boundary is already satisfied, and only the soft
    # rework observation is reported beside it.
    assert outcomes.close_obligations(decision_summary(ledger, 'd1'), outcomes.read_outcomes(ledger[1])) == []
    assert pending(ledger, 't1')['decisions'][0]['missing'] == ['rework_observation']
    assert completion(ledger, 't1')['state'] == 'complete'
    followup(ledger, 'd1', 'f1')
    assert pending(ledger, 't1') == {'count': 0, 'decisions': []}
    assert completion(ledger, 't1')['state'] == 'complete'


def test_a_judged_decision_does_not_settle_on_a_maker_self_check():
    # The live writer refuses a maker-judged close for a judged free route, so this exercises the
    # read-side boundary a completion claim is checked against: a maker self-check still leaves the
    # independent-judge obligation open, and only an independent-judged close settles it.
    decision = {'decision_id': 'd1', 'proof_class': 'judged'}
    maker_check = {'kind': 'close', 'decision_id': 'd1', 'close_id': 'a', 'repairs': 0,
                   'check': 'maker_judged', 'judged_by': 'parent', 'supersedes': None}
    assert outcomes.close_obligations(decision, [maker_check]) == ['independent_judge_check']
    independent = {**maker_check, 'close_id': 'b', 'check': 'independent_judged', 'judged_by': 'independent',
                   'supersedes': 'a'}
    assert outcomes.close_obligations(decision, [maker_check, independent]) == []


def test_a_forked_close_chain_owes_only_an_unambiguous_close():
    # The live writer refuses a second head on one decision, so this exercises the defensive branch
    # `close_obligations` keeps for rows read outside the writer: a fork owes only the unambiguous
    # close, and its unresolved head never attracts a rework observation.
    decision = {'decision_id': 'd1', 'proof_class': None}
    fork = [{'kind': 'close', 'decision_id': 'd1', 'close_id': 'a', 'repairs': 0, 'supersedes': None},
            {'kind': 'close', 'decision_id': 'd1', 'close_id': 'b', 'repairs': 0, 'supersedes': None}]
    assert outcomes.close_obligations(decision, fork) == ['unambiguous_close']


@pytest.mark.parametrize('brief', [False, True])
def test_a_revoked_receipt_keeps_no_actionable_launch_recipe(world, brief):
    add_opencode_fixture(world)
    argv = ['route', 'docs.lookup', *world.args(), '--input-provenance', PROVENANCE, '--data-classification', 'public',
            '--proof-class', 'oracle', '--reason', 'public task', '--record', '--decision-id', 'free-revoked',
            '--task-link', TASK_LINK, '--parent-model', 'pm', '--parent-provider-instance', 'pp',
            '--parent-thread', 'pt']
    first = run(world, *argv)
    assert first['launch_ready'] and first['data_policy']['state'] == 'allowed_public_experimental'
    ledger = world.receipts.read_bytes()
    world.policy['experimental_free_routes'][0]['input_policy']['allowed_github_owners'] = ['somebody-else']
    world.write()
    replay = run(world, *[a for a in argv if a not in ('--record',)], *(['--brief'] if brief else []))
    assert replay['replayed'] is True
    no_submission(replay)
    assert replay['data_policy']['refusal'] == 'experimental_route_receipt_not_currently_authorized'
    assert replay['launch_readiness_basis'] == 'experimental route refused by its current public-input data policy'
    assert 'submit by hand' not in json.dumps(replay)
    assert world.receipts.read_bytes() == ledger


def test_the_trial_authority_is_stated_honestly_in_the_policy_document():
    route = POLICY['experimental_free_routes'][0]
    selection = route['selection']
    assert selection['mode'] == 'preferred_normal_candidate'
    assert selection['authorization'] == 'owner_authorized_trial_2026-10-15'
    assert selection['retrospective'] is True and selection['threshold'] == 50
    assert selection['benchmark'] == 'evidence/deepseek-public-eval-2026-10-15.json'
    assert route['cost_usd'] == 0
    assert 'zero source fee' in route['cost_note'].lower()
    assert 'unknown' in route['cost_note'].lower()
