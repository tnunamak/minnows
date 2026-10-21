"""Resolve advisory policy into an explicit target without dispatch."""
import json
import os
import sys
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from experimental_routes import currency_refusal, operation_refusal
from runtime_sources import digest, read_source, providers, provider_instance, driver_family, resolve_arm, quota_context
from decision_receipts import read_receipts, find_receipt, append_receipt, validate_id, replay_receipt, validate_escalation_source
from decision_receipts import receipt_extensions, capture_choice
import outcomes
import calibrate
import directives as directive_store
import route_inputs
import quota_substitution
import routing
import destinations
import observations as observation_store
import observation_notes
import observe
import pacing
import cost_report
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
    parser.add_argument('--task-link', metavar='JSON', help='explicit consumer task, workflow, stage and role; top-level metadata')
    parser.add_argument('--choice', metavar='JSON', help='bounded parent workflow choice; descriptive only, never dispatch control')
    parser.add_argument('--task-result', metavar='JSON', help='close: explicit whole-consumer-result claim at referenced evidence standard')
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
    parser.add_argument('--critical-path', action='store_true', help='route launch fact: this work must not wait on quota pacing; with forecast bands and no other launchable pair, take the subscription with the least overshoot above its own band. Exhausted, unknown, avoided, unfunded and independence-failing pairs stay excluded')
    parser.add_argument('--inputs', choices=outcomes.INPUTS, help='launch fact: where the task inputs are; absent means unknown')
    parser.add_argument('--data-classification', choices=['public', 'sensitive'], help='experimental free routes require an explicit public declaration')
    parser.add_argument('--experimental-route', action='store_true', help='explicitly request a policy-declared experimental free route')
    parser.add_argument('--input-mode', choices=destinations.INPUT_MODES, help='route: how a typed destination gets local inputs; only caller-text, only with --inputs local')
    parser.add_argument('--sensitive', action='store_true', help='launch fact: the task touches credential or signing code; absent means unknown')
    parser.add_argument('--skip-destination', action='append', default=[], choices=destinations.SKIP_REASONS, metavar='REASON',
                        help='route: decline the destination (local-inputs|synchronous|credential|unavailable|other); repeatable; requires --reason')
    parser.add_argument('--destinations', type=Path, default=destinations.default_destinations_path(), help='destinations file; default ~/.config/model-policy/destinations.json')
    parser.add_argument('--outcomes', type=Path, help='outcome ledger; default outcomes.jsonl beside --receipts')
    parser.add_argument('--observations', type=Path, help='observation ledger; default observations.jsonl beside --receipts')
    parser.add_argument('--git-window-days', type=int, help='observe: days after a close that git signals are searched; default 14')
    parser.add_argument('--observe-state', type=Path,
                        help='observe: private incremental cursor; default $XDG_STATE_HOME/model-policy/observe-<ledger-key>.json (or ~/.local/state when unset)')
    parser.add_argument('--observe-time-budget-seconds', type=int, default=60, help='observe: total time budget; default 60 seconds')
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
    parser.add_argument('--json', action='store_true', help='route/resolve: print the full receipt instead of the one-line summary')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--export', type=Path)
    parser.add_argument('--report-dir', type=Path, help='report: new private output directory, outside source directories')
    parser.add_argument('--since')
    parser.add_argument('--thread')
    parser.add_argument('target', nargs='?', help='directive end: the directive ID')
    parser.add_argument('--maker', metavar='DECISION_ID', help='route: receipt of the maker that this checker must differ from')
    parser.add_argument('--maker-model', metavar='PROVIDER:MODEL', help='route: the maker as a caller claim, for direct or native work')
    parser.add_argument('--independence', choices=routing.INDEPENDENCE_LEVELS, help='route: required checker independence; default is the pack default for review')
    parser.add_argument('--accounts', type=Path, default=route_inputs.default_accounts_path(), help='account map; default ~/.config/model-policy/accounts.json')
    parser.add_argument('--quota-state', type=Path, help='route: quota substitution state; default quota-substitution.json beside --receipts')
    parser.add_argument('--directives', type=Path, help='directives file; default directives.jsonl beside --receipts')
    parser.add_argument('--model-catalog', type=Path, help='model-catalog pack models.json; default from the clone or DATA_PACKS_HOME')
    parser.add_argument('--now', help='RFC 3339 time for directive expiry, the failure lookback and quota staleness. Read-only: route --now is a simulation (no dispatchable target, cannot --record), directive list and calibrate accept it, every other command refuses it')
    parser.add_argument('--id', help='directive add: the directive ID')
    parser.add_argument('--effect', choices=directive_store.EFFECTS)
    parser.add_argument('--match', action='append', default=[], metavar='KEY=VALUE', help='directive add: provider, model, account or op')
    parser.add_argument('--until', help='directive add: RFC 3339 expiry')
    parser.add_argument('--source', choices=directive_store.SOURCES, help='directive add: who instructed it')


def quota_authorized_by(args, params):
    info = getattr(args, 'user_policy_info', None)
    if info and {'quota_substitution', 'quota_bands'} & set(info['keys']):
        return f"user policy overlay {info['path']}"
    return 'pack routing.quota_bands' if params.get('quota_bands') else 'pack routing.quota_substitution'


def authorized_experimental_route(args, policy):
    """Return only an exact, explicitly requested public route; no account map is needed."""
    if not args.experimental_route or args.data_classification != 'public' or args.sensitive:
        return None
    supplied = {}
    for item in args.override:
        key, separator, value = item.partition('=')
        if separator:
            supplied[key] = value
    for route in policy.get('experimental_free_routes', []):
        expected = {'provider': route['provider'], 'model': route['model'], 'account': route['account']}
        if (all(supplied.get(key) == value for key, value in expected.items()) and 'effort' in supplied
                and operation_refusal(route, args.op, routed=args.command == 'route', no_op=args.no_op,
                                      proof_class=args.proof_class, effort=supplied['effort']) is None):
            return route
    return None


def qualified_free_candidates(args, policy, at):
    """The declared free routes that this request may route to without an override, as pack-style candidates.

    The caller's own assertions gate them: explicit public inputs, no sensitive flag, and the proof class the
    qualification names. The declaration gates the rest: operation, effort and current evidence. The router then
    applies its ordinary filters (catalog, effort surface, avoid, independence) and the account's cooldown.
    """
    if args.data_classification != 'public' or args.sensitive:
        return []
    found = []
    for route in policy.get('experimental_free_routes', []):
        for item in route.get('qualified_operations') or []:
            efforts = item.get('efforts') if isinstance(item, dict) and item.get('id') == args.op else None
            for effort in efforts if isinstance(efforts, list) else []:
                if (operation_refusal(route, args.op, routed=True, no_op=False, proof_class=args.proof_class, effort=effort) is None
                        and currency_refusal(route, at) is None):
                    found.append({'arm': {'provider': route['provider'], 'model': route['model'], 'effort': effort}, 'basis': 'qualified_free',
                                  'prior': None, 'free_route': route, 'requires': {'proof_class': item['proof_classes']},
                                  'known_gaps': [f"Provisional bounded public evaluation, public inputs only, expires {route['expires_at_utc']}; "
                                                 f"scope {item['scope']}."],
                                  'evidence_refs': [item['evidence_ref']]})
    return found


def route_choice(args, policy, op, available, available_meta, quota, quota_meta, at, stack):
    """Run the router and add the evidence snapshot and calibration notes. No state commit."""
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
                               failures=failures, freshness=fresh, now=at, critical_path=args.critical_path,
                               experimental_route=authorized_experimental_route(args, policy),
                               free_candidates=qualified_free_candidates(args, policy, at),
                               substitution=routing.substitution_context(params, accounts, quota, quota_meta, at, args.quota_state, write=not args.now,
                                                              authorized_by=quota_authorized_by(args, params), stack=stack))
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


REFUSED_READINESS = 'experimental route refused by its current public-input data policy'


def clear_targets(value, *, refuse_submission=False):
    """Clear nested targets; a refusal also removes every submission channel."""
    if isinstance(value, dict):
        cleared = {}
        for key, child in value.items():
            if refuse_submission and key in ('dispatch', 'submit', 'how_to', 'simulated_target'):
                continue
            if key == 'target':
                cleared[key] = None
            elif refuse_submission and key == 'launch_ready':
                cleared[key] = False
            elif refuse_submission and key == 'launch_readiness_basis':
                cleared[key] = REFUSED_READINESS
            else:
                cleared[key] = clear_targets(child, refuse_submission=refuse_submission)
        return cleared
    if isinstance(value, list):
        return [clear_targets(v, refuse_submission=refuse_submission) for v in value]
    return value


WAIT_BASIS = 'no eligible arm has forecast headroom below its band; wait for a fresh reading or a reset, or decide explicitly'
UNFUNDED_BASIS = 'the target account declares funding and its verified_until is missing or past; verify the credit and route again'


def withheld(value, state, basis, code, detail, extra=None):
    """The receipt with every target cleared, `launch_ready` false and a `waiting` state. The evidence stays."""
    value['routing']['judgment_reasons'].append({'code': code, 'applies_to': 'routed', 'detail': detail})
    value['routing']['judgment_required'] = True
    value = clear_targets(value, refuse_submission=True)
    return value | {'launch_ready': False, 'launch_readiness_basis': basis,
                    'waiting': {'state': state, 'assertion': 'router_evidence', 'reason': basis} | (extra or {})}


def wait_detail(block, critical_path, free_routes):
    """What a pacing wait adds to `waiting`: its real semantics and every alternative that was considered first.

    The wait dispatches nothing and no timer retries it. `retry_after` is the earliest reset of an eligible
    subscription account; a fresh reading can change the answer sooner. A caller routes again to find out.
    """
    trace = block['trace']
    destination = block.get('destination') or {}
    detail = {'dispatches': False, 'retry_after': pacing.retry_after(trace),
              'retry': 'route again with fresh facts; this wait neither dispatches nor schedules a retry',
              'alternatives_considered': {
                  'destination': {k: destination[k] for k in ('chosen', 'considered', 'not_eligible', 'fallback', 'not_considered') if k in destination},
                  'prepaid': pacing.prepaid_summary(trace), 'free_routes': free_routes()}}
    if critical_path:
        detail['critical_path'] = {'requested': True, 'satisfied': False,
                                   'why': 'no eligible subscription has a known forecast overshoot (unknown, stale and exhausted accounts have none)'}
    return detail


def withhold_without_headroom(value, critical_path=False, free_routes=lambda: []):
    """With forecast bands, a routed subscription pair is dispatchable only with known positive headroom on an
    account that is not held constrained, unless `pacing` already picked a launchable alternative or the caller's
    critical-path exception. Unknown, stale, at-risk and hysteresis-held pairs wait. A metered pair (verified
    overflow) and an explicit override are not judged here."""
    block = value['routing']
    chosen = block['chosen']
    if not block['params'].get('quota_bands') or block['final']['overridden'] or not chosen:
        return value
    if (block.get('pacing') or {}).get('state') in (pacing.ALTERNATIVE_BEFORE_WAIT, pacing.CRITICAL_EXCEPTION):
        return value
    row = next(t for t in block['trace'] if t['removed_by'] is None and t['candidate'] == chosen['candidate'] and t['instance'] == chosen['instance'])
    held = (((block.get('quota_substitution') or {}).get('active_accounts') or {}).get(chosen['instance']) or {}).get('state') in quota_substitution.ACTIVE_STATES
    if row['billing'] != 'subscription' or (chosen['pace'] == 'on_track' and not held):
        return value
    return withheld(value, 'no_forecast_headroom', WAIT_BASIS, 'wait_no_forecast_headroom',
                    {'instance': chosen['instance'], 'pace': chosen['pace'], 'held_constrained': held},
                    wait_detail(block, critical_path, free_routes))


def withhold_unfunded(value, accounts, at, target):
    """The funding check for the final target, whoever chose it: the router, an override or a destination. A
    destination's proposed target counts even when the catalog does not list it and the caller would submit by hand."""
    final = value['routing']['final']
    if final['billing'] == 'free' and not value.get('destination') and final['account'] == (target or {}).get('providerInstanceId'):
        return value  # the exact declared free tuple is not paid credit, whatever the account map says
    entry = accounts.get((target or {}).get('providerInstanceId')) or {}
    if entry.get('billing') != 'metered' or 'funding' not in entry or routing.funded(entry['funding'], at):
        return value
    return withheld(value, 'funding_unverified', UNFUNDED_BASIS, 'wait_funding_unverified',
                    {'account': target['providerInstanceId'], 'verified_until': entry['funding']['verified_until']})


def free_route_rows(args, policy, ctx, at):
    """The declared experimental free routes judged for this request, as evidence in a wait. Nothing here selects one:
    the caller opts in with exact flags because public inputs are the caller's own assertion."""
    def qualify(route):
        qualified = next((item for item in route.get('qualified_operations', []) if item['id'] == args.op), None)
        effort = qualified['efforts'][0] if qualified and qualified['efforts'] else '<effort from the live catalog>'
        refusal = operation_refusal(route, args.op, routed=True, no_op=False, proof_class=args.proof_class, effort=effort)
        return effort, refusal or currency_refusal(route, at)
    return pacing.free_routes(policy.get('experimental_free_routes', []), qualify, ctx['failures'], ctx['demote_count'], routing.DEMOTING)


def validate_route_proof(op_id, proof_class):
    """Fresh route task-fit guard; explicit resolve/escalation retains its existing behavior."""
    state = outcomes.op_proof_assessment(op_id, proof_class)['state']
    if state == 'missing_proof':
        raise ValueError(f'{op_id} requires explicit --proof-class oracle|judged|none; none is a declaration, not an omitted flag')
    if state == 'standard_oracle_review_required':
        raise ValueError('implement.standard with --proof-class oracle requires op-fit review: bounded work wholly accepted/rejected '
                         'by a deterministic oracle should use implement.oracle-bounded; if tests do not cover the whole standard, '
                         'declare judged; for deliberate broader/long/security-sensitive oracle work use explicit resolve with '
                         'a --reason explaining op fit')
    if state == 'oracle_bounded_requires_oracle':
        raise ValueError('implement.oracle-bounded requires --proof-class oracle; judged or none does not declare an oracle-bounded task')


def execute(args, policy):
    # Lock order is quota sidecar -> receipts. Keep the quota snapshot stable until
    # acceptance, and release it on every validation error without committing.
    with ExitStack() as stack:
        return execute_locked(args, policy, stack)


def execute_locked(args, policy, stack):
    extensions = receipt_extensions(args)
    if extensions and args.command not in ('resolve', 'route'):
        raise ValueError('--task-link and --choice apply only to resolve or route')
    if getattr(args, 'task_result', None) is not None and args.command != 'close':
        raise ValueError('--task-result applies only to close')
    if args.command == 'report':
        # Independent read-only path: no router, observe execution, or state writes.
        if args.outcomes is None:
            args.outcomes = args.receipts.with_name('outcomes.jsonl')
        if args.observations is None:
            args.observations = args.receipts.with_name('observations.jsonl')
        return cost_report.execute(args)
    if getattr(args, 'report_dir', None) is not None:
        raise ValueError('--report-dir applies to report only')
    if args.now is not None and not args.now.strip():
        raise ValueError('--now must not be empty')
    if args.directives is None:
        args.directives = args.receipts.with_name('directives.jsonl')
    at_monotonic = time.monotonic()   # sample first so a pause around the wall clock can only make the evidence older
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
    if args.command != 'route' and args.quota_state is not None:
        raise ValueError('--quota-state applies to route only')
    if args.command == 'route':
        args.quota_state = args.quota_state or quota_substitution.default_path(args.receipts)
        lock_path = args.quota_state.with_name(args.quota_state.name + '.lock')
        for other, name in ((args.receipts, '--receipts'), (args.directives, '--directives'), (args.outcomes, '--outcomes'), (args.observations, '--observations')):
            outcomes.refuse_shared_ledger(other, args.quota_state, '--quota-state', first=name)
            outcomes.refuse_shared_ledger(other, lock_path, '--quota-state lock', first=name)
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
    if args.critical_path and not route:
        raise ValueError('--critical-path applies to route only')
    skip = list(dict.fromkeys(args.skip_destination))
    if skip and not route:
        raise ValueError('--skip-destination applies to route only')
    if skip and not (args.reason and args.reason.strip()):
        raise ValueError('--skip-destination requires --reason')
    if route:
        dest_entries, dest_meta = destinations.read_destinations(args.destinations)
    else:
        # resolve never consults a destination; it reads the config only to name what it did not consider.
        try:
            dest_entries, dest_meta = destinations.read_destinations(args.destinations)
        except (ValueError, OSError):
            dest_entries, dest_meta = [], {'state': 'unreadable', 'sha256': None}
    facts = outcomes.launch_facts(args)
    if getattr(args, 'input_mode', None):
        if not route or args.inputs != 'local':
            raise ValueError('--input-mode applies to route with an explicit --inputs local')
        facts = (facts or {'schema_version': 1}) | {'input_mode': args.input_mode}
    decision_id = args.decision_id or str(uuid.uuid4())
    validate_id(decision_id)
    request = {'op': args.op, 'no_op': args.no_op, 'overrides': overrides, 'account': args.account_hint,
               'quota_provider': args.quota_provider, 'quota_source': args.quota_source,
               'reason': args.reason, 'parent': {'model': args.parent_model, 'provider_instance_id': args.parent_provider_instance, 'thread_id': args.parent_thread},
               'escalate_from': args.escalate_from, 'why': args.why, 'handoff_boundary': args.handoff_boundary,
               'relaunch_of': args.relaunch_of}
    if args.experimental_route:
        request['experimental_route'] = True
        request['sensitive'] = args.sensitive
    if args.data_classification is not None:
        request['data_classification'] = args.data_classification
    if route:
        request |= {'route': True, 'maker': args.maker, 'maker_model': args.maker_model, 'independence': args.independence}
        # Added only when a destinations file or a skip exists, so a route without them keeps its old request shape.
        if dest_meta['state'] == 'loaded' or skip:
            request |= {'skip_destination': skip, 'destinations_sha256': dest_meta['sha256']}
    old = None if simulation else find_receipt(args.receipts, decision_id)
    if old:
        replayed = replay_receipt(old, request, facts, extensions)
        proposed = next((entry.get('target') for entry in dest_entries if entry['name'] == replayed.get('destination')), None)
        apply_experimental_route_policy(replayed, policy, datetime.now(timezone.utc), replay=True, destination_target=proposed)
        return destinations.historical_handoff(replayed)
    # Exact historical replay wins. Validate known fresh routes (including simulations)
    # before reading runtime sources, running probes or acquiring writable quota state.
    # Unknown ops retain the existing unknown-op path; resolve/escalation is unchanged.
    if route and args.op in ops:
        validate_route_proof(args.op, args.proof_class)
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
    def declared_for(arm):
        return next((row for row in policy.get('experimental_free_routes', [])
                     if (row['provider'], row['model']) == (arm['provider'], arm['model'])), None)

    declared_route = declared_for(arm)
    routing_block = None
    if route:
        routing_block, accounts, ctx = route_choice(args, policy, op, available, available_meta, quota, quota_meta, at, stack)
        routing_block['user_policy'] = getattr(args, 'user_policy_info', None)
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
            selection = resolve_arm(arm, available, available_meta, account, experimental_route=declared_for(arm))  # the routed arm may be a qualified free one
        final_instance = selection['target']['providerInstanceId'] if selection['target'] else account
        entry = accounts.get(final_instance) or {}
        quota_info = quota_context(quota, quota_meta, arm, entry.get('quota_provider'), entry.get('quota_source'), at)
        final_arm = {k: arm[k] for k in ('provider', 'model', 'effort')}
        routing.finalize(routing_block, ctx, op, final_arm, final_instance, selection, routed=routed, overridden=bool(overrides))
    else:
        selection = resolve_arm(arm, available, available_meta, overrides.get('account', args.account_hint),
                                experimental_route=declared_route)
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
                   'policy_path': str(args.policy.resolve()), 'policy_sha256': getattr(args, 'pack_policy_sha256', None) or digest(policy),
                   'effective_policy_sha256': digest(policy), 'policy_generated_at': policy.get('generated_at'),
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
        receipt_rows = read_receipts(args.receipts) if args.relaunch_of else []
        routing_block['destination'], offer = destinations.decide(
            dest_entries, purpose=args.purpose, facts=facts, skip=skip, reason=args.reason, request_id=decision_id, simulate=simulation,
            independence=independence, lineage=lambda name: destinations.chain(receipt_rows, args.relaunch_of, name), op=args.op, now=at, at_monotonic=at_monotonic)
    elif routing_block:
        routing_block['destination'] = destinations.not_considered(dest_meta['state'])
    if not route:
        return_value['destination_skip'] = destinations.resolve_skip(dest_entries, dest_meta, args.purpose, args.op)
    if routing_block:
        # Descriptive only: added once the destination is decided, so no ranking, target or judgment can depend on it.
        rows, state, _ = observation_store.read_observations(args.observations)
        found = observation_notes.notes(
            rows, state, read_receipts(args.receipts), at=at, params=policy.get('routing', {}), purpose=args.purpose,
            destination=(routing_block.get('destination') or {}).get('chosen') if offer else None,
            has_observe_command=any('observe_command' in e for e in dest_entries))
        if found:
            routing_block['calibration_notes'] = routing_block['calibration_notes'] | {'observations': found}
    proposed_destination = None
    if offer:
        # The model pair is only a fallback now: its reasons stay visible and no longer need judgment.
        for reason in routing_block['judgment_reasons']:
            if reason['code'] not in ('review_without_maker', 'independence_without_maker'):
                reason |= {'applies_to': 'model_fallback', 'informational': True}
            else:
                reason['applies_to'] = 'destination'
        if (facts or {}).get('inputs') is None and 'typed' not in offer:
            routing_block['judgment_reasons'].append(destinations.INPUTS_UNKNOWN)
        if destinations.independence_unknown(offer, independence):
            routing_block['judgment_reasons'].append(destinations.INDEPENDENCE_UNKNOWN)
        routing_block['judgment_required'] = routing.judgment_needed(routing_block['judgment_reasons'])
        return_value['destination'] = offer['name']
        return_value['launch_ready'] = False
        return_value['launch_readiness_basis'] = 'destination chosen; no model target to dispatch'
        dispatch = offer.pop('_dispatch', None)
        # Keep the chosen structured proposal until authorization, even if no
        # catalog target can be emitted. It is not the fallback arm's identity.
        proposed_destination = dispatch['target'] if dispatch else None
        return_value['destination_offer'] = offer
        # Assembled in full first, then cleared once: no offer, capacity or submit field can bring a model target back.
        return_value = clear_targets(return_value)
        if dispatch:
            # The destination's own T3 target, only when the live catalog lists it; the model fallback stays cleared.
            found = destinations.catalog_target(available, dispatch['target'])
            state = 'decision_id_not_accepted' if not dispatch['root_accepted'] else 'in_catalog' if found else 'not_in_catalog'
            return_value['routing']['destination']['target_state'] = state
            if state == 'in_catalog':
                return_value['target'] = dict(dispatch['target'])
                return_value['launch_ready'] = True
                return_value['launch_readiness_basis'] = ('destination target listed in the live catalog; dispatch with delegate_task: '
                                                          'clientRequestId = decision_id, mode async, brief_first_line as the first line')
                return_value['dispatch'] = {'clientRequestId': decision_id, 'mode': 'async',
                                            'brief_first_line': dispatch['brief_first_line'], 'root_decision_id': dispatch['root_decision_id']}
            elif state == 'not_in_catalog':
                return_value['launch_readiness_basis'] = 'destination chosen; its target is not in the live catalog; submit by hand'
            else:
                return_value['launch_readiness_basis'] = ('destination chosen; the root decision ID does not match its request_id_pattern; '
                                                          'submit by hand or re-route with a matching decision ID')
    if routing_block and not offer:
        return_value = withhold_without_headroom(return_value, args.critical_path, lambda: free_route_rows(args, policy, ctx, at))
    if routing_block and (return_value.get('target') or proposed_destination):
        return_value = withhold_unfunded(return_value, accounts, at, return_value.get('target') or proposed_destination)
    apply_experimental_route_policy(return_value, policy, at, available=available, destination_target=proposed_destination)

    if simulation:
        simulated = return_value['target']
        return_value = clear_targets(return_value)
        return_value['simulated_target'] = simulated
        return_value['launch_ready'] = False
        return_value['launch_readiness_basis'] = 'simulation only; no dispatchable target'
        return_value['simulation'] = {'now': at.isoformat(), 'real_clock_at_evaluation': datetime.now(timezone.utc).isoformat(),
                                      'note': 'simulation only; not a dispatch decision'}

    if 'task_link' in extensions:
        return_value['task_link'] = extensions['task_link']
    return_value['choice'] = capture_choice(return_value, extensions.get('choice'))

    def commit_quota():
        if route and not simulation and ctx['substitution']:
            state = ctx['substitution']['state']
            quota_substitution.commit(args.quota_state, state)
            # Recorded receipts are snapshots before the post-append commit.
            # Keep their output identical on replay; unrecorded routes can report it.
            if not args.record:
                summary = return_value['routing']['quota_substitution']
                summary['state_written'] = state['written']
                if state['write_error']:
                    summary['state_write_error'] = state['write_error']
            elif state['write_error']:
                # The receipt is already stored and must stay identical on replay, so say it on stderr.
                print(f"WARN quota substitution state not written after the receipt: {state['write_error']}", file=sys.stderr)

    if args.record:
        if route and ctx['substitution']:
            return_value['routing']['quota_substitution']['state_commit'] = 'after_receipt'
        recorded = append_receipt(args.receipts, return_value, extensions)
        if recorded.get('replayed'):
            proposed = next((entry.get('target') for entry in dest_entries if entry['name'] == recorded.get('destination')), None)
            apply_experimental_route_policy(recorded, policy, datetime.now(timezone.utc), replay=True, destination_target=proposed)
            return destinations.historical_handoff(recorded)
        commit_quota()
        return recorded
    commit_quota()
    return return_value


def dispatch_identities(result, available=None, destination_target=None):
    """Identify every returned target, retaining provider identity across receipt replay.

    Old receipts have selection arms but no identity list. Destination targets need
    the catalog driver on fresh evaluation; store it so replay never needs live I/O.
    """
    nodes = []

    def visit(value):
        if isinstance(value, dict):
            nodes.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    if destination_target:
        nodes.append({'target': destination_target})
    stored = (result.get('data_policy') or {}).get('targets', [])
    known = {(row['account'], row['model']): row['provider'] for row in stored}
    for node in nodes:
        arm, target = node.get('arm'), node.get('target')
        if isinstance(arm, dict) and isinstance(target, dict) and arm.get('model') == target.get('model'):
            known[(target.get('providerInstanceId'), target.get('model'))] = arm.get('provider')
    live = {provider_instance(row): driver_family(row) for row in providers(available or {})}
    # Refused destinations have no remaining targets. Their descriptive identities
    # must still be governed on replay after declarations or catalog rows disappear.
    identities = list(stored) if result.get('destination') else []
    overrides = (result.get('request') or {}).get('overrides') or {}
    for node in nodes:
        target = node.get('target')
        if not isinstance(target, dict):
            continue
        account, model = target.get('providerInstanceId'), target.get('model')
        provider = live.get(account, known.get((account, model), target.get('driverKind')))
        # Legacy OpenCode receipts/destinations can predate stored provider identity.
        if provider is None and account == 'opencode':
            provider = 'opencode'
        options = target.get('options') or {}
        effort = next((options[key] for key in ('effort', 'reasoningEffort', 'thinking', 'variant') if key in options),
                      None)
        identity = {'provider': provider, 'model': model, 'account': account, 'effort': effort}
        if identity not in identities:
            identities.append(identity)
    # A failed direct resolution must still explain a governed request's refusal.
    # A destination replaces this selection, so its fallback cannot authorize it.
    if not result.get('destination'):
        selection = result.get('selection') or {}
        arm = selection.get('arm') or {}
        if arm and selection.get('target') is None:
            identities.append({'provider': arm.get('provider'), 'model': arm.get('model'),
                               'account': overrides.get('account') or (result.get('request') or {}).get('account'),
                               'effort': arm.get('effort')})
    return identities


def routed_free_choice(identity, request, routed, route):
    """True when the router itself chose this exact governed identity: a route with no override and no destination,
    whose chosen arm and account are the identity and the declared route's own account. The caller's public assertion and the declared scope are still checked."""
    arm = (routed or {}).get('arm') or {}
    return (request.get('route') is True and not request.get('overrides')
            and all(arm.get(key) == identity[key] for key in ('provider', 'model', 'effort'))
            and (routed or {}).get('instance') == identity['account'] == route['account']
            and (routed or {}).get('selection_basis') == 'qualified_free_route')


def experimental_target_refusal(identity, request, sensitive, routes, evaluated_at, *, proof_class=None, generic_destination=False, routed=None):
    """One current exact authorization is required for each governed identity: the exact manual override,
    or the router's own choice of a qualified free candidate."""
    declared = next((row for row in routes if row['provider'] == identity['provider']
                     and row['model'] == identity['model']), None)
    model_alias = next((row for row in routes if row['model'] == identity['model']), None)
    route = declared or model_alias
    if route is None:
        return None, 'model_not_declared_for_experimental_route'
    if declared is None:
        return route, 'provider_alias_not_allowed'
    if sensitive:
        return route, 'sensitive_inputs'
    if request.get('data_classification') != 'public':
        return route, 'public_classification_required'
    if generic_destination:
        return route, 'governed_destination_unsupported'
    overrides = request.get('overrides') or {}
    explicit = (request.get('experimental_route') is True
                and bool((request.get('reason') or '').strip())
                and all(overrides.get(key) == identity[key] == route[key] for key in ('provider', 'model', 'account'))
                and overrides.get('effort') is not None and overrides['effort'] == identity['effort'])
    if not (explicit or routed_free_choice(identity, request, routed, route)):
        return route, 'exact_experimental_override_required'
    scope_refusal = operation_refusal(route, request.get('op'), routed=request.get('route') is True,
                                      no_op=request.get('no_op') is True, proof_class=proof_class,
                                      effort=identity['effort'])
    if scope_refusal:
        return route, scope_refusal
    return route, currency_refusal(route, evaluated_at)


def apply_experimental_route_policy(result, policy, evaluated_at, *, available=None, replay=False, destination_target=None):
    """Authorize actual targets once, for both fresh decisions and historical replay.

    Identification is intentionally independent of current declarations: revoking
    a route cannot turn its old receipts into ordinary authorized dispatches.
    OpenCode's legacy namespace identifies governed targets; it grants no permission.
    """
    routes = policy.get('experimental_free_routes', [])
    previous = result.get('data_policy') or {}
    governed_models = ({row['model'] for row in routes} | {previous.get('route')}
                       | {row['model'] for row in previous.get('targets', [])})
    governed = [identity for identity in dispatch_identities(result, available, destination_target)
                if (identity['provider'] == 'opencode' or identity['account'] == 'opencode'
                    or (identity['model'] or '').startswith('opencode/')
                    or identity['model'] in governed_models)]
    request = result.get('request') or {}
    if not governed:
        if request.get('experimental_route') and not result.get('destination') and not replay:
            raise ValueError('--experimental-route must resolve to an exact policy-declared provider, model, and account override')
        return
    sensitive = bool(request.get('sensitive') or (result.get('launch_facts') or {}).get('sensitive'))
    for identity in governed:
        route, refusal = experimental_target_refusal(identity, request, sensitive, routes, evaluated_at,
                                                     proof_class=(result.get('launch_facts') or {}).get('proof_class'),
                                                     generic_destination=bool(result.get('destination')),
                                                     routed=(result.get('routing') or {}).get('chosen'))
        if refusal:
            break
    if replay and refusal is None:
        return  # Preserve an authorized historical snapshot byte-for-byte.
    result['data_policy'] = {'route': identity['model'], 'account': identity['account'],
                             'classification': request.get('data_classification') or 'unknown',
                             't3_transport': (route or {}).get('t3_transport') or {},
                             'targets': governed,
                             'state': 'refused' if refusal else 'allowed_public_experimental',
                             'refusal': ('experimental_route_receipt_not_currently_authorized' if replay and refusal else refusal)}
    if refusal is None:
        return
    cleared = clear_targets(result, refuse_submission=True)
    result.clear()
    result.update(cleared)
    if isinstance(result.get('selection'), dict):
        result['selection'] |= {'state': 'data_policy_refused', 'target': None}
    result['launch_ready'] = False
    result['launch_readiness_basis'] = REFUSED_READINESS
    result.pop('simulated_target', None)
    if result.get('routing'):
        result['routing']['judgment_reasons'].append({'code': 'experimental_data_policy_refused',
                                                      'detail': result['data_policy']['refusal'],
                                                      'applies_to': 'final'})
        result['routing']['judgment_required'] = True
