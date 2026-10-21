"""`observe`: record facts that other systems already hold about recorded decisions. Reads only; never judges.

Each source collects candidate observations, then `observations.append_run` dedupes and appends under the lock.
A source that fails is recorded as `unavailable` or `error` in the source_run row, so a failure is never zero events.
"""
import json
import os
import stat
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import destinations as destination_store
import observations as store
from decision_receipts import read_receipts
from delegation_audit import PREFIX, T3_STATUSES, bounded, effort, normalize_since, read_db, safe_id, safe_ordinal, stamp
from outcomes import COMMIT_TIMEOUT, commit_state, commit_verification_summary, git_argv, git_env, read_outcomes, split_rows
from route_inputs import CANCELLATIONS, classify_failure

RECEIPT_ID = re.compile(r'[a-z]{1,12}[_-][0-9a-f-]{1,40}')  # the bridge's report_<uuid>
SHA1 = re.compile(r'[0-9a-f]{40}')
# Requested-config allowlist. Option ids and string values come from a live T3 database (effort, reasoningEffort, thinking, fastMode,
# serviceTier, contextWindow); `minimal`, `none`, `flex` and `1m` are documented vendor values not yet seen there.
OPTION_IDS = ('effort', 'reasoningEffort', 'thinking', 'fastMode', 'serviceTier', 'contextWindow')
OPTION_VALUE = re.compile(r'[a-z0-9][a-z0-9-]{0,19}')
OPTION_VALUES = frozenset({'minimal', 'none', 'low', 'medium', 'high', 'xhigh', 'max', 'default', 'priority', 'flex', '200k', '1m'})
KNOWN_INSTANCES = frozenset({'claudeAgent', 'codex', 'pi', 'claude-api'})  # driver-named instances; account names come from the receipt
KNOWN_MODELS = frozenset({'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-opus-4-8', 'claude-haiku-4-5', 'claude-opus-5',
                          'gpt-5.6-terra', 'gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-luna', 'default'})
# Destination vocabulary: the dot-bridge lifecycle (src/lifecycle.mjs, src/store.mjs, src/objectives.mjs, src/server.mjs `finish`) and the values in its
# live database. A destination adapter is untrusted: anything outside these sets is stored as 'redacted'.
EVENT_TYPES = frozenset({'queued', 'claimed', 'working', 'waiting', 'blocked', 'completed', 'consumed', 'accepted', 'integrated', 'abandoned',
                         'cancelled', 'delivery_sent', 'delivery_uncertain', 'reconciled', 'check_started', 'check_passed', 'check_failed',
                         'check_skipped'})
REPORT_OUTCOMES = ('completed', 'blocked', 'failed', 'cancelled', 'abandoned')
EVENT_REASONS = frozenset({'new', 'reclaim', 'resume', 'takeover', 'wait', 'get', 'rejected', 'expired'}
                          | {f'outcome={outcome}' for outcome in REPORT_OUTCOMES})
ASSIGNMENT_STATUSES = frozenset({'queued', 'claimed', 'finished', 'cancelled'})
TASK_KINDS = frozenset({'audit', 'audit-review', 'dot-fix', 'review', 'unknown'})
TASK_SOURCES = frozenset({'drainer', 'dot-fix', 'agent', 'unknown'})
ACTOR_KINDS = frozenset({'owner', 'dot', 'system', 'operator', 'dot-fix'})
CANCEL_REASONS = frozenset({'expired'})
BATCH = 25
OBSERVE_TIMEOUT_CAP = 60
OBSERVE_OUTPUT_LIMIT = 4 * 1024 * 1024
GIT_TIMEOUT = 20
GIT_OUTPUT_LIMIT = 8 * 1024 * 1024
GIT_MAX_COMMITS = 5000
GIT_MAX_FILES = 5000
MAX_REVERTS = 20
DEFAULT_WINDOW_DAYS = 14
OBSERVE_BATCH_ROWS = 500
OBSERVE_INITIAL_ROWS = 50_000
OBSERVE_TIME_BUDGET_SECONDS = 60


def result(state, reason=None, subjects=0, observations=(), **extra):
    return {'state': state, 'reason': reason, 'subjects_checked': subjects, 'observations': list(observations)} | extra


def short(error):
    return f'{type(error).__name__}: {str(error)[:120]}'


def enum(value, allowed, fallback='redacted'):
    """The value when it is one of `allowed`, None when absent, else `fallback`. Source text is never copied."""
    if value is None:
        return None
    return value if isinstance(value, str) and value in allowed else fallback


def project_options(options):
    """({option id: bool or known string}, dropped). Only documented ids with a bool or an allowlisted word survive; the rest is counted."""
    if options is None:
        return {}, 0
    if isinstance(options, dict):
        pairs = list(options.items())
    elif isinstance(options, list):
        pairs = [(o.get('id'), o['value']) if isinstance(o, dict) and 'value' in o else (None, None) for o in options]
    else:
        return {}, 1
    kept, dropped = {}, 0
    for name, value in pairs:
        keep = (isinstance(name, str) and name in OPTION_IDS and name not in kept
                and (type(value) is bool or (isinstance(value, str) and OPTION_VALUE.fullmatch(value) and value in OPTION_VALUES)))
        if keep:
            kept[name] = value
        else:
            dropped += 1
    return kept, dropped


def known(value, static, extra=()):
    """(value or 'other', unknown reason or None) for an identifier that must be a known one, or one the receipt itself named."""
    if value is None:
        return None, None
    return (value, None) if isinstance(value, str) and (value in static or value in extra) else ('other', 'value_not_in_allowlist')


def status_of(value):
    """(T3 status or 'other', unknown reason or None)."""
    value, dropped = bounded(value, T3_STATUSES)
    return value, 'value_not_in_allowlist' if dropped else None


def timestamp(value):
    """(iso string or None, reason or None). A value that is not an ISO timestamp is dropped, not copied."""
    if value is None:
        return None, 'not_recorded'
    try:
        return stamp(value).isoformat(), None
    except ValueError:
        return None, 'not_an_iso_timestamp'


def timestamps(**named):
    """({name: iso}, {name: reason}) for named source timestamps."""
    found, unknown = {}, {}
    for name, value in named.items():
        iso, reason = timestamp(value)
        if iso:
            found[name] = iso
        else:
            unknown[name] = reason
    return found, unknown


# ---- T3 ---------------------------------------------------------------------------------------------------------------

def checked_id(raw, name, unknown):
    """The id when it fits the T3 id grammar. Otherwise None, and `unknown[name]` says why: the source text is never retained."""
    value = safe_id(raw)
    if value is None:
        unknown[name] = 'invalid_id'
    return value


def checked_ordinal(raw, name, unknown):
    value = safe_ordinal(raw)
    if value is None:
        unknown[name] = 'invalid_ordinal'
    return value


def ids_of(**named):
    return {name: value for name, value in named.items() if value is not None}


def table_columns(db, name):
    return {row[1] for row in db.execute(f'PRAGMA table_info({PREFIX}{name})')}


def run_details(db, run_id):
    """Run status and timestamps, attempt statuses and classified terminal failures for one child run. No text columns."""
    columns = table_columns(db, 'runs')
    completed = 'completed_at' if 'completed_at' in columns else 'NULL'
    row = db.execute(f"""SELECT status,requested_at,{completed},json_extract(payload_json,'$.startedAt'),
        json_extract(payload_json,'$.completedAt') FROM {PREFIX}runs WHERE run_id=?""", (run_id,)).fetchone()
    attempts = []
    if {'attempt_id', 'run_id', 'attempt_ordinal', 'status'} <= table_columns(db, 'run_attempts'):
        attempts = db.execute(f'SELECT attempt_id,attempt_ordinal,status FROM {PREFIX}run_attempts WHERE run_id=? ORDER BY attempt_ordinal,attempt_id',
                              (run_id,)).fetchall()
    failures = []
    if {'turn_item_id', 'run_id', 'status', 'updated_at', 'payload_json'} <= table_columns(db, 'turn_items'):
        failures = db.execute(f"""SELECT turn_item_id,updated_at,json_extract(payload_json,'$.startedAt'),
            json_extract(payload_json,'$.failure.class'),
            coalesce(json_extract(payload_json,'$.failure.message'),json_extract(payload_json,'$.title'),'')
            FROM {PREFIX}turn_items WHERE run_id=? AND turn_item_id LIKE '%terminal-failure%' AND status='failed'
            ORDER BY turn_item_id""", (run_id,)).fetchall()
    return row, attempts, failures


PR_STATES = ('open', 'merged', 'closed', 'draft')
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}')


def pull_request_facts(db, thread, run_id, decision, ids, run_owner):
    """One snapshot of the pull requests linked to the child thread, timed by the thread's projection update, so a link
    that is later removed or becomes unreadable replaces the earlier snapshot. `shared_with_decisions` counts the
    decisions whose delegate calls ran in this thread: thread-wide PRs cannot be attributed when it is above 1."""
    columns = table_columns(db, 'threads')
    updated = 'updated_at' if 'updated_at' in columns else 'NULL'
    # Parsed here, not with json_extract: a malformed payload must not abort the whole T3 source.
    row = db.execute(f"SELECT payload_json,{updated} FROM {PREFIX}threads WHERE thread_id=?", (thread,)).fetchone()
    if row is None:
        return []
    runs = [r[0] for r in db.execute(f'SELECT run_id FROM {PREFIX}runs WHERE thread_id=?', (thread,))]
    sharing = len({(run_owner or {}).get(r) for r in runs if (run_owner or {}).get(r)} | {decision})
    stamps, why = timestamps(thread_updated_at=row[1])
    fact = {'child_thread_id': thread, 'state': 'listed', 'pull_requests': [], 'dropped': 0, 'shared_with_decisions': sharing}
    try:
        payload = json.loads(row[0]) if row[0] else {}
        linked = payload.get('pullRequests', []) if isinstance(payload, dict) else None
        linked = [] if linked is None else linked
    except (TypeError, ValueError):
        linked = None
    if not isinstance(linked, list):
        fact['state'] = 'unreadable'
        why = why | {'pull_requests': 'unreadable_projection'}
    else:
        for entry in linked:
            entry = entry if isinstance(entry, dict) else {}
            snap = entry.get('snapshot') if isinstance(entry.get('snapshot'), dict) else {}
            repo, number = entry.get('repository'), entry.get('number')
            if not (isinstance(repo, str) and REPOSITORY.fullmatch(repo) and type(number) is int and number > 0):
                fact['dropped'] += 1
                continue
            pr_stamps, _ = timestamps(merged_at=snap.get('mergedAt'), closed_at=snap.get('closedAt'), synced_at=snap.get('syncedAt'))
            fact['pull_requests'].append({'repository': repo, 'number': number, 'state': enum(snap.get('state'), PR_STATES), **pr_stamps})
    return [store.make_observation('t3', run_id, decision, 't3_child_pull_requests', fact, source_ids=ids, source_timestamps=stamps,
                                   unknown=why | ({'state': 'T3 snapshot; may lag GitHub'} if fact['pull_requests'] else {}))]


def child_thread_facts(db, run_id, decision, ids, run_owner=None):
    """Follow-up messages and linked pull requests of the child thread that ran `run_id`. Counts and enums only, no text."""
    if not ({'run_id', 'thread_id'} <= table_columns(db, 'runs') and {'thread_id', 'payload_json'} <= table_columns(db, 'threads')
            and {'thread_id', 'type', 'updated_at'} <= table_columns(db, 'turn_items')):
        return []
    row = db.execute(f'SELECT thread_id FROM {PREFIX}runs WHERE run_id=?', (run_id,)).fetchone()
    unknown = {}
    thread = checked_id(row[0], 'child_thread_id', unknown) if row and row[0] else None
    if not thread:
        return []
    found = []
    messages, last = db.execute(f"SELECT count(*), max(updated_at) FROM {PREFIX}turn_items WHERE thread_id=? AND type='user_message'",
                                (thread,)).fetchone()
    stamps, why = timestamps(last_user_message_at=last)
    found.append(store.make_observation(
        't3', run_id, decision, 't3_child_followups',
        # The first user message is the brief; later ones are follow-ups sent to the same child. They are not repair rounds:
        # parents often repair through a new child, and some follow-ups are questions (70 of 127 closes agreed, 2026-10-15).
        {'child_thread_id': thread, 'user_messages': messages, 'followup_messages': max(messages - 1, 0) if messages else None},
        source_ids=ids, source_timestamps=stamps, unknown=unknown | why | ({} if messages else {'followup_messages': 'no_user_messages'})))
    found += pull_request_facts(db, thread, run_id, decision, ids, run_owner)
    return found


def usage_observations(source, run_id, decision_id, usage):
    """One row per reported turn, labelled as reported. Never a sum: scopes can overlap. Ids arrive validated or None."""
    turns = [(a['attempt_id'], a['attempt_ordinal'], t) for a in usage['attempts'] for t in a['turns']]
    if not turns:
        return [store.make_observation(source, run_id, decision_id, 't3_usage_unavailable',
                                       {'state': usage['state'], 'reason': usage['reason']}, source_ids={'run_id': run_id})]
    found = []
    for attempt, ordinal, turn in turns:
        unknown = {'timestamps': 'not_projected_by_the_usage_reader'} | (
            {} if turn['usage_status'] in ('complete', 'partial') else {'tokens': f'usage_status_{turn["usage_status"]}'})
        unknown |= {name: 'invalid_id' for name, value in (('attempt_id', attempt), ('provider_turn_id', turn['provider_turn_id'])) if value is None}
        unknown |= {name: 'invalid_ordinal' for name, value in (('attempt_ordinal', ordinal), ('ordinal', turn['ordinal'])) if value is None}
        found.append(store.make_observation(
            source, run_id, decision_id, 't3_turn_usage',
            {'attempt_id': attempt, 'attempt_ordinal': ordinal, 'label': usage['label'], 'sums': usage['sums']} | turn,
            source_ids=ids_of(run_id=run_id, provider_turn_id=turn['provider_turn_id']), unknown=unknown))
    return found


def collect_t3(db_path, scope_ids, targets=None, calls_override=None, run_owner_override=None):
    if not db_path:
        return result('unavailable', 'no_db_supplied')
    if not Path(db_path).exists():
        return result('unavailable', 'database_missing')
    if calls_override is None:
        try:
            calls, _ = read_db(db_path, None, None)
        except ValueError as error:
            return result('error', short(error))
    else:
        calls = calls_override
    # Every delegate call, not only those in scope, says which decision owns a child run (for shared-thread checks).
    run_owner = run_owner_override or {c['child_run_id']: c['decision_id'] for c in calls if c.get('child_run_id')}
    calls = [c for c in calls if c['decision_id'] in scope_ids]
    targets = targets or {}
    found, runs = [], {}
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error as error:
        return result('error', short(error))
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for position, call in enumerate(calls):
            decision = call['decision_id']
            stamps, unknown = timestamps(started_at=call['timestamp'])
            call_status, why = status_of(call['status'])
            call_id = checked_id(call['call_id'], 'call_id', unknown)
            thread_id = checked_id(call['thread_id'], 'thread_id', unknown)
            child = checked_id(call['child_run_id'], 'child_run_id', unknown) if call['child_run_id'] is not None else None
            found.append(store.make_observation(
                't3', call_id or f'invalid-call:{decision}:{position}', decision, 't3_delegate_call',
                {'call_status': call_status, 'child_run_id': child},
                source_ids=ids_of(call_id=call_id, thread_id=thread_id), source_timestamps=stamps,
                unknown=unknown | ({} if child or 'child_run_id' in unknown else {'child_run_id': 'call_started_no_child_run'})
                | ({'call_status': why} if why else {})))
            if child and child not in runs:
                runs[child] = (decision, call)
        for run_id, (decision, call) in runs.items():
            row, attempts, failures = run_details(db, run_id)
            ids = {'run_id': run_id}
            if row is None:
                found.append(store.make_observation('t3', run_id, decision, 't3_run_status', {'status': None},
                                                    source_ids=ids, unknown={'status': 'run_row_absent'}))
                continue
            status, requested_at, completed_at, started_at, payload_completed = row
            stamps, unknown = timestamps(requested_at=requested_at, started_at=started_at, completed_at=completed_at or payload_completed)
            status, why = status_of(status)
            found.append(store.make_observation('t3', run_id, decision, 't3_run_status', {'status': status},
                                                source_ids=ids, source_timestamps=stamps, unknown=unknown | ({'status': why} if why else {})))
            options, options_dropped = project_options(call['child_requested_options'])
            named = targets.get(decision) or {}
            instance, instance_why = known(call['child_provider'], KNOWN_INSTANCES, (named.get('providerInstanceId'),))
            model, model_why = known(call['child_requested_model'], KNOWN_MODELS, (named.get('model'),))
            found.append(store.make_observation(
                't3', run_id, decision, 't3_requested_config',
                {'requested': {'provider_instance': instance, 'model': model, 'options': options, 'options_dropped': options_dropped,
                               'effort': effort({'options': options})},
                 'served_model': 'unattested'}, source_ids=ids, source_timestamps={k: v for k, v in stamps.items() if k == 'requested_at'},
                unknown=({} if call['child_provider'] else {'requested': 'child_run_has_no_provider_instance'})
                | ({'provider_instance': instance_why} if instance_why else {}) | ({'model': model_why} if model_why else {})))
            for attempt_id, ordinal, attempt_status in attempts:
                attempt_status, why = status_of(attempt_status)
                unknown = {'timestamps': 'attempt_rows_carry_none'} | ({'status': why} if why else {})
                attempt_id = checked_id(attempt_id, 'attempt_id', unknown)
                ordinal = checked_ordinal(ordinal, 'attempt_ordinal', unknown)
                found.append(store.make_observation(
                    't3', run_id, decision, 't3_attempt_status',
                    {'attempt_id': attempt_id, 'attempt_ordinal': ordinal, 'status': attempt_status},
                    source_ids=ids | ids_of(attempt_id=attempt_id), unknown=unknown))
            for item_id, updated_at, item_started, native_class, message in failures:
                if str(native_class).lower() in CANCELLATIONS:
                    continue
                stamps, unknown = timestamps(at=item_started or updated_at)
                item_id = checked_id(item_id, 'turn_item_id', unknown)
                found.append(store.make_observation(
                    't3', run_id, decision, 't3_terminal_failure',
                    {'turn_item_id': item_id, 'failure_class': classify_failure(message)},
                    source_ids=ids | ids_of(turn_item_id=item_id), source_timestamps=stamps, unknown=unknown))
            found += child_thread_facts(db, run_id, decision, ids, run_owner)
            found += usage_observations('t3', run_id, decision, call['observed_child_usage'])
        db.commit()
    except sqlite3.Error as error:
        return result('error', short(error))
    finally:
        db.close()
    return result('ok', None, len(runs) + sum(c['child_run_id'] is None for c in calls), found)


def read_t3_rowid_batch(db_path, after, through, limit, rowids=None):
    """Read one bounded turn_items rowid range. The query uses only the rowid primary index."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        source_filter = 'i.rowid IN (' + ','.join('?' for _ in rowids) + ')' if rowids else 'i.rowid>? AND i.rowid<=?'
        params = list(rowids) if rowids else [after, through]
        rows = db.execute(f"""SELECT i.rowid source_rowid,i.turn_item_id call_id,i.thread_id,
            json_extract(i.payload_json,'$.startedAt') timestamp,i.status,
            json_extract(i.payload_json,'$.input.clientRequestId') decision_id,
            json_quote(json_extract(i.payload_json,'$.input.target')) target,
            coalesce(json_extract(i.payload_json,'$.output.childRunId'),
                     json_extract(i.payload_json,'$.output.structuredContent.childRunId')) child_run_id,
            p.provider_instance_id parent_provider,
            json_extract(p.payload_json,'$.modelSelection.model') parent_model,
            c.status child_status,c.provider_instance_id child_provider,
            json_extract(c.payload_json,'$.modelSelection.model') child_requested_model,
            json_quote(json_extract(c.payload_json,'$.modelSelection.options')) child_requested_options,
            json_extract(i.payload_json,'$.toolName') tool_name
            FROM {PREFIX}turn_items i LEFT JOIN {PREFIX}runs p ON p.run_id=i.run_id
            LEFT JOIN {PREFIX}runs c ON c.run_id=coalesce(
              json_extract(i.payload_json,'$.output.childRunId'),
              json_extract(i.payload_json,'$.output.structuredContent.childRunId'))
            WHERE {source_filter} ORDER BY i.rowid LIMIT ?""", (*params, limit)).fetchall()
        db.commit()
    finally:
        db.close()
    calls = []
    matched_rowids = []
    for row in rows:
        call = dict(row)
        call.pop('tool_name', None)
        # The old collector matched the tool name with SQL LIKE '%delegate_task'.
        payload_tool = row['tool_name']
        if not isinstance(payload_tool, str) or not payload_tool.endswith('delegate_task'):
            continue
        matched_rowids.append(int(row['source_rowid']))
        for key in ('target', 'child_requested_options'):
            call[key] = json.loads(call[key]) if call[key] else None
        call.pop('source_rowid', None)
        call['observed_child_usage'] = read_child_usage_for_call(db_path, call['child_run_id'])
        calls.append(call)
    return rows[-1]['source_rowid'] if rows and not rowids else after, calls, len(rows), matched_rowids


def read_child_usage_for_call(db_path, child_run_id):
    if not child_run_id:
        from delegation_audit import usage_unknown
        return usage_unknown('no_child_run')
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        from delegation_audit import read_child_usage
        usage = read_child_usage(db, child_run_id, close_batches=True)
        db.commit()
        return usage
    except sqlite3.Error:
        from delegation_audit import usage_unknown
        return usage_unknown('unavailable', 'read_error')
    finally:
        db.close()


def owner_map(row_owners):
    ordered = sorted((int(rowid), value) for rowid, value in row_owners.items()
                     if isinstance(value, dict) and value.get('child_run_id'))
    return {value['child_run_id']: value['decision_id'] for _, value in ordered}


def incremental_t3(db_path, scope_ids, targets, state_path, deadline):
    """Collect at most one short rowid batch at a time, resuming from private local state."""
    if not db_path:
        return result('unavailable', 'no_db_supplied')
    if not Path(db_path).exists():
        return result('unavailable', 'database_missing')
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
        db.execute('PRAGMA query_only=ON')
        high = db.execute(f'SELECT coalesce(max(rowid),0) FROM {PREFIX}turn_items').fetchone()[0]
        db.close()
    except sqlite3.Error as error:
        return result('error', short(error))
    state = {}
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = state_path.parent.stat()
    if parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077:
        return result('error', 'observe_state_parent_not_private')
    if state_path.is_symlink():
        return result('error', 'observe_state_must_not_be_symlink')
    try:
        state = json.loads(Path(state_path).read_text())
        if not isinstance(state, dict):
            return result('error', 'observe_state_invalid')
        if state.get('database') != str(Path(db_path).resolve()):
            state = {}
        elif (type(state.get('rowid')) is not int or state['rowid'] < 0 or not isinstance(state.get('call_rowids'), list)
              or len(state['call_rowids']) > OBSERVE_INITIAL_ROWS
              or any(type(rowid) is not int or rowid < 1 for rowid in state['call_rowids'])
              or not isinstance(state.get('row_owners', {}), dict)
              or any(not rowid.isdigit() or int(rowid) < 1 or not isinstance(value, dict)
                     or not isinstance(value.get('child_run_id'), str)
                     or value.get('decision_id') is not None and not isinstance(value.get('decision_id'), str)
                     for rowid, value in state.get('row_owners', {}).items())
              or type(state.get('refresh_after', 0)) is not int or state.get('refresh_after', 0) < 0):
            return result('error', 'observe_state_invalid')
        elif state['rowid'] > high:
            state = {}
    except FileNotFoundError:
        pass
    except (ValueError, OSError):
        return result('error', 'observe_state_unreadable')
    initial = 'rowid' not in state
    cursor = max(0, high - OBSERVE_INITIAL_ROWS) if initial else state['rowid']
    floor = cursor
    call_rowids = list(state.get('call_rowids', []))
    row_owners = state.get('row_owners', {})
    refresh_after = state.get('refresh_after', 0)
    found, subjects, scanned = [], 0, 0
    refreshed = 0
    # Projection rows can change after insertion. Refresh known delegate rows by their rowid primary key,
    # then fetch their run/attempt/thread facts through the existing primary-key lookups.
    run_owner = owner_map(row_owners)
    refresh_done = True
    now = __import__('time').monotonic()
    refresh_deadline = now + max(0, deadline - now) * 0.25
    refresh_order = sorted(call_rowids)
    start = next((i for i, rowid in enumerate(refresh_order) if rowid > refresh_after), len(refresh_order))
    refresh_order = refresh_order[start:]
    for offset in range(0, len(refresh_order), OBSERVE_BATCH_ROWS):
        if __import__('time').monotonic() >= refresh_deadline:
            refresh_done = False
            break
        requested = refresh_order[offset:offset + OBSERVE_BATCH_ROWS]
        _, calls, count, matched_rowids = read_t3_rowid_batch(db_path, cursor, high, OBSERVE_BATCH_ROWS, requested)
        matched = set(matched_rowids)
        for rowid in requested:
            if rowid not in matched:
                row_owners.pop(str(rowid), None)
        for rowid, call in zip(matched_rowids, calls):
            if call.get('child_run_id'):
                row_owners[str(rowid)] = {'child_run_id': call['child_run_id'], 'decision_id': call['decision_id']}
        run_owner = owner_map(row_owners)
        if calls:
            batch = collect_t3(db_path, scope_ids, targets, calls_override=calls, run_owner_override=run_owner)
            if batch['state'] not in ('ok', 'unavailable'):
                return batch
            found.extend(batch['observations'])
            subjects += batch['subjects_checked']
        refreshed += count
        refresh_after = requested[-1]
    if refresh_done:
        refresh_after = 0
    while cursor < high and __import__('time').monotonic() < deadline:
        try:
            next_cursor, calls, count, matched_rowids = read_t3_rowid_batch(db_path, cursor, high, OBSERVE_BATCH_ROWS)
            if next_cursor == cursor:
                break
            call_rowids.extend(matched_rowids)
            for rowid, call in zip(matched_rowids, calls):
                if call.get('child_run_id'):
                    row_owners[str(rowid)] = {'child_run_id': call['child_run_id'], 'decision_id': call['decision_id']}
            run_owner = owner_map(row_owners)
            if calls:
                batch = collect_t3(db_path, scope_ids, targets, calls_override=calls, run_owner_override=run_owner)
                if batch['state'] not in ('ok', 'unavailable'):
                    return batch
                found.extend(batch['observations'])
                subjects += batch['subjects_checked']
            scanned += count
            cursor = next_cursor
        except (sqlite3.Error, ValueError, OSError) as error:
            return result('error', short(error), subjects, found)
    exhausted = cursor < high or not refresh_done
    call_rowids = sorted({rowid for rowid in call_rowids if rowid > max(0, cursor - OBSERVE_INITIAL_ROWS)})
    retained = {str(rowid) for rowid in call_rowids}
    row_owners = {rowid: value for rowid, value in row_owners.items() if rowid in retained}
    return result('partial' if exhausted else 'ok', 'time_budget_exhausted' if exhausted else None, subjects, found,
                  incremental={'initial_window': f'last {OBSERVE_INITIAL_ROWS} turn_items rowids' if initial else None,
                               'high_water_before': state.get('rowid'), 'rowid_from': floor, 'rowid_through': cursor,
                               'snapshot_high_water': high, 'rows_scanned': scanned, 'rows_refreshed': refreshed,
                               'budget_exhausted': exhausted},
                  _state={'database': str(Path(db_path).resolve()), 'rowid': cursor, 'call_rowids': call_rowids,
                          'row_owners': row_owners, 'refresh_after': refresh_after})


# ---- destinations -----------------------------------------------------------------------------------------------------

def destination_observations(source, request_id, record):
    """Facts for one request from a destination's observe command (untrusted). Every string is an enum member or 'redacted'."""
    if not isinstance(record, dict) or type(record.get('found')) is not bool:
        raise ValueError('request record needs a boolean found')
    ids = {'request_id': request_id}
    if not record['found']:
        return [store.make_observation(source, request_id, request_id, 'destination_assignment', {'found': False}, source_ids=ids)]
    stamps, unknown = timestamps(created_at=record.get('created_at'), cancelled_at=record.get('cancelled_at'))
    unknown = {k: v for k, v in unknown.items() if not (k == 'cancelled_at' and record.get('cancelled_at') is None)}
    found = [store.make_observation(
        source, request_id, request_id, 'destination_assignment',
        {'found': True, 'status': enum(record.get('status'), ASSIGNMENT_STATUSES), 'task_kind': enum(record.get('task_kind'), TASK_KINDS),
         'source': enum(record.get('source'), TASK_SOURCES), 'cancelled_reason': enum(record.get('cancelled_reason'), CANCEL_REASONS)},
        source_ids=ids, source_timestamps=stamps, unknown=unknown)]
    for report in record.get('reports') or []:
        stamps, unknown = timestamps(created_at=report.get('created_at'))
        receipt = report.get('receipt_id')
        receipt = receipt if isinstance(receipt, str) and RECEIPT_ID.fullmatch(receipt) else None
        found.append(store.make_observation(
            source, request_id, request_id, 'destination_report',
            {'outcome': enum(report.get('outcome'), REPORT_OUTCOMES), 'receipt_id': receipt},
            source_ids=ids | {'receipt_id': receipt}, source_timestamps=stamps, unknown=unknown))
    for event in record.get('events') or []:
        stamps, unknown = timestamps(at=event.get('at'))
        event_id = event.get('event_id')
        event_id = event_id if type(event_id) is int and event_id >= 0 else None
        found.append(store.make_observation(
            source, request_id, request_id, 'destination_event',
            {'event_id': event_id, 'event_type': enum(event.get('type'), EVENT_TYPES), 'actor_kind': enum(event.get('actor_kind'), ACTOR_KINDS),
             'reason': enum(event.get('reason'), EVENT_REASONS)}, source_ids=ids | {'event_id': event_id}, source_timestamps=stamps, unknown=unknown))
    return found


def ask_destination(entry, ids, deadline=None):
    """Run the observe command for one batch. Returns (requests dict, None) or (None, reason)."""
    timeout = min(entry['timeout_seconds'], OBSERVE_TIMEOUT_CAP)
    if deadline is not None:
        timeout = min(timeout, max(0.01, deadline - __import__('time').monotonic()))
    stdout, code, failure = destination_store.run_bounded(
        entry['observe_command'] + ['--stdin'], timeout, ('\n'.join(ids) + '\n').encode(), OBSERVE_OUTPUT_LIMIT)
    if failure:
        return None, f'observe_command_{failure}'
    try:
        value = json.loads(stdout.decode('utf-8'))
    except ValueError:
        return None, 'observe_command_invalid_json'
    if not isinstance(value, dict):
        return None, 'observe_command_invalid_shape'
    if 'error' in value:
        return None, 'observe_command_reported_error'
    if code != 0:
        return None, 'observe_command_nonzero_exit'
    if not isinstance(value.get('requests'), dict):
        return None, 'observe_command_invalid_shape'
    return value['requests'], None


def collect_destination(entry, ids, deadline=None):
    name = entry['name'] if entry else None
    if entry is None:
        return result('unavailable', 'destination_not_configured')
    if 'observe_command' not in entry:
        return result('unavailable', 'no_observe_command')
    source = f'destination:{name}'
    found, reason, checked = [], None, 0
    ordered = sorted(ids)
    for start in range(0, len(ordered), BATCH):
        if deadline is not None and __import__('time').monotonic() >= deadline:
            return result('partial', 'time_budget_exhausted', checked, found)
        batch = ordered[start:start + BATCH]
        requests, reason = ask_destination(entry, batch, deadline)
        if reason:
            break
        for request_id in batch:
            checked += 1
            try:
                if request_id not in requests:
                    raise ValueError('request id missing from output')
                found += destination_observations(source, request_id, requests[request_id])
            except (ValueError, TypeError, AttributeError) as error:
                reason = f'observe_command_invalid_record: {short(error)}'
                break
        if reason:
            break
    return result('error' if reason else 'ok', reason, checked, found)


# ---- git --------------------------------------------------------------------------------------------------------------

class GitUnavailable(Exception):
    pass


class Ineligible(Exception):
    """The evidence is not something observe may read (no repo, not a root, no such commit): counted in `skipped`."""


class GitFailure(Exception):
    """git could not answer (timeout, output limit, non-zero exit, a missing local object): counted in `failed`, never healthy."""


class BudgetExhausted(Exception):
    pass


def failure_reason(repo, deadline=None):
    """A non-zero exit in a partial clone usually means a missing object that only the promisor remote has; observe never fetches it."""
    timeout = GIT_TIMEOUT if deadline is None else max(0.01, min(GIT_TIMEOUT, deadline - __import__('time').monotonic()))
    stdout, code, failure = destination_store.run_bounded(git_argv(repo, 'config', '--get-regexp', r'^remote\..*\.promisor$'), timeout,
                                                          limit=GIT_OUTPUT_LIMIT, env=git_env())
    promisor = not failure and code == 0 and any(line.split()[-1:] == [b'true'] for line in stdout.splitlines())
    return 'git_missing_local_object' if promisor else 'git_failed'


def git(repo, *args, deadline=None):
    """Read-only git with GIT_* cleared, no optional locks, no lazy fetch, bounded time and output. Returns stdout text; raises GitFailure."""
    if deadline is not None and deadline - __import__('time').monotonic() <= 0:
        raise BudgetExhausted
    timeout = GIT_TIMEOUT if deadline is None else max(0.01, min(GIT_TIMEOUT, deadline - __import__('time').monotonic()))
    stdout, code, failure = destination_store.run_bounded(git_argv(repo, *args), timeout, limit=GIT_OUTPUT_LIMIT, env=git_env())
    if failure == 'missing_binary':
        raise GitUnavailable('git_not_found')
    if failure:
        if deadline is not None and __import__('time').monotonic() >= deadline:
            raise BudgetExhausted
        raise GitFailure(f'git_{failure}')
    if code != 0:
        raise GitFailure(failure_reason(repo, deadline))
    return stdout.decode('utf-8', 'replace')


def path_kind(path):
    """'dir', 'file', 'other' or 'absent' for one path; a final symlink is followed explicitly (dangling = 'absent'). Only real absence is 'absent':
    a permission or I/O error is a GitFailure ('filesystem_unreadable'), never a healthy skip."""
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 'absent'
    except OSError:
        raise GitFailure('filesystem_unreadable') from None
    if stat.S_ISLNK(mode):
        try:
            mode = os.stat(path).st_mode
        except (FileNotFoundError, NotADirectoryError):
            return 'absent'
        except OSError:
            raise GitFailure('filesystem_unreadable') from None
    return 'dir' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'other'


def is_repo_root(repo, deadline=None):
    """The path is a repository itself, not a directory inside another one: no other repo is ever scanned.

    A directory with neither `.git` nor the files of a bare repository is not a root, whatever git would discover above it. For a
    candidate root, git must confirm it; any failure to answer (dubious ownership, unreadable config, a non-zero exit) is a GitFailure.
    """
    if path_kind(os.path.join(repo, '.git')) != 'absent':
        return os.path.realpath(git(repo, 'rev-parse', '--show-toplevel', deadline=deadline).strip()) == os.path.realpath(repo)
    if path_kind(os.path.join(repo, 'HEAD')) == 'file' and path_kind(os.path.join(repo, 'objects')) == 'dir':
        return git(repo, 'rev-parse', '--git-dir', deadline=deadline).strip() == '.'  # a bare repository
    return False


def touched_files(repo, sha, deadline=None):
    names = [n for n in git(repo, 'diff-tree', '-r', '--no-renames', '--no-commit-id', '--name-only', '--root', sha, deadline=deadline).splitlines() if n]
    return set(names[:GIT_MAX_FILES]), len(names) > GIT_MAX_FILES


def later_commits(repo, sha, start, end, deadline=None):
    """[(sha, committed_at, set of files)] for commits not reachable from `sha`, committed within [start, end]."""
    text = git(repo, 'log', '--all', f'^{sha}', f'--after={start.isoformat()}', f'--before={end.isoformat()}', '--no-renames',
               f'--max-count={GIT_MAX_COMMITS}', '--name-only', '--format=%x01%H %cI', '--', deadline=deadline)
    commits = []
    for block in text.split('\x01')[1:]:
        head, _, names = block.partition('\n')
        commit, _, when = head.partition(' ')
        commits.append((commit, when.strip(), {n for n in names.splitlines() if n}))
    return commits, len(commits) >= GIT_MAX_COMMITS


def reverting_commits(repo, sha, start, end, deadline=None):
    """Later commits whose message says 'This reverts commit <sha>'. The message is matched by git and never read."""
    text = git(repo, 'log', '--all', f'^{sha}', f'--after={start.isoformat()}', f'--before={end.isoformat()}', '-F',
               f'--grep=This reverts commit {sha}', f'--max-count={MAX_REVERTS}', '--format=%H %cI', '--', deadline=deadline)
    return [tuple(line.split(' ', 1)) for line in text.splitlines() if line.strip()]


def scan_commit(close, ref, window_days, now, deadline=None):
    """Candidate follow-up signals for one evidenced commit of one close. Raises Ineligible (skip) or GitFailure (operational)."""
    repo, sha = ref['repo'], ref['sha']
    if not (os.path.isabs(repo) and SHA1.fullmatch(sha) and path_kind(repo) == 'dir'):
        raise Ineligible('repo_missing')
    if not is_repo_root(repo, deadline):
        raise Ineligible('not_a_repository_root')
    if deadline is not None and deadline - __import__('time').monotonic() < COMMIT_TIMEOUT:
        raise BudgetExhausted
    state = commit_state(repo, sha)
    if state != 'exists':
        if state == 'missing':
            raise Ineligible('sha_missing')
        raise GitFailure('git_timeout' if state == 'timeout' else 'git_failed')
    try:
        committed = stamp(git(repo, 'log', '-1', '--format=%cI', sha, deadline=deadline).strip())
    except ValueError:
        raise GitFailure('git_bad_output') from None
    closed = stamp(close['recorded_at'])
    end = min(now, closed + timedelta(days=window_days))
    files, truncated = touched_files(repo, sha, deadline)
    scope = {'repo': repo, 'evidenced_sha': sha, 'files_touched': len(files), 'files_truncated': truncated,
             'window': {'start': committed.isoformat(), 'end': end.isoformat(), 'days': window_days,
                        'end_bound_by': 'now' if end == now else 'close_time_plus_days'}}
    subject = f'{close["close_id"]}:{sha}'
    ids = {'close_id': close['close_id'], 'evidenced_commit': sha}
    decision = close['decision_id']
    found = [store.make_observation(
        'git', subject, decision, 'git_scan', {'evidenced_sha': sha, 'committed_at': committed.isoformat(),
                                               'files_touched': len(files), 'window_days': window_days,
                                               'meaning': store.LABELS['git']},
        source_ids=ids, source_timestamps={'committed_at': committed.isoformat()}, scope=scope)]
    if end >= committed:
        commits, capped = later_commits(repo, sha, committed, end, deadline)
        scope = scope | {'commits_capped': capped}
        for commit, when, names in commits:
            overlap = len(files & names)
            if commit != sha and overlap and stamp(when) >= committed:
                found.append(store.make_observation(
                    'git', subject, decision, 'same_file_later_commit',
                    {'sha': commit, 'committed_at': stamp(when).isoformat(), 'overlapping_file_count': overlap},
                    source_ids=ids | {'later_commit': commit}, source_timestamps={'committed_at': stamp(when).isoformat()}, scope=scope))
        for commit, when in reverting_commits(repo, sha, committed, end, deadline):
            found.append(store.make_observation(
                'git', subject, decision, 'explicit_revert_reference',
                {'explicit_revert_reference': True, 'evidenced_sha': sha, 'reverting_sha': commit},
                source_ids=ids | {'later_commit': commit}, source_timestamps={'committed_at': stamp(when).isoformat()}, scope=scope))
    return found


def collect_git(outcome_rows, scope_ids, window_days, now, deadline=None):
    closes, _ = split_rows(list(outcome_rows))
    found, checked, skipped, failed = [], 0, {}, {}
    seen = set()
    for close in closes:
        if deadline is not None and __import__('time').monotonic() >= deadline:
            return result('partial', 'time_budget_exhausted', checked, found, skipped=dict(sorted(skipped.items())),
                          failed=dict(sorted(failed.items())))
        if close['decision_id'] not in scope_ids:
            continue
        for ref in close['evidence']:
            if ref.get('type') != 'commit' or (close['close_id'], ref['repo'], ref['sha']) in seen:
                continue
            seen.add((close['close_id'], ref['repo'], ref['sha']))
            checked += 1
            try:
                found += scan_commit(close, ref, window_days, now, deadline)
            except BudgetExhausted:
                return result('partial', 'time_budget_exhausted', checked - 1, found,
                              skipped=dict(sorted(skipped.items())), failed=dict(sorted(failed.items())))
            except GitUnavailable as error:
                return result('unavailable', str(error))
            except Ineligible as error:
                skipped[str(error)] = skipped.get(str(error), 0) + 1
            except (GitFailure, ValueError) as error:
                reason = str(error) if isinstance(error, GitFailure) else 'git_bad_output'
                failed[reason] = failed.get(reason, 0) + 1
    extra = {'skipped': dict(sorted(skipped.items())), 'failed': dict(sorted(failed.items()))}
    if not failed:
        return result('ok', None, checked, found, **extra)
    reason = 'operational_failure: ' + ', '.join(f'{name}={count}' for name, count in extra['failed'].items())
    return result('partial' if checked - sum(failed.values()) - sum(skipped.values()) else 'error', reason, checked, found, **extra)


# ---- run --------------------------------------------------------------------------------------------------------------

def execute(args):
    """Collect from every source, append the new facts and one source_run row. Source failures are recorded, not raised."""
    if args.git_window_days is None:
        args.git_window_days = DEFAULT_WINDOW_DAYS
    if type(args.git_window_days) is not int or not 1 <= args.git_window_days <= 3650:
        raise ValueError('--git-window-days must be an integer 1-3650')
    since = stamp(normalize_since(args.since)) if args.since else None
    started = datetime.now(timezone.utc)
    import time
    budget = getattr(args, 'observe_time_budget_seconds', OBSERVE_TIME_BUDGET_SECONDS)
    if type(budget) is not int or not 1 <= budget <= 3600:
        raise ValueError('--observe-time-budget-seconds must be an integer 1-3600')
    deadline = time.monotonic() + budget
    receipts = [r for r in read_receipts(args.receipts) if not since or stamp(r['recorded_at']) >= since]
    outcome_rows = read_outcomes(args.outcomes)
    entries, _ = destination_store.read_destinations(args.destinations)
    by_name = {e['name']: e for e in entries}
    scope = store.decision_sources(receipts, outcome_rows)
    collected = {}
    pending_state = None
    state_path = getattr(args, 'observe_state', None) or Path.home() / '.local/state/model-policy/observe.json'
    try:
        t3_result = incremental_t3(args.db, scope.get('t3', set()), {r['decision_id']: r.get('target') for r in receipts}, state_path, deadline)
        pending_state = t3_result.pop('_state', None)
        collected['t3'] = t3_result
    except (OSError, ValueError, sqlite3.Error) as error:
        collected['t3'] = result('error', short(error))
    for source, ids in sorted(scope.items()):
        if source.startswith('destination:'):
            if time.monotonic() >= deadline:
                collected[source] = result('partial', 'time_budget_exhausted')
                continue
            name = source.split(':', 1)[1]
            try:
                collected[source] = collect_destination(by_name.get(name), ids, deadline)
            except (OSError, ValueError) as error:
                collected[source] = result('error', short(error))
    try:
        if time.monotonic() >= deadline:
            collected['git'] = result('partial', 'time_budget_exhausted')
        else:
            collected['git'] = collect_git(outcome_rows, scope.get('git', set()), args.git_window_days, started, deadline)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        collected['git'] = result('error', short(error))
    incremental_info = collected.get('t3', {}).pop('incremental', None)
    run = store.append_run(args.observations, str(uuid.uuid4()), started.isoformat(), datetime.now(timezone.utc).isoformat(), collected)
    if pending_state is not None:
        temp = state_path.with_name(state_path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        with open(temp, 'x') as stream:
            os.chmod(temp, 0o600)
            stream.write(json.dumps(pending_state, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, state_path)
        directory = os.open(state_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    # Printed, never stored: what each evidenced commit's close recorded, beside the live re-check above. Old rows are not_recorded.
    closes, _ = split_rows(outcome_rows)
    evidenced = [c for c in closes if c['decision_id'] in scope.get('git', set())]
    return run | ({'incremental': incremental_info} if incremental_info else {}) | {
        'recorded_commit_verification': commit_verification_summary(evidenced)}
