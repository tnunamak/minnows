"""Derived outcomes: rules over recorded facts only; judged closes always win; completion is never acceptance."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/model-policy-ops/lib'))
import derived_outcomes  # noqa: E402


def obs(decision, fact_type, fact, at='2026-10-15T01:00:00+00:00', observed='2026-10-15T02:00:00+00:00'):
    return {'kind': 'observation', 'decision_id': decision, 'fact_type': fact_type, 'fact': {'type': fact_type} | fact,
            'source_timestamps': {'at': at}, 'observed_at': observed}


def receipt(decision, **extra):
    return {'decision_id': decision, **extra}


def derive(decision, observations=(), receipts=()):
    return derived_outcomes.derive([decision], list(receipts) + [receipt(decision)], list(observations))[decision]


def test_completion_alone_is_unknown():
    out = derive('d', [obs('d', 't3_run_status', {'status': 'completed'})])
    assert (out['outcome'], out['rule'], out['reason']) == ('unknown', None, 'completed_no_disposition')
    assert out['assertion'] == 'derived_from_recorded_facts' and out['repairs'] == 'unknown'


def test_quality_escalation_rejects_and_wins_over_a_merged_pr():
    facts = [obs('d', 't3_child_pull_requests', {'pull_requests': [{'state': 'merged'}]})]
    for why in ('oracle_failed', 'judged_insufficient'):
        out = derive('d', facts, [receipt('e', escalation={'from': 'd', 'why': why})])
        assert (out['outcome'], out['rule']) == ('rejected', 'escalated_for_quality') and out['facts']['escalated_by'] == ['e']


def test_a_relaunch_is_not_a_rejection():
    out = derive('d', [obs('d', 't3_run_status', {'status': 'cancelled'})], [receipt('r', relaunch={'from': 'd', 'why': 'infra'})])
    assert (out['outcome'], out['reason']) == ('unknown', 'relaunched') and out['facts']['relaunched_by'] == ['r']


def test_destination_owner_accept_accepts():
    out = derive('d', [obs('d', 'destination_event', {'event_type': 'consumed'}), obs('d', 'destination_event', {'event_type': 'accepted'})])
    assert (out['outcome'], out['rule']) == ('accepted', 'destination_owner_accepted')


def test_consumed_is_not_accepted():
    out = derive('d', [obs('d', 'destination_event', {'event_type': 'consumed'})])
    assert out['outcome'] == 'unknown'


def test_merged_pr_accepts_and_never_claims_after_repair():
    facts = [obs('d', 't3_child_pull_requests', {'pull_requests': [{'state': 'closed'}, {'state': 'merged'}]}),
             obs('d', 't3_child_followups', {'followup_messages': 3})]
    out = derive('d', facts)
    assert (out['outcome'], out['rule'], out['repairs']) == ('accepted', 'pull_request_merged', 'unknown')
    assert out['facts']['followup_messages'] == 3


def test_closed_unmerged_pr_is_unknown_not_rejected():
    out = derive('d', [obs('d', 't3_child_pull_requests', {'pull_requests': [{'state': 'closed'}]})])
    assert (out['outcome'], out['reason']) == ('unknown', 'pull_request_closed_unmerged')


def test_latest_pr_snapshot_wins():
    old = obs('d', 't3_child_pull_requests', {'pull_requests': [{'state': 'open'}]}, at='2026-10-15T01:00:00+00:00')
    new = obs('d', 't3_child_pull_requests', {'pull_requests': [{'state': 'merged'}]}, at='2026-10-15T03:00:00+00:00')
    assert derive('d', [new, old])['outcome'] == 'accepted'


def test_failed_runs_and_missing_facts_say_why():
    for status in ('failed', 'cancelled', 'interrupted'):
        assert derive('d', [obs('d', 't3_run_status', {'status': status})])['reason'] == f'run_{status}'
    assert derive('d', [obs('d', 't3_delegate_call', {'call_status': 'completed'})])['reason'] == 'no_child_run'
    assert derive('d')['reason'] == 'no_facts'


def test_facts_of_another_decision_never_leak():
    assert derive('d', [obs('x', 'destination_event', {'event_type': 'accepted'})])['outcome'] == 'unknown'
