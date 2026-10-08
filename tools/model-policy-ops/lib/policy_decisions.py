"""Resolve advisory policy into an explicit target without dispatch."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from runtime_sources import digest, read_source, providers, resolve_arm, quota_context
from decision_receipts import read_receipts, find_receipt, append_receipt, validate_id, replay_receipt, validate_escalation_source
import outcomes
from delegation_audit import read_db, read_export, audit, normalize_since


def add_arguments(parser):
    parser.add_argument('--available', metavar='FILE|auto')
    parser.add_argument('--quota', metavar='FILE|auto')
    parser.add_argument('--account-hint', help='exact T3 provider instance ID')
    parser.add_argument('--quota-provider', help='actual clawmeter provider ID; no inferred mapping')
    parser.add_argument('--quota-source', help='actual clawmeter source ID')
    parser.add_argument('--override', action='append', default=[], metavar='FIELD=VALUE')
    parser.add_argument('--no-op', action='store_true')
    parser.add_argument('--reason')
    parser.add_argument('--decision-id')
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--receipts', type=Path, default=Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'model-policy/decisions.jsonl')
    parser.add_argument('--parent-model')
    parser.add_argument('--parent-provider-instance')
    parser.add_argument('--parent-thread')
    parser.add_argument('--escalate-from')
    parser.add_argument('--relaunch-of')
    parser.add_argument('--why', choices=['oracle_failed', 'judged_insufficient', 'infra', 'config', 'unavailable'])
    parser.add_argument('--handoff-boundary')
    parser.add_argument('--purpose', choices=outcomes.PURPOSES, help='launch fact, stored outside request')
    parser.add_argument('--proof-class', choices=outcomes.PROOF_CLASSES, help='launch fact: how the result is checked')
    parser.add_argument('--urgent', action='store_true', help='launch fact; absent means unknown')
    parser.add_argument('--irreversible', action='store_true', help='launch fact; absent means unknown')
    parser.add_argument('--outcomes', type=Path, help='outcome ledger; default outcomes.jsonl beside --receipts')
    parser.add_argument('--close-id')
    parser.add_argument('--followup-id')
    parser.add_argument('--outcome', choices=outcomes.OUTCOMES)
    parser.add_argument('--judged-by', choices=outcomes.JUDGED_BY)
    parser.add_argument('--check', choices=outcomes.CHECKS)
    parser.add_argument('--owner-input', choices=outcomes.OWNER_INPUT)
    parser.add_argument('--repairs', type=int, help='repair rounds counted by the parent; absent means unknown')
    parser.add_argument('--supersedes', metavar='CLOSE_ID')
    parser.add_argument('--evidence', action='append', default=[], metavar='JSON')
    parser.add_argument('--closer-thread', help='optional: thread that closes the decision; audit flags a difference from the parent thread')
    parser.add_argument('--note', help='one line, at most 280 characters; no prompts or secrets (not filtered)')
    parser.add_argument('--finding', choices=outcomes.FINDINGS)
    parser.add_argument('--checked-scope', help='what the follow-up looked at; no prompts or secrets (not filtered)')
    parser.add_argument('--json', action='store_true', help='resolve/audit always emit JSON')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--export', type=Path)
    parser.add_argument('--since')
    parser.add_argument('--thread')


def execute(args, policy):
    if args.outcomes is None:
        args.outcomes = args.receipts.with_name('outcomes.jsonl')
    if args.command in ('close', 'followup', 'audit'):
        outcomes.refuse_shared_ledger(args.receipts, args.outcomes)
    if args.command in ('close', 'followup'):
        return outcomes.execute(args)
    if args.command == 'audit':
        if bool(args.db) == bool(args.export):
            raise ValueError('audit requires exactly one of --db or --export')
        since = normalize_since(args.since)
        rows, out_of_scope = (read_export(args.export), None) if args.export else read_db(args.db, since, args.thread)
        return audit(rows, read_receipts(args.receipts), since, args.thread,
                     time_anchor='delegate_call.startedAt' if args.db else 'export.timestamp',
                     out_of_scope=out_of_scope, outcome_rows=outcomes.read_outcomes(args.outcomes))
    for source_id in (args.escalate_from, args.relaunch_of):
        if source_id is not None:
            validate_id(source_id)
    ops = {o['id']: o for o in policy['operating_points']}
    if args.no_op == bool(args.op):
        raise ValueError('resolve requires an op ID or --no-op, exclusively')
    overrides = {}
    for item in args.override:
        key, sep, value = item.partition('=')
        if not sep or key not in ('provider', 'model', 'effort', 'account') or not value.strip() or key in overrides:
            raise ValueError('override requires unique provider/model/effort/account=VALUE')
        overrides[key] = value
    if (overrides or args.no_op or args.escalate_from or args.relaunch_of) and not (args.reason and args.reason.strip()):
        raise ValueError('override, --no-op, escalation and relaunch require --reason')
    if args.account_hint and 'account' in overrides and args.account_hint != overrides['account']:
        raise ValueError('--account-hint conflicts with account override')
    if args.escalate_from and args.relaunch_of:
        raise ValueError('--escalate-from and --relaunch-of are mutually exclusive')
    if args.why and not (args.escalate_from or args.relaunch_of):
        raise ValueError('--why requires --escalate-from or --relaunch-of')
    if args.handoff_boundary and not args.escalate_from:
        raise ValueError('--handoff-boundary requires --escalate-from')
    if args.escalate_from and args.why not in ('oracle_failed', 'judged_insufficient'):
        raise ValueError('quality escalation requires --why oracle_failed or judged_insufficient; use --relaunch-of for unavailability')
    if args.relaunch_of and args.why not in ('infra', 'config', 'unavailable'):
        raise ValueError('relaunch requires --why infra, config or unavailable')
    if args.record and not all((args.decision_id, args.parent_model, args.parent_provider_instance, args.parent_thread)):
        raise ValueError('--record requires --decision-id and explicit --parent-model/--parent-provider-instance/--parent-thread')
    facts = outcomes.launch_facts(args)
    decision_id = args.decision_id or str(uuid.uuid4())
    validate_id(decision_id)
    request = {'op': args.op, 'no_op': args.no_op, 'overrides': overrides, 'account': args.account_hint,
               'quota_provider': args.quota_provider, 'quota_source': args.quota_source,
               'reason': args.reason, 'parent': {'model': args.parent_model, 'provider_instance_id': args.parent_provider_instance, 'thread_id': args.parent_thread},
               'escalate_from': args.escalate_from, 'why': args.why, 'handoff_boundary': args.handoff_boundary,
               'relaunch_of': args.relaunch_of}
    old = find_receipt(args.receipts, decision_id)
    if old:
        return replay_receipt(old, request, facts)
    relaunch = None
    if args.relaunch_of:
        if args.relaunch_of == decision_id or not find_receipt(args.receipts, args.relaunch_of):
            raise ValueError('relaunch source receipt not found or self-linked')
        relaunch = {'from': args.relaunch_of, 'why': args.why}
    available, available_meta = read_source(args.available, ['t3code', '--json', 'models', 'list'])
    quota, quota_meta = read_source(args.quota, ['clawmeter', '--json'])
    if available_meta['state'] == 'snapshot' and not providers(available):
        available_meta['state'] = 'schema_mismatch'
    request.update({'available': available_meta, 'quota': quota_meta})
    if args.op and args.op not in ops:
        raise ValueError(f'unknown op: {args.op}')
    op = ops.get(args.op)
    arm = dict(op['expands_to']) if op else {}
    arm.update({k: v for k, v in overrides.items() if k != 'account'})
    if not all(arm.get(k) for k in ('provider', 'model', 'effort')):
        raise ValueError('--no-op requires provider/model/effort overrides')
    if arm['effort'] in ('xhigh', 'max') and not (args.reason and args.reason.strip()):
        raise ValueError('xhigh/max require --reason')
    escalation = None
    if args.escalate_from:
        previous = find_receipt(args.receipts, args.escalate_from)
        if not previous:
            raise ValueError('escalation source receipt not found')
        validate_escalation_source(read_receipts(args.receipts), args.escalate_from)
        if not args.why or not (args.handoff_boundary and args.handoff_boundary.strip()):
            raise ValueError('escalation requires --why and --handoff-boundary for a new task boundary')
        links = previous['policy_context'].get('escalate_to', [])
        if not op or op['id'] not in links:
            raise ValueError('select an explicit target op from the source receipt escalate_to links')
        if overrides:
            raise ValueError('linked escalation selects a policy arm; use a separate reasoned override for other targets')
        if {k: arm[k] for k in ('provider', 'model', 'effort')} == {k: previous['selection']['arm'][k] for k in ('provider', 'model', 'effort')}:
            raise ValueError('same-arm quality retry is not escalation')
        escalation = {'from': args.escalate_from, 'to_op': args.op, 'why': args.why,
                      'handoff_boundary': args.handoff_boundary}
    selection = resolve_arm(arm, available, available_meta, overrides.get('account', args.account_hint))
    quota_info = quota_context(quota, quota_meta, arm, args.quota_provider, args.quota_source)
    context = dict(op) if op else {'id': None, 'known_gaps': ['No policy operating point selected'], 'evidence_refs': [], 'escalate_to': []}
    alternatives = []
    for link in context.get('escalate_to', []):
        if link not in ops:
            raise ValueError(f'policy escalation link missing: {link}')
        linked_arm = ops[link]['expands_to']
        alternatives.append({'op': link, 'selection': resolve_arm(linked_arm, available, available_meta),
                             'policy_context': ops[link]})
    return_value = {'schema_version': 1, 'decision_id': decision_id, 'request': request,
                   'recorded_at': datetime.now(timezone.utc).isoformat(), 'parent': request['parent'],
                   'policy_version': policy['policy_version'], 'catalog_ref': policy['catalog_ref'],
                   'policy_path': str(args.policy.resolve()), 'policy_sha256': digest(policy), 'policy_generated_at': policy.get('generated_at'),
                   'policy_context': context, 'confidence_semantics': 'ordinal evidence/prior; not probability or optimality',
                   'selection': selection, 'target': selection['target'],
                   'launch_ready': selection['target'] is not None,
                   'launch_readiness_basis': 'catalog and effort surface only; auth and quota not attested',
                   'availability': available_meta, 'quota': quota_info, 'alternatives': alternatives,
                   'reason': args.reason, 'overrides': overrides}
    if facts:
        return_value['launch_facts'] = facts
    if escalation:
        return_value['escalation'] = escalation
    if relaunch:
        return_value['relaunch'] = relaunch
    if args.record:
        return append_receipt(args.receipts, return_value)
    return return_value
