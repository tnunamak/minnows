"""`observe`: record facts that other systems already hold about recorded decisions. Reads only; never judges.

Each source collects candidate observations, then `observations.append_run` dedupes and appends under the lock.
A source that fails is recorded as `unavailable` or `error` in the source_run row, so a failure is never zero events.
"""
import json
import fcntl
import hashlib
import os
import stat
import re
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import destinations as destination_store
import observations as store
from decision_receipts import read_receipts
from delegation_audit import BudgetExhausted, PREFIX, T3_STATUSES, bounded, effort, normalize_since, read_db, safe_id, safe_ordinal, stamp
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
OBSERVE_ANCHOR_ROWS = 10_000
MAX_STATE_ANCHORS = 128


def default_observe_state():
    state_home = os.environ.get('XDG_STATE_HOME')
    root = Path(state_home) if state_home and Path(state_home).is_absolute() else Path.home() / '.local/state'
    return root / 'model-policy/observe.json'


def private_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('observe state parent directory must not be group- or world-writable')


def private_file(path, create=False):
    if path.is_symlink():
        raise ValueError('observe_state_must_not_be_symlink')
    if not path.exists() and create:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('observe_state_file_not_private')
    return True


def call_index_path(state_path):
    state_path = Path(state_path)
    return state_path.with_name(state_path.stem + '.calls.sqlite3')


def open_call_index(state_path):
    path = call_index_path(state_path)
    private_parent(path)
    exists = private_file(path)
    mask = os.umask(0o077)
    try:
        db = sqlite3.connect(path)
    finally:
        os.umask(mask)
    os.chmod(path, 0o600)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS index_meta (id INTEGER PRIMARY KEY CHECK(id=1), instance TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS known_calls (source_rowid INTEGER PRIMARY KEY, turn_item_id TEXT NOT NULL, child_run_id TEXT, decision_id TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS quarantined_rows (source_rowid INTEGER PRIMARY KEY, reason TEXT NOT NULL)')
    identity = db.execute('SELECT instance FROM index_meta WHERE id=1').fetchone()
    if identity is None:
        identity = str(uuid.uuid4())
        db.execute('INSERT INTO index_meta(id,instance) VALUES(1,?)', (identity,))
        db.commit()
    else:
        identity = identity[0]
    return db, identity, exists


def read_last_ledger_line(path):
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            stream.seek(max(0, end - 1024 * 1024))
            lines = stream.read().splitlines()
        row = json.loads(lines[-1]) if lines else None
        info = os.stat(path)
        return {'path': str(Path(path).resolve()), 'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size,
                'mtime_ns': info.st_mtime_ns,
                'tail_sha256': hashlib.sha256(lines[-1] if lines else b'').hexdigest(),
                'last_run_id': row.get('run_id') if isinstance(row, dict) and row.get('kind') == 'source_run' else None}
    except FileNotFoundError:
        return None


def observe_state_lock(state_path, deadline):
    """Serialize observers that share a cursor and ledger; honor the run budget while waiting."""
    state_path = Path(state_path)
    private_parent(state_path)
    lock_path = state_path.with_name(state_path.name + '.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.fchmod(fd, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('observe_state_lock_not_private')
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BudgetExhausted
                time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
        return fd
    except Exception:
        os.close(fd)
        raise


def release_observe_state_lock(fd):
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def save_state(path, state):
    path = Path(path)
    private_parent(path)
    private_file(path)
    temp = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(state, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


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


def run_details(db, run_id, deadline=None):
    """Run status and timestamps, attempt statuses and classified terminal failures for one child run. No text columns."""
    columns = table_columns(db, 'runs')
    completed = 'completed_at' if 'completed_at' in columns else 'NULL'
    row = db.execute(f"""SELECT status,requested_at,{completed},json_extract(payload_json,'$.startedAt'),
        json_extract(payload_json,'$.completedAt') FROM {PREFIX}runs WHERE run_id=?""", (run_id,)).fetchone()
    attempts = []
    if {'attempt_id', 'run_id', 'attempt_ordinal', 'status'} <= table_columns(db, 'run_attempts'):
        after_ordinal = -1
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExhausted
            page = db.execute(f'SELECT attempt_id,attempt_ordinal,status FROM {PREFIX}run_attempts '
                              'WHERE run_id=? AND attempt_ordinal>? ORDER BY attempt_ordinal,attempt_id LIMIT 500',
                              (run_id, after_ordinal)).fetchall()
            attempts.extend(page)
            if len(page) < 500:
                break
            after_ordinal = page[-1][1]
    failures = []
    if {'turn_item_id', 'run_id', 'status', 'updated_at', 'payload_json'} <= table_columns(db, 'turn_items'):
        if 'ordinal' in table_columns(db, 'turn_items'):
            after_ordinal = -1
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise BudgetExhausted
                page = db.execute(f"""SELECT ordinal,turn_item_id,updated_at,json_extract(payload_json,'$.startedAt'),
                    json_extract(payload_json,'$.failure.class'),
                    coalesce(json_extract(payload_json,'$.failure.message'),json_extract(payload_json,'$.title'),'')
                    FROM {PREFIX}turn_items WHERE run_id=? AND ordinal>? AND turn_item_id LIKE '%terminal-failure%' AND status='failed'
                    ORDER BY ordinal LIMIT 500""", (run_id, after_ordinal)).fetchall()
                failures.extend(tuple(row[1:]) for row in page)
                if len(page) < 500:
                    break
                after_ordinal = page[-1][0]
        else:
            failures = db.execute(f"""SELECT turn_item_id,updated_at,json_extract(payload_json,'$.startedAt'),
                json_extract(payload_json,'$.failure.class'),
                coalesce(json_extract(payload_json,'$.failure.message'),json_extract(payload_json,'$.title'),'')
                FROM {PREFIX}turn_items WHERE run_id=? AND turn_item_id LIKE '%terminal-failure%' AND status='failed'
                ORDER BY turn_item_id LIMIT 500""", (run_id,)).fetchall()
    return row, attempts, failures


PR_STATES = ('open', 'merged', 'closed', 'draft')
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}')


def pull_request_facts(db, thread, run_id, decision, ids, run_owner, deadline=None):
    """One snapshot of the pull requests linked to the child thread, timed by the thread's projection update, so a link
    that is later removed or becomes unreadable replaces the earlier snapshot. `shared_with_decisions` counts the
    decisions whose delegate calls ran in this thread: thread-wide PRs cannot be attributed when it is above 1."""
    columns = table_columns(db, 'threads')
    updated = 'updated_at' if 'updated_at' in columns else 'NULL'
    # Parsed here, not with json_extract: a malformed payload must not abort the whole T3 source.
    row = db.execute(f"SELECT payload_json,{updated} FROM {PREFIX}threads WHERE thread_id=?", (thread,)).fetchone()
    if row is None:
        return []
    runs = []
    if 'ordinal' in table_columns(db, 'runs'):
        after_ordinal = -1
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExhausted
            page = db.execute(f'SELECT run_id,ordinal FROM {PREFIX}runs WHERE thread_id=? AND ordinal>? '
                              'ORDER BY ordinal LIMIT 500', (thread, after_ordinal)).fetchall()
            runs.extend(row[0] for row in page)
            if len(page) < 500:
                break
            after_ordinal = page[-1][1]
    else:
        runs = [row[0] for row in db.execute(f'SELECT run_id FROM {PREFIX}runs WHERE thread_id=?', (thread,))]
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


def child_thread_facts(db, run_id, decision, ids, run_owner=None, include_thread_snapshot=True, deadline=None):
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
    columns = table_columns(db, 'turn_items')
    if 'ordinal' in columns:
        messages, last, after_ordinal = 0, None, -1
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExhausted
            page = db.execute(f"SELECT ordinal,updated_at FROM {PREFIX}turn_items WHERE thread_id=? AND type='user_message' "
                              'AND ordinal>? ORDER BY ordinal LIMIT 500', (thread, after_ordinal)).fetchall()
            messages += len(page)
            last = max((row[1] for row in page if row[1] is not None), default=last)
            if len(page) < 500:
                break
            after_ordinal = page[-1][0]
    else:
        messages, last = db.execute(f"SELECT count(*), max(updated_at) FROM {PREFIX}turn_items WHERE thread_id=? AND type='user_message'",
                                    (thread,)).fetchone()
    stamps, why = timestamps(last_user_message_at=last)
    found.append(store.make_observation(
        't3', run_id, decision, 't3_child_followups',
        # The first user message is the brief; later ones are follow-ups sent to the same child. They are not repair rounds:
        # parents often repair through a new child, and some follow-ups are questions (70 of 127 closes agreed, 2026-10-15).
        {'child_thread_id': thread, 'user_messages': messages, 'followup_messages': max(messages - 1, 0) if messages else None},
        source_ids=ids, source_timestamps=stamps, unknown=unknown | why | ({} if messages else {'followup_messages': 'no_user_messages'})))
    if include_thread_snapshot:
        found += pull_request_facts(db, thread, run_id, decision, ids, run_owner or {}, deadline)
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


def collect_t3(db_path, scope_ids, targets=None, calls_override=None, deadline=None, run_owner_override=None,
               include_thread_snapshot=True):
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
    calls = [c for c in calls if c['decision_id'] in scope_ids]
    targets = targets or {}
    run_owner = run_owner_override or {
        c['child_run_id']: c['decision_id'] for c in calls if c.get('child_run_id') and c.get('decision_id')}
    found, runs = [], {}
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error as error:
        return result('error', short(error))
    try:
        db.execute('PRAGMA query_only=ON')
        if deadline is not None:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        for position, call in enumerate(calls):
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExhausted
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
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExhausted
            row, attempts, failures = run_details(db, run_id, deadline)
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
            found += child_thread_facts(db, run_id, decision, ids, run_owner, include_thread_snapshot, deadline)
            found += usage_observations('t3', run_id, decision, call['observed_child_usage'])
    except sqlite3.Error as error:
        if deadline is not None and time.monotonic() >= deadline:
            raise BudgetExhausted from None
        return result('error', short(error))
    finally:
        db.close()
    return result('ok', None, len(runs) + sum(c['child_run_id'] is None for c in calls), found)


def t3_batch_sql(source_filter):
    return f"""SELECT i.rowid source_rowid,i.turn_item_id call_id,i.thread_id,
        CASE WHEN json_valid(i.payload_json) THEN json_extract(i.payload_json,'$.startedAt') END timestamp,i.status,i.type,
        CASE WHEN json_valid(i.payload_json) THEN json_extract(i.payload_json,'$.input.clientRequestId') END decision_id,
        CASE WHEN json_valid(i.payload_json) THEN json_quote(json_extract(i.payload_json,'$.input.target')) END target,
        CASE WHEN json_valid(i.payload_json) THEN coalesce(json_extract(i.payload_json,'$.output.childRunId'),
                 json_extract(i.payload_json,'$.output.structuredContent.childRunId')) END child_run_id,
        p.provider_instance_id parent_provider,
        CASE WHEN json_valid(p.payload_json) THEN json_extract(p.payload_json,'$.modelSelection.model') END parent_model,
        c.status child_status,c.provider_instance_id child_provider,
        CASE WHEN json_valid(c.payload_json) THEN json_extract(c.payload_json,'$.modelSelection.model') END child_requested_model,
        CASE WHEN json_valid(c.payload_json) THEN json_quote(json_extract(c.payload_json,'$.modelSelection.options')) END child_requested_options,
        CASE WHEN json_valid(i.payload_json) THEN json_extract(i.payload_json,'$.toolName') END tool_name
        FROM {PREFIX}turn_items i LEFT JOIN {PREFIX}runs p ON p.run_id=i.run_id
        LEFT JOIN {PREFIX}runs c ON c.run_id=CASE WHEN json_valid(i.payload_json) THEN coalesce(
          json_extract(i.payload_json,'$.output.childRunId'),
          json_extract(i.payload_json,'$.output.structuredContent.childRunId')) END
        WHERE {source_filter} ORDER BY i.rowid LIMIT ?"""


def read_t3_rowid_batch(db_path, after, through, limit, rowids=None, scope_ids=None, deadline=None):
    """Read one bounded turn_items rowid range. The query uses only the rowid primary index."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if deadline is not None:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        source_filter = 'i.rowid IN (' + ','.join('?' for _ in rowids) + ')' if rowids else 'i.rowid>? AND i.rowid<=?'
        params = list(rowids) if rowids else [after, through]
        rows = db.execute(t3_batch_sql(source_filter), (*params, limit)).fetchall()
        db.commit()
    finally:
        db.close()
    calls = []
    matched_rowids = []
    for row in rows:
        call = dict(row)
        call.pop('tool_name', None)
        source_rowid = int(call.pop('source_rowid'))
        call_type = call.pop('type')
        # Match the previous collector's type guard and case-insensitive SQL LIKE.
        payload_tool = row['tool_name']
        if call_type != 'dynamic_tool' or not isinstance(payload_tool, str) or not payload_tool.lower().endswith('delegate_task'):
            continue
        matched_rowids.append(source_rowid)
        for key in ('target', 'child_requested_options'):
            call[key] = json.loads(call[key]) if call[key] else None
        call['_source_rowid'] = source_rowid
        if scope_ids is None or call['decision_id'] in scope_ids:
            call['observed_child_usage'] = read_child_usage_for_call(db_path, call['child_run_id'], deadline)
        else:
            from delegation_audit import usage_unknown
            call['observed_child_usage'] = usage_unknown('not_in_scope')
        calls.append(call)
    return rows[-1]['source_rowid'] if rows and not rowids else after, calls, len(rows), matched_rowids, rows[-1]['call_id'] if rows and not rowids else None


def read_child_usage_for_call(db_path, child_run_id, deadline=None):
    if not child_run_id:
        from delegation_audit import usage_unknown
        return usage_unknown('no_child_run')
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if deadline is not None:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        from delegation_audit import read_child_usage
        usage = read_child_usage(db, child_run_id, close_batches=True, deadline=deadline)
        db.commit()
        return usage
    except BudgetExhausted:
        raise
    except sqlite3.Error:
        if deadline is not None and time.monotonic() >= deadline:
            raise BudgetExhausted from None
        from delegation_audit import usage_unknown
        return usage_unknown('unavailable', 'read_error')
    finally:
        db.close()


def database_identity(db_path, db):
    info = os.stat(db_path)
    table = db.execute("SELECT rootpage FROM sqlite_master WHERE type='table' AND name=?", (PREFIX + 'turn_items',)).fetchone()
    return {'path': str(Path(db_path).resolve()), 'device': info.st_dev, 'inode': info.st_ino,
            'application_id': db.execute('PRAGMA application_id').fetchone()[0],
            'user_version': db.execute('PRAGMA user_version').fetchone()[0], 'turn_items_rootpage': table[0] if table else None}


def anchors_valid(db, anchors):
    for rowid, turn_item_id in anchors.items():
        row = db.execute(f'SELECT turn_item_id FROM {PREFIX}turn_items WHERE rowid=?', (int(rowid),)).fetchone()
        if row is None or row[0] != turn_item_id:
            return False
    return True


def reset_call_index(call_db):
    call_db.execute('DELETE FROM known_calls')
    call_db.execute('DELETE FROM quarantined_rows')
    call_db.commit()


def remember_calls(call_db, calls):
    call_db.executemany('INSERT INTO known_calls(source_rowid,turn_item_id,child_run_id,decision_id) VALUES(?,?,?,?) '
                        'ON CONFLICT(source_rowid) DO UPDATE SET turn_item_id=excluded.turn_item_id, '
                        'child_run_id=excluded.child_run_id,decision_id=excluded.decision_id',
                        [(c['_source_rowid'], c['call_id'], c.get('child_run_id'), c.get('decision_id')) for c in calls])
    call_db.commit()


def indexed_run_owners(call_db):
    rows = call_db.execute('SELECT child_run_id,decision_id FROM known_calls WHERE child_run_id IS NOT NULL '
                           'ORDER BY source_rowid').fetchall()
    return {run_id: decision for run_id, decision in rows if run_id and decision}


def collect_indexed_thread_facts(db_path, call_db, scope_ids, run_owner, deadline, after_rowid=0):
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        found, subjects, after = [], 0, after_rowid
        while time.monotonic() < deadline:
            page = call_db.execute('SELECT source_rowid,child_run_id,decision_id FROM known_calls '
                                   'WHERE source_rowid>? ORDER BY source_rowid LIMIT ?',
                                   (after, OBSERVE_BATCH_ROWS)).fetchall()
            if not page:
                return found, subjects, 0, True
            for rowid, run_id, decision in page:
                if run_id and decision in scope_ids:
                    try:
                        facts = child_thread_facts(db, run_id, decision, {'run_id': run_id}, run_owner,
                                                   include_thread_snapshot=True, deadline=deadline)
                    except BudgetExhausted:
                        return found, subjects, after, False
                    found.extend(facts)
                    subjects += 1
                after = rowid
            if len(page) < OBSERVE_BATCH_ROWS:
                return found, subjects, 0, True
        return found, subjects, after, False
    except sqlite3.Error as error:
        if time.monotonic() >= deadline:
            return [], 0, after_rowid, False
        raise error
    finally:
        db.close()


def delete_stale_calls(call_db, requested, matched):
    stale = sorted(set(requested) - set(matched))
    call_db.executemany('DELETE FROM known_calls WHERE source_rowid=?', [(rowid,) for rowid in stale])
    call_db.commit()


def next_source_rowid(db_path, after, through):
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return db.execute(f'SELECT rowid FROM {PREFIX}turn_items WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT 1',
                          (after, through)).fetchone()
    finally:
        db.close()


def incremental_t3(db_path, scope_ids, targets, state_path, deadline, ledger_marker=None):
    """Collect indexed rowid batches and resume from private, ledger-bound state."""
    if not db_path:
        return result('unavailable', 'no_db_supplied')
    if not Path(db_path).exists():
        return result('unavailable', 'database_missing')
    state_path = Path(state_path)
    try:
        private_parent(state_path)
        private_file(state_path)
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if not isinstance(state, dict):
            return result('error', 'observe_state_invalid')
        call_db, call_index_id, _ = open_call_index(state_path)
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        high = db.execute(f'SELECT coalesce(max(rowid),0) FROM {PREFIX}turn_items').fetchone()[0]
        identity = database_identity(db_path, db)
        previous_ledger = state.get('ledger')
        ledger_matches = ledger_marker is None or (
            isinstance(previous_ledger, dict)
            and all(previous_ledger.get(key) == ledger_marker.get(key) for key in
                    ('path', 'device', 'inode', 'size', 'mtime_ns', 'tail_sha256', 'last_run_id')))
        valid = (state.get('database_identity') == identity and state.get('call_index_id') == call_index_id
                 and ledger_matches and type(state.get('rowid')) is int and state['rowid'] <= high
                 and isinstance(state.get('anchors', {}), dict) and anchors_valid(db, state.get('anchors', {})))
        if state and not valid:
            state = {}
            reset_call_index(call_db)
        initial = 'rowid' not in state
        cursor = max(0, high - OBSERVE_INITIAL_ROWS) if initial else state['rowid']
        floor = cursor
        anchors = state.get('anchors', {})
        if initial and cursor:
            anchor = db.execute(f'SELECT rowid,turn_item_id FROM {PREFIX}turn_items WHERE rowid<=? ORDER BY rowid DESC LIMIT 1',
                                (cursor,)).fetchone()
            if anchor:
                anchors[str(anchor[0])] = anchor[1]
        refresh_after = state.get('refresh_after', 0)
        found, subjects, scanned, refreshed = [], 0, 0, 0
        refresh_done = True
        refresh_deadline = time.monotonic() + max(0, deadline - time.monotonic()) * 0.5
        while time.monotonic() < refresh_deadline:
            page = call_db.execute('SELECT source_rowid FROM known_calls WHERE source_rowid>? ORDER BY source_rowid LIMIT ?',
                                   (refresh_after, OBSERVE_BATCH_ROWS)).fetchall()
            if not page:
                refresh_after = 0
                break
            requested = [row[0] for row in page]
            try:
                _, calls, count, matched_rowids, _ = read_t3_rowid_batch(
                    db_path, cursor, high, OBSERVE_BATCH_ROWS, requested, scope_ids, deadline)
                run_owner = indexed_run_owners(call_db)
                batch = collect_t3(db_path, scope_ids, targets, calls_override=calls, deadline=deadline,
                                   run_owner_override=run_owner, include_thread_snapshot=cursor >= high) if calls else result('ok')
                if batch['state'] not in ('ok', 'unavailable'):
                    raise sqlite3.OperationalError(batch.get('reason') or 'T3 batch failed')
                delete_stale_calls(call_db, requested, matched_rowids)
                remember_calls(call_db, calls)
                found.extend(batch['observations'])
                subjects += batch['subjects_checked']
                refreshed += count
                refresh_after = requested[-1]
            except BudgetExhausted:
                refresh_done = False
                # Move the refresh cursor so one slow usage thread cannot block later rows forever.
                refresh_after = requested[0]
                break
            except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, OSError) as error:
                refresh_done = False
                for rowid in requested:
                    if time.monotonic() >= deadline:
                        break
                    try:
                        _, one_calls, count, matched, _ = read_t3_rowid_batch(
                            db_path, cursor, high, 1, [rowid], scope_ids, deadline)
                        delete_stale_calls(call_db, [rowid], matched)
                        remember_calls(call_db, one_calls)
                        run_owner = indexed_run_owners(call_db)
                        one = collect_t3(db_path, scope_ids, targets, calls_override=one_calls, deadline=deadline,
                                         run_owner_override=run_owner, include_thread_snapshot=cursor >= high) if one_calls else result('ok')
                        if one['state'] not in ('ok', 'unavailable'):
                            raise sqlite3.OperationalError(one.get('reason') or 'T3 row failed')
                        found.extend(one['observations'])
                        subjects += one['subjects_checked']
                        refreshed += count
                    except BudgetExhausted:
                        refresh_after = rowid
                        break
                    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, OSError) as row_error:
                        call_db.execute('INSERT OR REPLACE INTO quarantined_rows(source_rowid,reason) VALUES(?,?)',
                                        (rowid, short(row_error)))
                        call_db.commit()
                    refresh_after = rowid
                if refresh_after >= requested[-1]:
                    refresh_done = True
            if len(page) < OBSERVE_BATCH_ROWS:
                if refresh_done:
                    refresh_after = 0
                    break
        else:
            refresh_done = False
        while cursor < high and time.monotonic() < deadline:
            try:
                next_cursor, calls, count, matched_rowids, anchor_key = read_t3_rowid_batch(
                    db_path, cursor, high, OBSERVE_BATCH_ROWS, scope_ids=scope_ids, deadline=deadline)
                if next_cursor == cursor:
                    break
                remember_calls(call_db, calls)
                run_owner = indexed_run_owners(call_db)
                batch = collect_t3(db_path, scope_ids, targets, calls_override=calls, deadline=deadline,
                                   run_owner_override=run_owner, include_thread_snapshot=next_cursor >= high) if calls else result('ok')
                if batch['state'] not in ('ok', 'unavailable'):
                    raise sqlite3.OperationalError(batch.get('reason') or 'T3 batch failed')
                found.extend(batch['observations'])
                subjects += batch['subjects_checked']
                scanned += count
                cursor = next_cursor
                if anchor_key and cursor - max((int(value) for value in anchors), default=0) >= OBSERVE_ANCHOR_ROWS:
                    anchors[str(cursor)] = anchor_key
                    if len(anchors) > MAX_STATE_ANCHORS:
                        anchors = dict(sorted(anchors.items(), key=lambda item: int(item[0]))[-MAX_STATE_ANCHORS:])
            except BudgetExhausted:
                break
            except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, OSError) as error:
                # Retry a bad batch row by row. Quarantine only the row that still fails so later rows advance.
                if time.monotonic() >= deadline:
                    break
                single_cursor = cursor
                for _ in range(OBSERVE_BATCH_ROWS):
                    one_cursor, one_count, anchor_key = None, 0, None
                    try:
                        one_cursor, one_calls, one_count, one_ids, anchor_key = read_t3_rowid_batch(
                            db_path, single_cursor, high, 1, scope_ids=scope_ids, deadline=deadline)
                        if one_cursor == single_cursor:
                            break
                        remember_calls(call_db, one_calls)
                        run_owner = indexed_run_owners(call_db)
                        one = collect_t3(db_path, scope_ids, targets, calls_override=one_calls, deadline=deadline,
                                         run_owner_override=run_owner, include_thread_snapshot=one_cursor >= high) if one_calls else result('ok')
                        if one['state'] not in ('ok', 'unavailable'):
                            raise sqlite3.OperationalError(one.get('reason') or 'T3 row failed')
                        found.extend(one['observations'])
                        subjects += one['subjects_checked']
                    except BudgetExhausted:
                        break
                    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, OSError) as row_error:
                        bad_row = one_cursor if one_cursor is not None else next_source_rowid(db_path, single_cursor, high)
                        bad_rowid = bad_row[0] if isinstance(bad_row, tuple) else bad_row
                        if bad_rowid is None:
                            break
                        call_db.execute('INSERT OR REPLACE INTO quarantined_rows(source_rowid,reason) VALUES(?,?)',
                                        (bad_rowid, short(row_error)))
                        call_db.commit()
                        one_cursor, one_count = bad_rowid, 1
                    single_cursor = one_cursor
                    scanned += one_count
                    cursor = one_cursor
                    if anchor_key:
                        anchors[str(cursor)] = anchor_key
                    if time.monotonic() >= deadline:
                        break
                if cursor == single_cursor and time.monotonic() < deadline:
                    continue
                if time.monotonic() >= deadline:
                    break
        if cursor >= high and scanned:
            refresh_after = 0
            run_owner = indexed_run_owners(call_db)
            thread_facts, thread_subjects, refresh_after, refresh_done = collect_indexed_thread_facts(
                db_path, call_db, scope_ids, run_owner, deadline)
            found.extend(thread_facts)
        quarantined = call_db.execute('SELECT count(*) FROM quarantined_rows').fetchone()[0]
        exhausted = cursor < high or not refresh_done
        reason = 'time_budget_exhausted' if exhausted else 'rows_quarantined' if quarantined else None
        outcome = result('partial' if exhausted or quarantined else 'ok', reason, subjects, found,
                         incremental={'initial_window': f'last {OBSERVE_INITIAL_ROWS} turn_items rowids' if initial else None,
                                      'high_water_before': state.get('rowid'), 'rowid_from': floor, 'rowid_through': cursor,
                                      'snapshot_high_water': high, 'rows_scanned': scanned, 'rows_refreshed': refreshed,
                                      'refresh_cursor': refresh_after, 'quarantined_rows': quarantined,
                                      'budget_exhausted': exhausted},
                         _state={'database_identity': identity, 'call_index_id': call_index_id, 'rowid': cursor,
                                 'anchors': anchors, 'refresh_after': refresh_after, 'ledger': state.get('ledger'),
                                 'git_cursor': state.get('git_cursor'), 'destination_cursors': state.get('destination_cursors', {})})
        return outcome
    except BudgetExhausted:
        return result('partial', 'time_budget_exhausted')
    except (OSError, ValueError, sqlite3.Error) as error:
        return result('error', short(error))
    finally:
        if 'db' in locals():
            db.close()
        if 'call_db' in locals():
            call_db.close()


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
        timeout = min(timeout, max(0.01, deadline - time.monotonic()))
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


def collect_destination(entry, ids, deadline=None, start_after=None):
    name = entry['name'] if entry else None
    if entry is None:
        return result('unavailable', 'destination_not_configured')
    if 'observe_command' not in entry:
        return result('unavailable', 'no_observe_command')
    source = f'destination:{name}'
    found, reason, checked = [], None, 0
    ordered = [item for item in sorted(ids) if start_after is None or item > start_after]
    for start in range(0, len(ordered), BATCH):
        if deadline is not None and time.monotonic() >= deadline:
            return result('partial', 'time_budget_exhausted', checked, found, _cursor=start_after)
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
                start_after = request_id
            except (ValueError, TypeError, AttributeError) as error:
                reason = f'observe_command_invalid_record: {short(error)}'
                break
        if reason:
            break
    state = 'error' if reason else 'ok'
    cursor = start_after if reason or (deadline is not None and time.monotonic() >= deadline) else None
    return result('partial' if cursor else state, 'time_budget_exhausted' if cursor and not reason else reason,
                  checked, found, _cursor=cursor)


# ---- git --------------------------------------------------------------------------------------------------------------

class GitUnavailable(Exception):
    pass


class Ineligible(Exception):
    """The evidence is not something observe may read (no repo, not a root, no such commit): counted in `skipped`."""


class GitFailure(Exception):
    """git could not answer (timeout, output limit, non-zero exit, a missing local object): counted in `failed`, never healthy."""


def failure_reason(repo, deadline=None):
    """A non-zero exit in a partial clone usually means a missing object that only the promisor remote has; observe never fetches it."""
    timeout = GIT_TIMEOUT if deadline is None else max(0.01, min(GIT_TIMEOUT, deadline - time.monotonic()))
    stdout, code, failure = destination_store.run_bounded(git_argv(repo, 'config', '--get-regexp', r'^remote\..*\.promisor$'), timeout,
                                                          limit=GIT_OUTPUT_LIMIT, env=git_env())
    promisor = not failure and code == 0 and any(line.split()[-1:] == [b'true'] for line in stdout.splitlines())
    return 'git_missing_local_object' if promisor else 'git_failed'


def git(repo, *args, deadline=None):
    """Read-only git with GIT_* cleared, no optional locks, no lazy fetch, bounded time and output. Returns stdout text; raises GitFailure."""
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise BudgetExhausted
    timeout = GIT_TIMEOUT if deadline is None else max(0.01, min(GIT_TIMEOUT, deadline - time.monotonic()))
    stdout, code, failure = destination_store.run_bounded(git_argv(repo, *args), timeout, limit=GIT_OUTPUT_LIMIT, env=git_env())
    if failure == 'missing_binary':
        raise GitUnavailable('git_not_found')
    if failure:
        if deadline is not None and time.monotonic() >= deadline:
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
    if deadline is not None and deadline - time.monotonic() < COMMIT_TIMEOUT:
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


def collect_git(outcome_rows, scope_ids, window_days, now, deadline=None, start_after=None):
    closes, _ = split_rows(list(outcome_rows))
    found, checked, skipped, failed = [], 0, {}, {}
    tasks = {}
    for close in closes:
        if close['decision_id'] not in scope_ids:
            continue
        for ref in close['evidence']:
            if ref.get('type') != 'commit':
                continue
            key = json.dumps((close['close_id'], ref['repo'], ref['sha']), separators=(',', ':'))
            tasks.setdefault(key, (close, ref))
    last_key = None
    for key, (close, ref) in sorted(tasks.items()):
        if deadline is not None and time.monotonic() >= deadline:
            return result('partial', 'time_budget_exhausted', checked, found, skipped=dict(sorted(skipped.items())),
                          failed=dict(sorted(failed.items())), _cursor=last_key or start_after)
        if start_after is not None and key <= start_after:
            continue
        checked += 1
        try:
            found += scan_commit(close, ref, window_days, now, deadline)
            last_key = key
        except BudgetExhausted:
            return result('partial', 'time_budget_exhausted', checked - 1, found,
                          skipped=dict(sorted(skipped.items())), failed=dict(sorted(failed.items())), _cursor=last_key or start_after)
        except GitUnavailable as error:
            return result('unavailable', str(error), _cursor=last_key or start_after)
        except Ineligible as error:
            skipped[str(error)] = skipped.get(str(error), 0) + 1
            last_key = key
        except (GitFailure, ValueError) as error:
            reason = str(error) if isinstance(error, GitFailure) else 'git_bad_output'
            failed[reason] = failed.get(reason, 0) + 1
            last_key = key
    extra = {'skipped': dict(sorted(skipped.items())), 'failed': dict(sorted(failed.items()))}
    if not failed:
        return result('ok', None, checked, found, **extra, _cursor=None)
    reason = 'operational_failure: ' + ', '.join(f'{name}={count}' for name, count in extra['failed'].items())
    return result('partial' if checked - sum(failed.values()) - sum(skipped.values()) else 'error', reason, checked, found, **extra,
                  _cursor=None)


# ---- run --------------------------------------------------------------------------------------------------------------

def execute(args):
    """Serialize cursor/ledger updates so concurrent observers cannot lose progress."""
    budget = getattr(args, 'observe_time_budget_seconds', OBSERVE_TIME_BUDGET_SECONDS)
    if type(budget) is not int or not 1 <= budget <= 3600:
        raise ValueError('--observe-time-budget-seconds must be an integer 1-3600')
    state_path = getattr(args, 'observe_state', None) or default_observe_state()
    deadline = time.monotonic() + budget
    lock_fd = observe_state_lock(state_path, deadline)
    args._observe_deadline = deadline
    try:
        return _execute_locked(args)
    finally:
        del args._observe_deadline
        release_observe_state_lock(lock_fd)


def _execute_locked(args):
    """Collect from every source, append the new facts and one source_run row. Source failures are recorded, not raised."""
    if args.git_window_days is None:
        args.git_window_days = DEFAULT_WINDOW_DAYS
    if type(args.git_window_days) is not int or not 1 <= args.git_window_days <= 3650:
        raise ValueError('--git-window-days must be an integer 1-3650')
    since = stamp(normalize_since(args.since)) if args.since else None
    started = datetime.now(timezone.utc)
    budget = getattr(args, 'observe_time_budget_seconds', OBSERVE_TIME_BUDGET_SECONDS)
    if type(budget) is not int or not 1 <= budget <= 3600:
        raise ValueError('--observe-time-budget-seconds must be an integer 1-3600')
    deadline = getattr(args, '_observe_deadline', time.monotonic() + budget)
    budget_start = deadline - budget
    receipts = [r for r in read_receipts(args.receipts) if not since or stamp(r['recorded_at']) >= since]
    outcome_rows = read_outcomes(args.outcomes)
    entries, _ = destination_store.read_destinations(args.destinations)
    by_name = {e['name']: e for e in entries}
    scope = store.decision_sources(receipts, outcome_rows)
    collected = {}
    pending_state = None
    state_path = getattr(args, 'observe_state', None) or default_observe_state()
    try:
        previous_state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except (OSError, ValueError):
        previous_state = {}
    if not isinstance(previous_state, dict):
        previous_state = {}
    destination_cursors = previous_state.get('destination_cursors', {})
    if not isinstance(destination_cursors, dict):
        destination_cursors = {}
    t3_deadline = budget_start + budget * 0.60
    try:
        ledger_before = read_last_ledger_line(args.observations)
        t3_result = incremental_t3(args.db, scope.get('t3', set()), {r['decision_id']: r.get('target') for r in receipts},
                                   state_path, min(deadline, t3_deadline), ledger_before or {})
        pending_state = t3_result.pop('_state', None)
        collected['t3'] = t3_result
    except (OSError, ValueError, sqlite3.Error) as error:
        collected['t3'] = result('error', short(error))
    destination_sources = [(source, ids) for source, ids in sorted(scope.items()) if source.startswith('destination:')]
    destination_start = budget_start + budget * 0.60
    destination_end = budget_start + budget * 0.80
    for source_index, (source, ids) in enumerate(destination_sources):
        if source.startswith('destination:'):
            name = source.split(':', 1)[1]
            slice_deadline = min(deadline, destination_start + (destination_end - destination_start) *
                                 ((source_index + 1) / max(1, len(destination_sources))))
            try:
                collected[source] = collect_destination(by_name.get(name), ids, slice_deadline, destination_cursors.get(source))
            except (OSError, ValueError) as error:
                collected[source] = result('error', short(error))
            cursor = collected[source].pop('_cursor', None)
            if cursor is not None:
                destination_cursors[source] = cursor
            else:
                destination_cursors.pop(source, None)
    try:
        if time.monotonic() >= deadline:
            collected['git'] = result('partial', 'time_budget_exhausted', _cursor=previous_state.get('git_cursor'))
        else:
            collected['git'] = collect_git(outcome_rows, scope.get('git', set()), args.git_window_days, started,
                                           deadline, previous_state.get('git_cursor'))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        collected['git'] = result('error', short(error))
    git_cursor = collected['git'].pop('_cursor', None)
    incremental_info = collected.get('t3', {}).pop('incremental', None)
    run = store.append_run(args.observations, str(uuid.uuid4()), started.isoformat(), datetime.now(timezone.utc).isoformat(), collected)
    if pending_state is not None:
        pending_state['destination_cursors'] = destination_cursors
        pending_state['git_cursor'] = git_cursor
        pending_state['ledger'] = read_last_ledger_line(args.observations)
        save_state(state_path, pending_state)
    # Printed, never stored: what each evidenced commit's close recorded, beside the live re-check above. Old rows are not_recorded.
    closes, _ = split_rows(outcome_rows)
    evidenced = [c for c in closes if c['decision_id'] in scope.get('git', set())]
    return run | ({'incremental': incremental_info} if incremental_info else {}) | {
        'recorded_commit_verification': commit_verification_summary(evidenced)}
