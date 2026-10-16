"""Outcomes derived from recorded facts, for decisions with no judged close. Pure: reads receipts and observations only.

A derived outcome is never a close. It names the rule that decided it and the facts it used; when no rule decides,
the outcome is `unknown` with the reason. Rules, first match wins:

- `escalated_for_quality`: a later receipt escalates from this decision with `oracle_failed` or `judged_insufficient`
  -> rejected.
- `destination_owner_accepted`: the destination recorded an `accepted` event with actor kind `owner` -> accepted.
- `pull_request_merged`: in the latest snapshot of the child thread's linked pull requests (by the thread's projection
  time), one is merged and no other decision ran in that thread -> accepted. Repairs stay unknown: a
  follow-up message to the same child is not a repair round (parents often repair through a new child), so a derived
  outcome is never `accepted_after_repair`.
- otherwise unknown: `pull_request_thread_shared`, `pull_requests_unreadable`, `pull_request_closed_unmerged`, `relaunched`, `run_failed`, `run_cancelled`, `run_interrupted`,
  `completed_no_disposition`, `no_child_run` or `no_facts`.

Completion is never acceptance, and a closed unmerged pull request is not a rejection (it may have been superseded).
"""

QUALITY_ESCALATIONS = ('oracle_failed', 'judged_insufficient')
RULES = ('escalated_for_quality', 'destination_owner_accepted', 'pull_request_merged')


def latest(rows):
    """The observation with the latest source time, then the latest observation time (later state wins)."""
    def key(row):
        times = row.get('source_timestamps') or {}
        return (max(times.values()) if times else '', row.get('observed_at') or '')
    return max(rows, key=key) if rows else None


def index(observations):
    by = {}
    for row in observations:
        if row.get('kind') == 'observation' and row.get('decision_id'):
            by.setdefault((row['decision_id'], row['fact_type']), []).append(row)
    return by


def lineage(receipts):
    """{decision_id: {'escalated_by': [(id, why)], 'relaunched_by': [id]}} from the receipts that point back at it."""
    links = {}
    for receipt in receipts:
        for field, key in (('escalation', 'escalated_by'), ('relaunch', 'relaunched_by')):
            block = receipt.get(field)
            if isinstance(block, dict) and block.get('from'):
                entry = links.setdefault(block['from'], {'escalated_by': [], 'relaunched_by': []})
                entry[key].append((receipt['decision_id'], block.get('why')) if key == 'escalated_by' else receipt['decision_id'])
    return links


def derive_one(decision_id, by, links):
    facts = {'followup_messages': None, 'terminal_status': None, 'pull_requests': None, 'destination_accepted': False,
             'escalated_by': [], 'relaunched_by': []}
    link = links.get(decision_id) or {}
    facts['escalated_by'] = [d for d, _ in link.get('escalated_by', [])]
    facts['relaunched_by'] = list(link.get('relaunched_by', []))
    status = latest(by.get((decision_id, 't3_run_status'), []))
    facts['terminal_status'] = (status or {}).get('fact', {}).get('status')
    followups = latest(by.get((decision_id, 't3_child_followups'), []))
    if followups:
        facts['followup_messages'] = followups['fact'].get('followup_messages')
    prs = latest(by.get((decision_id, 't3_child_pull_requests'), []))
    pr_fact = (prs or {}).get('fact') or {}
    if prs:
        facts['pull_requests'] = [p.get('state') for p in pr_fact.get('pull_requests', [])]
    facts['destination_accepted'] = any(row['fact'].get('event_type') == 'accepted' and row['fact'].get('actor_kind') == 'owner'
                                        for row in by.get((decision_id, 'destination_event'), []))

    def result(outcome, rule=None, reason=None):
        return {'outcome': outcome, 'rule': rule, 'reason': reason, 'repairs': 'unknown', 'facts': facts,
                'assertion': 'derived_from_recorded_facts'}

    if any(why in QUALITY_ESCALATIONS for _, why in link.get('escalated_by', [])):
        return result('rejected', 'escalated_for_quality')
    if facts['destination_accepted']:
        return result('accepted', 'destination_owner_accepted')
    if prs and pr_fact.get('state') == 'unreadable':
        return result('unknown', reason='pull_requests_unreadable')
    if facts['pull_requests'] and 'merged' in facts['pull_requests']:
        if (pr_fact.get('shared_with_decisions') or 1) > 1:
            return result('unknown', reason='pull_request_thread_shared')
        return result('accepted', 'pull_request_merged')
    if facts['pull_requests'] and all(state == 'closed' for state in facts['pull_requests']):
        return result('unknown', reason='pull_request_closed_unmerged')
    if facts['relaunched_by']:
        return result('unknown', reason='relaunched')
    status = facts['terminal_status']
    if status in ('failed', 'cancelled', 'interrupted'):
        return result('unknown', reason=f'run_{status}')
    if status == 'completed':
        return result('unknown', reason='completed_no_disposition')
    has_call = by.get((decision_id, 't3_delegate_call'))
    return result('unknown', reason='no_child_run' if has_call else 'no_facts')


def derive(decision_ids, receipts, observations):
    """{decision_id: derived outcome} for the given decisions."""
    by, links = index(observations), lineage(receipts)
    return {decision_id: derive_one(decision_id, by, links) for decision_id in decision_ids}
