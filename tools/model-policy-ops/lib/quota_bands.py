"""Forecast bands: the headroom of an account's projected use at reset below its band's upper limit.

A user supplies the bands in the per-user overlay (`routing.quota_bands`); the shipped pack has none. A band is
`{low, high}` in percent of the window, picked by how many days are left before the window resets. The rules are
an ordered list, widest first: `{min_days_left, exclusive?, low, high}`. The first rule that matches the days left
is the band. A far reset gets a wide band and a near reset a narrow one. The last rule must have `min_days_left` 0
and be inclusive, so every window that has not reset has a band.

Headroom is `high - projected_pct`. `route` treats an account as a target only with a known positive headroom.
Unknown, stale or unusable readings give `None`, never headroom.
"""
from quota_substitution import parse_time

RULE_KEYS = {'min_days_left', 'exclusive', 'low', 'high'}


def number(value):
    return type(value) in (int, float)


def check(rules, present=True):
    """Error strings for `routing.quota_bands`; an absent key is off."""
    if not present:
        return []
    if not (isinstance(rules, list) and rules and all(isinstance(r, dict) for r in rules)):
        return ['routing.quota_bands must be a non-empty list of rules']
    errors = []
    for i, rule in enumerate(rules):
        at = f'routing.quota_bands[{i}]'
        if not {'min_days_left', 'low', 'high'} <= set(rule) <= RULE_KEYS:
            errors.append(f'{at}: keys must be min_days_left, low, high and optional exclusive')
        elif not all(number(rule[k]) for k in ('min_days_left', 'low', 'high')) or not isinstance(rule.get('exclusive', False), bool):
            errors.append(f'{at}: min_days_left, low and high must be numbers and exclusive a boolean')
        elif not (rule['min_days_left'] >= 0 and 0 < rule['low'] < rule['high'] <= 200):
            errors.append(f'{at}: needs min_days_left >= 0 and 0 < low < high <= 200')
    if errors:
        return errors
    if any(a['min_days_left'] <= b['min_days_left'] for a, b in zip(rules, rules[1:])):
        errors.append('routing.quota_bands: min_days_left must strictly decrease from the first rule to the last')
    last = rules[-1]
    if last['min_days_left'] != 0 or last.get('exclusive'):
        errors.append('routing.quota_bands: the last rule must have min_days_left 0 and not be exclusive')
    return errors


def band_for(rules, days_left):
    for rule in rules:
        if days_left > rule['min_days_left'] or (days_left == rule['min_days_left'] and not rule.get('exclusive')):
            return {'low': rule['low'], 'high': rule['high']}
    return None


def reset_time(resets_at):
    """Seconds since the epoch for a zoned ISO reset time, or None."""
    moment = parse_time(resets_at)
    return moment.timestamp() if moment else None


def headroom(rules, projected_pct, resets_at, now):
    """{band, days_left, headroom} for one forecast window, or None when its reset time is unusable or has passed."""
    reset = reset_time(resets_at)
    if reset is None or reset <= now.timestamp():
        return None
    days_left = (reset - now.timestamp()) / 86400
    band = band_for(rules, days_left)
    if band is None:
        return None
    return {'band': band, 'days_left': round(days_left, 3), 'headroom': round(band['high'] - projected_pct, 2)}
