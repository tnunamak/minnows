"""Allowance pools: free or prepaid arms whose cap resets on a known schedule.

A free route may declare an `allowance` (see `normal_free_routes`): the pool it draws on, how often the pool
resets, and the limit message the provider returns. When a launch fails with that message, the pool is
exhausted until its next known reset. This module keeps that fact in a small local state file, so it
survives restarts, and answers one question for `route`: is this pool exhausted now?

It reads no network and no T3 data. `policy_decisions` passes in the limit events that the T3 reader found.
The state file holds one row per pool: `{until, recorded_at, source, message}`. A row whose `until` is not
in the future is ignored, so the reset needs no cleanup job.
"""
import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_NAME = 'allowance-exhaustion.json'
SCHEMA_VERSION = 1
PERIODS = ('daily_utc', 'weekly')
SOURCES = ('t3_failure', 'caller')
UNITS = {'day': timedelta(days=1), 'hour': timedelta(hours=1), 'minute': timedelta(minutes=1)}
RESET_IN = re.compile(r'(\d+)\s*(day|hour|minute)s?', re.I)
RESETS_PHRASE = re.compile(r'resets?\s+in\b([^.\n]*)', re.I)


def default_path(receipts):
    return Path(receipts).with_name(STATE_NAME)


def pool_key(route):
    """One key per provider, account and pool. Two models of one pool share it; two pools of one account do not."""
    return f"{route['provider']}/{route['account']}/{route['allowance']['pool']}"


def pools(routes):
    """{key: {route, models}} for every declared pool. Routes of one pool share its allowance and its exhaustion."""
    found = {}
    for route in routes or []:
        if isinstance(route.get('allowance'), dict):
            found.setdefault(pool_key(route), {'route': route, 'models': set()})['models'].add(route['model'])
    return found


TRANSIENT_LIMIT = re.compile(r'rate[- ]?limit|too many requests|\b429\b')


def matches_limit(allowance, message):
    """True when a failure message says the pool's allowance is spent.

    Any message that mentions a limit counts, whatever its wording, unless it names a short-lived rate limit
    (that one clears in seconds and must not close a pool until its reset). The pool's declared `limit_markers`
    also count, so a provider whose cap message has no "limit" in it can still be declared.
    """
    text = (message or '').lower()
    if any(marker in text for marker in allowance['limit_markers']):
        return True
    return 'limit' in text and not TRANSIENT_LIMIT.search(text)


def next_reset(allowance, at, message=None):
    """The next known reset after `at`: the provider's own "resets in N days/hours" when the message has one,
    else the declared schedule (next 00:00 UTC, or the next weekly boundary from the declared anchor)."""
    phrase = RESETS_PHRASE.search(message or '')
    parts = RESET_IN.findall(phrase.group(1)) if phrase else []
    if parts:
        return at + sum((int(count) * UNITS[unit.lower()] for count, unit in parts), timedelta())
    if allowance['period'] == 'daily_utc':
        midnight = at.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight + timedelta(days=1)
    anchor = datetime.fromisoformat(allowance['anchor'].replace('Z', '+00:00'))
    week = timedelta(days=7)
    periods = int((at - anchor) // week) + 1
    return anchor + max(periods, 0) * week


def _parse(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def read_state(path):
    """The stored rows. A missing file is no exhaustion. A malformed file is an error: silence would hide a dead pool."""
    try:
        value = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError(f'allowance state {path} is not valid JSON') from error
    rows = value.get('pools') if isinstance(value, dict) else None
    if not isinstance(rows, dict) or value.get('schema_version') != SCHEMA_VERSION:
        raise ValueError(f'allowance state {path} needs schema_version {SCHEMA_VERSION} and a "pools" object')
    for key, row in rows.items():
        if not isinstance(row, dict) or _parse(row.get('until')) is None:
            raise ValueError(f'allowance state {path}: pool {key} needs a zoned "until" time')
    return rows


def active(rows, key, now):
    """The row while the pool is exhausted at `now`, else None. The reset instant itself is available again."""
    row = rows.get(key)
    return row if row and _parse(row['until']) > now else None


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path.with_name(path.name + '.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_state(path, rows):
    path = Path(path)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps({'schema_version': SCHEMA_VERSION, 'pools': rows}, indent=1, sort_keys=True) + '\n')
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def merge(rows, key, until, recorded_at, source, message):
    """Return the rows with this exhaustion added. A later known reset wins; an earlier one never shortens it."""
    existing = rows.get(key)
    if existing and _parse(existing['until']) >= until:
        return rows
    excerpt = ' '.join((message or '').split())[:200] or None
    return rows | {key: {'until': until.astimezone(timezone.utc).isoformat(), 'recorded_at': recorded_at.astimezone(timezone.utc).isoformat(),
                         'source': source, 'message': excerpt}}


def from_events(rows, routes, events, now):
    """The rows plus the exhaustion that limit events imply. Pure: `events` are {instance, model, at, message}.

    An event matches a pool when its instance is the pool's account, its message has one of the pool's limit
    markers, and its model is a model of the pool. An event with no model counts only when the account has a
    single pool, because a guess would exhaust a pool that still has allowance. The reset is computed from the event's
    time, so a limit seen last night expires at the right moment today. Expired results are not stored.
    """
    declared = pools(routes)
    for event in sorted(events, key=lambda e: e['at']):
        at = _parse(event['at'])
        if at is None:
            continue
        for key, found in declared.items():
            route = found['route']
            if route['account'] != event['instance'] or not matches_limit(route['allowance'], event['message']):
                continue
            if event.get('model') is None:
                if sum(1 for other in declared.values() if other['route']['account'] == route['account']) != 1:
                    continue
            elif event['model'] not in found['models']:
                continue
            until = next_reset(route['allowance'], at, event['message'])
            if until > now:
                rows = merge(rows, key, until, at, 't3_failure', event['message'])
    return rows


def record(path, routes, *, account, pool, message, at, until=None, source='caller'):
    """Record a limit that the caller saw. Writes the state file under its lock and returns the stored row."""
    key = next((k for k, found in pools(routes).items()
                if found['route']['account'] == account and found['route']['allowance']['pool'] == pool), None)
    if key is None:
        raise ValueError(f'no declared allowance pool {pool} on account {account}')
    route = pools(routes)[key]['route']
    when = until or next_reset(route['allowance'], at, message)
    if when <= at:
        raise ValueError('the reset time must be later than now')
    with locked(path):
        rows = merge(read_state(path), key, when, at, source, message)
        write_state(path, rows)
    return key, rows[key]


def clear(path, routes, *, account, pool):
    """Forget an exhaustion, for example after the owner adds allowance. Returns whether a row was removed."""
    keys = [k for k, found in pools(routes).items()
            if found['route']['account'] == account and found['route']['allowance']['pool'] == pool]
    if not keys:
        raise ValueError(f'no declared allowance pool {pool} on account {account}')
    with locked(path):
        rows = read_state(path)
        kept = {k: v for k, v in rows.items() if k not in keys}
        if len(kept) != len(rows):
            write_state(path, kept)
        return len(kept) != len(rows)


def sync(path, routes, events, now, *, write):
    """The exhaustion rows after folding in `events`. With `write`, new rows are stored under the lock; without, only returned.

    The common case, no new limit event, reads the file and takes no lock.
    """
    if not pools(routes):
        return {}
    rows = read_state(path)
    updated = from_events(rows, routes, events, now)
    if write and updated != rows:
        with locked(path):
            updated = from_events(read_state(path), routes, events, now)
            write_state(path, updated)
    return updated


def status(routes, rows, now):
    """One report row per declared pool: `exhausted` with its reset, else `available`."""
    report = []
    for key, found in pools(routes).items():
        route, hit = found['route'], active(rows, key, now)
        report.append({'pool': key, 'provider': route['provider'], 'account': route['account'], 'name': route['allowance']['pool'],
                       'models': sorted(found['models']), 'period': route['allowance']['period'],
                       'state': 'exhausted' if hit else 'available',
                       'exhausted_until': hit['until'] if hit else None, 'source': hit['source'] if hit else None})
    return report
