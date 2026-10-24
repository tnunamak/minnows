"""Runtime regressions from the 2026-10-24 quota routing incident."""
import subprocess
from unittest.mock import patch

import pytest

from test_quota_bands import BandWorld, NOW, route
import test_route
from test_route import set_forecast
from runtime_sources import quota_context, read_source


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    return BandWorld(tmp_path)


def exhaustion(world, window='7d All', **changes):
    provider = world.quota['providers']['claude']
    source = next(s for s in provider['sources'] if s['source']['id'] == 'default')
    source['view'] = {'windows': [{'name': window, 'exhaustion': {
        'state': 'runs_out', 'runs_out_in_seconds': 60000, 'early_by_seconds': 300000, **changes}}]}
    return source


def test_runs_out_inside_a_wide_band_is_not_headroom(world):
    world.codex(80)
    world.set_claude(110, 'claudeAgent')
    exhaustion(world)
    out = world.route('implement.quota-tight')
    assert out['target']['providerInstanceId'] == 'codex'
    view = out['routing']['quota']['claudeAgent']
    assert view['pace'] == 'at_risk'
    assert view['pace_reason'] == 'runs_out_before_reset'
    assert view['binding']['headroom'] <= 0
    assert out['routing']['quota_substitution']['active_accounts']['claudeAgent']['state'] == 'activated'


@pytest.mark.parametrize('window,changes', [
    ('bonus', {}), ('7d All', {'early_by_seconds': 0}),
    ('7d All', {'state': 'unknown'}), ('7d All', {'runs_out_in_seconds': -1}),
])
def test_unusable_or_irrelevant_exhaustion_does_not_block(world, window, changes):
    world.set_claude(110, 'claudeAgent')
    exhaustion(world, window=window, **changes)
    out = world.route('implement.quota-tight')
    assert out['target']['providerInstanceId'] == 'claudeAgent'


@pytest.mark.parametrize('state', ['missing', 'error', 'stale'])
def test_missing_quota_reports_source_failure_before_missing_provider(state):
    info = quota_context({}, {'state': state}, {'model': 'gpt-6-luna'}, 'openai')
    assert info['state'] == state


def test_catalog_timeout_has_bounded_retry():
    command = ['t3code', '--json', 'models', 'list']
    answer = subprocess.CompletedProcess(command, 0, '{"providers": []}', '')
    with patch('runtime_sources.subprocess.run', side_effect=[subprocess.TimeoutExpired(command, 30), answer]) as run:
        value, metadata = read_source('auto', command, retry_timeout=True)
    assert value == {'providers': []} and metadata['state'] == 'snapshot'
    assert run.call_count == 2


def test_persistent_catalog_timeout_stays_unknown():
    command = ['t3code', '--json', 'models', 'list']
    with patch('runtime_sources.subprocess.run', side_effect=subprocess.TimeoutExpired(command, 30)) as run:
        value, metadata = read_source('auto', command, retry_timeout=True)
    assert value == {} and metadata['issue'] == 'timeout'
    assert run.call_count == 2


def test_early_short_window_is_not_hidden_by_weekly_headroom(world):
    world.codex(80)
    world.set_claude(110, 'claudeAgent')
    exhaustion(world, window='5h')
    out = world.route('implement.quota-tight')
    assert out['target']['providerInstanceId'] == 'codex'
    assert out['routing']['quota']['claudeAgent']['binding']['window'] == '5h'


def test_critical_exception_reports_the_exhaustion_cap(world):
    world.codex(150)
    world.set_claude(180)
    world.set_claude(110, 'claudeAgent')
    exhaustion(world)
    out = world.route('implement.quota-tight', '--critical-path')
    assert out['target']['providerInstanceId'] == 'claudeAgent'
    chosen = out['routing']['pacing']['candidates'][0]
    assert chosen['band_high'] == 130 and chosen['headroom_limit_pct'] == 100
    assert chosen['overshoot_pp'] == chosen['projected_pct'] - chosen['headroom_limit_pct']


@pytest.mark.parametrize('short_use', [96, None])
def test_short_window_runout_does_not_change_drain_current_use(world, short_use):
    source = exhaustion(world, window='5h')
    for window in source['usage']['windows']:
        if window['name'] == '7d All':
            window['utilization'] = 50
        if window['name'] == '5h':
            window['utilization'] = short_use
    world.policy['routing']['drain_accounts'] = {'claudeAgent': {
        'until_current_pct': 95, 'expires_at': '2026-10-12T00:00:00Z', 'reason': 'test reset credit'}}
    out = world.route('implement.quota-tight')
    assert out['target']['providerInstanceId'] == 'claudeAgent'
    drain = out['routing']['drain_accounts']['accounts']['claudeAgent']
    assert drain['applies'] and drain['current_pct'] == 50


def activate_short_window(world):
    world.codex(150)
    world.set_claude(180)
    world.set_claude(110, 'claudeAgent')
    set_forecast(world, 'claude', 'default', '5h', 110)
    source = exhaustion(world, window='5h')
    first = world.route('implement.quota-tight')
    assert first['target'] is None
    assert first['routing']['quota_substitution']['active_accounts']['claudeAgent']['window'] == '5h'
    source['view']['windows'][0]['exhaustion'] = {'state': 'lasts'}
    return source


def test_short_window_hold_survives_cleared_runout_until_release(world):
    activate_short_window(world)
    set_forecast(world, 'claude', 'default', '5h', 100)
    held = world.route('implement.quota-tight')
    row = held['routing']['quota_substitution']['active_accounts']['claudeAgent']
    assert row['window'] == '5h' and row['state'] == 'held_by_hysteresis'
    assert held['target'] is None
    # Even when the weekly window has less numerical headroom, the short hold
    # has not crossed its release threshold.
    world.set_claude(127, 'claudeAgent')
    assert world.route('implement.quota-tight')['target'] is None
    set_forecast(world, 'claude', 'default', '5h', 94)
    assert world.route('implement.quota-tight')['target']['providerInstanceId'] == 'claudeAgent'


def test_unknown_short_window_hold_is_not_critical_path_headroom(world):
    source = activate_short_window(world)
    del source['forecast']['windows']['5h']
    out = world.route('implement.quota-tight', '--critical-path')
    assert out['target']['providerInstanceId'] != 'claudeAgent'
    assert all(c['instance'] != 'claudeAgent' for c in out['routing']['pacing']['candidates'])


def test_catalog_failure_is_not_reported_as_a_missing_account(world):
    world.catalog = {'ok': False, 'error': 'unavailable'}
    out = route(world)
    assert {row['removed_by'] for row in out['routing']['trace']} == {'catalog_unavailable'}
    assert out['waiting']['state'] == 'catalog_unavailable'
    assert out['target'] is None


def test_genuine_missing_account_still_has_its_own_reason(world):
    world.catalog['data']['providers'] = [p for p in world.catalog['data']['providers'] if p['driver'] != 'codex']
    out = route(world)
    assert out['routing']['trace'][0]['removed_by'] == 'no_catalog_account'
