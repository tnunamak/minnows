"""Read-only decision reconciliation; no dispatch, judgment, or source writes.

Only explicit current closes establish decision outcomes. Optional task markers
establish whole-result caller claims, never complete cost, billed dollars,
served models, or parent allocation.
"""
import fcntl
import hashlib
import json
import os
import secrets
import stat
import tempfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decision_receipts import read_receipts, validate_id, validate_task_link, metadata_id
import outcomes
import observations as observation_store
import delegation_audit as audit

SCHEMA_VERSION = 1
REPORT_VERSION = 1
RUNTIME_SEMANTICS = 't3-v0.0.46-nightly.20261014.3105-claude-codex-inclusive-v1'
TOKEN_FIELDS = tuple(name for name, _ in audit.USAGE_FIELDS)
CATEGORIES = TOKEN_FIELDS + ('fresh_input_tokens', 'total_tokens')
OPTION_IDS = {'effort', 'reasoningEffort', 'thinking', 'fastMode', 'serviceTier', 'contextWindow'}
OPTION_VALUES = {'minimal', 'none', 'low', 'medium', 'high', 'xhigh', 'max', 'default',
                 'priority', 'flex', 'standard', '200k', '1m'}
MAX_SOURCE_BYTES = 256 * 1024 * 1024
GAPS = ['no_price_binding', 'served_model_unattested', 'parent_overhead_unallocated',
        'native_subagent_usage_unknown', 'whole_task_population_coverage_unknown',
        'observation_order_is_not_source_revision', 'provider_instance_driver_mapping_unavailable',
        'producing_runtime_and_historical_interpretation_unattested']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identifier(value):
    return audit.safe_id(value)


def enum(value, choices):
    return value if isinstance(value, str) and value in choices else None if value is None else 'other'


def timestamp(value):
    return audit.stamp(value).isoformat() if value is not None else None


def config(target):
    """Project configuration only; free text/unknown option surfaces never pass."""
    if not isinstance(target, dict):
        return None
    raw = audit.normalize_options(target.get('options'))
    kept, dropped = {}, 0
    for key, value in (raw or {}).items():
        if key in OPTION_IDS and (type(value) is bool or
                                 (isinstance(value, str) and value in OPTION_VALUES)):
            kept[key] = value
        else:
            dropped += 1
    if target.get('options') is not None and raw is None:
        dropped += 1
    effort = target.get('reasoningEffort')
    if effort in OPTION_VALUES:
        kept['reasoningEffort'] = effort
    elif effort is not None:
        dropped += 1
    return {'provider_instance_id': identifier(target.get('providerInstanceId')),
            'model': identifier(target.get('model')), 'options': kept,
            'options_state': 'unknown' if raw is None else 'projected',
            'options_dropped': dropped, 'effort': audit.effort({'options': kept}),
            'served_model': 'unattested'}


def count(value):
    return value if type(value) is int and value >= 0 else None


def project_turn(turn, provider):
    values = {name: count(turn.get(name)) for name in TOKEN_FIELDS}
    invalid = [name for name in TOKEN_FIELDS if turn.get(name) is not None and values[name] is None]
    status = enum(turn.get('usage_status'), ('complete', 'partial', 'unavailable', 'unknown'))
    if status not in ('complete', 'partial'):
        values = dict.fromkeys(TOKEN_FIELDS)
    known = provider in ('codex', 'claudeAgent', 'claude')
    main_scope = turn.get('usage_scope') == 'main_agent'
    a, b, c = (values[k] for k in ('input_tokens', 'cached_input_tokens', 'cache_creation_tokens'))
    output, reasoning = values['output_tokens'], values['reasoning_tokens']
    partition = 'unverified_scope' if not main_scope else 'unknown_semantics' if not known else 'missing'
    fresh = total = None
    if known and main_scope and all(v is not None for v in (a, b, c)):
        partition = 'valid' if b + c <= a else 'invalid'
        fresh = a - b - c if partition == 'valid' else None
    if known and output is not None and reasoning is not None and reasoning > output:
        invalid.append('reasoning_exceeds_output')
    if known and main_scope and a is not None and output is not None and partition != 'invalid' and not invalid:
        total = a + output  # output already includes reasoning; input already includes caches.
    subagents = turn.get('has_subagents') if type(turn.get('has_subagents')) is bool else None
    return {'provider_turn_id': identifier(turn.get('provider_turn_id')),
            'ordinal': count(turn.get('ordinal')),
            'turn_status': enum(turn.get('turn_status'), audit.T3_STATUSES),
            'usage_status': status, 'usage_scope': enum(turn.get('usage_scope'), ('main_agent',)),
            'has_subagents': subagents, 'nested_usage': 'none_reported' if subagents is False else 'unknown',
            **values, 'fresh_input_tokens': fresh, 'total_tokens': total,
            'partition_state': partition, 'invalid_fields': sorted(invalid),
            'semantics': RUNTIME_SEMANTICS if known else 'unknown',
            'semantics_basis': 'current_adapter_interpretation_assumption' if known else 'unknown_instance_driver_mapping',
            'producing_runtime': 'unattested', 'historical_applicability': 'unknown',
            'total_usd': None, 'credits': None, 'pricing_state': 'unpriced',
            'pricing_reasons': ['no_price_binding'] + ([] if known else ['unknown_provider_semantics'])}


def project_calls(rows):
    """Only fields read by audit; exports do not currently project turn usage."""
    projected = []
    for row in rows:
        target = config(row.get('target'))
        child = config({'providerInstanceId': row.get('child_provider'),
                        'model': row.get('child_requested_model'),
                        'options': row.get('child_requested_options')})
        raw_usage = row.get('observed_child_usage') or {}
        attempts = []
        for attempt in raw_usage.get('attempts', []):
            attempts.append({'attempt_id': identifier(attempt.get('attempt_id')),
                             'attempt_ordinal': count(attempt.get('attempt_ordinal')),
                             'turns': [project_turn(t, child['provider_instance_id'])
                                       for t in attempt.get('turns', [])]})
        projected.append({'call_id': identifier(row.get('call_id')),
                          'decision_id': identifier(row.get('decision_id')),
                          'thread_id': identifier(row.get('thread_id')),
                          'started_at': timestamp(row.get('timestamp')),
                          'status': enum(row.get('status'), audit.T3_STATUSES),
                          'child_status': enum(row.get('child_status'), audit.T3_STATUSES),
                          'child_run_id': identifier(row.get('child_run_id')),
                          'target': target, 'child_requested_configuration': child,
                          'usage_state': enum(raw_usage.get('state'), ('complete', 'partial', 'unavailable',
                               'no_child_run', 'no_attempt_rows', 'no_turn_rows', 'unknown_table_absent'))
                               if raw_usage else 'unknown_not_in_export',
                          'usage_reason': enum(raw_usage.get('reason'), ('read_error', 'schema_mismatch')),
                          'attempts': attempts})
    # Identical joins are one call. Distinct records with the same ID are conflicts.
    unique = {canonical(c): c for c in projected}
    return [unique[k] for k in sorted(unique)]


def usage_units(calls, namespace):
    runs, attempts, call_owners, units = defaultdict(set), defaultdict(set), defaultdict(set), {}
    for call in calls:
        decision, run = call['decision_id'], call['child_run_id']
        if run:
            runs[run].add(decision)
        if call['call_id']:
            call_owners[call['call_id']].add((decision, run, canonical(call['child_requested_configuration'])))
        for attempt in call['attempts']:
            aid = attempt['attempt_id']
            if aid:
                attempts[aid].add((decision, run))
            for turn in attempt['turns']:
                tid = turn['provider_turn_id']
                # Missing identity cannot be made a new billable unit.
                if not tid:
                    continue
                unit = units.setdefault(tid, {'owners': set(), 'variants': {}, 'call_ids': set()})
                unit['owners'].add((decision, run, aid))
                unit['variants'][canonical(turn)] = turn
                if call['call_id']:
                    unit['call_ids'].add(call['call_id'])
    result = []
    for tid, item in sorted(units.items()):
        owners = sorted(item['owners'], key=canonical)
        reasons = []
        if len(owners) != 1:
            reasons.append('turn_ownership_conflict')
        if any(run and len(runs[run]) != 1 for _, run, _ in owners):
            reasons.append('run_ownership_conflict')
        if any(aid and len(attempts[aid]) != 1 for _, _, aid in owners):
            reasons.append('attempt_ownership_conflict')
        if any(len(call_owners[cid]) != 1 for cid in item['call_ids']):
            reasons.append('call_ownership_conflict')
        if len(item['variants']) != 1:
            reasons.append('inconsistent_authoritative_turn_snapshots')
        if any(not all(owner) for owner in owners):
            reasons.append('missing_ownership')
        variants = [item['variants'][k] for k in sorted(item['variants'])]
        result.append({'key': [namespace, tid], 'provider_turn_id': tid,
                       'owners': [{'decision_id': d, 'run_id': r, 'attempt_id': a} for d, r, a in owners],
                       'call_ids': sorted(item['call_ids']), 'state': 'excluded' if reasons else 'attributed',
                       'conflicts': reasons, 'decision_id': owners[0][0] if not reasons else None,
                       'turn': variants[0] if len(variants) == 1 else None,
                       'variants': variants if len(variants) != 1 else []})
    return result


def token_summary(units):
    turns = [u['turn'] for u in units if u['state'] == 'attributed']
    result = {}
    for name in CATEGORIES:
        values = [t[name] if t['usage_scope'] == 'main_agent' else None for t in turns]
        known = [value for value in values if value is not None]
        result[name] = {'observed_sum': sum(known) if known else None,
                        'known_units': len(known), 'missing_units': len(values) - len(known)}
    return result


def source_snapshot(paths, directory):
    """Read each live ledger once, under shared locks; all helpers use pinned copies."""
    snapshots, sources = {}, {}
    with ExitStack() as stack:
        streams = {}
        for name, path, optional in sorted(paths):
            path = Path(path)
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except FileNotFoundError:
                if not optional:
                    raise ValueError(f'missing mandatory {name} source') from None
                sources[name] = {'path': str(path.absolute()), 'state': 'unavailable', 'sha256': None,
                                 'reason': 'missing_optional_source'}
                continue
            stream = stack.enter_context(os.fdopen(fd, 'rb'))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError(f'{name} source must be a regular file')
            fcntl.flock(stream, fcntl.LOCK_SH)
            streams[name] = (stream, path)
        for name, (stream, path) in streams.items():
            data = stream.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                raise ValueError(f'{name} source exceeds byte limit')
            destination = Path(directory) / name
            with destination.open('xb') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
            snapshots[name] = destination
            sources[name] = {'path': str(path.resolve()), 'state': 'pinned', 'sha256': sha(data),
                             'hash_kind': 'exact_source_bytes', 'bytes': len(data)}
    return snapshots, sources


def validate_receipts(receipts):
    seen = set()
    for r in receipts:
        if not isinstance(r, dict) or not isinstance(r.get('decision_id'), str) or not isinstance(r.get('request'), dict):
            raise ValueError('malformed receipt record')
        validate_id(r['decision_id'])
        if r['decision_id'] in seen:
            raise ValueError('duplicate receipt decision ID')
        seen.add(r['decision_id'])
        audit.stamp(r.get('recorded_at'))
        if r.get('parent') is not None and not isinstance(r['parent'], dict):
            raise ValueError('malformed receipt parent')
    return {r['decision_id']: r for r in receipts}


def load_sources(args):
    paths = [('receipts', args.receipts, False), ('outcomes', args.outcomes, False),
             ('observations', args.observations, True)]
    if args.export:
        paths.append(('export', args.export, False))
    with tempfile.TemporaryDirectory(prefix='policy-report-inputs-') as temporary:
        pins, sources = source_snapshot(paths, temporary)
        try:
            receipts = read_receipts(pins['receipts'])
            validate_receipts(receipts)
            closes = outcomes.read_outcomes(pins['outcomes'])
            for row in closes:
                audit.stamp(row['recorded_at'] if row['kind'] == 'close' else row['observed_at'])
            observed, state, _ = (observation_store.read_observations(pins['observations'])
                                  if 'observations' in pins else ([], 'absent', None))
            sources['observations']['read_state'] = state
            if state == 'unreadable':
                sources['observations']['state'] = 'unavailable'
                sources['observations']['reason'] = 'malformed_optional_source'
            if args.export:
                rows, outside = audit.read_export(pins['export']), None
            else:
                rows, outside = audit.read_db(args.db, None, None)
            calls = project_calls(rows)
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            raise ValueError('malformed or unreadable mandatory report source') from error
    if args.db:
        sources['db'] = {'path': str(args.db.resolve()), 'state': 'transaction_projection',
                         'sha256': sha(canonical({'calls': calls, 'out_of_scope': outside}).encode()),
                         'hash_kind': 'canonical_projected_transaction_not_database_file',
                         'database_file_sha256': None}
    return receipts, closes, observed, calls, outside, sources


def observed_timing(observations, decision, run):
    """Historical timestamp facts, with no latest-by-collection-time inference."""
    pairs = set()
    for obs in observations:
        if obs.get('kind') != 'observation' or obs.get('source') != 't3' or obs.get('fact_type') != 't3_run_status':
            continue
        if obs.get('decision_id') != decision or (obs.get('source_ids') or {}).get('run_id') != run:
            continue
        times = obs.get('source_timestamps') or {}
        if times.get('started_at') and times.get('completed_at'):
            pairs.add((timestamp(times['started_at']), timestamp(times['completed_at'])))
    base = {'elapsed_seconds': None, 'state': 'unknown', 'wall_time_total': None}
    if len(pairs) != 1:
        return base | {'reason': 'conflicting_historical_timestamps' if pairs else 'run_timestamps_not_available'}
    start, end = next(iter(pairs))
    elapsed = (audit.stamp(end) - audit.stamp(start)).total_seconds()
    return base | {'started_at': start, 'completed_at': end,
                   'elapsed_seconds': elapsed if elapsed >= 0 else None,
                   'state': 'historical_source_fact' if elapsed >= 0 else 'invalid',
                   'reason': 'not_summed_across_parallel_runs'}


def edges_for(receipt, known, cohort):
    result = []
    for name, field in (('relaunch', 'relaunch'), ('escalate', 'escalation')):
        block = receipt.get(field)
        if isinstance(block, dict) and identifier(block.get('from')):
            result.append({'kind': name, 'decision_id': identifier(block['from'])})
    maker = identifier(receipt['request'].get('maker'))
    if maker:
        result.append({'kind': 'maker', 'decision_id': maker})
    return [edge | {'receipt_present': edge['decision_id'] in known,
                    'in_launch_cohort': edge['decision_id'] in cohort} for edge in result]


def consumer_tasks(receipts, outcome_rows, start, as_of):
    """Cohort the existing full-source projection; never infer task acceptance."""
    projection = outcomes.task_projection(receipts, outcome_rows)
    links = defaultdict(list)
    coverage = Counter(total_source_receipts=len(receipts), valid_task_links=0,
                       missing_task_links=0, malformed_task_links=0)
    for receipt in receipts:
        raw = receipt.get('task_link')
        if 'task_link' not in receipt:
            coverage['missing_task_links'] += 1
            continue
        try:
            validate_task_link(raw)
            valid = True
        except ValueError:
            valid = False
        coverage['valid_task_links' if valid else 'malformed_task_links'] += 1
        task = raw.get('consumer_task_id') if isinstance(raw, dict) else None
        if not isinstance(task, str) or task not in projection['tasks']:
            continue
        # Retain identifiable broken links too: dropping them could invent a cohort.
        links[task].append((receipt, raw, valid))
    closes = {row.get('close_id'): row for row in outcome_rows if row.get('kind') == 'close'}
    rows = []
    coverage.update(observed_tasks=len(projection['tasks']), in_cohort_tasks=0,
                    outside_cohort_tasks=0, unknown_first_linkage_tasks=0,
                    source_declared_results=0, source_unknown_results=0,
                    in_cohort_declared_results=0, in_cohort_unknown_results=0)
    for task, state in sorted(projection['tasks'].items()):
        coverage['source_declared_results' if state['state'] == 'declared' else 'source_unknown_results'] += 1
        linked, times, unresolved = [], [], not links[task]
        for receipt, raw, valid in links[task]:
            try:
                value = receipt.get('recorded_at')
                if value is None:
                    raise ValueError('missing linkage time')
                times.append(audit.stamp(value))
            except (ValueError, TypeError, AttributeError):
                unresolved = True
            linked.append({'decision_id': receipt['decision_id'],
                           'role': raw.get('role') if valid else None,
                           'workflow_id': metadata_identifier(raw.get('workflow_id')),
                           'stage_id': metadata_identifier(raw.get('stage_id')),
                           'task_link_state': 'valid' if valid else 'malformed'})
        first = min(times) if times and not unresolved else None
        if first is None:
            coverage['unknown_first_linkage_tasks'] += 1
            continue
        if not start <= first < as_of:
            coverage['outside_cohort_tasks'] += 1
            continue
        coverage['in_cohort_tasks'] += 1
        coverage['in_cohort_declared_results' if state['state'] == 'declared' else 'in_cohort_unknown_results'] += 1
        current = closes.get(state['current_close_id'])
        provenance = None
        if state['state'] == 'declared' and current is not None:
            indices = current['task_result']['standard_evidence']
            provenance = {key: current.get(key) for key in ('close_id', 'decision_id', 'judged_by', 'check')}
            provenance |= {'assertion': 'caller_claim', 'standard_evidence_indices': indices,
                           'evidence': [{'index': index,
                                         'type': enum(current['evidence'][index].get('type'), outcomes.EVIDENCE_KEYS),
                                         'verified_state': enum(current['evidence'][index].get('verified_state'),
                                             ('hash_matches', 'exists', 'missing', 'mismatch', 'unreadable',
                                              'too_large', 'timeout', 'error', 'not_checked'))}
                                        for index in indices],
                           'verification_scope': 'existence_or_hash_only_not_acceptance'}
        rows.append({'consumer_task_id': task, **state,
                     'linked_decisions': sorted(linked, key=lambda item: item['decision_id']),
                     'workflow_ids': sorted({item['workflow_id'] for item in linked if item['workflow_id']}),
                     'first_observed_linkage_at': first.isoformat(),
                     'first_observed_linkage_day_utc': first.date().isoformat(),
                     'current_close_provenance': provenance})
    return {'rows': rows, 'coverage': dict(coverage),
            'observed_cohort_confirmed_accepted_count': sum(row['accepted_count'] for row in rows),
            'total_cost': None,
            'cohort': {'since_utc': start.isoformat(), 'through_utc': as_of.isoformat(),
                       'boundaries': '[since,as_of)', 'anchor': 'earliest_observed_linked_receipt.recorded_at'},
            'semantics': {'acceptance': 'explicit_current_whole_result_caller_claims_only',
                          'judgment_time': 'current_source_snapshot_not_historical_cutoff',
                          'cohort': 'first_observed_linkage_not_actual_task_start',
                          'coverage': 'observed_subset_not_population_success_rate; missing_link_is_not_no_task_or_no_cost',
                          'legacy_task_accepted_result_count': 'unknown_population_total'}}


def metadata_identifier(value):
    """Preserve exact metadata IDs, without exposing malformed free text."""
    try:
        metadata_id(value, 'metadata ID')
    except ValueError:
        return None
    return value


def make_report(receipts, outcome_rows, observations, calls, outside, sources, *, as_of, since=None):
    by_id = validate_receipts(receipts)
    as_of = audit.stamp(as_of) if isinstance(as_of, str) else as_of.astimezone(timezone.utc)
    start = audit.stamp(since) if since else as_of - timedelta(days=14)
    if start > as_of:
        raise ValueError('--since must not be after collection time')
    cohort = {d for d, r in by_id.items() if start <= audit.stamp(r['recorded_at']) < as_of}
    closes, followups = outcomes.split_rows(outcome_rows)
    heads = outcomes.chain_heads(closes)
    source = sources.get('db') or sources.get('export')
    namespace = 't3:' + source['path']
    units = usage_units(calls, namespace)
    parent_decisions = defaultdict(set)
    for d, r in by_id.items():
        parent = identifier((r.get('parent') or {}).get('thread_id'))
        if parent:
            parent_decisions[parent].add(d)
    rows = []
    for d in sorted(cohort, key=lambda k: (audit.stamp(by_id[k]['recorded_at']), k)):
        r = by_id[d]
        current = heads.get(d, [])
        close = current[0] if len(current) == 1 else None
        current_view = None
        if close:
            current_view = {k: close.get(k) for k in ('close_id', 'outcome', 'judged_by', 'check', 'owner_input', 'supersedes')}
            current_view |= {'recorded_at': timestamp(close.get('recorded_at')), 'repairs': count(close.get('repairs')),
                             'assertion': 'caller_claim'}
        linked = [c for c in calls if c['decision_id'] == d]
        linked = [c | {'run_timing': observed_timing(observations, d, c['child_run_id'])} for c in linked]
        own_units = [u for u in units if any(o['decision_id'] == d for o in u['owners'])]
        attributed = [u for u in own_units if u['state'] == 'attributed']
        parent = identifier((r.get('parent') or {}).get('thread_id'))
        latency = (audit.stamp(close['recorded_at']) - audit.stamp(r['recorded_at'])).total_seconds() if close else None
        destination = identifier(r.get('destination')) or identifier(
            ((r.get('routing') or {}).get('destination') or {}).get('chosen')) or 'model'
        fs = [f for f in followups if f['decision_id'] == d]
        rows.append({'decision_id': d, 'launch_at': timestamp(r['recorded_at']),
                     'launch_day_utc': audit.stamp(r['recorded_at']).date().isoformat(),
                     'op': identifier(r['request'].get('op')), 'destination': destination,
                     'receipt_target': config(r.get('target')), 'parent_thread_id': parent,
                     'shared_parent': len(parent_decisions.get(parent, ())) > 1 if parent else None,
                     'edges': edges_for(r, by_id, cohort), 'calls': linked,
                     'usage_keys': [u['key'] for u in own_units],
                     'outcome_state': 'closed' if close else 'broken_chain' if current else 'unclosed',
                     'decision_outcome': close['outcome'] if close else 'unknown',
                     'current_close': current_view, 'superseded_closes': sum(c['decision_id'] == d for c in closes) - bool(close),
                     'repairs_state': 'known' if close and count(close.get('repairs')) is not None else 'missing',
                     'followups': {'state': 'observed' if fs else 'not_checked',
                                   'items': [{'followup_id': f['followup_id'], 'close_id': f['close_id'],
                                              'finding': f['finding'], 'observed_at': timestamp(f['observed_at']),
                                              'on_current_close': bool(close and f['close_id'] == close['close_id'])} for f in fs]},
                     'tokens': token_summary(attributed), 'usage_units': len(attributed),
                     'scope_unverified_units': sum(u['turn']['usage_scope'] != 'main_agent' for u in attributed),
                     'conflicting_units': sum(u['state'] != 'attributed' for u in own_units),
                     'decision_latency_seconds': latency if latency is not None and latency >= 0 else None,
                     'decision_latency_state': 'unknown' if latency is None else 'invalid' if latency < 0 else 'receipt_to_close_only',
                     'run_wall_time_seconds': None, 'total_usd': None, 'credits': None,
                     'task_cost': None, 'task_accepted_result_count': None})
    daily = []
    groups = defaultdict(list)
    for row in rows:
        groups[(row['launch_day_utc'], row['op'] or 'unknown', row['destination'])].append(row)
    for key, members in sorted(groups.items()):
        ids = {r['decision_id'] for r in members}
        owned = [u for u in units if u['state'] == 'attributed' and u['decision_id'] in ids]
        conflicts = [u for u in units if u['state'] != 'attributed' and any(o['decision_id'] in ids for o in u['owners'])]
        current = [r['current_close'] for r in members if r['current_close']]
        accepted = sum(c['outcome'] in ('accepted', 'accepted_after_repair') for c in current)
        labels = Counter(r['decision_outcome'] if r['outcome_state'] == 'closed' else 'unclosed' for r in members)
        daily.append({'launch_day_utc': key[0], 'op': key[1], 'destination': key[2], 'decisions': len(members),
                      **{name: labels[name] for name in ('accepted', 'accepted_after_repair', 'rejected', 'unclosed')},
                      'other': sum(v for k, v in labels.items() if k not in ('accepted', 'accepted_after_repair', 'rejected', 'unclosed')),
                      'judged_by': dict(sorted(Counter(c['judged_by'] for c in current).items())),
                      'check': dict(sorted(Counter(c['check'] for c in current).items())),
                      'repairs_known': sum(r['repairs_state'] == 'known' for r in members),
                      'repairs_missing': sum(r['repairs_state'] == 'missing' for r in members),
                      'repairs_observed_sum': sum(c['repairs'] for c in current if c['repairs'] is not None)
                                              if any(c['repairs'] is not None for c in current) else None,
                      'calls': sum(len(r['calls']) for r in members), 'unique_usage_units': len(owned),
                      'scope_unverified_units': sum(u['turn']['usage_scope'] != 'main_agent' for u in owned),
                      'tokens': token_summary(owned), 'priced_units': 0, 'unpriced_units': len(owned),
                      'conflicting_units': len(conflicts),
                      'nested_unknown_units': sum(u['turn']['has_subagents'] is not False for u in owned),
                      'decisions_without_calls': sum(not r['calls'] for r in members),
                      'calls_without_usage': sum(not any(a['turns'] for a in c['attempts']) for r in members for c in r['calls']),
                      'shared_parent_decisions': sum(r['shared_parent'] is True for r in members),
                      'parent_unknown_decisions': sum(r['shared_parent'] is None for r in members),
                      'accepted_denominator': accepted, 'known_components_usd_per_accepted': None,
                      'total_usd': None, 'credits': None, 'task_cost': None, 'task_accepted_result_count': None,
                      'wall_time_seconds': None})
    return {'schema_version': SCHEMA_VERSION, 'report_version': REPORT_VERSION, 'runtime_semantics_version': RUNTIME_SEMANTICS,
            'runtime_semantics_basis': 'interpretation_assumption_not_producing_runtime_attestation',
            'collection': {'as_of_utc': as_of.isoformat()},
            'cohort': {'since_utc': start.isoformat(), 'through_utc': as_of.isoformat(),
                       'anchor': 'receipt.recorded_at', 'boundaries': '[since,as_of)',
                       'default_window': 'rolling_14_days' if since is None else None},
            'sources': sources, 'source_gaps': GAPS + (['export_usage_not_projected'] if 'export' in sources else [])
                       + (['optional_observations_unavailable'] if sources.get('observations', {}).get('state') == 'unavailable' else []),
            'rows': rows, 'daily': daily,
            'consumer_tasks': consumer_tasks(receipts, outcome_rows, start, as_of),
            'usage': [u for u in units if any(o['decision_id'] in cohort for o in u['owners'])],
            'linked_decision_views': [{'decision_id': r['decision_id'], 'edges': r['edges'],
                                      'nonadditive': True, 'task_cost': None} for r in rows if r['edges']],
            'coverage': {'all_receipts': len(receipts), 'in_cohort_decisions': len(rows),
                         'authoritative_calls': len(calls),
                         'unlinked_calls': sum(c['decision_id'] not in by_id for c in calls),
                         'calls_outside_launch_cohort': sum(c['decision_id'] in by_id and c['decision_id'] not in cohort for c in calls),
                         'unknown_call_ids': sum(c['call_id'] is None for c in calls),
                         'turns_without_identity': sum(t['provider_turn_id'] is None for c in calls for a in c['attempts'] for t in a['turns']),
                         'all_source_unique_turns': len(units),
                         'all_source_conflicting_turns': sum(u['state'] != 'attributed' for u in units),
                         'historical_usage_observations_ignored': sum(o.get('fact_type') == 't3_turn_usage' for o in observations),
                         'outcomes_without_receipts': sum(o['decision_id'] not in by_id for o in outcome_rows)},
            'out_of_scope': outside, 'total_usd': None, 'credits': None, 'task_cost': None,
            'task_accepted_result_count': None, 'parent_overhead_usage': 'unknown_unallocated',
            'native_subagent_usage': 'unknown', 'pricing': {'state': 'not_implemented', 'reason': 'no_price_binding'},
            'semantics': {'acceptance': 'current_explicit_decision_close_only',
                          'tokens': 'observed_unique_main_agent_turn_components_with_missingness',
                          'numerator': 'all_attributed_attempts_including_failed_repaired_rejected',
                          'time': 'decision_latency_is_receipt_to_close; parallel_elapsed_not_summed',
                          'money': 'USD_and_credits_unknown_and_separate',
                          'task': 'observed_explicit_whole_result_claims_separate_from_unknown_population_and_cost',
                          'comparisons': 'descriptive_not_causal'}}


def markdown(report):
    lines = ['# Decision reconciliation', '', 'Task cost: unknown. Task accepted-result count: unknown.',
             'USD: unknown. Credits: unknown. No pricing scenario is implemented.',
             'Launch cohort (UTC): ' + report['cohort']['since_utc'] + ' through ' + report['cohort']['through_utc'],
             'Every failed, repaired and rejected attributed attempt remains in observed tokens.',
             'Token cells are known sums / missing units; unknown is never zero. Reasoning is included in output.',
             'Conflicts are excluded. Linked views are nonadditive; elapsed runs are never summed as wall time.', '',
             '| UTC launch day | Op | Destination | Decisions | Accepted | After repair | Rejected | Unclosed | Other | Calls | Units | Priced / unpriced / conflicts / nested unknown |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for d in report['daily']:
        lines.append('| ' + ' | '.join(str(d[k]) for k in ('launch_day_utc', 'op', 'destination', 'decisions',
                     'accepted', 'accepted_after_repair', 'rejected', 'unclosed', 'other', 'calls', 'unique_usage_units'))
                     + ' | ' + ' / '.join(str(d[k]) for k in ('priced_units', 'unpriced_units', 'conflicting_units', 'nested_unknown_units')) + ' |')
    for d in report['daily']:
        lines += ['', '## ' + d['launch_day_utc'] + ' / ' + d['op'] + ' / ' + d['destination'],
                  '- Judge mix: ' + canonical(d['judged_by']) + '; check mix: ' + canonical(d['check']),
                  '- Repairs known / missing: ' + str(d['repairs_known']) + ' / ' + str(d['repairs_missing']),
                  '- Unverified usage scope units: ' + str(d['scope_unverified_units']),
                  '- Main-agent tokens: ' + '; '.join(k + '=' + str(v['observed_sum']) + ' / ' + str(v['missing_units'])
                                          for k, v in d['tokens'].items()),
                  '- Calls without usage: ' + str(d['calls_without_usage']) + '; decisions without calls: ' + str(d['decisions_without_calls'])
                  + '; shared parent: ' + str(d['shared_parent_decisions']) + '; parent unknown: ' + str(d['parent_unknown_decisions']), '']
    lines += ['', 'Coverage: ' + canonical(report['coverage']),
              'Source gaps: ' + ', '.join(report['source_gaps']),
              'See reconciliation.json for per-turn missingness, conflicts, current closes, lineage and provenance.', '']
    tasks = report.get('consumer_tasks')
    if tasks is not None:
        lines += ['', '## Observed consumer tasks',
                  'Confirmed accepted whole-result claims in observed cohort: '
                  + str(tasks['observed_cohort_confirmed_accepted_count']) + '. Cost: unknown.',
                  'First observed linkage is not actual task start; bounds are [since,as_of).',
                  'Judgments are current source-snapshot claims, not historical state at the cutoff.',
                  'This incomplete observed subset is not a population success rate. Missing links do not mean no tasks or no cost.',
                  'Legacy task accepted-result count remains an unknown population total.',
                  'Coverage: ' + canonical(tasks['coverage']), '',
                  '| Consumer task | First linked UTC | Workflows | State / reason | Current close | Judgment | Accepted count |',
                  '|---|---|---|---|---|---|---:|']
        for row in tasks['rows']:
            lines.append('| ' + ' | '.join(str(value) for value in (
                row['consumer_task_id'], row['first_observed_linkage_at'], ', '.join(row['workflow_ids']),
                row['state'] + ' / ' + row['reason'], row['current_close_id'],
                row['terminal_judgment'], row['accepted_count'])) + ' |')
        for row in tasks['rows']:
            lines += ['', '### ' + row['consumer_task_id'], '']
            lines.append('Linked decisions (roles and provenance): ' + canonical(row['linked_decisions']))
            lines.append('Current whole-result judge/check/evidence provenance: '
                         + canonical(row['current_close_provenance']))
    return '\n'.join(lines)


def check_paths(report_dir, sources):
    """Fresh report directory, disjoint from input directories; no symlink traversal."""
    report_dir = Path(os.path.abspath(report_dir))
    for node in (report_dir, *report_dir.parents):
        if node.is_symlink():
            raise ValueError('report path must not traverse symlinks')
    if report_dir.exists():
        raise ValueError('--report-dir must be a new directory')
    parent = report_dir.parent
    if not parent.is_dir() or parent.stat().st_mode & 0o022 or parent.stat().st_uid != os.getuid():
        raise ValueError('report parent must exist, be owned by this user, and not be writable by others')
    resolved = [Path(p).resolve() for p in sources]
    for source in resolved:
        if report_dir == source or report_dir in source.parents or source.parent == report_dir or source.parent in report_dir.parents:
            raise ValueError('report directory overlaps a source directory')
    for i, source in enumerate(resolved):
        for other in resolved[i + 1:]:
            if source == other or (source.exists() and other.exists() and os.path.samefile(source, other)):
                raise ValueError('report sources must be distinct files')
    return report_dir


def open_directory(path):
    """Anchor output operations to directory descriptors without symlink traversal."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_artifacts(report, report_dir, sources):
    report_dir = check_paths(report_dir, sources)
    parent_fd = open_directory(report_dir.parent)
    stage_name = '.policy-report-' + secrets.token_hex(16)
    stage_fd = None
    made = published = False
    names = ('reconciliation.json', 'daily.md')
    try:
        parent_stat = os.fstat(parent_fd)
        if parent_stat.st_mode & 0o022 or parent_stat.st_uid != os.getuid():
            raise ValueError('report parent changed or is writable by others')
        os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        made = True
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        for name, data in zip(names, (canonical(report) + '\n', markdown(report))):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stage_fd)
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(stage_fd)
        check_paths(report_dir, sources)
        current_parent = report_dir.parent.stat()
        if (current_parent.st_dev, current_parent.st_ino) != (parent_stat.st_dev, parent_stat.st_ino):
            raise ValueError('report parent changed during collection')
        # The owner-only writable parent and descriptor-relative rename prevent
        # untrusted path substitution. Both artifacts become visible together.
        os.rename(stage_name, report_dir.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        published = True
        os.fsync(parent_fd)
    finally:
        if made and not published and stage_fd is not None:
            for name in names:
                try:
                    os.unlink(name, dir_fd=stage_fd)
                except FileNotFoundError:
                    pass
        if stage_fd is not None:
            os.close(stage_fd)
        if made and not published:
            os.rmdir(stage_name, dir_fd=parent_fd)
        os.close(parent_fd)
    return {name: str(report_dir / name) for name in names}


def execute(args):
    if args.record or args.now is not None:
        raise ValueError('report refuses --record and --now')
    if bool(args.db) == bool(args.export):
        raise ValueError('report requires exactly one of --db or --export')
    if not args.report_dir:
        raise ValueError('report requires --report-dir')
    if args.op or args.thread:
        raise ValueError('report takes no op or --thread; its cohort is receipt launch time')
    sources = [args.receipts, args.outcomes, args.observations, args.db or args.export]
    check_paths(args.report_dir, sources)
    capture_started = datetime.now(timezone.utc)
    receipts, closes, observed, calls, outside, provenance = load_sources(args)
    capture_finished = datetime.now(timezone.utc)
    report = make_report(receipts, closes, observed, calls, outside, provenance,
                         as_of=capture_finished, since=args.since)
    report['collection'] |= {'capture_started_utc': capture_started.isoformat(),
                             'capture_finished_utc': capture_finished.isoformat()}
    paths = write_artifacts(report, args.report_dir, sources)
    return {'report_version': REPORT_VERSION, 'paths': paths,
            'decisions': len(report['rows']), 'daily_groups': len(report['daily']),
            'coverage': report['coverage'], 'total_usd': None, 'task_cost': None,
            'task_accepted_result_count': None}
