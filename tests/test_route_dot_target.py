"""`route` returns a destination's own T3 target (router contract item 2): golden output, relaunch chains, catalog checks.

The destination here is configured like a local ACP worker pool: instance `pool`, driver `acpRegistry`, model `pool`.
"""
import pytest

from test_route import world  # noqa: F401  (fixture)
from test_route_destinations import capacity, config, entry, failing, nonnull_targets, route, simulate

TARGET = {'providerInstanceId': 'pool', 'driverKind': 'acpRegistry', 'model': 'pool'}
TOKEN = '[pool-decision:<root_decision_id>]'
FLAGS = ('--purpose', 'review', '--inputs', 'github')
PARENT = ('--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt')


def pool_entry(tmp, **extra):
    row = entry(capacity(tmp, 'cap'), name='pool') | {'vendor': 'codex', 'target': dict(TARGET), 'brief_token': TOKEN,
                                                      'max_chain_failures': 2}
    return row | extra


def add_catalog_row(world, **extra):
    world.catalog['data']['providers'].append({'instanceId': 'pool', 'driver': 'acpRegistry', 'enabled': True, 'status': 'ready',
                                               'models': [{'id': 'pool'}, {'id': 'default'}]} | extra)


@pytest.fixture
def pool(world, tmp_path):
    add_catalog_row(world)
    world.cfg = config(tmp_path, pool_entry(tmp_path))
    return world


def record(world, decision_id, *extra):
    return route(world, world.cfg, *FLAGS, *extra, record=decision_id)


def test_golden_destination_target(pool):
    out = route(pool, pool.cfg, *FLAGS)
    assert out['destination'] == 'pool' and out['target'] == TARGET and out['launch_ready'] is True
    assert out['dispatch'] == {'clientRequestId': 'probe', 'mode': 'async', 'brief_first_line': '[pool-decision:probe]',
                               'root_decision_id': 'probe'}
    assert out['routing']['destination']['target_state'] == 'in_catalog'
    assert out['destination_offer']['name'] == 'pool', 'the hand-submit offer stays until the caller no longer needs it'
    assert nonnull_targets(out) == ['/target'], 'the model fallback stays cleared; only the destination target is set'
    assert 'options' not in out['target'], 'effort is null: the target carries no options'


def test_recorded_receipt_replays_with_the_same_target_and_dispatch(pool):
    first = record(pool, 'd1')
    again = record(pool, 'd1')
    assert again['replayed'] is True and again['target'] == first['target'] == TARGET
    assert again['dispatch'] == first['dispatch'] and first['dispatch']['brief_first_line'] == '[pool-decision:d1]'


def test_a_relaunch_keeps_the_root_token_and_two_failures_in_a_chain_pick_a_model(pool):
    record(pool, 'd1')
    second = record(pool, 'd2', '--relaunch-of', 'd1', '--why', 'unavailable', '--reason', 'pool child cancelled by a restart')
    assert second['target'] == TARGET
    assert second['dispatch'] == {'clientRequestId': 'd2', 'mode': 'async', 'brief_first_line': '[pool-decision:d1]', 'root_decision_id': 'd1'}
    third = record(pool, 'd3', '--relaunch-of', 'd2', '--why', 'infra', '--reason', 'pool child failed again')
    assert third.get('destination') is None and 'dispatch' not in third
    assert third['target'] and third['target']['providerInstanceId'] != 'pool', 'a model target'
    block = third['routing']['destination']
    assert block['chosen'] == 'model' and block['fallback']['reasons'] == ['chain_failures']
    assert block['fallback']['chain_failures'] == {'pool': 2}
    assert block['considered'] == [{'name': 'pool', 'state': 'declined_by_chain', 'chain_failures': 2}]


def test_a_model_relaunch_in_the_chain_does_not_count_as_a_destination_failure(pool):
    record(pool, 'd1')
    model = record(pool, 'm2', '--relaunch-of', 'd1', '--why', 'unavailable', '--reason', 'skip once', '--skip-destination', 'unavailable')
    assert model.get('destination') is None
    out = record(pool, 'd3', '--relaunch-of', 'm2', '--why', 'infra', '--reason', 'model child failed')
    assert out['target'] == TARGET and out['dispatch']['root_decision_id'] == 'd1', 'one destination failure so far'


@pytest.mark.parametrize('row', [{'enabled': False}, {'canRunChildTask': False}, {'status': 'error'}, {'driver': 'codex'},
                                 {'models': [{'id': 'default'}]}, {'models': [{'id': 'pool', 'canRunChildTask': False}]}])
def test_a_target_the_catalog_does_not_list_stays_a_hand_submit_offer(world, tmp_path, row):
    add_catalog_row(world, **row)
    out = route(world, config(tmp_path, pool_entry(tmp_path)), *FLAGS)
    assert out['destination'] == 'pool' and out['target'] is None and out['launch_ready'] is False and 'dispatch' not in out
    assert out['routing']['destination']['target_state'] == 'not_in_catalog' and out['destination_offer']['name'] == 'pool'


def test_a_root_id_the_destination_cannot_use_keeps_the_hand_submit_offer(world, tmp_path):
    add_catalog_row(world)
    cfg = config(tmp_path, pool_entry(tmp_path, request_id_pattern=r'[-\w]{8,120}'))
    out = route(world, cfg, *FLAGS, record='a.b:c')
    assert out['target'] is None and 'dispatch' not in out and out['launch_ready'] is False
    assert out['routing']['destination']['target_state'] == 'decision_id_not_accepted'
    ok = route(world, cfg, *FLAGS, record='dec-0001')
    assert ok['target'] == TARGET and ok['dispatch']['brief_first_line'] == '[pool-decision:dec-0001]'


def test_a_destination_without_a_target_is_unchanged(world, tmp_path):
    add_catalog_row(world)
    out = route(world, config(tmp_path, entry(capacity(tmp_path, 'cap'), name='pool')), *FLAGS)
    assert out['target'] is None and 'dispatch' not in out and 'target_state' not in out['routing']['destination']


def test_simulation_never_returns_the_destination_target(pool):
    out = simulate(pool, pool.cfg, *FLAGS)
    assert out['target'] is None and 'dispatch' not in out and out['simulated_target'] != TARGET
    assert out['routing']['destination']['considered'] == [{'name': 'pool', 'state': 'not_probed_in_simulation'}]


@pytest.mark.parametrize('change, message', [
    ({'target': dict(TARGET, options={'effort': 'high'})}, 'target must be'),
    ({'target': {'model': 'pool'}}, 'target must be'),
    ({'brief_token': '[pool-decision]'}, 'brief_token must be'),
    ({'brief_token': '<root_decision_id><root_decision_id>'}, 'brief_token must be'),
    ({'max_chain_failures': 0}, 'max_chain_failures must be'),
    ({'max_chain_failures': True}, 'max_chain_failures must be'),
    ({'request_id_pattern': '('}, 'request_id_pattern must be'),
])
def test_target_fields_are_validated(world, tmp_path, change, message):
    assert message in failing(world, config(tmp_path, pool_entry(tmp_path, **change)), *FLAGS)


def test_target_needs_its_token_and_limit(world, tmp_path):
    row = pool_entry(tmp_path)
    del row['brief_token']
    assert 'brief_token must be' in failing(world, config(tmp_path, row), *FLAGS)


def test_a_destination_target_on_an_unverified_funded_account_is_withheld(pool):
    pool.accounts['accounts']['pool'] = {'billing': 'metered', 'quota_provider': None, 'quota_source': None, 'funding': {'verified_until': None}}
    out = route(pool, pool.cfg, *FLAGS)
    assert out['target'] is None and out['launch_ready'] is False and 'dispatch' not in out
    assert out['waiting']['state'] == 'funding_unverified' and nonnull_targets(out) == []
    pool.accounts['accounts']['pool']['funding']['verified_until'] = '2099-01-01T00:00:00Z'
    assert route(pool, pool.cfg, *FLAGS)['target'] == TARGET


@pytest.mark.parametrize('case', ['not_in_catalog', 'decision_id_not_accepted'])
def test_an_unverified_funded_destination_gets_no_hand_submit_offer_either(world, tmp_path, case):
    add_catalog_row(world, **({'enabled': False} if case == 'not_in_catalog' else {}))
    world.accounts['accounts']['pool'] = {'billing': 'metered', 'quota_provider': None, 'quota_source': None, 'funding': {'verified_until': None}}
    row = pool_entry(tmp_path, **({'request_id_pattern': r'[-\w]{8,120}'} if case != 'not_in_catalog' else {}))
    out = route(world, config(tmp_path, row), *FLAGS, record='a.b:c' if case != 'not_in_catalog' else None)
    assert out['target'] is None and out['launch_ready'] is False and out['waiting']['state'] == 'funding_unverified'

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*map(keys, value.values()), set())
        return set().union(*map(keys, value), set()) if isinstance(value, list) else set()
    assert not keys(out) & {'submit', 'how_to', 'dispatch'}
