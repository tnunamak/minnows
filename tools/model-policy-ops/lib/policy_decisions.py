"""Resolve advisory policy into an explicit target without dispatch."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from runtime_sources import digest, read_source, providers, resolve_arm, quota_context
from decision_receipts import read_receipts, find_receipt, append_receipt, validate_id, replay_receipt, validate_escalation_source
import outcomes
import calibrate
import directives as directive_store
import route_inputs
import routing
import destinations
import observations as observation_store
import observation_notes
import observe
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
    parser.add_argument('--inputs', choices=outcomes.INPUTS, help='launch fact: where the task inputs are; absent means unknown')
    parser.add_argument('--sensitive', action='store_true', help='launch fact: the task touches credential or signing code; absent means unknown')
    parser.add_argument('--skip-destination', action='append', default=[], choices=destinations.SKIP_REASONS, metavar='REASON',
                        help='route: decline the destination (local-inputs|synchronous|credential|unavailable|other); repeatable; requires --reason')
    parser.add_argument('--destinations', type=Path, default=destinations.default_destinations_path(), help='destinations file; default ~/.config/model-policy/destinations.json')
    parser.add_argument('--outcomes', type=Path, help='outcome ledger; default outcomes.jsonl beside --receipts')
    parser.add_argument('--observations', type=Path, help='observation ledger; default observations.jsonl beside --receipts')
    parser.add_argument('--git-window-days', type=int, help='observe: days after a close that git signals are searched; default 14')
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
    parser.add_argument('target', nargs='?', help='directive end: the directive ID')
    parser.add_argument('--maker', metavar='DECISION_ID', help='route: receipt of the maker that this checker must differ from')
    parser.add_argument('--maker-model', metavar='PROVIDER:MODEL', help='route: the maker as a caller claim, for direct or native work')
    parser.add_argument('--independence', choices=routing.INDEPENDENCE_LEVELS, help='route: required checker independence; default is the pack default for review')
    parser.add_argument('--accounts', type=Path, default=route_inputs.default_accounts_path(), help='account map; default ~/.config/model-policy/accounts.json')
    parser.add_argument('--directives', type=Path, help='directives file; default directives.jsonl beside --receipts')
    parser.add_argument('--model-catalog', type=Path, help='model-catalog pack models.json; default from the clone or DATA_PACKS_HOME')
    parser.add_argument('--now', help='RFC 3339 time for directive expiry, the failure lookback and quota staleness. Read-only: route --now is a simulation (no dispatchable target, cannot --record), directive list and calibrate accept it, every other command refuses it')
    parser.add_argument('--id', help='directive add: the directive ID')
    parser.add_argument('--effect', choices=directive_store.EFFECTS)
    parser.add_argument('--match', action='append', default=[], metavar='KEY=VALUE', help='directive add: provider, model, account or op')
    parser.add_argument('--until', help='directive add: RFC 3339 expiry')
    parser.add_argument('--source', choices=directive_store.SOURCES, help='directive add: who instructed it')


def route_choice(args, policy, op, available, available_meta, quota, quota_meta, at):
    """Run the router and add the evidence snapshot and calibration notes. Read-only."""
    params = policy.get('routing', {})
    accounts, accounts_meta = route_inputs.read_accounts(args.accounts)
    records = directive_store.read_directives(args.directives)
    max_days = params.get('directive_max_days')
    failures = route_inputs.read_failures(args.db, at, params.get('failure_lookback_minutes'))
    model_catalog = route_inputs.read_model_catalog(args.model_catalog or route_inputs.default_model_catalog_path())
    lineage_key = params.get('lineage_key')
    fresh = route_inputs.freshness(policy, providers(available), model_catalog, lineage_key)
    maker = None
    if args.maker or args.maker_model:
        maker = route_inputs.resolve_maker(args.receipts, args.maker, args.maker_model, args.db, available,
                                           route_inputs.known_providers(policy, available, lineage_key))
    line_of = lambda provider, model: route_inputs.line_of(provider, model, model_catalog, lineage_key)
    ctx = routing.make_context(op, params, available=available, available_meta=available_meta, quota=quota, quota_meta=quota_meta,
                               accounts=accounts, directives=directive_store.active(records, at, max_days), maker=maker,
                               independence=args.independence, proof_class=args.proof_class, line_of=line_of,
                               failures=failures, freshness=fresh, now=at)
    block = routing.route(op, params, ctx)
    try:
        outcome_rows = outcomes.read_outcomes(args.outcomes)
    except ValueError:
        outcome_rows = None
    receipts = read_receipts(args.receipts)
    chosen = block['chosen']
    block['directives_rejected'] = directive_store.rejected(records, at, max_days)
    block['inputs'] = {'accounts': accounts_meta, 'directives': {'active': [d['id'] for d in directive_store.active(records, at, max_days)]},
                       'failures': {k: failures[k] for k in ('state', 'reason', 'lookback_minutes', 'unjoined')},
                       'freshness': fresh['state'], 'now': at.isoformat()}
    block['evidence'] = (calibrate.evidence_snapshot(receipts, outcome_rows, op['id'], chosen['arm'], chosen['instance'], failures)
                         if chosen and outcome_rows is not None else {'label': 'descriptive_only', 'state': 'unavailable'})
    block['calibration_notes'] = (calibrate.calibration_notes(receipts, outcome_rows, policy, op, fresh)
                                  if outcome_rows is not None else {'descriptive_only': True, 'state': 'unavailable'})
    return block, accounts, ctx


def directive_known(args, policy):
    """What a directive may name: the pack, the live catalog and the model catalog (providers, models, ops), and accounts."""
    available, _ = read_source(args.available, ['t3code', '--json', 'models', 'list'])
    model_catalog = route_inputs.read_model_catalog(args.model_catalog or route_inputs.default_model_catalog_path())
    live_models = set().union(*route_inputs.catalog_models(providers(available)).values()) if providers(available) else set()
    try:
        mapped = set(route_inputs.read_accounts(args.accounts)[0])
    except ValueError:
        mapped = set()
    return {'provider': route_inputs.known_providers(policy, available, policy.get('routing', {}).get('lineage_key')),
            'model': {model for _, model in route_inputs.pack_arms(policy)} | live_models | set(model_catalog),
            'op': {o['id'] for o in policy['operating_points']},
            'account': set(route_inputs.instance_families(available)) | mapped}


def run_calibrate(args, policy, at):
    outcomes.refuse_shared_ledger(args.receipts, args.outcomes)
    since = directive_store.parse_time(args.since, '--since') if args.since else None
    receipts = [r for r in read_receipts(args.receipts) if not since or datetime.fromisoformat(r['recorded_at']) >= since]
    available, available_meta = read_source(args.available, ['t3code', '--json', 'models', 'list'])
    model_catalog = route_inputs.read_model_catalog(args.model_catalog or route_inputs.default_model_catalog_path())
    return calibrate.report(receipts=receipts, outcome_rows=outcomes.read_outcomes(args.outcomes), policy=policy,
                            observations=observation_store.read_observations(args.observations),
                            records=directive_store.read_directives(args.directives), available_providers=providers(available),
                            model_catalog=model_catalog, db=args.db, since=since, at=at, params=policy.get('routing', {}))


def clear_targets(value):
    """Null every `target` key inside a simulation result, so no nested copy is dispatchable."""
    if isinstance(value, dict):
        return {k: None if k == 'target' else clear_targets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clear_targets(v) for v in value]
    return value


def execute(args, policy):
    if args.now is not None and not args.now.strip():
        raise ValueError('--now must not be empty')
    if args.directives is None:
        args.directives = args.receipts.with_name('directives.jsonl')
    at = directive_store.parse_time(args.now, '--now') if args.now else directive_store.now()
    if args.now and not (args.command in ('route', 'calibrate') or (args.command == 'directive' and args.op == 'list')):
        name = args.command + (f' {args.op}' if args.command == 'directive' else '')
        raise ValueError(f'--now is not allowed for {name}: it is a read-only simulation clock and {name} writes state with the real clock')
    if args.command == 'directive':
        outcomes.refuse_shared_ledger(args.receipts, args.directives, '--directives')
        max_days = policy.get('routing', {}).get('directive_max_days')
        return directive_store.execute(args, at, directive_known(args, policy) if args.op == 'add' else None, max_days)
    if args.outcomes is None:
        args.outcomes = args.receipts.with_name('outcomes.jsonl')
    if args.observations is None:
        args.observations = args.receipts.with_name('observations.jsonl')
    if args.command in ('audit', 'calibrate', 'observe'):
        outcomes.refuse_shared_ledger(args.receipts, args.observations, '--observations')
        outcomes.refuse_shared_ledger(args.outcomes, args.observations, '--observations', first='--outcomes')
    if args.command == 'calibrate':
        return run_calibrate(args, policy, at)
    if args.command in ('close', 'followup', 'audit', 'observe'):
        outcomes.refuse_shared_ledger(args.receipts, args.outcomes)
    if args.command == 'observe':
        return observe.execute(args)
    if args.command in ('close', 'followup'):
        return outcomes.execute(args)
    if args.command == 'audit':
        if bool(args.db) == bool(args.export):
            raise ValueError('audit requires exactly one of --db or --export')
        since = normalize_since(args.since)
        rows, out_of_scope = (read_export(args.export), None) if args.export else read_db(args.db, since, args.thread)
        return audit(rows, read_receipts(args.receipts), since, args.thread,
                     time_anchor='delegate_call.startedAt' if args.db else 'export.timestamp',
                     out_of_scope=out_of_scope, outcome_rows=outcomes.read_outcomes(args.outcomes),
                     observations=observation_store.read_observations(args.observations))
    for source_id in (args.escalate_from, args.relaunch_of):
        if source_id is not None:
            validate_id(source_id)
    ops = {o['id']: o for o in policy['operating_points']}
    route = args.command == 'route'
    simulation = route and bool(args.now)
    if simulation and args.record:
        raise ValueError('route --now is a simulation and cannot be recorded: a synthetic clock could revive an expired directive inside a dispatchable receipt (drop --now or drop --record)')
    if route and (args.no_op or args.escalate_from or args.handoff_boundary or args.account_hint or args.quota_provider or args.quota_source):
        raise ValueError('route takes an op ID only: no --no-op, --escalate-from, --handoff-boundary, --account-hint or --quota-provider/--quota-source (the account map names the quota source; use --override account=...)')
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
    skip = list(dict.fromkeys(args.skip_destination))
    if skip and not route:
        raise ValueError('--skip-destination applies to route only')
    if skip and not (args.reason and args.reason.strip()):
        raise ValueError('--skip-destination requires --reason')
    dest_entries, dest_meta = destinations.read_destinations(args.destinations) if route else ([], {'state': 'missing', 'sha256': None})
    facts = outcomes.launch_facts(args)
    decision_id = args.decision_id or str(uuid.uuid4())
    validate_id(decision_id)
    request = {'op': args.op, 'no_op': args.no_op, 'overrides': overrides, 'account': args.account_hint,
               'quota_provider': args.quota_provider, 'quota_source': args.quota_source,
               'reason': args.reason, 'parent': {'model': args.parent_model, 'provider_instance_id': args.parent_provider_instance, 'thread_id': args.parent_thread},
               'escalate_from': args.escalate_from, 'why': args.why, 'handoff_boundary': args.handoff_boundary,
               'relaunch_of': args.relaunch_of}
    if route:
        request |= {'route': True, 'maker': args.maker, 'maker_model': args.maker_model, 'independence': args.independence}
        # Added only when a destinations file or a skip exists, so a route without them keeps its old request shape.
        if dest_meta['state'] == 'loaded' or skip:
            request |= {'skip_destination': skip, 'destinations_sha256': dest_meta['sha256']}
    old = None if simulation else find_receipt(args.receipts, decision_id)
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
    if not route and arm['effort'] in ('xhigh', 'max') and not (args.reason and args.reason.strip()):
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
    routing_block = None
    if route:
        routing_block, accounts, ctx = route_choice(args, policy, op, available, available_meta, quota, quota_meta, at)
        chosen = routing_block['chosen']
        routed = {'arm': chosen['arm'], 'account': chosen['instance']} if chosen else None
        arm = dict(arm, **chosen['arm']) if chosen else arm
        arm.update({k: v for k, v in overrides.items() if k != 'account'})
        if arm['effort'] in ('xhigh', 'max') and not (args.reason and args.reason.strip()):
            raise ValueError('xhigh/max require --reason')
        account = overrides.get('account') or (chosen['instance'] if chosen and arm['provider'] == chosen['arm']['provider'] else None)
        if chosen is None and not overrides:
            selection = {'arm': arm, 'accounts': routing.catalog_instances(available, arm['provider']), 'state': 'no_route_target',
                         'target': None, 'authentication': 'not_attested'}
        else:
            selection = resolve_arm(arm, available, available_meta, account)
        final_instance = selection['target']['providerInstanceId'] if selection['target'] else account
        entry = accounts.get(final_instance) or {}
        quota_info = quota_context(quota, quota_meta, arm, entry.get('quota_provider'), entry.get('quota_source'), at)
        final_arm = {k: arm[k] for k in ('provider', 'model', 'effort')}
        routing.finalize(routing_block, ctx, op, final_arm, final_instance, selection, routed=routed, overridden=bool(overrides))
    else:
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
    if routing_block:
        return_value['routing'] = routing_block
    if facts:
        return_value['launch_facts'] = facts
    if escalation:
        return_value['escalation'] = escalation
    if relaunch:
        return_value['relaunch'] = relaunch
    offer = None
    if routing_block and dest_meta['state'] == 'loaded':
        required = routing_block['independence']
        vendors, complete = routing.maker_vendor_coverage(required['maker'])
        independence = {'level': required['required'], 'maker_vendors': vendors, 'maker_complete': complete} if required['required'] else None
        routing_block['destination'], offer = destinations.decide(
            dest_entries, purpose=args.purpose, facts=facts, skip=skip, reason=args.reason, request_id=decision_id, simulate=simulation,
            independence=independence)
    if routing_block:
        # Descriptive only: added once the destination is decided, so no ranking, target or judgment can depend on it.
        rows, state, _ = observation_store.read_observations(args.observations)
        found = observation_notes.notes(
            rows, state, read_receipts(args.receipts), at=at, params=policy.get('routing', {}), purpose=args.purpose,
            destination=(routing_block.get('destination') or {}).get('chosen') if offer else None,
            has_observe_command=any('observe_command' in e for e in dest_entries))
        if found:
            routing_block['calibration_notes'] = routing_block['calibration_notes'] | {'observations': found}
    if offer:
        # The model pair is only a fallback now: its reasons stay visible and no longer need judgment.
        for reason in routing_block['judgment_reasons']:
            if reason['code'] != 'review_without_maker':
                reason |= {'applies_to': 'model_fallback', 'informational': True}
            else:
                reason['applies_to'] = 'destination'
        if (facts or {}).get('inputs') is None:
            routing_block['judgment_reasons'].append(destinations.INPUTS_UNKNOWN)
        if destinations.independence_unknown(offer, independence):
            routing_block['judgment_reasons'].append(destinations.INDEPENDENCE_UNKNOWN)
        routing_block['judgment_required'] = routing.judgment_needed(routing_block['judgment_reasons'])
        return_value['destination'] = offer['name']
        return_value['launch_ready'] = False
        return_value['launch_readiness_basis'] = 'destination chosen; no model target to dispatch'
        return_value['destination_offer'] = offer
        # Assembled in full first, then cleared once: no offer, capacity or submit field can bring a model target back.
        return_value = clear_targets(return_value)
    if simulation:
        simulated = return_value['target']
        return_value = clear_targets(return_value)
        return_value['simulated_target'] = simulated
        return_value['launch_ready'] = False
        return_value['launch_readiness_basis'] = 'simulation only; no dispatchable target'
        return_value['simulation'] = {'now': at.isoformat(), 'real_clock_at_evaluation': datetime.now(timezone.utc).isoformat(),
                                      'note': 'simulation only; not a dispatch decision'}
    if args.record:
        return append_receipt(args.receipts, return_value)
    return return_value
