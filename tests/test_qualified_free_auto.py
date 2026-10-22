"""A qualified free route is a routed candidate: no override and no flag, but only inside its declared scope.

The scope is the normal `free_routes` declaration: the operation, the proof class, the effort, public inputs that the
caller asserts, and current evidence. Each test changes one input and runs the real CLI. No model is called.
"""
import json

import pytest

import test_route
from test_critical_path import CRITICAL, over_band, pacing
from test_quota_bands import ORACLE, BandWorld, add_prepaid
from test_quota_substitution import NOW
from test_route import DEEPSEEK_FREE, RATE, add_opencode_fixture, all_target_values, event
from test_route_destinations import config
from test_route_dot_target import FLAGS, TARGET, add_catalog_row, pool_entry

OP = 'implement.oracle-bounded'
PUBLIC = ('--data-classification', 'public')
LATER = '2026-10-16T01:11:00Z'  # after the pinned capture has gone stale, inside the route's evidence window
FREE_TARGET = {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE, 'options': {'variant': 'high'}}
THREE = ('2026-10-16T00:10:00.000Z', '2026-10-16T00:15:00.000Z', '2026-10-16T00:20:00.000Z')


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    found = BandWorld(tmp_path)
    add_opencode_fixture(found)
    return found


def declared(world):
    return world.policy['free_routes'][0]


def current_at_pinned_capture(world):
    """The route's evidence is dated 2026-10-15; the quota capture is 2026-10-11. Date the evidence earlier so one clock serves both."""
    route = declared(world)
    route['status'] = 'free_as_of_2026-10-10'
    for item in route['evidence']:
        item['accessed'] = '2026-10-10'


def route_at(world, clock, *extra, op=OP, proof=ORACLE, record=None):
    flags = ['--decision-id', record or 'auto', *(['--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt']
                                                   if record else [])]
    result, out = world.cli('route', op, *world.args(), *proof, *flags, *extra, evaluation_time=clock)
    assert result.returncode == 0, result.stderr
    return out


def selected(out):
    return out['target'] == FREE_TARGET and out['launch_ready'] is True and 'waiting' not in out


def not_dispatched(out):
    return out['target'] is None and out['launch_ready'] is False


# ---- selection before a wait and before the critical-path exception -----------------------------------------------------

def test_a_qualified_public_oracle_task_takes_the_free_route_instead_of_waiting(world):
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out)
    chosen = out['routing']['chosen']
    assert chosen['arm'] == {'provider': 'opencode', 'model': DEEPSEEK_FREE, 'effort': 'high'} and chosen['basis'] == 'qualified_free'
    assert chosen['selection_basis'] == 'qualified_free_route' and chosen['evidence'] == 'qualified'
    assert out['routing']['final']['billing'] == 'free' and out['routing']['final']['overridden'] is False
    assert out['request']['overrides'] == {} and 'experimental_route' not in out['request']
    assert out['data_policy']['state'] == 'allowed_public_normal' and out['data_policy']['refusal'] is None
    assert out['routing']['judgment_required'] is False




def test_the_free_route_precedes_the_critical_path_exception(world):
    current_at_pinned_capture(world)
    over_band(world)
    out = route_at(world, NOW, *PUBLIC, CRITICAL)
    assert selected(out) and pacing(out) == {'state': 'not_needed', 'critical_path': True}
    assert 'critical_path_pacing_exception' not in {r['code'] for r in out['routing']['judgment_reasons']}


def test_the_critical_path_exception_still_serves_a_task_that_is_not_qualified(world):
    current_at_pinned_capture(world)
    over_band(world)
    out = route_at(world, NOW, '--data-classification', 'sensitive', CRITICAL)
    assert pacing(out)['state'] == 'critical_path_exception' and out['target']['providerInstanceId'] != 'opencode'


def test_a_qualified_normal_route_ranks_before_a_subscription_with_headroom(world):
    current_at_pinned_capture(world)
    world.codex(100)
    out = route_at(world, NOW, *PUBLIC)
    assert out['target']['providerInstanceId'] == 'opencode'


def test_docs_lookup_is_qualified_too(world):
    out = route_at(world, LATER, *PUBLIC, op='docs.lookup')
    assert out['routing']['chosen']['basis'] == 'qualified_free' and out['launch_ready'] is True
    assert route_at(world, LATER, op='docs.lookup')['routing']['chosen']['basis'] != 'qualified_free'


# ---- dot and verified prepaid come first --------------------------------------------------------------------------------

def test_a_destination_precedes_the_free_route(world, tmp_path):
    add_catalog_row(world)
    cfg = config(tmp_path, pool_entry(tmp_path))
    out = route_at(world, LATER, '--destinations', cfg, *FLAGS, *PUBLIC)
    assert out['destination'] == 'pool' and out['target'] == TARGET and out['routing']['chosen']['basis'] == 'qualified_free'
    assert out['routing']['chosen']['target'] is None, 'the model fallback stays cleared'
    assert FREE_TARGET not in all_target_values(out)


def allow_prepaid(world, verified_until):
    """The metered account of `add_prepaid`, with an owner directive that is current at LATER."""
    add_prepaid(world, verified_until)
    world.add_directive('allow-prepaid-later', 'allow-metered', 'account=claude-api', until='2026-10-17T00:00:00Z', recorded_at='2026-10-16T00:00:00+00:00')


def test_verified_prepaid_is_considered_and_serves_when_the_free_route_cools(world):
    allow_prepaid(world, '2026-10-17T00:00:00Z')
    cool(world)
    out = route_at(world, '2026-10-16T00:30:00Z', *PUBLIC)
    assert out['target']['providerInstanceId'] == 'claude-api' and 'waiting' not in out
    assert next(t for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')['removed_by'] == 'cooling_down'
    free = route_at(world, '2026-10-16T01:11:00Z', *PUBLIC)
    assert selected(free)
    assert next(t for t in free['routing']['trace'] if t.get('instance') == 'claude-api')['removed_by'] is None, 'verified prepaid stays a ranked alternative'


def test_unverified_prepaid_does_not_stop_the_free_route(world):
    allow_prepaid(world, None)
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out)
    assert next(t for t in out['routing']['trace'] if t.get('instance') == 'claude-api')['removed_by'] == 'funding_unverified'


# ---- the declared scope --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('extra', [['--data-classification', 'sensitive'], [*PUBLIC, '--sensitive']])
def test_sensitive_or_unclassified_inputs_never_reach_the_free_route(world, extra):
    out = route_at(world, LATER, *extra)
    assert not_dispatched(out) and DEEPSEEK_FREE not in json.dumps(out['routing'].get('chosen'))
    assert all(t['arm']['provider'] != 'opencode' for t in out['routing']['trace'])


def test_an_operation_without_a_qualification_is_not_offered_the_route(world):
    out = route_at(world, LATER, *PUBLIC, op='implement.quota-tight', proof=('--proof-class', 'judged'))
    assert all(t['arm']['provider'] != 'opencode' for t in out['routing']['trace'])


def test_a_declaration_with_no_qualified_operation_is_revoked(world):
    declared(world)['operations'] = []
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out) and all(t['arm']['provider'] != 'opencode' for t in out['routing']['trace'])


def test_the_effort_must_be_on_the_live_catalog(world):
    world.catalog['data']['providers'][-1]['models'][0]['options'][0]['values'] = [{'id': 'low'}, {'id': 'max'}]
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out)
    row = next(t for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')
    assert row['removed_by'] == 'not_runnable' and row['detail']['state'] == 'unsupported_effort'


def test_a_missing_catalog_account_removes_the_route(world):
    world.catalog['data']['providers'].pop()
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out)
    assert next(t for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')['removed_by'] == 'no_catalog_account'


@pytest.mark.parametrize('change', [
    lambda route: route.update(status='free_as_of_2026-10-17'),
], ids=['evidence-from-the-future'])
def test_stale_or_revoked_evidence_never_reaches_the_free_route(world, change):
    change(declared(world))
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out) and all(t['arm']['provider'] != 'opencode' for t in out['routing']['trace'])


def test_an_owner_avoid_directive_removes_the_free_route(world):
    world.add_directive('avoid-free', 'avoid', f'model={DEEPSEEK_FREE}', until='2026-10-17T00:00:00Z', recorded_at='2026-10-16T00:00:00+00:00')
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out)
    assert next(t for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')['removed_by'] == 'avoid_directive'


def test_checker_independence_removes_a_same_vendor_free_route(world):
    out = route_at(world, LATER, *PUBLIC, '--maker-model', f'opencode:{DEEPSEEK_FREE}', '--independence', 'vendor')
    row = next(t for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')
    assert row['removed_by'] == 'independence' and out['target'] != FREE_TARGET


# ---- the cooldown expires and is reevaluated ------------------------------------------------------------------------------

def cool(world):
    world.events = [event('opencode', RATE, at=at) for at in THREE]


def test_a_cooling_route_waits_and_the_same_route_is_chosen_after_the_cooldown(world):
    cool(world)
    cooling = route_at(world, '2026-10-16T00:30:00Z', *PUBLIC)
    assert not_dispatched(cooling)
    row = next(t for t in cooling['routing']['trace'] if t['arm']['provider'] == 'opencode')
    assert row['removed_by'] == 'cooling_down' and row['detail']['retry_after'] == '2026-10-16T01:10:00+00:00'
    assert not_dispatched(route_at(world, '2026-10-16T01:10:00Z', *PUBLIC))
    assert selected(route_at(world, '2026-10-16T01:10:01Z', *PUBLIC))


def test_missing_telemetry_is_unknown_and_reported(world):
    world.with_db = False
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out) and out['routing']['chosen']['availability'] == 'unknown'
    row = next(t for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')
    assert row['availability']['class'] == 'unknown' and row['availability']['reason']


# ---- receipts, replay, simulation, the manual path -------------------------------------------------------------------------

def test_a_recorded_route_replays_and_is_refused_once_the_declaration_is_revoked(world):
    first = route_at(world, LATER, *PUBLIC, record='auto-1')
    assert selected(first)
    assert route_at(world, LATER, *PUBLIC, record='auto-1')['replayed'] is True
    declared(world)['operations'] = []
    revoked = route_at(world, LATER, *PUBLIC, record='auto-1')
    assert revoked['target'] is None and revoked['launch_ready'] is False
    assert revoked['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'


def test_a_simulation_never_launches_the_free_route(world):
    result, out = world.cli('route', OP, *world.args(), *ORACLE, *PUBLIC, '--decision-id', 'sim', '--now', LATER, raw=True)
    assert result.returncode == 0, result.stderr
    assert out['target'] is None and out['launch_ready'] is False and out['simulated_target'] == FREE_TARGET and 'dispatch' not in out


def test_the_manual_free_route_needs_its_exact_overrides_and_a_reason(world):
    result, out = world.cli('route', OP, *world.args(), *ORACLE, '--decision-id', 'manual', *test_route.deepseek_flags(classification='public'),
                            evaluation_time=LATER)
    assert result.returncode == 0, result.stderr
    assert out['target'] == FREE_TARGET and out['routing']['final']['overridden'] is True
    assert 'experimental_route' not in out['request']
    result, _ = world.cli('route', OP, *world.args(), *ORACLE, *PUBLIC, '--decision-id', 'unreasoned', '--override', 'provider=opencode',
                          '--override', f'model={DEEPSEEK_FREE}', '--override', 'effort=high', '--override', 'account=opencode',
                          evaluation_time=LATER)
    assert result.returncode == 1 and 'require --reason' in result.stderr


def test_only_the_declared_account_can_carry_the_free_route(world):
    second = json.loads(json.dumps(world.catalog['data']['providers'][-1]))
    second['instanceId'] = 'opencode-alt'
    world.catalog['data']['providers'].append(second)
    world.accounts['accounts']['opencode-alt'] = {'billing': 'subscription', 'quota_provider': None, 'quota_source': None}
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out)
    assert not any(t.get('instance') == 'opencode-alt' for t in out['routing']['trace'] if t['arm']['provider'] == 'opencode')


def test_a_receipt_rebound_to_another_account_is_refused_on_replay(world):
    first = route_at(world, LATER, *PUBLIC, record='bind-1')
    assert selected(first)
    receipts = world.receipts
    row = json.loads(receipts.read_text().splitlines()[-1])
    text = json.dumps(row)
    for key in ('providerInstanceId', 'instance', 'account'):  # consistent on the other account: target, chosen pair, final pair
        text = text.replace(f'"{key}": "opencode"', f'"{key}": "opencode-alt"')
    assert text.count('opencode-alt') >= 3
    receipts.write_text(text + '\n')
    again = route_at(world, LATER, *PUBLIC, record='bind-1')
    assert again['target'] is None and again['launch_ready'] is False
    assert FREE_TARGET['model'] not in json.dumps(all_target_values(again))


def test_the_free_tuple_is_not_withheld_for_its_account_map_funding(world):
    world.accounts['accounts']['opencode'] = {'billing': 'metered', 'quota_provider': None, 'quota_source': None,
                                              'funding': {'verified_until': None}}
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out) and out['routing']['final']['billing'] == 'free'
