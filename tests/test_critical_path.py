"""`route --critical-path`: critical-path work does not wait on quota pacing; discretionary work still does.

The inputs are the forecast-band world of `test_quota_bands` (neutral band values, pinned 2026-10-11 capture). Overshoot is
the projected percent above an account's own band upper limit. Each test changes one input and runs the real CLI.
"""
import json

import pytest

import test_route
from test_quota_bands import ORACLE, BandWorld, add_prepaid, reset_in
from test_quota_substitution import NOW, QuotaWorld, pick
from test_route import DEEPSEEK_FREE, RATE, add_opencode_fixture, codes, deepseek_flags, event
from test_route_destinations import config
from test_route_dot_target import FLAGS, TARGET, add_catalog_row, pool_entry

CRITICAL = '--critical-path'
OP = 'implement.quota-tight'  # every Claude account is a validated candidate; codex is not one


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    return BandWorld(tmp_path)


def over_band(world, alt=135, agent=150, work=140, codex=140):
    """Every account above its band upper limit: claude 130, codex 120. Overshoot: alt 5, work 10, agent 20, codex 20."""
    world.set_claude(alt, 'claude-alt')
    world.set_claude(agent, 'claudeAgent')
    world.set_claude(work, 'claude-work')
    world.codex(codex)


def route(world, *extra):
    return world.route(OP, *extra)


def pacing(out):
    return out['routing']['pacing']


def clear_forecast(world, source_id):
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == source_id)
    source['forecast']['windows'].clear()


# ---- the flag ---------------------------------------------------------------------------------------------------------

def test_discretionary_waits_and_critical_path_takes_the_least_overshoot(world):
    over_band(world)
    waiting = route(world)
    assert waiting['target'] is None and waiting['waiting']['state'] == 'no_forecast_headroom' and 'critical_path' not in waiting.get('launch_facts', {})
    out = route(world, CRITICAL)
    assert pick(out) == ('claude-alt', 'claude-sonnet-5-5')
    assert out['launch_ready'] is True and 'waiting' not in out and out['target']['providerInstanceId'] == 'claude-alt'
    assert out['launch_facts'] == {'schema_version': 1, 'proof_class': 'judged', 'critical_path': True}
    assert pacing(out)['state'] == 'critical_path_exception' and pacing(out)['chosen_overshoot_pp'] == 5
    assert [row['overshoot_pp'] for row in pacing(out)['candidates']] == sorted(row['overshoot_pp'] for row in pacing(out)['candidates'])
    assert all(row['quota_ref'] in out['routing']['quota'] for row in pacing(out)['candidates'])
    assert out['routing']['judgment_required'] is False and 'critical_path_pacing_exception' in codes(out)


def test_the_choice_follows_the_overshoot_not_a_fixed_account(world):
    over_band(world, alt=150, agent=135, work=140)
    assert pick(route(world, CRITICAL)) == ('claudeAgent', 'claude-sonnet-5-5')
    over_band(world, alt=150, agent=140, work=135)
    assert pick(route(world, CRITICAL)) == ('claude-work', 'claude-sonnet-5-5')


def test_overshoot_uses_each_accounts_own_band(world):
    # equal projections: claudeAgent resets in 3 days (band 120) and claude-alt in 6 (band 130), so the wider band overshoots less
    over_band(world, alt=140, agent=140, work=150)
    reset_in(world, 'default', 3)
    assert pick(route(world, CRITICAL)) == ('claude-alt', 'claude-sonnet-5-5')


def test_a_tie_goes_to_the_earlier_reset_then_the_instance_id(world):
    over_band(world, alt=135, agent=150, work=135)
    assert pick(route(world, CRITICAL))[0] == 'claude-work'  # resets 5.2 days out, claude-alt 6.3
    reset_in(world, 'alt', 5.2)
    reset_in(world, 'work', 5.2)
    assert pick(route(world, CRITICAL))[0] == 'claude-alt'


def test_unknown_and_stale_accounts_have_no_overshoot(world):
    over_band(world)
    clear_forecast(world, 'alt')  # the least overshoot, but no forecast
    assert pick(route(world, CRITICAL))[0] == 'claude-work'
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'work')
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['resets_at'] = '2026-10-10T00:00:00Z'  # stale window
    assert pick(route(world, CRITICAL))[0] == 'claudeAgent'


def test_with_no_known_overshoot_critical_path_still_waits_and_says_why(world):
    for provider in world.quota['providers'].values():
        for source in provider.get('sources', [provider]):
            source['forecast']['windows'].clear()
    out = route(world, CRITICAL)
    assert out['target'] is None and out['launch_ready'] is False and 'dispatch' not in out
    assert out['waiting']['critical_path']['satisfied'] is False and pacing(out)['state'] == 'critical_path_no_known_candidate'


def test_an_exhausted_account_is_never_the_exception(world):
    over_band(world)
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'alt')
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['utilization'] = 100
    out = route(world, CRITICAL)
    assert pick(out)[0] == 'claude-work'
    assert next(t for t in out['routing']['trace'] if t.get('instance') == 'claude-alt' and t['arm']['provider'] == 'claude')['removed_by'] == 'quota_exhausted'


def test_avoid_and_independence_filters_still_apply(world):
    over_band(world)
    world.add_directive('avoid-alt', 'avoid', 'account=claude-alt', until='2026-10-12T00:00:00Z')
    assert pick(route(world, CRITICAL))[0] == 'claude-work'


def test_unverified_credit_is_not_the_exception(world):
    over_band(world)
    add_prepaid(world)
    out = route(world, CRITICAL)
    assert pick(out)[0] == 'claude-alt'
    assert next(t for t in out['routing']['trace'] if t.get('instance') == 'claude-api')['removed_by'] == 'funding_unverified'


def test_a_validated_candidate_keeps_its_rank_over_a_smaller_overshoot(world):
    # Quality tiers come first: codex is the validated primary for this op and claude-alt is not, though it overshoots less.
    over_band(world, alt=135, codex=140)
    out = world.route('implement.oracle-bounded', *ORACLE, CRITICAL)
    assert pick(out)[0] == 'codex' and pacing(out)['chosen_overshoot_pp'] == 20


def test_the_flag_changes_nothing_without_bands(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    plain = QuotaWorld(tmp_path)
    plain.codex(130)
    first = route(plain)
    again = route(plain, CRITICAL)
    assert first['target'] == again['target'] and 'pacing' not in again['routing']
    assert again['launch_facts']['critical_path'] is True


# ---- free, prepaid and destination before a wait ----------------------------------------------------------------------

def test_verified_prepaid_precedes_the_critical_path_exception(world):
    over_band(world)
    add_prepaid(world, '2026-10-12T00:00:00Z')
    out = route(world, CRITICAL)
    assert pick(out) == ('claude-api', 'claude-sonnet-5-5') and 'waiting' not in out and pacing(out)['state'] == 'not_needed'


def test_verified_prepaid_precedes_a_wait_on_an_unknown_forecast(world):
    # The ranked best pair is a subscription with no forecast. Its pacing is unknown, so it cannot launch; funded credit can.
    add_prepaid(world, '2026-10-12T00:00:00Z')
    for source in world.quota['providers']['claude']['sources']:
        source['forecast']['windows'].clear()
    out = route(world)
    assert out['target']['providerInstanceId'] == 'claude-api' and 'waiting' not in out and pacing(out)['state'] == 'alternative_before_wait'
    assert pacing(out)['skipped']['pace'] == 'unknown'


def test_a_destination_precedes_both_the_wait_and_the_exception(world, tmp_path):
    over_band(world)
    add_catalog_row(world)
    cfg = config(tmp_path, pool_entry(tmp_path))
    out = world.route('review.audit', '--destinations', cfg, *FLAGS, CRITICAL)
    assert out['destination'] == 'pool' and out['target'] == TARGET and 'waiting' not in out
    assert out['routing']['chosen']['target'] is None, 'the model fallback stays cleared'


# ---- what a wait says -------------------------------------------------------------------------------------------------

def test_a_wait_reports_its_semantics_and_what_it_considered(world):
    over_band(world)
    add_prepaid(world)
    out = route(world)
    wait = out['waiting']
    assert wait['dispatches'] is False and 'schedule' in wait['retry'] and wait['retry_after'] == '2026-10-13T22:42:47-05:00'
    considered = wait['alternatives_considered']
    assert considered['prepaid'] == [{'instance': 'claude-api', 'arm_model': 'claude-sonnet-5-5', 'state': 'funding_unverified'},
                                     {'instance': 'claude-api-alt', 'arm_model': 'claude-sonnet-5-5', 'state': 'billing_not_subscription'}]
    assert considered['destination']['chosen'] == 'model' and considered['free_routes'][0]['model'] == DEEPSEEK_FREE
    assert 'critical_path' not in wait


def test_the_brief_output_carries_the_flag_and_the_pacing_record(world):
    over_band(world)
    out = world.route(OP, CRITICAL, '--brief')
    assert out['critical_path'] is True and out['pacing']['state'] == 'critical_path_exception' and out['launch_ready'] is True


# ---- receipts, replay, simulation, malformed input ---------------------------------------------------------------------

def test_a_recorded_critical_decision_replays_and_a_changed_flag_is_a_changed_input(world):
    over_band(world)
    first = world.route(OP, CRITICAL, record='crit-1')
    again = world.route(OP, CRITICAL, record='crit-1')
    assert again['replayed'] is True and again['target'] == first['target'] and again['routing']['pacing'] == first['routing']['pacing']
    result, _ = world.cli('route', OP, *world.args(), '--proof-class', 'judged', '--decision-id', 'crit-1', '--record',
                          '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt')
    assert result.returncode != 0


def test_a_receipt_without_the_field_replays_without_it(world):
    over_band(world)
    first = world.route(OP, record='plain-1')
    again = world.route(OP, record='plain-1')
    assert again['replayed'] is True and 'critical_path' not in again['launch_facts'] and 'pacing' not in again['routing']
    assert first['waiting'] == again['waiting'] and 'critical_path' not in json.dumps(first['launch_facts'])


def test_a_simulation_never_launches(world):
    over_band(world)
    result, out = world.cli('route', OP, *world.args(), '--proof-class', 'judged', CRITICAL, '--decision-id', 'sim', '--now', '2026-10-11T00:25:00Z', raw=True)
    assert result.returncode == 0, result.stderr
    assert out['target'] is None and out['launch_ready'] is False and out['simulated_target']['providerInstanceId'] == 'claude-alt'
    assert 'dispatch' not in out and not world.receipts.exists()


@pytest.mark.parametrize('extra', [['--critical-path=yes'], ['--critical-path=false'], ['--critical-path', 'true']])
def test_a_value_on_the_flag_is_refused(world, extra):
    result, _ = world.cli('route', OP, *world.args(), '--proof-class', 'judged', '--decision-id', 'bad', *extra)
    assert result.returncode != 0 and ('critical-path' in result.stderr or 'unrecognized' in result.stderr)


def test_the_flag_applies_to_route_only(world):
    result, _ = world.cli('resolve', OP, *world.args(), '--proof-class', 'judged', '--decision-id', 'bad', CRITICAL)
    assert result.returncode != 0 and 'route only' in result.stderr


# ---- the free route's cooldown expires --------------------------------------------------------------------------------

def wait_at(world, clock):
    """A wait at `clock`: the quota capture is stale by then, so no subscription can launch."""
    result, out = world.cli('route', 'implement.oracle-bounded', *world.args(), *ORACLE, '--decision-id', 'cool', evaluation_time=clock)
    assert result.returncode == 0, result.stderr
    return out


def test_a_rate_limit_cooldown_expires_and_is_reevaluated_from_fresh_events(world):
    add_opencode_fixture(world)
    world.events = [event('opencode', RATE, at=at) for at in ('2026-10-16T00:10:00.000Z', '2026-10-16T00:15:00.000Z', '2026-10-16T00:20:00.000Z')]
    cooling = wait_at(world, '2026-10-16T00:30:00Z')['waiting']['alternatives_considered']['free_routes'][0]
    assert cooling['qualified'] is True and cooling['availability'] == {'state': 'cooling_down', 'retry_after': '2026-10-16T01:10:00+00:00', 'events': 3}
    assert '--experimental-route' in cooling['opt_in'] and f'model={DEEPSEEK_FREE}' in cooling['opt_in']
    later = wait_at(world, '2026-10-16T01:11:00Z')['waiting']['alternatives_considered']['free_routes'][0]
    assert later['availability'] == {'state': 'available', 'retry_after': None, 'events': 2}


def test_an_unqualified_operation_is_reported_not_offered(world):
    add_opencode_fixture(world)
    result, out = world.cli('route', 'implement.quota-tight', *world.args(), '--proof-class', 'judged', '--decision-id', 'cool', evaluation_time='2026-10-16T00:30:00Z')
    assert result.returncode == 0, result.stderr
    row = out['waiting']['alternatives_considered']['free_routes'][0]
    assert row['qualified'] is False and row['refusal'] == 'operation_not_qualified' and 'opt_in' not in row


def test_an_exact_override_onto_a_cooling_account_is_kept_and_reports_the_retry_time(world):
    add_opencode_fixture(world)
    world.events = [event('opencode', RATE, at=at) for at in ('2026-10-16T00:10:00.000Z', '2026-10-16T00:15:00.000Z', '2026-10-16T00:20:00.000Z')]
    result, out = world.cli('route', 'implement.oracle-bounded', *world.args(), *ORACLE, '--decision-id', 'ov', *deepseek_flags(classification='public'),
                            evaluation_time='2026-10-16T00:30:00Z')
    assert result.returncode == 0, result.stderr
    assert out['target']['providerInstanceId'] == 'opencode' and out['launch_ready'] is True
    reason = next(r for r in out['routing']['judgment_reasons'] if r['code'] == 'final_account_cooling_down')
    assert reason['informational'] is True and reason['detail']['retry_after'] == '2026-10-16T01:10:00+00:00'


def cool(world, *times):
    add_opencode_fixture(world)
    world.events = [event('opencode', RATE, at=at) for at in times]


def free_row(world, clock):
    return wait_at(world, clock)['waiting']['alternatives_considered']['free_routes'][0]['availability']


def test_cooldown_orders_events_by_instant_not_by_text(world):
    # 01:05+01:00 is 00:05Z, the oldest; text order would pick 00:10Z and report 01:10Z
    cool(world, '2026-10-16T00:10:00.000Z', '2026-10-16T01:05:00.000+01:00', '2026-10-16T00:20:00.000Z')
    assert free_row(world, '2026-10-16T00:30:00Z') == {'state': 'cooling_down', 'retry_after': '2026-10-16T01:05:00+00:00', 'events': 3}


def test_cooldown_with_more_events_than_the_threshold_waits_for_enough_to_leave(world):
    cool(world, *(f'2026-10-16T00:{m}:00.000Z' for m in ('05', '10', '15', '20')))  # 4 events, threshold 3: the 2nd oldest must leave
    assert free_row(world, '2026-10-16T00:30:00Z')['retry_after'] == '2026-10-16T01:10:00+00:00'


def test_cooldown_ends_just_after_retry_after_not_at_it(world):
    cool(world, '2026-10-16T00:10:00.000Z', '2026-10-16T00:15:00.000Z', '2026-10-16T00:20:00.000Z')
    assert free_row(world, '2026-10-16T01:10:00Z')['state'] == 'cooling_down'
    assert free_row(world, '2026-10-16T01:10:01Z')['state'] == 'available'


def test_cooldown_is_unknown_without_telemetry(world):
    add_opencode_fixture(world)
    world.with_db = False
    assert free_row(world, '2026-10-16T00:30:00Z') == {'state': 'unknown', 'retry_after': None, 'events': 0}


def test_a_prefer_directive_outranks_a_smaller_overshoot(world):
    over_band(world)
    world.add_directive('like-work', 'prefer', 'account=claude-work', until='2026-10-12T00:00:00Z')
    assert pick(route(world, CRITICAL))[0] == 'claude-work'
