"""Passive calibration: proposals from receipts, outcomes, directives and the catalog. Never edits anything.

Outcomes and closes are descriptive. They never reorder candidates. Assignment is not random, most
closes are parent claims, and parent overhead is unknown.
"""
from datetime import timedelta

import directives as directive_store
import observations as observation_store
from outcomes import chain_heads, split_rows
from route_inputs import freshness, read_failures
from routing import ARM_KEYS, expand_candidates

CONFOUNDING = ('descriptive only: arms are not assigned at random, closes are mostly caller claims, '
               'and parent and review overhead are unknown; no causal claim')
OUTCOME_SCOPE = ('a close describes the delegated result only. For purpose=review it describes the review (findings accepted), '
                 'not the success of the reviewed work or the cost of the whole procedure.')
# These outcomes record a choice, not a verdict on the arm. They never count as a rejection or an arm failure.
NOT_ARM_FAILURES = ('abandoned_by_choice', 'superseded')
INDEPENDENT_CHECKS = ('oracle', 'independent_judged')
MIN_CLUSTER = 2
MAX_REASONS = 5
MAX_IDS = 20


def final_arm(receipt):
    return {k: receipt['selection']['arm'][k] for k in ARM_KEYS}


def purpose_of(receipt):
    return (receipt.get('launch_facts') or {}).get('purpose') or 'unknown'


def final_account(receipt):
    target = receipt.get('target')
    return target.get('providerInstanceId') if isinstance(target, dict) else receipt.get('overrides', {}).get('account')


def proposals_for(op, arm, account):
    """Changes that would make a repeated override unnecessary. Directives expire; a pack change stands."""
    found = []
    candidates = {tuple(c['arm'][k] for k in ARM_KEYS): c for c in expand_candidates(op)}
    candidate = candidates.get(tuple(arm[k] for k in ARM_KEYS))
    match = {'op': op['id'], 'model': arm['model']} | ({'account': account} if account else {})
    if candidate is None:
        found.append({'kind': 'pack_candidate', 'op': op['id'], 'arm': arm, 'basis': 'unvalidated',
                      'note': 'add as unvalidated, or define a new op if the task shape differs; the owner reviews the evidence'})
    elif candidate['basis'] == 'unvalidated':
        found.append({'kind': 'directive', 'effect': 'authorize', 'match': match,
                      'note': 'needs --until and --source; a permanent authorization is an owner decision'})
    if account or candidate is not None:
        found.append({'kind': 'directive', 'effect': 'prefer', 'match': match, 'note': 'needs --until and --source'})
    return found


def override_clusters(receipts, policy, op_id=None, minimum=MIN_CLUSTER):
    """Receipts with overrides, grouped by op, final (provider, model, effort, account) and the overridden fields.

    The reasons are free text. They are listed with counts and never classified.
    """
    ops = {o['id']: o for o in policy['operating_points']}
    groups = {}
    for receipt in receipts:
        op = receipt['request'].get('op')
        if not receipt.get('overrides') or op is None or (op_id and op != op_id):
            continue
        arm, account = final_arm(receipt), final_account(receipt)
        groups.setdefault((op, *(arm[k] for k in ARM_KEYS), account, tuple(sorted(receipt['overrides']))), []).append(receipt)
    result = []
    for key, rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0], str(kv[0][1:]))):
        if len(rows) < minimum:
            continue
        op, provider, model, effort, account, fields = key
        reasons = {}
        for row in rows:
            reasons[row.get('reason') or ''] = reasons.get(row.get('reason') or '', 0) + 1
        top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_REASONS]
        arm = {'provider': provider, 'model': model, 'effort': effort}
        result.append({'op': op, 'final': arm | {'account': account}, 'overridden_fields': list(fields), 'count': len(rows),
                       'decision_ids': sorted(r['decision_id'] for r in rows)[:MAX_IDS],
                       'reasons': [{'reason': reason, 'count': count} for reason, count in top],
                       'proposals': proposals_for(ops[op], arm, account) if op in ops else []})
    return result


def tally(values):
    result = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items(), key=lambda kv: str(kv[0])))


def outcome_counts(pairs):
    """Counts for (receipt, current close) pairs. `rejections` counts only outcome `rejected`; see NOT_ARM_FAILURES."""
    by_outcome = tally(c['outcome'] for _, c in pairs)
    return {'closed': len(pairs), 'by_outcome': by_outcome, 'rejections': by_outcome.get('rejected', 0)}


def quality_signals(receipts, outcome_rows, op_id=None):
    """Per (op, exact arm): decisions, current closes by outcome, check type and launch purpose, and follow-up counts."""
    closes, followups = split_rows(list(outcome_rows))
    heads = {d: rows[0] for d, rows in chain_heads(closes).items() if len(rows) == 1}
    followed = {f['decision_id'] for f in followups}
    groups = {}
    for receipt in receipts:
        op = receipt['request'].get('op')
        if op is None or (op_id and op != op_id):
            continue
        groups.setdefault((op, *(final_arm(receipt)[k] for k in ARM_KEYS)), []).append(receipt)
    result = []
    for key, rows in sorted(groups.items()):
        closed = [(r, heads[r['decision_id']]) for r in rows if r['decision_id'] in heads]
        by_check = {}
        for check in sorted({c['check'] for _, c in closed}):
            group = [c for _, c in closed if c['check'] == check]
            known = [c['repairs'] for c in group if c['repairs'] is not None]
            by_check[check] = {'closed': len(group), 'outcomes': tally(c['outcome'] for c in group),
                               'repairs_reported_sum': sum(known), 'repairs_unknown': len(group) - len(known),
                               'check_is': 'independent_or_oracle' if check in INDEPENDENT_CHECKS else 'maker_or_parent_claim'}
        by_purpose = {purpose: {'decisions': sum(purpose_of(r) == purpose for r in rows)}
                      | outcome_counts([(r, c) for r, c in closed if purpose_of(r) == purpose])
                      for purpose in sorted({purpose_of(r) for r in rows})}
        result.append({'op': key[0], 'arm': dict(zip(ARM_KEYS, key[1:])), 'decisions': len(rows), 'closed': len(closed),
                       'unclosed': len(rows) - len(closed), 'by_outcome': tally(c['outcome'] for _, c in closed),
                       'rejections': sum(c['outcome'] == 'rejected' for _, c in closed), 'by_purpose': by_purpose,
                       'by_check': by_check, 'closed_with_followup': sum(r['decision_id'] in followed for r, _ in closed)})
    return result


def evidence_snapshot(receipts, outcome_rows, op_id, arm, instance, failures):
    """Decision support for the chosen exact arm in this op. Never changes the ranking."""
    match = next((s for s in quality_signals(receipts, outcome_rows, op_id) if s['arm'] == arm), None)
    events = failures['instances'].get(instance, {}) if failures['state'] == 'observed' else None
    return {'label': 'descriptive_only', 'confounding': CONFOUNDING, 'outcome_scope': OUTCOME_SCOPE, 'keyed_on': dict(arm, op=op_id),
            'arm': match or {'decisions': 0, 'closed': 0, 'note': 'no recorded decision for this exact arm in this op'},
            'recent_failure_events': {'instance': instance, 'lookback_minutes': failures['lookback_minutes'],
                                      'state': failures['state'], 'by_class': events}}


def calibration_notes(receipts, outcome_rows, policy, op, fresh):
    """Proposals for one op, embedded in each route output. Proposals only; nothing is applied."""
    arms = {c['arm']['model'] for c in expand_candidates(op)}
    checked = [{'arm': s['arm'], 'check': check, 'rejected': data['outcomes'].get('rejected', 0)}
               for s in quality_signals(receipts, outcome_rows, op['id'])
               for check, data in s['by_check'].items()
               if data['check_is'] == 'independent_or_oracle' and data['outcomes'].get('rejected')]
    return {'descriptive_only': True, 'applies_changes': False,
            'override_clusters': override_clusters(receipts, policy, op['id']),
            'stale_pack': {'state': fresh['state'],
                           'missing_from_live_catalog': [m for m in fresh['missing_from_live_catalog'] if m in arms],
                           'newer_ga_not_in_pack': [n for n in fresh['newer_ga_not_in_pack'] if n['pack_model'] in arms],
                           'unchecked': [u for u in fresh['unchecked'] if u['model'] in arms]},
            'checked_rejections': checked}


def directive_coverage(records, receipts, at, max_days):
    states = tally(directive_store.classify(r, at, max_days) for r in records)
    return {'recorded': len(records), 'by_state': states,
            'receipts_that_applied_a_directive': sum(bool((r.get('routing') or {}).get('directives_applied')) for r in receipts)}


def report(*, receipts, outcome_rows, policy, records, available_providers, model_catalog, db, since, at, params,
           observations=((), 'not_read', None)):
    """Everything `calibrate` prints. Reads only; the caller has already loaded the inputs."""
    closes, _ = split_rows(list(outcome_rows))
    heads = chain_heads(closes)
    fresh = freshness(policy, available_providers, model_catalog, params.get('lineage_key'))
    minutes = params.get('failure_lookback_minutes')
    if since:
        minutes = (at - since).total_seconds() / 60
    failures = read_failures(db, at, minutes)
    routed = sum(bool(r['request'].get('route')) for r in receipts)
    rows, state, reason = observations
    observed = observation_store.section(state, reason, rows, receipts, outcome_rows)
    observed['candidates_for_parent_followup_review'] = {
        'decision_ids': observation_store.revert_candidates(rows, {r['decision_id'] for r in receipts}),
        'meaning': 'a later commit names the evidenced commit in an explicit revert reference; a candidate for parent review, no verdict'}
    return {'schema_version': 1, 'applies_changes': False, 'observations': observed, 'scope': {'since': since.isoformat() if since else None, 'now': at.isoformat()},
            'override_clusters': override_clusters(receipts, policy),
            'stale_pack': fresh,
            'quality_signals': {'label': 'descriptive_only', 'confounding': CONFOUNDING, 'outcome_scope': OUTCOME_SCOPE,
                                'not_arm_failures': list(NOT_ARM_FAILURES),
                                'oracle_and_independent_checks_are_separate_from_maker_or_parent_claims': True,
                                'arms': quality_signals(receipts, outcome_rows)},
            'availability': {'state': failures['state'], 'reason': failures['reason'], 'lookback_minutes': failures['lookback_minutes'],
                             'unjoined': failures['unjoined'], 'by_instance': failures['instances']},
            'coverage': {'decisions': len(receipts), 'routed_decisions': routed, 'resolved_decisions': len(receipts) - routed,
                         'decisions_with_a_close': sum(r['decision_id'] in heads for r in receipts),
                         'overrides': sum(bool(r.get('overrides')) for r in receipts),
                         'directives': directive_coverage(records, receipts, at, params.get('directive_max_days'))}}
