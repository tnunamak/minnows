"""What `route` does when the pair it picked is over its quota band, and how a wait reports itself.

With forecast bands (see `quota_bands`) a subscription pair launches only with known positive headroom and no
hysteresis hold. When the best pair cannot launch, in order:

1. The first ranked pair that can launch wins: a verified prepaid or free account, or a subscription with headroom.
   A wait never precedes an eligible alternative that the filters already admitted.
2. A caller that set the `critical_path` launch fact gets the eligible subscription with the least overshoot, which
   is the projected percent above that account's own band upper limit, for the window that binds its pace. Unknown,
   stale or exhausted accounts have no overshoot and are never picked.
3. Otherwise the route waits. `waiting` says so: it dispatches nothing and no timer retries it. A caller routes
   again with fresh facts, at the earliest reset that `waiting.retry_after` names or sooner.

This module does not read a clock or a file. It never picks a pair that a filter removed.
"""
from datetime import timedelta, timezone

from quota_substitution import parse_time

ALTERNATIVE_BEFORE_WAIT = 'alternative_before_wait'
CRITICAL_EXCEPTION = 'critical_path_exception'
CRITICAL_NO_CANDIDATE = 'critical_path_no_known_candidate'
NOT_NEEDED = 'not_needed'


def launches(row, held, view):
    """True when the pair needs no pacing exception: a non-subscription pair, or a subscription with headroom and no hold."""
    return row['billing'] != 'subscription' or (view['pace'] == 'on_track' and row['instance'] not in held)


def overshoot(row):
    """Percentage points of projected use above the band upper limit; negative inside the band; None when unknown."""
    headroom = row['rank'].get('headroom')
    return None if headroom is None else round(-headroom, 2)


def reset_seconds(row):
    moment = parse_time(row['rank'].get('resets_at'))
    return float('inf') if moment is None else moment.timestamp()


def critical_key(row):
    rank = row['rank']
    return (rank['eligibility'], rank['prefer'], rank['availability'], overshoot(row), rank['candidate_order'], reset_seconds(row), rank['instance'])


def candidate_row(row):
    binding = row['_view']['binding']
    return {'instance': row['instance'], 'model': row['arm']['model'], 'effort': row['arm']['effort'], 'overshoot_pp': overshoot(row),
            'projected_pct': binding['projected_pct'], 'band_high': binding['band']['high'], 'window': binding['window'],
            'resets_at': binding['resets_at'], 'quota_ref': row['quota']['ref']}


def pick(survivors, picked, held, critical_path):
    """(row, pacing): the pair to choose and the record of why, or (picked, None) when pacing did not matter.

    `survivors` are the ranked rows that passed every filter and still carry `_view`; `picked` is the best of them
    after quota substitution. `held` is the set of accounts held by hysteresis.
    """
    if picked is None:
        return None, None
    if launches(picked, held, picked['_view']):
        return picked, ({'state': NOT_NEEDED, 'critical_path': True} if critical_path else None)
    skipped = {'instance': picked['instance'], 'model': picked['arm']['model'], 'pace': picked['_view']['pace'], 'held': picked['instance'] in held}
    # A pair that only an exact override can authorize is never a pacing alternative: the wait must not decide
    # the parent's opt-in for it.
    for row in survivors:
        if not row.get('exact_override_required') and launches(row, held, row['_view']):
            return row, {'state': ALTERNATIVE_BEFORE_WAIT, 'critical_path': critical_path, 'skipped': skipped}
    if not critical_path:
        return picked, None
    pool = sorted((row for row in survivors if row['billing'] == 'subscription' and overshoot(row) is not None), key=critical_key)
    if not pool:
        return picked, {'state': CRITICAL_NO_CANDIDATE, 'critical_path': True, 'skipped': skipped, 'candidates': []}
    return pool[0], {'state': CRITICAL_EXCEPTION, 'critical_path': True, 'skipped': skipped,
                     'order': 'eligibility, prefer, availability, overshoot_pp, candidate order, earliest reset, instance',
                     'chosen_overshoot_pp': overshoot(pool[0]), 'candidates': [candidate_row(row) for row in pool]}


def relax_pacing_reasons(reasons, pacing):
    """A critical-path pick keeps its launch: the pacing reasons stay in the receipt but stop demanding judgment."""
    if not pacing or pacing['state'] != CRITICAL_EXCEPTION:
        return reasons
    softened = [r | {'informational': True} if r['code'] in ('pace_not_on_track', 'quota_substitution_unavailable') else r for r in reasons]
    return softened + [{'code': 'critical_path_pacing_exception', 'informational': True, 'applies_to': 'routed',
                        'detail': {'instance': pacing['candidates'][0]['instance'], 'overshoot_pp': pacing['chosen_overshoot_pp'],
                                   'skipped': pacing['skipped']}}]


def retry_after(trace):
    """The earliest reset among eligible subscription accounts with a known reset time, or None."""
    resets = [moment for row in trace if row['removed_by'] is None and row['billing'] == 'subscription'
              if (moment := parse_time((row.get('rank') or {}).get('resets_at')))]
    return min(resets).isoformat() if resets else None


def cooldown(failures, instance, demote_count, classes):
    """{state, retry_after, events} for an account's recent demoting failures.

    `cooling_down` while the events inside the lookback reach `demote_count`. `retry_after` is the instant at which
    the (count - demote_count + 1)th oldest event is exactly one lookback old. The lookback is inclusive, so the
    account is available again once the clock is later than `retry_after`, not at it. So the account is reevaluated from fresh events
    on every route and no cooldown is stored. `unknown` when the telemetry is unavailable.
    """
    if failures['state'] != 'observed' or demote_count is None or failures.get('unjoined'):
        return {'state': 'unknown', 'retry_after': None, 'events': 0}
    raw = [t for name in classes for t in failures.get('times', {}).get(instance, {}).get(name, [])]
    count = sum(failures['instances'].get(instance, {}).get(name, 0) for name in classes)
    if count < demote_count:
        return {'state': 'available', 'retry_after': None, 'events': count}
    parsed = sorted(moment for moment in map(parse_time, raw) if moment)  # sort instants, not their text spelling
    anchor = parsed[count - demote_count] if len(parsed) == count else None
    until = (anchor + timedelta(minutes=failures['lookback_minutes'])).astimezone(timezone.utc) if anchor else None
    return {'state': 'cooling_down', 'retry_after': until.isoformat() if until else None, 'events': count}


def free_routes(routes, qualify, failures, demote_count, classes):
    """One row per declared experimental free route: whether it qualifies for this request and whether it is cooling down.

    `qualify(route)` returns `(effort, refusal)`. The row only reports: the caller still opts in with the exact flags,
    because public inputs are the caller's own assertion.
    """
    rows = []
    for route in routes:
        effort, refusal = qualify(route)
        row = {'provider': route['provider'], 'model': route['model'], 'account': route['account'], 'qualified': refusal is None,
               'refusal': refusal, 'availability': cooldown(failures, route['account'], demote_count, classes)}
        if refusal is None:
            row['opt_in'] = ['--experimental-route', '--data-classification', 'public', '--override', f'provider={route["provider"]}',
                             '--override', f'model={route["model"]}', '--override', f'effort={effort}',
                             '--override', f'account={route["account"]}', '--reason', '<why this public task may use the free route>']
        rows.append(row)
    return rows


def prepaid_summary(trace):
    """Every metered pair that the filters saw, with the filter that removed it or `eligible`."""
    return [{'instance': row['instance'], 'arm_model': row['arm']['model'], 'state': row['removed_by'] or 'eligible'}
            for row in trace if row.get('billing') == 'metered']
