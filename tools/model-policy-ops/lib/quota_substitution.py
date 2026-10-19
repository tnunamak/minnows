"""Quota-aware substitution: per-account constraint state with hysteresis, and the choice of a substitute.

The pack carries the thresholds (`routing.quota_substitution`); a pack without them turns the feature off.
An account becomes constrained when its binding pace-window projection reaches `activate_projected_pct`
and stays constrained until the projection falls below `release_projected_pct`. The memory that
hysteresis needs lives in a small private state file. `route` never uses the memory to widen a choice:
unknown or stale quota is neither constrained nor a substitution target.

A constraint belongs to one quota window: the binding window's name and its `resets_at`. It never crosses a
reset. When the observed window differs from the stored one, the stored constraint is dropped and the new window
starts from the activation threshold. When quota is unknown and the stored window's reset time has passed, the
constraint ends with its window; the account is then unknown, not headroom. A row without a usable `resets_at`
(stored before window identity existed, or observed from a source time without a zone) keeps the old behavior: a
different window name still ends it, otherwise hysteresis applies and unknown quota holds it until a known reading.
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
# Same window when the name matches and the reset times are this close. Reset times in the receipt ledger move by at
# most 1.2 s within one window (clock jitter in the source); consecutive windows of one account differ by 5 h or more.
SAME_WINDOW_SECONDS = 60
REASON = ('the best pair sits on an account whose weekly quota is projected at or above the activation threshold, '
          'and an eligible pack candidate sits on an account with a known projection below it')


def default_path(receipts):
    return Path(receipts).with_name(STATE_FILE)


def parse_time(value):
    """An aware datetime, or None when the value is not an ISO time with a zone."""
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def same_window(prior, binding):
    """False for a different window name; for the same name, True or False by reset time, or None when either reset
    time is missing or unusable (a row stored before window identity, or a source time without a zone)."""
    if prior['window'] != binding['window']:
        return False
    stored, observed = parse_time(prior.get('resets_at')), parse_time(binding.get('resets_at'))
    if stored is None or observed is None:
        return None
    return abs((stored - observed).total_seconds()) <= SAME_WINDOW_SECONDS


def read_memory(path):
    """(memory, state, issue). `memory` is {instance: {since, window, resets_at?}}, or None when it cannot be trusted.

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
        if 'resets_at' in entry and parse_time(entry['resets_at']) is None:
            return None, 'unreadable', 'schema_mismatch'  # the writer stores only zoned ISO times
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

    `observed` is {instance: binding or None}; a binding has `projected_pct`, `window`, `resets_at` and `utilization`,
    and None means the projection is unknown. `memory` is the stored state, or None when it cannot be trusted (decide
    without it). Returns the new memory, the account rows that appear in the receipt, and the account facts that
    ranking uses. `utilization` is observed use and `projected_pct` is the source's forecast; rows carry both.
    """
    accounts = dict(memory or {})
    report, constrained, known, unknown = {}, {}, {}, []
    for instance, binding in sorted(observed.items()):
        prior = (memory or {}).get(instance)
        if binding is None:
            unknown.append(instance)  # no new fact about the current window
            if not prior:
                continue
            ended = parse_time(prior.get('resets_at'))
            if ended is not None and ended <= at:
                # the stored constraint was about a window that has reset; unknown is not headroom either
                del accounts[instance]
                report[instance] = {'projected_pct': None, 'utilization': None, 'window': prior['window'],
                                    'resets_at': prior['resets_at'], 'state': 'ended_with_window', 'since': at.isoformat()}
                continue
            # still constrained until a known projection falls below release; never headroom while unknown
            row = {'projected_pct': None, 'utilization': None, 'window': prior['window'], 'resets_at': prior.get('resets_at'),
                   'state': 'held_unknown', 'since': prior['since']}
            report[instance] = constrained[instance] = row
            continue
        projected, window = binding['projected_pct'], binding['window']
        # with forecast bands, each account's own band for its days left gives the limits; else the pack thresholds
        band = binding.get('band')
        activate, release = (band['high'], band['low']) if band else (thresholds['activate_projected_pct'], thresholds['release_projected_pct'])
        facts = {'projected_pct': projected, 'utilization': binding.get('utilization'), 'window': window,
                 'resets_at': binding.get('resets_at')}
        reset_from = None
        stored_reset = parse_time(prior.get('resets_at')) if prior else None
        if prior and (same_window(prior, binding) is False
                      or (same_window(prior, binding) is None and stored_reset is not None and stored_reset <= at)):
            # the constraint ended with its window (shown by the reading, or by the stored reset time having passed)
            reset_from, prior = prior, None
        known[instance] = projected
        if projected >= activate or (prior and projected >= release):
            state = 'activated' if projected >= activate else 'held_by_hysteresis'
            since = prior['since'] if prior else at.isoformat()
            # keep the stored reset time inside one window, so source jitter does not rewrite the file on every call;
            # store only a zoned time, so an unusable source time leaves the row without identity instead of a bad one
            observed_reset = facts['resets_at'] if parse_time(facts['resets_at']) else None
            resets_at = prior.get('resets_at') if prior and prior.get('resets_at') else observed_reset
            accounts[instance] = {'since': since, 'window': window} | ({'resets_at': resets_at} if resets_at else {})
            row = facts | {'state': state, 'since': since}
            report[instance] = constrained[instance] = row
        elif prior or reset_from:
            del accounts[instance]
            report[instance] = facts | {'state': 'released', 'since': at.isoformat()}
        else:
            continue
        if reset_from:
            report[instance]['previous_window'] = {'window': reset_from['window'], 'resets_at': reset_from.get('resets_at')}
        elif prior and same_window(prior, binding) is None:
            # hysteresis still applies: without identity, a reset cannot be shown
            report[instance]['previous_window'] = 'not_recorded' if not prior.get('resets_at') else 'not_comparable'
    return accounts, report, constrained, known, unknown


def evaluate(path, thresholds, observed, at, *, write, stack=None):
    """Evaluate in memory. A real route holds the sidecar lock until its caller's stack closes.

    `write=False` is a read-only simulation. `write=True` only prepares a transition;
    the caller must commit it after validation and, when recording, receipt acceptance.
    """
    issue = None
    try:
        if write:
            stack.enter_context(exclusive(path))
        try:
            parent_mode = Path(path).parent.stat().st_mode
        except FileNotFoundError:
            parent_mode = None  # read-only evaluation with no state directory
        if parent_mode is not None and parent_mode & 0o022:
            memory, state, issue = None, 'unreadable', 'state directory must not be group- or world-writable'
        else:
            memory, state, issue = read_memory(path)
    except OSError as error:
        memory, state, issue = None, 'unreadable', error.strerror or str(error)
    accounts, report, constrained, known, unknown = decide(memory, observed, thresholds, at)
    return {'state': state, 'issue': issue, 'written': False, 'write_error': None, 'report': report,
            'constrained': constrained, 'known': known, 'unknown': unknown, 'seen': observed,
            '_accounts': accounts, '_changed': state == 'missing' or accounts != memory}


def commit(path, state):
    """Commit a prepared transition while the caller still holds the sidecar lock.

    State-file failures remain advisory, as before; a rejected route never calls this.
    """
    if state['state'] == 'unreadable' or not state['_changed']:
        return
    try:
        write_state(path, state['_accounts'])
        state['written'] = True
    except OSError as error:
        state['write_error'] = error.strerror or type(error).__name__


def substitute(survivors, sub):
    """The pair that replaces the best one, or None. `survivors` are the rows that passed every filter.

    A substitute sits on an account that is not constrained and whose projection is known and below the
    activation threshold. Order: pack candidate order, then availability (healthy, then unknown, then demoted for
    recent rate-limit or transport failures), then the account's `substitution_rank` (lower first; none last), then
    the lower projection, then the instance ID. A pair on an account with a live drain entry (see `drain`) is always a
    substitute target and sorts before every other, then by candidate order, availability and instance ID. With forecast bands the limit is the account's band upper limit
    for its days left, and the order after availability is the most headroom, then the earliest reset, then the
    instance ID (see `quota_bands`); `substitution_rank` does not apply.
    """
    state, ranks, bands = sub['state'], sub['ranks'], 'quota_bands' in sub['thresholds']
    known, seen = state['known'], state.get('seen')
    limit = lambda instance: seen[instance]['band']['high'] if bands else sub['thresholds']['activate_projected_pct']
    draining = lambda row: (row.get('rank') or {}).get('drain', 1) == 0  # a live drain entry (see `drain`): headroom by decree
    eligible = [row for row in survivors
                if draining(row) or (row['instance'] in known and row['instance'] not in state['constrained'] and known[row['instance']] < limit(row['instance']))]

    def key(row):
        instance = row['instance']
        availability = (row.get('rank') or {}).get('availability', 1)
        if draining(row):
            return (0, row['candidate'], availability, instance)
        if bands:
            reset = parse_time(seen[instance]['resets_at'])
            return (1, row['candidate'], availability, -seen[instance]['headroom'], float('inf') if reset is None else reset.timestamp(), instance)
        rank = ranks.get(instance)
        return (1, row['candidate'], availability, float('inf') if rank is None else rank, known[instance], instance)

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
        block |= {'from': move['from'], 'to': move['to'], 'reason': REASON, 'authorized_by': sub['authorized_by']}
    return block
