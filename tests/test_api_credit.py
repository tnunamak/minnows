"""Prepaid API credit is paced to its own grant expiry, separately from weekly subscriptions."""
import copy
import runpy
from datetime import datetime

import pytest

from test_route import TOOL, World, entry

import api_credit

NOW = '2026-11-19T12:00:00Z'


@pytest.fixture
def world(tmp_path):
    found = World(tmp_path)
    for provider in found.quota['providers'].values():
        for row in [provider, *provider.get('sources', [])]:
            for window in row.get('usage', {}).get('windows', []):
                window.update(utilization=100, resets_at='2026-12-01T00:00:00Z')
    found.accounts['accounts']['claude-api'] = {
        'billing': 'prepaid', 'quota_provider': 'claude', 'quota_source': 'default',
        'funding': {'verified_until': None},
        'credit_pool': {'provider': 'claude_api', 'organization': 'Fixture organization'},
    }
    found.quota['providers']['claude_api'] = {'usage': {'credit_pools': [{
        'organization': 'Fixture organization', 'currency': 'USD', 'monthly_credit': 10000,
        'month_spend': 4000, 'balance': 6000, 'month_resets_at': '2026-12-01T00:00:00Z',
        'observed_at': NOW, 'expires_at': '2026-12-10T12:00:00Z', 'expiring': 6000,
        'live': True, 'burn_per_day': 100, 'burn_days': 7,
    }]}}
    return found


def run(world, decision):
    result, out = world.cli('route', 'implement.quota-tight', *world.args(), '--proof-class', 'judged',
                            '--decision-id', decision, evaluation_time=NOW)
    assert result.returncode == 0, result.stderr
    return out


def approve(world):
    world.add_directive('approved', 'allow-metered', 'account=claude-api', 'decision=approved-spend',
                        recorded_at=NOW, until='2026-11-20T00:00:00Z')


def pace(world, metadata=None):
    return api_credit.grant_pace(world.quota, metadata or {'state': 'snapshot'},
                                 world.accounts['accounts']['claude-api'],
                                 datetime.fromisoformat(NOW.replace('Z', '+00:00')))


def test_exhausted_subscriptions_do_not_authorize_prepaid_expiring_api_credit(world):
    out = run(world, 'unapproved-spend')
    assert out['target'] is None and out['launch_ready'] is False
    row = entry(out, 'claude-api', 0)
    assert row['billing'] == 'prepaid'
    assert row['removed_by'] == 'api_credit_needs_tim_approval'
    assert out['routing']['quota'][row['quota']['ref']]['api_credit']['days_left'] == 21


def test_per_spend_approval_selects_api_and_shows_its_grant_cost_pace(world):
    approve(world)
    out = run(world, 'approved-spend')
    assert out['target']['providerInstanceId'] == 'claude-api' and out['launch_ready'] is True
    assert entry(out, 'claude-api', 0)['billing'] == 'prepaid'
    found = out['api_credit']['grant_pace']
    assert found['state'] == 'snapshot' and found['window'] == 'grant_expiry'
    assert found['currency'] == 'USD' and found['amount_unit'] == 'minor'
    assert (found['spent_this_month'], found['balance'], found['days_left']) == (4000, 6000, 21)
    assert found['projected_spend_before_expiry'] == 2100 and found['pace'] == 'on_track'
    assert found['projected_unused_at_expiry'] == 3900 and found['remaining_per_day'] == 285.71
    assert run(world, 'different-spend')['target'] is None


@pytest.mark.parametrize('expiry,days', [('2026-11-24T12:00:00Z', 5), ('2026-12-10T12:00:00Z', 21)])
def test_grant_expiry_controls_pace_instead_of_weekly_or_calendar_month_reset(world, expiry, days):
    pool = world.quota['providers']['claude_api']['usage']['credit_pools'][0]
    pool['expires_at'] = expiry
    found = pace(world)
    assert found['days_left'] == days and found['projected_spend_before_expiry'] == 100 * days


@pytest.mark.parametrize('change,reason', [({'live': False}, 'credit_reading_not_live'),
                                           ({'live': None}, 'credit_reading_not_live'),
                                           ({'observed_at': '2026-10-27T00:00:00Z'}, 'credit_reading_stale_or_missing'),
                                           ({'observed_at': None}, 'credit_reading_stale_or_missing'),
                                           ({'observed_at': '2026-11-20T00:00:00Z'}, 'credit_reading_stale_or_missing'),
                                           ({'balance': float('nan')}, 'credit_reading_incomplete'),
                                           ({'signed_out': True}, 'credit_reading_not_live')])
def test_nonlive_or_old_readings_are_unknown_including_compact_output(world, change, reason):
    pool = world.quota['providers']['claude_api']['usage']['credit_pools'][0]
    pool.update(change)
    found = pace(world)
    assert found['state'] == 'unknown' and found['pace'] == 'unknown' and found['reason'] == reason
    assert 'balance' not in found and 'projected_spend_before_expiry' not in found
    out = run(world, 'unapproved-spend')
    line = runpy.run_path(str(TOOL))['line_decision'](out, world.receipts, recorded=False)
    assert line['api_credit_options']['claude-api']['reason'] == reason


def test_pool_identity_is_exact_and_never_uses_the_weekly_source(world):
    account = world.accounts['accounts']['claude-api']
    account['credit_pool']['organization'] = 'A different organization'
    assert pace(world)['state'] == 'unknown'
    account['credit_pool']['organization'] = 'Fixture organization'
    pools = world.quota['providers']['claude_api']['usage']['credit_pools']
    pools.append(copy.deepcopy(pools[0]))
    assert pace(world)['state'] == 'unknown'


def test_compact_output_keeps_grant_cost_for_the_approver(world):
    approve(world)
    out = run(world, 'approved-spend')
    cli = runpy.run_path(str(TOOL))
    for name in ('brief_decision', 'line_decision'):
        assert cli[name](out, world.receipts, recorded=False)['api_credit'] == out['api_credit']


@pytest.mark.parametrize('change', [{'balance': 0, 'expiring': 0}, {'expires_at': NOW}])
def test_approval_does_not_revive_an_empty_or_expired_grant(world, change):
    approve(world)
    world.quota['providers']['claude_api']['usage']['credit_pools'][0].update(change)
    out = run(world, 'approved-spend')
    assert out['target'] is None and entry(out, 'claude-api', 0)['removed_by'] == 'api_credit_exhausted'


def test_only_the_expiring_grant_sets_the_daily_allowance(world):
    pool = world.quota['providers']['claude_api']['usage']['credit_pools'][0]
    pool.update(expiring=1000, balance=6000)
    found = pace(world)
    assert found['remaining_per_day'] == 47.62 and found['pace'] == 'at_risk'
    assert found['projected_spend_before_expiry'] == 1000


def test_folded_clawmeter_pools_require_the_exact_source_mapping(world):
    provider = world.quota['providers'].pop('claude_api')
    world.quota['providers']['credit-provider'] = {'sources': [
        {'source': {'id': 'first'}, 'usage': provider['usage']},
        {'source': {'id': 'other'}, 'usage': {'credit_pools': []}},
    ]}
    mapping = world.accounts['accounts']['claude-api']['credit_pool']
    mapping.update(provider='credit-provider', source='other')
    assert pace(world)['state'] == 'unknown'
    mapping['source'] = 'first'
    assert pace(world)['state'] == 'snapshot'
