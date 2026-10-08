"""Read-only T3 projection audit. Requested configuration never proves served output."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

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


def audit(rows, receipts, since=None, thread=None, time_anchor='export.timestamp', out_of_scope=None):
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
            'relaunch': receipt.get('relaunch') if receipt else None})
    total = len(reports)
    distinct_reports = {r['decision_id']: r for r in reports if r['matched_receipt']}.values()
    def rate(count):
        return {'count': count, 'denominator': total, 'rate': count / total if total else None}
    return {'schema_version': 1, 'scope': {'since': since, 'thread': thread, 'time_anchor': time_anchor,
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
            'overrides': sum(r['override'] for r in distinct_reports),
            'override_reasons': sum(r['override_reason_present'] for r in distinct_reports),
            'escalations': sum(bool(r['escalation']) for r in distinct_reports),
            'relaunches': sum(bool(r['relaunch']) for r in distinct_reports),
            'out_of_scope_delegations': out_of_scope if out_of_scope is not None else {
                'provider_native': {'count': None, 'state': 'unknown_not_observed'},
                'top_level_threads': {'count': None, 'state': 'unknown_not_observed'}},
            'unmatched_delegations': [r['call_id'] for r in reports if not r['matched_receipt']],
            'unmatched_receipts': [r['decision_id'] for r in scoped_receipts if r['decision_id'] not in matched_ids],
            'delegations': reports, 'limits': ['Coverage measures app-owned receipt coverage, not universal delegation compliance',
                'Out-of-scope counts measure observed T3 projection records only; no universal native capture or decision-ID join',
                'Absent projection tables and export out-of-scope counts are unknown; not zero',
                'Failed calls stay in denominator; completed is lifecycle, not success',
                'Native observed model/effort pending independent prefix inspection; no alias inference']}
