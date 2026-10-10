"""Observation ledger: facts that other systems recorded about decisions.

An observation never creates, changes or implies a close, an outcome label or a quality verdict.
Rows are append-only. `observation_id` is the sha256 of (source, subject, fact), so a repeat run appends nothing.
"""
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime

from decision_receipts import append_row, locked_private_rows, numbered_rows, validate_id
from outcomes import split_rows

SCHEMA = 1
SOURCE_STATES = ('ok', 'unavailable', 'error')
SOURCE = re.compile(r't3|git|destination:[^\s:][^\s]{0,63}')
LABELS = {
    'completed': 'completed ≠ accepted',
    'consumed': 'consumed ≠ correct',
    'tokens': 'tokens ≠ subscription cost',
    'served_model': 'requested configuration only; the served model is unattested',
    'git': 'candidate follow-up signals, not fixes, rework or outcomes',
    'scope': 'facts only; an observation never creates, changes or implies a close, outcome label or quality verdict',
}
OBSERVATION_KEYS = {'schema_version', 'kind', 'observation_id', 'source', 'subject_id', 'decision_id', 'fact_type', 'fact',
                    'source_ids', 'source_timestamps', 'unknown', 'scope', 'observed_at', 'run_id'}
SOURCE_RUN_KEYS = {'schema_version', 'kind', 'run_id', 'started_at', 'finished_at', 'sources', 'labels'}
SOURCE_ENTRY_KEYS = {'state', 'reason', 'subjects_checked', 'new_observations'}


def observation_id(source, subject_id, fact):
    canonical = json.dumps([source, subject_id, fact], sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def make_observation(source, subject_id, decision_id, fact_type, fact, *, source_ids=None, source_timestamps=None,
                     unknown=None, scope=None):
    """One candidate row. `fact` is the identity: whatever is in it, a change to it is a new observation."""
    fact = {'type': fact_type} | fact
    return {'schema_version': SCHEMA, 'kind': 'observation', 'observation_id': observation_id(source, subject_id, fact),
            'source': source, 'subject_id': subject_id, 'decision_id': decision_id, 'fact_type': fact_type, 'fact': fact,
            'source_ids': source_ids or {}, 'source_timestamps': source_timestamps or {}, 'unknown': unknown or {},
            'scope': scope}


def check_row(row):
    """Raise ValueError with a reason when one row is not what this CLI writes."""
    if not isinstance(row, dict):
        raise ValueError('row is not an object')
    if row.get('schema_version') != SCHEMA:
        raise ValueError(f'schema_version must be {SCHEMA}')
    kind = row.get('kind')
    if kind not in ('observation', 'source_run'):
        raise ValueError('kind must be observation or source_run')
    missing = sorted((OBSERVATION_KEYS if kind == 'observation' else SOURCE_RUN_KEYS) - set(row))
    if missing:
        raise ValueError(f'{kind} row missing {", ".join(missing)}')
    for name in ('observed_at',) if kind == 'observation' else ('started_at', 'finished_at'):
        try:
            datetime.fromisoformat(row[name])
        except (TypeError, ValueError):
            raise ValueError(f'{name} must be an ISO timestamp') from None
    if kind == 'source_run':
        sources = row['sources']
        if not isinstance(sources, dict) or not all(
                SOURCE.fullmatch(name) and isinstance(entry, dict) and SOURCE_ENTRY_KEYS <= set(entry)
                and entry['state'] in SOURCE_STATES for name, entry in sources.items()):
            raise ValueError('sources must map each source to a state in ok, unavailable or error')
        return
    if not isinstance(row['source'], str) or not SOURCE.fullmatch(row['source']):
        raise ValueError('source must be t3, git or destination:NAME')
    if not isinstance(row['fact'], dict) or row['fact'].get('type') != row['fact_type']:
        raise ValueError('fact must be an object whose type is fact_type')
    if row['observation_id'] != observation_id(row['source'], row['subject_id'], row['fact']):
        raise ValueError('observation_id does not match source, subject_id and fact')
    if row['decision_id'] is not None:
        validate_id(row['decision_id'])


def validate_observation_rows(numbered, name='observations.jsonl'):
    """Fail closed on the first bad row or repeated ID. Nothing is skipped or repaired."""
    seen = {}
    for number, row in numbered:
        try:
            check_row(row)
        except ValueError as error:
            raise ValueError(f'{name} line {number}: {error}') from None
        own = row['observation_id'] if row['kind'] == 'observation' else row['run_id']
        key = (row['kind'], own)
        if key in seen:
            raise ValueError(f'{name} line {number}: id {own!r} already used on line {seen[key]}')
        seen[key] = number


def read_observations(path):
    """(rows, state, reason). A missing file is `absent`; a bad file is `unreadable` with the reason. Never raises."""
    try:
        with open(path) as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            numbered = numbered_rows(stream, os.path.basename(path))
        validate_observation_rows(numbered, os.path.basename(path))
    except FileNotFoundError:
        return [], 'absent', None
    except (ValueError, OSError) as error:
        return [], 'unreadable', str(error)
    return [row for _, row in numbered], 'observed', None


def append_run(path, run_id, started_at, finished_at, collected):
    """Append the new observations of every source, then ONE source_run row, under the exclusive lock.

    `collected` maps a source to {state, reason, subjects_checked, observations, ...extra}. Returns the source_run row.
    Dedupe happens here, under the lock, so concurrent runs append each fact once.
    """
    with locked_private_rows(path, validate_observation_rows) as (stream, rows):
        known = {r['observation_id'] for r in rows if r['kind'] == 'observation'}
        if any(r['kind'] == 'source_run' and r['run_id'] == run_id for r in rows):
            raise ValueError(f'run id {run_id!r} already recorded')
        sources = {}
        for source, result in sorted(collected.items()):
            fresh = []
            for observation in result['observations']:
                if observation['observation_id'] not in known:
                    known.add(observation['observation_id'])
                    fresh.append(observation)
            for observation in fresh:
                append_row(stream, observation | {'observed_at': finished_at, 'run_id': run_id})
            sources[source] = {k: v for k, v in result.items() if k != 'observations'} | {'new_observations': len(fresh)}
        record = {'schema_version': SCHEMA, 'kind': 'source_run', 'run_id': run_id, 'started_at': started_at,
                  'finished_at': finished_at, 'sources': sources, 'labels': LABELS}
        append_row(stream, record)
    return record


def decision_sources(receipts, outcome_rows):
    """{source: set of decision IDs in scope}. The denominators of the coverage numbers."""
    scope = {}
    for receipt in receipts:
        chosen = ((receipt.get('routing') or {}).get('destination') or {}).get('chosen') or 'model'
        scope.setdefault('t3' if chosen == 'model' else f'destination:{chosen}', set()).add(receipt['decision_id'])
    ids = {r['decision_id'] for r in receipts}
    closes, _ = split_rows(list(outcome_rows))
    commits = {c['decision_id'] for c in closes if c['decision_id'] in ids and any(e.get('type') == 'commit' for e in c['evidence'])}
    if commits:
        scope['git'] = commits
    return scope


def tally(values):
    result = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def revert_candidates(rows, decision_ids):
    return sorted({r['decision_id'] for r in rows if r['kind'] == 'observation' and r['fact_type'] == 'explicit_revert_reference'
                   and r['decision_id'] in decision_ids})


def section(state, reason, rows, receipts, outcome_rows):
    """The `observations` section of audit and calibrate. Reads nothing; adds no key to any other section."""
    base = {'state': state, 'reason': reason, 'labels': LABELS, 'file': 'observations.jsonl'}
    if state != 'observed':
        return base | {'coverage': {}, 'coverage_meaning': None, 'latest_source_runs': {}, 'fact_type_counts': {}}
    scope = decision_sources(receipts, outcome_rows)
    seen = {}
    for row in rows:
        if row['kind'] == 'observation' and row['decision_id']:
            seen.setdefault(row['source'], set()).add(row['decision_id'])
    coverage = {source: {'decisions_with_observation': len(seen.get(source, set()) & ids), 'decisions_in_scope': len(ids)}
                for source, ids in sorted(scope.items())}
    latest = {}
    for row in rows:
        if row['kind'] == 'source_run':
            for source, entry in row['sources'].items():
                latest[source] = {'run_id': row['run_id'], 'finished_at': row['finished_at'], 'state': entry['state'],
                                  'reason': entry['reason']}
    ids = set().union(*scope.values()) if scope else set()
    return base | {'coverage': coverage,
                   'coverage_meaning': 'per source: decisions with at least one observation / decisions in scope for the source',
                   'latest_source_runs': dict(sorted(latest.items())),
                   'fact_type_counts': tally(r['fact_type'] for r in rows if r['kind'] == 'observation' and r['decision_id'] in ids),
                   'source_runs': sum(r['kind'] == 'source_run' for r in rows)}
