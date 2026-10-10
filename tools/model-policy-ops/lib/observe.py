"""`observe`: record facts that other systems already hold about recorded decisions. Reads only; never judges.

Each source collects candidate observations, then `observations.append_run` dedupes and appends under the lock.
A source that fails is recorded as `unavailable` or `error` in the source_run row, so a failure is never zero events.
"""
import json
import os
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import destinations as destination_store
import observations as store
from decision_receipts import read_receipts
from delegation_audit import PREFIX, T3_STATUSES, bounded, effort, normalize_since, read_db, stamp
from outcomes import commit_state, git_argv, git_env, read_outcomes, split_rows
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


def usage_observations(source, run_id, decision_id, usage):
    """One row per reported turn, labelled as reported. Never a sum: scopes can overlap."""
    turns = [(a['attempt_id'], a['attempt_ordinal'], t) for a in usage['attempts'] for t in a['turns']]
    if not turns:
        return [store.make_observation(source, run_id, decision_id, 't3_usage_unavailable',
                                       {'state': usage['state'], 'reason': usage['reason']}, source_ids={'run_id': run_id})]
    return [store.make_observation(
        source, run_id, decision_id, 't3_turn_usage',
        {'attempt_id': attempt, 'attempt_ordinal': ordinal, 'label': usage['label'], 'sums': usage['sums']} | turn,
        source_ids={'run_id': run_id, 'provider_turn_id': turn['provider_turn_id']},
        unknown={'timestamps': 'not_projected_by_the_usage_reader'} | (
            {} if turn['usage_status'] in ('complete', 'partial') else {'tokens': f'usage_status_{turn["usage_status"]}'}))
        for attempt, ordinal, turn in turns]


def collect_t3(db_path, scope_ids, targets=None):
    if not db_path:
        return result('unavailable', 'no_db_supplied')
    if not Path(db_path).exists():
        return result('unavailable', 'database_missing')
    try:
        calls, _ = read_db(db_path, None, None)
    except ValueError as error:
        return result('error', short(error))
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
        for call in calls:
            decision = call['decision_id']
            stamps, unknown = timestamps(started_at=call['timestamp'])
            call_status, why = status_of(call['status'])
            found.append(store.make_observation(
                't3', call['call_id'], decision, 't3_delegate_call',
                {'call_status': call_status, 'child_run_id': call['child_run_id']},
                source_ids={'call_id': call['call_id'], 'thread_id': call['thread_id']}, source_timestamps=stamps,
                unknown=unknown | ({} if call['child_run_id'] else {'child_run_id': 'call_started_no_child_run'})
                | ({'call_status': why} if why else {})))
            if call['child_run_id'] and call['child_run_id'] not in runs:
                runs[call['child_run_id']] = (decision, call)
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
                found.append(store.make_observation(
                    't3', run_id, decision, 't3_attempt_status',
                    {'attempt_id': attempt_id, 'attempt_ordinal': ordinal, 'status': attempt_status},
                    source_ids=ids | {'attempt_id': attempt_id}, unknown={'timestamps': 'attempt_rows_carry_none'} | ({'status': why} if why else {})))
            for item_id, updated_at, item_started, native_class, message in failures:
                if str(native_class).lower() in CANCELLATIONS:
                    continue
                stamps, unknown = timestamps(at=item_started or updated_at)
                found.append(store.make_observation(
                    't3', run_id, decision, 't3_terminal_failure',
                    {'turn_item_id': item_id, 'failure_class': classify_failure(message)},
                    source_ids=ids | {'turn_item_id': item_id}, source_timestamps=stamps, unknown=unknown))
            found += usage_observations('t3', run_id, decision, call['observed_child_usage'])
        db.commit()
    except sqlite3.Error as error:
        return result('error', short(error))
    finally:
        db.close()
    return result('ok', None, len(runs) + sum(c['child_run_id'] is None for c in calls), found)


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


def ask_destination(entry, ids):
    """Run the observe command for one batch. Returns (requests dict, None) or (None, reason)."""
    timeout = min(entry['timeout_seconds'], OBSERVE_TIMEOUT_CAP)
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


def collect_destination(entry, ids):
    name = entry['name'] if entry else None
    if entry is None:
        return result('unavailable', 'destination_not_configured')
    if 'observe_command' not in entry:
        return result('unavailable', 'no_observe_command')
    source = f'destination:{name}'
    found, reason, checked = [], None, 0
    ordered = sorted(ids)
    for start in range(0, len(ordered), BATCH):
        batch = ordered[start:start + BATCH]
        requests, reason = ask_destination(entry, batch)
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


STOPS = ('git_timeout', 'git_output_too_large', 'git_os_error')


def failure_reason(repo):
    """A non-zero exit in a partial clone usually means a missing object that only the promisor remote has; observe never fetches it."""
    stdout, code, failure = destination_store.run_bounded(git_argv(repo, 'config', '--get-regexp', r'^remote\..*\.promisor$'), GIT_TIMEOUT,
                                                          limit=GIT_OUTPUT_LIMIT, env=git_env())
    promisor = not failure and code == 0 and any(line.split()[-1:] == [b'true'] for line in stdout.splitlines())
    return 'git_missing_local_object' if promisor else 'git_failed'


def git(repo, *args):
    """Read-only git with GIT_* cleared, no optional locks, no lazy fetch, bounded time and output. Returns stdout text; raises GitFailure."""
    stdout, code, failure = destination_store.run_bounded(git_argv(repo, *args), GIT_TIMEOUT, limit=GIT_OUTPUT_LIMIT, env=git_env())
    if failure == 'missing_binary':
        raise GitUnavailable('git_not_found')
    if failure:
        raise GitFailure(f'git_{failure}')
    if code != 0:
        raise GitFailure(failure_reason(repo))
    return stdout.decode('utf-8', 'replace')


def is_repo_root(repo):
    """The path is a repository itself, not a directory inside another one: no other repo is ever scanned."""
    try:
        top = git(repo, 'rev-parse', '--show-toplevel').strip()
        return os.path.realpath(top) == os.path.realpath(repo)
    except GitFailure as error:
        if str(error) in STOPS:
            raise
        try:
            return git(repo, 'rev-parse', '--git-dir').strip() == '.'  # a bare repository
        except GitFailure as inner:
            if str(inner) in STOPS:
                raise
            return False


def touched_files(repo, sha):
    names = [n for n in git(repo, 'diff-tree', '-r', '--no-renames', '--no-commit-id', '--name-only', '--root', sha).splitlines() if n]
    return set(names[:GIT_MAX_FILES]), len(names) > GIT_MAX_FILES


def later_commits(repo, sha, start, end):
    """[(sha, committed_at, set of files)] for commits not reachable from `sha`, committed within [start, end]."""
    text = git(repo, 'log', '--all', f'^{sha}', f'--after={start.isoformat()}', f'--before={end.isoformat()}', '--no-renames',
               f'--max-count={GIT_MAX_COMMITS}', '--name-only', '--format=%x01%H %cI', '--')
    commits = []
    for block in text.split('\x01')[1:]:
        head, _, names = block.partition('\n')
        commit, _, when = head.partition(' ')
        commits.append((commit, when.strip(), {n for n in names.splitlines() if n}))
    return commits, len(commits) >= GIT_MAX_COMMITS


def reverting_commits(repo, sha, start, end):
    """Later commits whose message says 'This reverts commit <sha>'. The message is matched by git and never read."""
    text = git(repo, 'log', '--all', f'^{sha}', f'--after={start.isoformat()}', f'--before={end.isoformat()}', '-F',
               f'--grep=This reverts commit {sha}', f'--max-count={MAX_REVERTS}', '--format=%H %cI', '--')
    return [tuple(line.split(' ', 1)) for line in text.splitlines() if line.strip()]


def scan_commit(close, ref, window_days, now):
    """Candidate follow-up signals for one evidenced commit of one close. Raises Ineligible (skip) or GitFailure (operational)."""
    repo, sha = ref['repo'], ref['sha']
    if not (os.path.isabs(repo) and SHA1.fullmatch(sha) and os.path.isdir(repo)):
        raise Ineligible('repo_missing')
    if not is_repo_root(repo):
        raise Ineligible('not_a_repository_root')
    state = commit_state(repo, sha)
    if state != 'exists':
        if state == 'missing':
            raise Ineligible('sha_missing')
        raise GitFailure('git_timeout' if state == 'timeout' else 'git_failed')
    try:
        committed = stamp(git(repo, 'log', '-1', '--format=%cI', sha).strip())
    except ValueError:
        raise GitFailure('git_bad_output') from None
    closed = stamp(close['recorded_at'])
    end = min(now, closed + timedelta(days=window_days))
    files, truncated = touched_files(repo, sha)
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
        commits, capped = later_commits(repo, sha, committed, end)
        scope = scope | {'commits_capped': capped}
        for commit, when, names in commits:
            overlap = len(files & names)
            if commit != sha and overlap and stamp(when) >= committed:
                found.append(store.make_observation(
                    'git', subject, decision, 'same_file_later_commit',
                    {'sha': commit, 'committed_at': stamp(when).isoformat(), 'overlapping_file_count': overlap},
                    source_ids=ids | {'later_commit': commit}, source_timestamps={'committed_at': stamp(when).isoformat()}, scope=scope))
        for commit, when in reverting_commits(repo, sha, committed, end):
            found.append(store.make_observation(
                'git', subject, decision, 'explicit_revert_reference',
                {'explicit_revert_reference': True, 'evidenced_sha': sha, 'reverting_sha': commit},
                source_ids=ids | {'later_commit': commit}, source_timestamps={'committed_at': stamp(when).isoformat()}, scope=scope))
    return found


def collect_git(outcome_rows, scope_ids, window_days, now):
    closes, _ = split_rows(list(outcome_rows))
    found, checked, skipped, failed = [], 0, {}, {}
    seen = set()
    for close in closes:
        if close['decision_id'] not in scope_ids:
            continue
        for ref in close['evidence']:
            if ref.get('type') != 'commit' or (close['close_id'], ref['repo'], ref['sha']) in seen:
                continue
            seen.add((close['close_id'], ref['repo'], ref['sha']))
            checked += 1
            try:
                found += scan_commit(close, ref, window_days, now)
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
    receipts = [r for r in read_receipts(args.receipts) if not since or stamp(r['recorded_at']) >= since]
    outcome_rows = read_outcomes(args.outcomes)
    entries, _ = destination_store.read_destinations(args.destinations)
    by_name = {e['name']: e for e in entries}
    scope = store.decision_sources(receipts, outcome_rows)
    collected = {}
    try:
        collected['t3'] = collect_t3(args.db, scope.get('t3', set()), {r['decision_id']: r.get('target') for r in receipts})
    except (OSError, ValueError, sqlite3.Error) as error:
        collected['t3'] = result('error', short(error))
    for source, ids in sorted(scope.items()):
        if source.startswith('destination:'):
            name = source.split(':', 1)[1]
            try:
                collected[source] = collect_destination(by_name.get(name), ids)
            except (OSError, ValueError) as error:
                collected[source] = result('error', short(error))
    try:
        collected['git'] = collect_git(outcome_rows, scope.get('git', set()), args.git_window_days, started)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        collected['git'] = result('error', short(error))
    return store.append_run(args.observations, str(uuid.uuid4()), started.isoformat(), datetime.now(timezone.utc).isoformat(), collected)
