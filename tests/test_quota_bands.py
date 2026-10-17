"""`route` with forecast bands: schedule among qualified pairs by headroom below each account's own band upper limit.

Inputs are the pinned 2026-10-11T00:25Z capture of `test_quota_substitution`. Band values here are neutral test
values in the shape of the user overlay; the shipped pack carries none. Each test changes one input and runs the real CLI.
Days left in the capture: claude-alt 6.3, claudeAgent 5.1 and claude-work 5.2 (band 70/130), codex 3.1 (80/120).
`reset_in` moves one account's reset so that tests control which band it sits in.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import test_route
from test_quota_substitution import CLAUDE, FIX, NOW, QuotaWorld, active, block, pick
from test_route import REPO, codes, entry

BANDS = [{'min_days_left': 5, 'low': 70, 'high': 130},
         {'min_days_left': 2, 'exclusive': True, 'low': 80, 'high': 120},
         {'min_days_left': 1, 'low': 90, 'high': 110},
         {'min_days_left': 0, 'low': 95, 'high': 105}]
ORACLE = ('--proof-class', 'oracle')


class BandWorld(QuotaWorld):
    """Bands replace the fixed substitution thresholds; the pack keeps no `quota_substitution`."""

    def __init__(self, tmp):
        super().__init__(tmp)
        del self.policy['routing']['quota_substitution']
        self.policy['routing']['quota_bands'] = json.loads(json.dumps(BANDS))
        self.codex(130)  # above its 120 band: the primary arm has no headroom
        for instance in CLAUDE:
            self.set_claude(130, instance)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    return BandWorld(tmp_path)


def reset_in(world, source_id, days):
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == source_id)
    reset = datetime(2026, 10, 11, 0, 25, tzinfo=timezone.utc) + timedelta(days=days)
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['resets_at'] = reset.isoformat().replace('+00:00', 'Z')


def route(world, *extra):
    return world.route('implement.oracle-bounded', *ORACLE, *extra)


def add_prepaid(world, verified_until=None):
    """A metered account with no quota mapping, allowed by an owner directive: pace unknown, and its credit is
    unverified unless the account map holds a check that has not expired."""
    world.accounts['accounts']['claude-api'] = {'billing': 'metered', 'quota_provider': None, 'quota_source': None,
                                                'funding': {'verified_until': verified_until}}
    world.add_directive('allow-prepaid', 'allow-metered', 'account=claude-api', until='2026-10-12T00:00:00Z')


def test_most_headroom_below_the_account_band_wins(world):
    # claudeAgent 100 of 120 = 20 (the lowest projection, but a narrower band); claude-alt 105 of 130 = 25; claude-work 110 of 130 = 20
    reset_in(world, 'default', 3)
    world.set_claude(100, 'claudeAgent')
    world.set_claude(105, 'claude-alt')
    world.set_claude(110, 'claude-work')
    out = route(world)
    assert pick(out) == ('claude-alt', 'claude-sonnet-5-5')
    assert out['routing']['chosen']['selection_basis'] == 'quota_substitution_exception'
    assert block(out)['to']['instance'] == 'claude-alt' and block(out)['substituted'] is True
    view = out['routing']['quota']['claude-alt']['binding']
    assert (view['band'], view['headroom']) == ({'low': 70, 'high': 130}, 25)


def test_headroom_uses_each_accounts_own_days_left(world):
    # equal projections: the account far from its reset has the wider band, so the larger headroom
    reset_in(world, 'default', 3)
    reset_in(world, 'work', 3)
    for instance in CLAUDE:
        world.set_claude(110, instance)
    assert pick(route(world)) == ('claude-alt', 'claude-sonnet-5-5')


def test_a_subscription_with_headroom_beats_unverified_prepaid_on_a_claude_primary(world):
    add_prepaid(world)
    world.set_claude(110, 'claude-alt')  # at_risk by the fixed 100 rule, 20 inside its 130 band
    out = world.route('implement.quota-tight')
    assert pick(out) == ('claude-alt', 'claude-sonnet-5-5')
    assert out['routing']['judgment_required'] is False


def test_a_subscription_with_headroom_replaces_a_constrained_primary_not_unverified_prepaid(world):
    add_prepaid(world)
    world.set_claude(110, 'claude-alt')
    assert pick(route(world)) == ('claude-alt', 'claude-sonnet-5-5')


@pytest.mark.parametrize('verified_until', [None, '2026-10-10T00:00:00Z', '2026-10-11T00:25:00Z'])
def test_unverified_or_expired_credit_is_never_a_target(world, verified_until):
    add_prepaid(world, verified_until)
    out = world.route('implement.quota-tight')
    assert entry(out, 'claude-api')['removed_by'] == 'funding_unverified'
    assert out['target'] is None and out['launch_ready'] is False
    assert out['waiting']['state'] == 'no_forecast_headroom' and 'dispatch' not in out
    assert 'wait_no_forecast_headroom' in codes(out)


def test_verified_credit_is_overflow_when_no_subscription_has_headroom(world):
    add_prepaid(world, '2026-10-12T00:00:00Z')
    out = world.route('implement.quota-tight')
    assert pick(out) == ('claude-api', 'claude-sonnet-5-5')
    assert out['launch_ready'] is True and 'waiting' not in out
    assert out['routing']['judgment_required'] is True and 'pace_not_on_track' in codes(out)


def test_verified_credit_does_not_outrank_a_subscription_with_headroom(world):
    add_prepaid(world, '2026-10-12T00:00:00Z')
    world.set_claude(110, 'claude-alt')
    assert pick(world.route('implement.quota-tight')) == ('claude-alt', 'claude-sonnet-5-5')


def test_credit_without_a_funding_declaration_keeps_the_directive_behavior(world):
    add_prepaid(world)
    del world.accounts['accounts']['claude-api']['funding']
    assert pick(world.route('implement.quota-tight')) == ('claude-api', 'claude-sonnet-5-5')


def test_no_headroom_and_no_prepaid_waits_instead_of_dispatching(world):
    out = route(world)
    assert out['routing']['judgment_required'] is True
    assert 'quota_substitution_unavailable' in codes(out) and 'wait_no_forecast_headroom' in codes(out)
    assert block(out)['substituted'] is False
    assert out['target'] is None and out['launch_ready'] is False and 'dispatch' not in out
    assert out['waiting']['state'] == 'no_forecast_headroom'
    assert all(row.get('target') is None for row in [out['routing']['chosen']])


def test_an_explicit_override_is_not_withheld(world):
    out = route(world, '--override', 'provider=claude', '--override', 'model=claude-sonnet-5-5', '--override', 'account=claude-alt', '--reason', 'caller intent')
    assert out['launch_ready'] is True and 'waiting' not in out and out['target']['providerInstanceId'] == 'claude-alt'


def test_a_declared_unverified_account_is_withheld_even_by_an_explicit_override(world):
    add_prepaid(world)
    out = route(world, '--override', 'provider=claude', '--override', 'model=claude-sonnet-5-5', '--override', 'account=claude-api',
                '--reason', 'caller intent')
    assert out['target'] is None and out['launch_ready'] is False and out['waiting']['state'] == 'funding_unverified'
    assert 'final_funding_unverified' in codes(out) and 'dispatch' not in out
    assert route(world, '--override', 'provider=claude', '--override', 'model=claude-sonnet-5-5', '--override', 'account=claude-api',
                 '--reason', 'caller intent', '--brief')['dispatch'] is None
    world.accounts['accounts']['claude-api']['funding']['verified_until'] = '2026-10-12T00:00:00Z'  # a current check lets it through
    out = route(world, '--override', 'provider=claude', '--override', 'model=claude-sonnet-5-5', '--override', 'account=claude-api',
                '--reason', 'caller intent')
    assert out['launch_ready'] is True and out['target']['providerInstanceId'] == 'claude-api'


def test_unknown_forecasts_are_not_dispatchable_headroom(world):
    for provider in world.quota['providers'].values():
        for source in provider.get('sources', [provider]):
            source['forecast']['windows'].clear()
    out = route(world)
    assert out['target'] is None and out['waiting']['state'] == 'no_forecast_headroom'


def test_a_held_constrained_account_with_no_alternative_waits(world):
    world.codex(120)
    route(world)  # activates codex
    world.codex(90)  # between low and high: held by hysteresis, claude has no headroom either
    out = route(world)
    assert active(out)['codex'] == 'held_by_hysteresis'
    assert out['target'] is None and out['waiting']['state'] == 'no_forecast_headroom'
    world.set_claude(50, 'claude-alt')  # an alternative with headroom is dispatchable again
    out = route(world)
    assert pick(out) == ('claude-alt', 'claude-sonnet-5-5') and out['launch_ready'] is True


def test_a_recorded_wait_replays_without_a_target(world):
    first = world.route('implement.oracle-bounded', *ORACLE, record='wait-1')
    again = world.route('implement.oracle-bounded', *ORACLE, record='wait-1')
    assert first['waiting'] == again['waiting'] and again['target'] is None and again['launch_ready'] is False


@pytest.mark.parametrize('funding', [{}, {'verified_until': 'tomorrow'}, {'verified_until': '2026-10-12T00:00:00'}, 'yes',
                                     {'verified_until': None, 'balance': 1}])
def test_a_malformed_funding_declaration_is_refused(world, funding):
    add_prepaid(world)
    world.accounts['accounts']['claude-api']['funding'] = funding
    result, _ = world.cli('route', 'implement.quota-tight', *world.args(), '--proof-class', 'judged', '--decision-id', 'bad')
    assert result.returncode != 0 and 'funding' in result.stderr


def test_the_brief_output_says_waiting_and_names_no_dispatch(world):
    out = route(world, '--brief')
    assert out['waiting']['state'] == 'no_forecast_headroom' and out['dispatch'] is None and out['target'] is None


def test_a_healthy_primary_is_not_replaced(world):
    world.codex(100)  # 20 below its 120 band
    out = route(world)
    assert pick(out)[0] == 'codex' and block(out)['substituted'] is False
    world.set_claude(50, 'claude-alt')
    assert pick(route(world))[0] == 'codex', 'headroom elsewhere does not change the qualified primary'


def test_tie_goes_to_the_earlier_reset_then_the_instance_id(world):
    # claudeAgent and claude-work both have 20 below 120; claude-alt has none
    reset_in(world, 'default', 3)
    reset_in(world, 'work', 3.1)
    world.set_claude(100, 'claudeAgent')
    world.set_claude(100, 'claude-work')
    assert pick(route(world)) == ('claudeAgent', 'claude-sonnet-5-5'), 'earlier reset wins the tie'
    reset_in(world, 'work', 3)
    assert pick(route(world)) == ('claude-work', 'claude-sonnet-5-5'), 'same reset: the instance ID decides'
    reset_in(world, 'work', 2.9)
    assert pick(route(world)) == ('claude-work', 'claude-sonnet-5-5')
    reset_in(world, 'default', 2.8)
    assert pick(route(world)) == ('claudeAgent', 'claude-sonnet-5-5')


def test_unknown_and_stale_forecasts_are_not_headroom(world):
    world.set_claude(50, 'claude-alt')
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'alt')
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['resets_at'] = '2026-10-10T00:00:00Z'
    world.set_claude(119, 'claudeAgent')
    out = route(world)
    assert pick(out) == ('claudeAgent', 'claude-sonnet-5-5')
    assert 'claude-alt' in block(out)['unknown_accounts']
    row = next(t for t in out['routing']['trace'] if t.get('instance') == 'claude-alt' and t['arm']['provider'] == 'claude')
    assert row['quota']['pace'] == 'unknown' and row['quota']['headroom'] is None
    del source['forecast']['windows']['7d All']
    assert pick(route(world))[0] == 'claudeAgent'


def test_avoid_directive_still_wins_over_headroom(world):
    world.set_claude(50, 'claude-alt')
    world.set_claude(100, 'claudeAgent')
    world.add_directive('avoid-alt', 'avoid', 'account=claude-alt', until='2026-10-12T00:00:00Z')
    assert pick(route(world)) == ('claudeAgent', 'claude-sonnet-5-5')


def test_independence_still_wins_over_headroom(world):
    world.set_claude(50, 'claude-alt')
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5-5')
    assert pick(out)[0] == 'codex', 'vendor independence removes every Claude pair'
    assert 'quota_substitution_unavailable' in codes(out)


@pytest.mark.offline_unit
@pytest.mark.parametrize('days, high', [(6.0, 130), (5.0, 130), (4.9, 120), (2.1, 120), (2.0, 110), (1.0, 110), (0.9, 105)])
def test_band_edges_follow_the_ordered_rules(days, high):
    from quota_bands import band_for
    assert band_for(BANDS, days)['high'] == high


@pytest.mark.parametrize('days, high', [(5.0, 130), (2.0, 110), (0.9, 105)])
def test_band_edges_cli_parity(world, days, high):
    from quota_bands import band_for
    reset_in(world, 'alt', days)
    world.set_claude(100, 'claude-alt')
    out = route(world)
    actual = out['routing']['quota']['claude-alt']['binding']['band']
    assert actual == band_for(BANDS, days)
    assert actual['high'] == high


def test_hysteresis_uses_the_band_low_and_high(world):
    steps = [(119, {}, 'codex'), (120, {'codex': 'activated'}, 'claude'), (90, {'codex': 'held_by_hysteresis'}, 'claude'),
             (80, {'codex': 'held_by_hysteresis'}, 'claude'), (79.9, {'codex': 'released'}, 'codex')]
    world.set_claude(60)
    for projected, expected, vendor in steps:
        world.codex(projected)
        out = route(world)
        assert active(out) == expected, projected
        assert out['routing']['chosen']['arm']['provider'] == vendor, projected


def test_exhausted_account_is_never_headroom(world):
    world.set_claude(50, 'claude-alt')
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'alt')
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['utilization'] = 100
    world.set_claude(100, 'claudeAgent')
    assert pick(route(world))[0] == 'claudeAgent'


def test_without_bands_nothing_changes(world):
    del world.policy['routing']['quota_bands']
    out = route(world)
    assert 'headroom' not in json.dumps(out['routing']['quota'])
    assert 'quota_substitution' not in out['routing']


@pytest.mark.parametrize('bands, ok', [
    (BANDS, True), ([], False), ('x', False), ([{'min_days_left': 0, 'low': 90, 'high': 110}], True),
    ([{'min_days_left': 1, 'low': 90, 'high': 110}], False),
    ([{'min_days_left': 0, 'exclusive': True, 'low': 90, 'high': 110}], False),
    ([{'min_days_left': 5, 'low': 90, 'high': 110}, {'min_days_left': 5, 'low': 90, 'high': 110}, {'min_days_left': 0, 'low': 90, 'high': 110}], False),
    ([{'min_days_left': 0, 'low': 110, 'high': 90}], False), ([{'min_days_left': 0, 'low': 0, 'high': 90}], False),
    ([{'min_days_left': 0, 'low': 90, 'high': 201}], False), ([{'min_days_left': 0, 'low': 90, 'high': 110, 'extra': 1}], False),
    ([{'min_days_left': 0, 'low': True, 'high': 110}], False)])
def test_check_validates_the_bands(world, bands, ok):
    world.policy['routing']['quota_bands'] = bands
    world.write()
    result, _ = world.cli('check', '--policy', world.tmp / 'policy.json', raw=True)
    assert (result.returncode == 0) is ok, result.stderr + result.stdout


def test_the_shipped_pack_carries_no_bands():
    policy = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
    assert 'quota_bands' not in (policy.get('routing') or {})
