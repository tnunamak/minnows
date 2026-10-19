"""Drain accounts: a user's one-off wish to burn an account's 7-day allowance down before the normal reset.

A user supplies the entries in the per-user overlay (`routing.drain_accounts`); the shipped pack has none. An entry,
keyed by provider instance ID, is `{until_current_pct, expires_at, reason}`. It applies while `now < expires_at` and
the account's current use is known and below `until_current_pct`. Use is the highest utilization among the pace
windows. While an entry applies, `route` treats the account as having headroom (a high projection neither holds nor
substitutes away from it) and ranks its pairs first among the pairs that passed every filter. Unknown use never
applies an entry, and once use reaches the limit or the entry expires the account is judged as before.
"""
from quota_substitution import parse_time

ENTRY_KEYS = {'until_current_pct', 'expires_at', 'reason'}


def check(entries, present=True):
    """Error strings for `routing.drain_accounts`; an absent key is off."""
    if not present:
        return []
    if not (isinstance(entries, dict) and entries):
        return ['routing.drain_accounts must be a non-empty object keyed by provider instance ID']
    errors = []
    for instance, entry in entries.items():
        at = f'routing.drain_accounts[{instance!r}]'
        if not (isinstance(instance, str) and instance):
            errors.append('routing.drain_accounts: every key must be a non-empty provider instance ID')
        elif not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            errors.append(f'{at}: must be an object with exactly until_current_pct, expires_at and reason')
        elif not (type(entry['until_current_pct']) in (int, float) and 0 < entry['until_current_pct'] <= 100):
            errors.append(f'{at}: until_current_pct must be a number with 0 < until_current_pct <= 100')
        elif not (isinstance(entry['expires_at'], str) and parse_time(entry['expires_at'])):
            errors.append(f'{at}: expires_at must be an ISO 8601 time with a zone, for example 2026-10-22T00:00:00Z')
        elif not (isinstance(entry['reason'], str) and entry['reason'].strip()):
            errors.append(f'{at}: reason must be a non-empty string')
    return errors


def current_pct(windows):
    """The highest known utilization among the pace windows, or None when any has no known use."""
    values = [w.get('utilization') for w in windows]
    if not values or any(type(v) not in (int, float) for v in values):
        return None
    return max(values)


def status(entry, current, now):
    """{applies, why, ...entry} for one entry: `applies` is True only while it is live and use is known and below the limit."""
    row = {'until_current_pct': entry['until_current_pct'], 'expires_at': entry['expires_at'], 'reason': entry['reason'],
           'current_pct': current}
    expires = parse_time(entry['expires_at'])
    if expires is None or now >= expires:
        return row | {'applies': False, 'why': 'expired'}
    if current is None:
        return row | {'applies': False, 'why': 'current_use_unknown'}
    if current >= entry['until_current_pct']:
        return row | {'applies': False, 'why': 'threshold_reached'}
    return row | {'applies': True, 'why': 'active'}
