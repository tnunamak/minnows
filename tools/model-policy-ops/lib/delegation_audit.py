"""Read-only T3 projection audit. Requested configuration never proves served output."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from outcomes import chain_heads, split_rows

PREFIX = 'orchestration_v2_projection_'


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        return parsed.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError('audit timestamps must be ISO-8601 with timezone') from error


def normalize_since(since):
    return stamp(since).isoformat() if since else None


USAGE_LABEL = 'T3-reported child turn usage; per decision attempt, main agent only; not quota, not dollars, not procedure cost'
USAGE_COLUMNS = {
    'attempts': {'attempt_id', 'run_id', 'attempt_ordinal', 'provider_turn_id'},
    'turns': {'provider_turn_id', 'run_attempt_id', 'ordinal', 'status', 'payload_json'},
}
USAGE_FIELDS = (('input_tokens', 'inputTokens'), ('cached_input_tokens', 'cachedInputTokens'),
                ('cache_creation_tokens', 'cacheCreationTokens'), ('output_tokens', 'outputTokens'),
                ('reasoning_tokens', 'reasoningTokens'))


def usage_unknown(state, reason=None):
    return {'label': USAGE_LABEL, 'state': state, 'reason': reason, 'attempts': [], 'sums': 'never computed; scopes can overlap'}


def count(value):
    return value if type(value) is int and value >= 0 else None


def read_child_usage(db, child_run_id):
    """Per-attempt, per-turn whitelisted metadata for one child run. Never sums, never guesses."""
    for name, table in (('attempts', PREFIX + 'run_attempts'), ('turns', PREFIX + 'provider_turns')):
        columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        if not columns:
            return usage_unknown('unknown_table_absent', table)
        if not USAGE_COLUMNS[name] <= columns:
            return usage_unknown('unavailable', 'schema_mismatch')
    fields = ','.join(f"json_extract(t.payload_json,'$.turnTokenUsage.{key}')" for _, key in USAGE_FIELDS)
    sql = f"""SELECT a.attempt_id,a.attempt_ordinal,t.provider_turn_id,t.ordinal,t.status,
      json_extract(t.payload_json,'$.turnTokenUsage.usageStatus'),json_extract(t.payload_json,'$.turnTokenUsage.usageScope'),
      json_extract(t.payload_json,'$.turnTokenUsage.hasSubagents'),{fields}
      FROM {PREFIX}run_attempts a LEFT JOIN {PREFIX}provider_turns t
        ON t.run_attempt_id=a.attempt_id OR (a.provider_turn_id IS NOT NULL AND t.provider_turn_id=a.provider_turn_id)
      WHERE a.run_id=? ORDER BY a.attempt_ordinal,a.attempt_id,t.ordinal,t.provider_turn_id"""
    attempts = {}
    for row in db.execute(sql, (child_run_id,)):
        attempt = attempts.setdefault(row[0], {'attempt_id': row[0], 'attempt_ordinal': row[1], 'turns': []})
        if row[2] is None or any(t['provider_turn_id'] == row[2] for t in attempt['turns']):
            continue
        status, scope = row[5], row[6]
        subagents = {0: False, 1: True}.get(row[7])  # SQLite JSON booleans arrive as 0/1
        reported = {name: count(row[8 + i]) for i, (name, _) in enumerate(USAGE_FIELDS)}
        keep = status in ('complete', 'partial')
        attempt['turns'].append({
            'provider_turn_id': row[2], 'ordinal': row[3], 'turn_status': row[4],
            'usage_status': status if status in ('complete', 'partial', 'unavailable') else 'unknown',
            'usage_scope': scope if isinstance(scope, str) else None,
            'has_subagents': subagents,
            # hasSubagents false is the only evidence that no nested usage exists.
            'nested_usage': 'none_reported' if subagents is False else 'unknown',
            **(reported if keep else {name: None for name in reported})})
    listed = list(attempts.values())
    turns = [t for a in listed for t in a['turns']]
    if not listed:
        return usage_unknown('no_attempt_rows')
    if not turns:
        state = 'no_turn_rows'
    elif all(t['usage_status'] == 'complete' for t in turns):
        state = 'complete'
    elif any(t['usage_status'] == 'partial' for t in turns):
        state = 'partial'
    else:
        state = 'unavailable'
    return {'label': USAGE_LABEL, 'state': state, 'reason': None, 'attempts': listed,
            'multiple_attempts': len(listed) > 1, 'multiple_turns': len(turns) > 1,
            'sums': 'never computed; scopes can overlap'}


def read_db(path, since=None, thread=None):
    try:
        db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        filters = ["i.type='dynamic_tool'", "json_extract(i.payload_json,'$.toolName') like '%delegate_task'"]
        params = []
        if since:
            filters.append("julianday(json_extract(i.payload_json,'$.startedAt'))>=julianday(?)")
            params.append(since)
        if thread:
            filters.append('i.thread_id=?')
            params.append(thread)
        # Project only delegation metadata. Task/prompt/title text never leaves SQLite.
        sql = f"""SELECT i.turn_item_id call_id,i.thread_id,json_extract(i.payload_json,'$.startedAt') timestamp,i.status,
          json_extract(i.payload_json,'$.input.clientRequestId') decision_id,
          json_quote(json_extract(i.payload_json,'$.input.target')) target,
          coalesce(json_extract(i.payload_json,'$.output.childRunId'),
                   json_extract(i.payload_json,'$.output.structuredContent.childRunId')) child_run_id,
          p.provider_instance_id parent_provider,
          json_extract(p.payload_json,'$.modelSelection.model') parent_model,
          c.status child_status,c.provider_instance_id child_provider,
          json_extract(c.payload_json,'$.modelSelection.model') child_requested_model,
          json_quote(json_extract(c.payload_json,'$.modelSelection.options')) child_requested_options
          FROM {PREFIX}turn_items i LEFT JOIN {PREFIX}runs p ON p.run_id=i.run_id
          LEFT JOIN {PREFIX}runs c ON c.run_id=coalesce(
            json_extract(i.payload_json,'$.output.childRunId'),
            json_extract(i.payload_json,'$.output.structuredContent.childRunId'))
          WHERE {' AND '.join(filters)} ORDER BY julianday(json_extract(i.payload_json,'$.startedAt')),i.turn_item_id"""
        rows = [dict(row) for row in db.execute(sql, params)]
        for row in rows:
            for key in ('target', 'child_requested_options'):
                row[key] = json.loads(row[key]) if row[key] else None
        for row in rows:
            try:
                row['observed_child_usage'] = read_child_usage(db, row['child_run_id']) if row['child_run_id'] else usage_unknown('no_child_run')
            except sqlite3.Error:
                row['observed_child_usage'] = usage_unknown('unavailable', 'read_error')
        out_of_scope = {}
        for kind, table, conditions, time_column in (
            ('provider_native', PREFIX + 'subagents', ["origin='provider_native'"], 'started_at'),
            ('top_level_threads', PREFIX + 'turn_items', ["type='dynamic_tool'", "(json_extract(payload_json,'$.toolName')='t3_thread_launch' OR json_extract(payload_json,'$.toolName') GLOB '*__t3_thread_launch' OR json_extract(payload_json,'$.toolName') GLOB '*.t3_thread_launch')"], "json_extract(payload_json,'$.startedAt')"),
        ):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                out_of_scope[kind] = {'count': None, 'state': 'unknown_table_absent'}
                continue
            count_params = []
            if since:
                conditions.append(f'julianday({time_column})>=julianday(?)')
                count_params.append(since)
            if thread:
                conditions.append('thread_id=?')
                count_params.append(thread)
            count = db.execute(f"SELECT count(*) FROM {table} WHERE {' AND '.join(conditions)}", count_params).fetchone()[0]
            out_of_scope[kind] = {'count': count, 'state': 'observed_t3_projection_records'}
        db.commit()
        return rows, out_of_scope
    except (sqlite3.Error, ValueError) as error:
        raise ValueError(f'T3 database schema/read mismatch: {error}') from error
    finally:
        if 'db' in locals():
            db.close()


def read_export(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('delegations'), list):
        raise ValueError('audit export requires schema_version=1 and delegations array; see README')
    rows = []
    for row in value['delegations']:
        if not isinstance(row, dict) or not all(k in row for k in ('call_id', 'thread_id', 'timestamp', 'input', 'output')):
            raise ValueError('audit export delegation needs call_id/thread_id/timestamp/input/output')
        source_input = row['input']
        output = row['output']
        if not isinstance(source_input, dict):
            raise ValueError('audit export input must be an object')
        output = output if isinstance(output, dict) else {}
        nested = output.get('structuredContent') or {}
        if not isinstance(nested, dict):
            nested = {}
        rows.append({'call_id': row['call_id'], 'thread_id': row['thread_id'], 'timestamp': row['timestamp'],
                     'decision_id': source_input.get('clientRequestId'), 'target': source_input.get('target'),
                     'child_run_id': output.get('childRunId') or nested.get('childRunId'),
                     'status': row.get('status'), 'parent_provider': row.get('parent_provider'),
                     'parent_model': row.get('parent_model'), 'child_status': row.get('child_status'),
                     'child_provider': row.get('child_provider'), 'child_requested_model': row.get('child_requested_model'),
                     'child_requested_options': row.get('child_requested_options')})
    return rows


def normalize_options(options):
    if isinstance(options, dict):
        return options
    if isinstance(options, list) and options:
        result = {}
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get('id'), str) or not option['id'] or 'value' not in option or option['id'] in result:
                return None
            result[option['id']] = option['value']
        return result
    return None


def normalize_target(target):
    if not isinstance(target, dict):
        return None
    options = normalize_options(target.get('options'))
    return target | {'options': options} if options is not None else None


def targets_match(left, right):
    normalized_left = normalize_target(left)
    normalized_right = normalize_target(right)
    return normalized_left is not None and normalized_right is not None and normalized_left == normalized_right


def options_state(target):
    return 'valid' if isinstance(target, dict) and normalize_options(target.get('options')) is not None else 'malformed'


def effort(target):
    if not isinstance(target, dict):
        return None
    options = normalize_options(target.get('options'))
    if options is None:
        return None
    values = [options[k] for k in ('reasoningEffort', 'effort', 'thinking') if k in options]
    if target.get('reasoningEffort') is not None:
        values.append(target['reasoningEffort'])
    return values[0] if len(values) == 1 else None


QUOTA_LABEL = 'unattributed_account_state: launch-time window snapshot from the receipt; not a per-task delta'


def close_view(close, parent_thread):
    keys = ('close_id', 'outcome', 'judged_by', 'check', 'repairs', 'owner_input', 'recorded_at', 'evidence_summary', 'supersedes',
            'closer_thread')
    closer = close['closer_thread']
    # A flag for review, not a statement about who may close. None means no closer was recorded.
    return {k: close[k] for k in keys} | {'closer_thread_differs_from_parent': None if closer is None else closer != parent_thread}


def outcome_fields(receipt, closes, heads, followups, row):
    """Additive per-call keys: close chain, followups, usage, quota. Absent facts stay unknown."""
    usage = row.get('observed_child_usage') or usage_unknown('unknown_not_in_export')
    fields = {'observed_child_usage': usage,
              'parent_overhead_usage': {'state': 'unknown', 'reason': 'parent runs contain other work; not attributable'}}
    if not receipt:
        return fields | {'decision_outcome': {'state': 'no_receipt'}, 'followups': {'state': 'no_receipt', 'items': []},
                         'launch_facts': None, 'quota': {'state': 'no_receipt'}}
    decision_id = receipt['decision_id']
    current = heads.get(decision_id, [])
    chain = [c['close_id'] for c in closes if c['decision_id'] == decision_id]
    state = 'unclosed' if not chain else 'closed' if len(current) == 1 else 'broken_chain'
    items = [{'followup_id': f['followup_id'], 'close_id': f['close_id'], 'finding': f['finding'], 'checked_scope': f['checked_scope'],
              'observed_at': f['observed_at'], 'lag_seconds_since_close': f['lag_seconds_since_close'],
              'evidence_states': [e['verified_state'] for e in f['evidence']]}
             for f in followups if f['decision_id'] == decision_id]
    return fields | {
        'decision_outcome': {'state': state, 'current': close_view(current[0], receipt['parent']['thread_id']) if state == 'closed' else None, 'chain': chain},
        'followups': {'state': 'observed' if items else 'not_checked', 'items': items},
        'launch_facts': receipt.get('launch_facts'),
        'quota': {'state': QUOTA_LABEL, 'launch_snapshot': receipt.get('quota')}}


def tally(values):
    result = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items(), key=lambda kv: str(kv[0])))


def outcome_summary(reports, by_id, matched_ids, closes, heads, followups):
    """Descriptive counts over distinct decisions. Repeated calls never inflate a distribution."""
    first = {}
    for r in reports:
        if r['matched_receipt']:
            first.setdefault(r['decision_id'], r)
    decisions = sorted(matched_ids)
    state = {d: first[d]['decision_outcome'] for d in decisions}
    closed = [d for d in decisions if state[d]['state'] == 'closed']
    current = {d: state[d]['current'] for d in closed}
    followed = {f['decision_id'] for f in followups}
    calls = len(reports)
    calls_closed = sum(1 for r in reports if r['matched_receipt'] and r['decision_outcome']['state'] == 'closed')

    def distribution(ids):
        return {'closed': tally(current[d]['outcome'] for d in ids if d in current),
                'unclosed_or_unreadable': sum(d not in current for d in ids)}

    def strata(label):
        groups = {}
        for d in decisions:
            groups.setdefault(label(d), []).append(d)
        return {key: {'decisions': len(ids)} | distribution(ids) for key, ids in sorted(groups.items(), key=lambda kv: str(kv[0]))}

    def fact(name):
        return lambda d: (by_id[d].get('launch_facts') or {}).get(name, 'unknown')

    child_runs = {}
    for r in reports:
        if r.get('child_run_id'):
            child_runs.setdefault(r['child_run_id'], r['observed_child_usage']['state'])
    unreadable = [d for d in decisions if state[d]['state'] == 'broken_chain']
    return {
        'unit': 'one delegated decision and its attempts; not whole-procedure or top-level task performance',
        'descriptive_only': True, 'causal_claims': 'none',
        'confounding': 'Comparisons across arms are confounded by task assignment unless the receipt records randomized assignment.',
        'closes_are_claims': 'outcome, judged_by and check are caller claims; evidence verification covers existence or file hash only',
        'denominators': {'call_attempts': calls, 'distinct_decisions': len(decisions),
                         'calls_without_receipt': sum(not r['matched_receipt'] for r in reports)},
        'close_coverage': {'decisions_closed': len(closed), 'decisions': len(decisions),
                           'rate': len(closed) / len(decisions) if decisions else None,
                           'calls_on_closed_decisions': calls_closed, 'call_attempts': calls},
        'outcome_distribution': distribution(decisions),
        'judged_by': tally(current[d]['judged_by'] for d in closed),
        'check': tally(current[d]['check'] for d in closed),
        'owner_input': tally(current[d]['owner_input'] for d in closed),
        'accepted_with_hash_matched_check_log': sum(current[d]['evidence_summary']['accepted_with_hash_matched_check_log'] for d in closed),
        'closer_thread': {'differs_from_parent': sum(current[d]['closer_thread_differs_from_parent'] is True for d in closed),
                          'not_recorded': sum(current[d]['closer_thread_differs_from_parent'] is None for d in closed),
                          'meaning': 'a review flag; the tool does not decide who may close a decision'},
        'superseded_closes': sum(1 for c in closes if c['decision_id'] in matched_ids and c['supersedes']),
        'broken_chains': unreadable,
        'followup_coverage': {'closed_decisions_with_followup': len([d for d in closed if d in followed]),
                              'closed_decisions': len(closed),
                              'meaning': 'absence of a followup means not checked, never no rework'},
        'strata': {'purpose': strata(fact('purpose')), 'proof_class': strata(fact('proof_class')),
                   'op': strata(lambda d: by_id[d]['request'].get('op') or 'no_op')},
        'child_usage_coverage': {'unique_child_runs': len(child_runs), 'by_state': tally(child_runs.values()),
                                 'label': USAGE_LABEL},
        'outcome_rows_for_unknown_decisions': sum(1 for c in closes if c['decision_id'] not in by_id),
    }


def destination_block(receipt):
    return (receipt.get('routing') or {}).get('destination') or {}


def destination_chosen(receipt):
    return destination_block(receipt).get('chosen') or 'model'


def destination_summary(receipts):
    """Descriptive counts over route receipts. A destination receipt has no model dispatch, so it is not an unmatched anomaly."""
    routes = {}
    eligible = offered = 0
    fallback = {'facts': {}, 'parent': {}, 'capacity': {}}
    for receipt in receipts:
        block = destination_block(receipt)
        name = destination_chosen(receipt)
        if name != 'model':
            entry = routes.setdefault(name, {'decisions': 0, 'ids': []})
            entry['decisions'] += 1
            entry['ids'].append(receipt['decision_id'])
        if not block.get('considered'):
            continue  # no destination listed this purpose when the receipt was recorded
        eligible += 1
        offered += name != 'model'
        found = block.get('fallback')
        if found:
            counts = fallback.setdefault(found['source'], {})
            for reason in found['reasons']:
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(routes.items())), {
        'eligible': eligible, 'offered': offered, 'fallback': {k: dict(sorted(v.items())) for k, v in fallback.items()},
        'meaning': 'descriptive; offered = destination chosen; not proof the work was submitted or used'}


def audit(rows, receipts, since=None, thread=None, time_anchor='export.timestamp', out_of_scope=None, outcome_rows=()):
    start = stamp(since) if since else None
    rows = [r for r in rows if (not thread or r['thread_id'] == thread) and (not start or stamp(r['timestamp']) >= start)]
    scoped_receipts = [r for r in receipts if (not thread or r['parent']['thread_id'] == thread) and (not start or stamp(r['recorded_at']) >= start)]
    by_id = {}
    for receipt in receipts:
        decision_id = receipt['decision_id']
        if decision_id in by_id:
            raise ValueError(f'duplicate decision_id in receipts: {decision_id}')
        by_id[decision_id] = receipt
    children_by_id = {}
    for row in rows:
        if row.get('decision_id') and row.get('child_run_id'):
            children_by_id.setdefault(row['decision_id'], set()).add(row['child_run_id'])
    closes, followups = split_rows(list(outcome_rows))
    heads = chain_heads(closes)
    matched_ids = set()
    reports = []
    for row in rows:
        receipt = by_id.get(row['decision_id'])
        if receipt:
            matched_ids.add(receipt['decision_id'])
        target = row.get('target')
        child_target = {'providerInstanceId': row.get('child_provider'), 'model': row.get('child_requested_model'), 'options': row.get('child_requested_options')}
        reports.append(row | {'matched_receipt': bool(receipt),
            'request_match': targets_match(target, receipt['target']) if receipt else None,
            'child_request_match': targets_match(child_target, target) if row.get('child_run_id') and row.get('child_provider') else None,
            'options_state': {'call': options_state(target),
                              'receipt': options_state(receipt['target']) if receipt else None,
                              'child': options_state(child_target) if row.get('child_run_id') else None},
            'receipt_before_call': stamp(receipt['recorded_at']) <= stamp(row['timestamp']) if receipt else None,
            'decision_id_multiple_children': len(children_by_id.get(row['decision_id'], set())) > 1,
            'parent_match': {'thread': row['thread_id'] == receipt['parent']['thread_id'],
                             'provider': row.get('parent_provider') == receipt['parent']['provider_instance_id'],
                             'model': row.get('parent_model') == receipt['parent']['model']} if receipt else None,
            'requested_effort': effort(target), 'effort_explicit': effort(target) is not None,
            'requested_tier': 'requested_configuration', 'observed_model': None, 'observed_effort': None,
            'observed_tier': 'unknown_no_native_prefix_evidence', 'requested_vs_observed': 'unknown',
            'outcome': 'unknown', 'reason_present': bool(receipt and receipt['reason']),
            'override': bool(receipt and receipt['overrides']),
            'override_reason_present': bool(receipt and receipt['overrides'] and receipt['reason']),
            'escalation': receipt.get('escalation') if receipt else None,
            'relaunch': receipt.get('relaunch') if receipt else None,
            **outcome_fields(receipt, closes, heads, followups, row)})
    destination_routes, spend_first = destination_summary(scoped_receipts)
    total = len(reports)
    distinct_reports = {r['decision_id']: r for r in reports if r['matched_receipt']}.values()
    def rate(count):
        return {'count': count, 'denominator': total, 'rate': count / total if total else None}
    summary = outcome_summary(reports, by_id, matched_ids, closes, heads, followups)
    sources = [('route' if by_id[r['decision_id']]['request'].get('route') else 'resolve') if r['matched_receipt'] else 'no_receipt' for r in reports]
    route_coverage = {'route': sources.count('route'), 'resolve': sources.count('resolve'), 'no_receipt': sources.count('no_receipt'),
                      'denominator': total, 'rate': sources.count('route') / total if total else None,
                      'meaning': 'app-owned delegate calls by the origin of their receipt; a call with no receipt did not use route or resolve'}
    return {'schema_version': 1, 'outcomes': summary, 'scope': {'since': since, 'thread': thread, 'time_anchor': time_anchor,
                'delegation_kind': 'T3 app-owned delegate_task', 'receipt_time_anchor': 'receipt.recorded_at'},
            'coverage': rate(sum(r['matched_receipt'] for r in reports)) | {'metric': 'app_owned_receipt_coverage'},
            'effort_explicitness': rate(sum(r['effort_explicit'] for r in reports)),
            'reason_coverage': rate(sum(r['reason_present'] for r in reports)),
            'request_match': rate(sum(r['request_match'] is True for r in reports)),
            'counts': {'call_attempts': total,
                       'unique_child_runs': len({r['child_run_id'] for r in reports if r.get('child_run_id')}),
                       'distinct_receipt_decisions': len(matched_ids)},
            'repeated_decision_ids_multiple_children': [
                {'decision_id': decision_id, 'child_run_ids': sorted(children)}
                for decision_id, children in sorted(children_by_id.items()) if len(children) > 1],
            'route_coverage': route_coverage,
            'overrides': sum(r['override'] for r in distinct_reports),
            'override_reasons': sum(r['override_reason_present'] for r in distinct_reports),
            'escalations': sum(bool(r['escalation']) for r in distinct_reports),
            'relaunches': sum(bool(r['relaunch']) for r in distinct_reports),
            'out_of_scope_delegations': out_of_scope if out_of_scope is not None else {
                'provider_native': {'count': None, 'state': 'unknown_not_observed'},
                'top_level_threads': {'count': None, 'state': 'unknown_not_observed'}},
            'unmatched_delegations': [r['call_id'] for r in reports if not r['matched_receipt']],
            'unmatched_receipts': [r['decision_id'] for r in scoped_receipts
                                   if r['decision_id'] not in matched_ids and destination_chosen(r) == 'model'],
            'destination_routes': destination_routes, 'spend_first': spend_first,
            'delegations': reports, 'limits': ['Coverage measures app-owned receipt coverage, not universal delegation compliance',
                'Out-of-scope counts measure observed T3 projection records only; no universal native capture or decision-ID join',
                'Absent projection tables and export out-of-scope counts are unknown; not zero',
                'Failed calls stay in denominator; completed is lifecycle, not success',
                'Native observed model/effort pending independent prefix inspection; no alias inference']}
