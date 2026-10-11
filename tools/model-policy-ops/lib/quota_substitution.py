"""Quota-aware substitution: per-account constraint state with hysteresis, and the choice of a substitute.

The pack carries the thresholds (`routing.quota_substitution`); a pack without them turns the feature off.
An account becomes constrained when its binding pace-window projection reaches `activate_projected_pct`
and stays constrained until the projection falls below `release_projected_pct`. The memory that
hysteresis needs lives in a small private state file. `route` never uses the memory to widen a choice:
unknown or stale quota is neither constrained nor a substitution target.
"""
import fcntl
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

STATE_FILE = 'quota-substitution.json'
SCHEMA_VERSION = 1
ACTIVE_STATES = ('activated', 'held_by_hysteresis')
AUTHORIZED_BY = 'pack routing.quota_substitution (owner directive)'
REASON = ('the best pair sits on an account whose weekly quota is projected at or above the activation threshold, '
          'and an eligible pack candidate sits on an account with a known projection below it')


def default_path(receipts):
    return Path(receipts).with_name(STATE_FILE)


def read_memory(path):
    """(memory, state, issue). `memory` is {instance: {since, window}}, or None when it cannot be trusted.

    `state` is `loaded`, `missing` or `unreadable`. A symlink, a file that is not a regular file, a file that
    group or world can write, and malformed content are all `unreadable`, and the caller never rewrites them.
    """
    try:
        # O_NONBLOCK: a FIFO with no writer must not block the open; the type check below rejects it
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}, 'missing', None
    except OSError as error:
        return None, 'unreadable', error.strerror
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        return None, 'unreadable', 'not_a_regular_file'
    with os.fdopen(fd, encoding='utf-8') as stream:
        if info.st_mode & 0o022:
            return None, 'unreadable', 'group_or_world_writable'
        try:
            value = json.load(stream)
        except (ValueError, UnicodeDecodeError):
            return None, 'unreadable', 'malformed_json'
    rows = value.get('accounts') if isinstance(value, dict) else None
    if not isinstance(rows, dict) or value.get('schema_version') != SCHEMA_VERSION:
        return None, 'unreadable', 'schema_mismatch'
    for entry in rows.values():
        if not (isinstance(entry, dict) and isinstance(entry.get('since'), str) and isinstance(entry.get('window'), str)):
            return None, 'unreadable', 'schema_mismatch'
        try:
            datetime.fromisoformat(entry['since'])
        except ValueError:
            return None, 'unreadable', 'schema_mismatch'
    return rows, 'loaded', None


@contextmanager
def exclusive(path):
    """An exclusive lock on a sidecar file, so the state file can be replaced while the lock is held."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o022:
        raise OSError('state directory must not be group- or world-writable')
    fd = os.open(path.with_name(path.name + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        yield


def write_state(path, accounts):
    """Write a temporary file in the same directory, fsync, close, then replace. The mode is 0600."""
    path = Path(path)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps({'schema_version': SCHEMA_VERSION, 'accounts': accounts}, sort_keys=True, indent=1) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def decide(memory, observed, thresholds, at):
    """Apply the hysteresis rule to every observed account.

    `observed` is {instance: binding or None}; a binding has `projected_pct` and `window`, and None means the
    projection is unknown. `memory` is the stored state, or None when it cannot be trusted (decide without it).
    Returns the new memory, the account rows that appear in the receipt, and the account facts that ranking uses.
    """
    activate, release = thresholds['activate_projected_pct'], thresholds['release_projected_pct']
    accounts = dict(memory or {})
    report, constrained, known, unknown = {}, {}, {}, []
    for instance, binding in sorted(observed.items()):
        if binding is None:
            unknown.append(instance)  # no new fact: the stored state stays as it was
            prior = (memory or {}).get(instance)
            if prior:
                # still constrained until a known projection falls below release; never headroom while unknown
                row = {'projected_pct': None, 'window': prior['window'], 'state': 'held_unknown', 'since': prior['since']}
                report[instance] = constrained[instance] = row
            continue
        projected, window = binding['projected_pct'], binding['window']
        prior = (memory or {}).get(instance)
        known[instance] = projected
        if projected >= activate or (prior and projected >= release):
            state = 'activated' if projected >= activate else 'held_by_hysteresis'
            since = prior['since'] if prior else at.isoformat()
            accounts[instance] = {'since': since, 'window': window}
            row = {'projected_pct': projected, 'window': window, 'state': state, 'since': since}
            report[instance] = constrained[instance] = row
        elif prior:
            del accounts[instance]
            report[instance] = {'projected_pct': projected, 'window': window, 'state': 'released', 'since': at.isoformat()}
    return accounts, report, constrained, known, unknown


def evaluate(path, thresholds, observed, at, *, write):
    """The substitution state for this evaluation. `write` is false for a simulation: nothing is created or changed.

    The read, the decision and the write happen under one exclusive lock. When the memory is unreadable or the
    location is not safe, the decision is made without memory and nothing is written.
    """
    written = False
    issue = write_error = None
    if write:
        try:
            with exclusive(path):
                memory, state, issue = read_memory(path)
                outcome = decide(memory, observed, thresholds, at)
                if state != 'unreadable' and (state == 'missing' or outcome[0] != memory):
                    try:
                        write_state(path, outcome[0])
                        written = True
                    except OSError as error:
                        write_error = error.strerror or type(error).__name__
        except OSError as error:
            memory, state, issue = None, 'unreadable', error.strerror or str(error)
            outcome = decide(None, observed, thresholds, at)
    else:
        parent = Path(path).parent
        if parent.exists() and parent.stat().st_mode & 0o022:
            memory, state, issue = None, 'unreadable', 'state directory must not be group- or world-writable'
        else:
            memory, state, issue = read_memory(path)
        outcome = decide(memory, observed, thresholds, at)
    _, report, constrained, known, unknown = outcome
    return {'state': state, 'issue': issue, 'written': written, 'write_error': write_error, 'report': report,
            'constrained': constrained, 'known': known, 'unknown': unknown}


def substitute(survivors, sub):
    """The pair that replaces the best one, or None. `survivors` are the rows that passed every filter.

    A substitute sits on an account that is not constrained and whose projection is known and below the
    activation threshold. Order: pack candidate order, then availability (healthy, then unknown, then demoted for
    recent rate-limit or transport failures), then the account's `substitution_rank` (lower first; none last), then
    the lower projection, then the instance ID.
    """
    state, ranks, limit = sub['state'], sub['ranks'], sub['thresholds']['activate_projected_pct']
    known = state['known']
    eligible = [row for row in survivors
                if row['instance'] in known and row['instance'] not in state['constrained'] and known[row['instance']] < limit]

    def key(row):
        rank = ranks.get(row['instance'])
        availability = (row.get('rank') or {}).get('availability', 1)
        return (row['candidate'], availability, float('inf') if rank is None else rank, known[row['instance']], row['instance'])

    return min(eligible, key=key) if eligible else None


def summary(sub, *, move=None):
    """The `routing.quota_substitution` block. `move` is {from, to} when the best pair was replaced."""
    state = sub['state']
    block = {'active_accounts': state['report'], 'thresholds': sub['thresholds'], 'substituted': bool(move),
             'state': state['state'], 'state_written': state['written'], 'unknown_accounts': state['unknown']}
    if state['issue']:
        block['state_issue'] = state['issue']
    if state['write_error']:
        block['state_write_error'] = state['write_error']
    if move:
        block |= {'from': move['from'], 'to': move['to'], 'reason': REASON, 'authorized_by': AUTHORIZED_BY}
    return block
