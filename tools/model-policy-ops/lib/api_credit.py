"""Prepaid API-credit pace to each grant's expiry, independent of weekly quotas.

Clawmeter amounts are currency minor units. A balance or projection never authorizes spend.
"""
import math
from datetime import timedelta

from quota_substitution import parse_time

# Match clawmeter's live-credit observation horizon, rather than revive old balances.
MAX_OBSERVATION_AGE = timedelta(minutes=5)


def is_credit(entry):
    return bool(entry and (entry.get('billing') in ('metered', 'prepaid') or 'funding' in entry))


def amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def grant_pace(quota, metadata, entry, at):
    """Read one explicitly mapped clawmeter pool; missing or ambiguous evidence stays unknown."""
    result = {'window': 'grant_expiry', 'state': 'unknown', 'pace': 'unknown', 'amount_unit': 'minor'}
    mapping = (entry or {}).get('credit_pool')
    if not mapping:
        return result | {'reason': 'credit_pool_unmapped'}
    rows = quota.get('providers')
    row = rows.get(mapping['provider']) if isinstance(rows, dict) else None
    if isinstance(row, dict) and mapping.get('source') is not None:
        sources = row.get('sources')
        sources = [s for s in sources if isinstance(s, dict) and isinstance(s.get('source'), dict)
                   and s['source'].get('id') == mapping['source']] if isinstance(sources, list) else []
        row = sources[0] if len(sources) == 1 else None
    usage = row.get('usage') if isinstance(row, dict) else None
    pools = usage.get('credit_pools') if isinstance(usage, dict) else None
    pools = [p for p in pools if isinstance(p, dict) and p.get('organization') == mapping['organization']] if isinstance(pools, list) else []
    if len(pools) != 1:
        return result | {'reason': 'credit_pool_missing_or_ambiguous'}
    pool = pools[0]
    observed, expiry = parse_time(pool.get('observed_at')), parse_time(pool.get('expires_at'))
    if observed:
        result['observed_at'] = observed.isoformat()
    if metadata.get('state') != 'snapshot' or pool.get('live') is not True or pool.get('signed_out'):
        return result | {'reason': 'credit_reading_not_live'}
    if (observed is None or not timedelta(0) <= at - observed <= MAX_OBSERVATION_AGE or
            usage.get('is_expired') or usage.get('error') or pool.get('expired_since_check')):
        return result | {'reason': 'credit_reading_stale_or_missing'}
    balance, expiring = pool.get('balance'), pool.get('expiring')
    if (not isinstance(pool.get('currency'), str) or not pool['currency'] or
            not amount(balance) or not amount(expiring) or expiring > balance or expiry is None):
        return result | {'reason': 'credit_reading_incomplete'}
    result |= {'state': 'snapshot', 'currency': pool['currency'], 'balance': balance,
               'expiring_amount': expiring, 'expires_at': expiry.isoformat()}
    if amount(pool.get('month_spend')):
        result['spent_this_month'] = pool['month_spend']
    if expiry <= at or balance == 0 or expiring == 0:
        return result | {'pace': 'exhausted', 'reason': 'grant_expired_or_empty', 'days_left': 0 if expiry <= at else round((expiry - at).total_seconds() / 86400, 4)}
    days_left = (expiry - at).total_seconds() / 86400
    result |= {'days_left': round(days_left, 4), 'remaining_per_day': round(expiring / days_left, 2)}
    burn, basis = pool.get('burn_per_day'), pool.get('burn_days')
    if not amount(burn) or type(basis) is not int or basis <= 0:
        return result | {'reason': 'credit_burn_unknown'}
    projected = min(expiring, burn * days_left)
    return result | {'pace': 'at_risk' if burn * days_left > expiring else 'on_track',
                     'burn_per_day': burn, 'burn_basis_days': basis,
                     'projected_spend_before_expiry': round(projected, 2),
                     'projected_unused_at_expiry': round(max(0, expiring - projected), 2),
                     'projection_basis': 'observed daily burn times days left to this grant expiry; estimate only'}
