"""Generic destinations cannot submit governed models, including historical offers."""
import copy
import json
import runpy
import subprocess
import sys
from datetime import datetime

import pytest

from test_route import REPO, DEEPSEEK_FREE, ORACLE_OP, add_opencode_fixture, deepseek_flags, world  # noqa: F401
from test_route_authorization import CLOCK, destination, nested_nodes, run
import directives
import policy_decisions as pd


def no_submission(out):
    assert out['launch_ready'] is False and out['target'] is None
    assert out['free_route']['state'] == 'refused'
    for node in nested_nodes(out):
        assert not {'submit', 'how_to', 'simulated_target'} & node.keys()
        assert node.get('target') is None
        assert node.get('dispatch') is None
        if 'launch_ready' in node:
            assert node['launch_ready'] is False
        if 'launch_readiness_basis' in node:
            assert 'submit by hand' not in node['launch_readiness_basis']


@pytest.mark.parametrize('brief', [False, True])
@pytest.mark.parametrize('overridden', [False, True])
@pytest.mark.parametrize('catalog', ['listed', 'model-missing', 'provider-missing'])
def test_chosen_identity_survives_catalog_failure(world, brief, overridden, catalog):
    add_opencode_fixture(world)
    flags = deepseek_flags() if overridden else []
    if catalog == 'model-missing':
        world.catalog['data']['providers'][-1]['models'] = []
    elif catalog == 'provider-missing':
        world.catalog['data']['providers'].pop()
    out = run(world, 'route', 'fanout.explore', *world.args(), *destination(world, DEEPSEEK_FREE),
              *flags, *(['--brief'] if brief else []))
    no_submission(out)
    assert out['free_route']['targets'] == [
        {'provider': 'opencode', 'model': DEEPSEEK_FREE, 'account': 'opencode', 'effort': None}]
    assert out['free_route']['refusal'] == 'governed_destination_unsupported'


@pytest.mark.parametrize('effort', ['banana', 'high'])
@pytest.mark.parametrize('surface', [False, True])
def test_generic_destination_never_infers_requested_effort(world, effort, surface):
    add_opencode_fixture(world)
    if not surface:
        world.catalog['data']['providers'][-1]['models'][0]['options'] = []
    flags = deepseek_flags()
    flags[flags.index('effort=high')] = 'effort=' + effort
    out = run(world, 'route', 'fanout.explore', *world.args(), *flags, *destination(world, DEEPSEEK_FREE))
    no_submission(out)
    assert out['free_route']['refusal'] == 'governed_destination_unsupported'
    assert out['free_route']['targets'][0]['effort'] is None


@pytest.mark.parametrize('brief', [False, True])
def test_rejected_request_id_cannot_fall_back_to_manual_submission(world, brief):
    add_opencode_fixture(world)
    dest_flags = destination(world, DEEPSEEK_FREE)
    path = world.tmp / 'destinations.json'
    config = json.loads(path.read_text())
    config['destinations'][0]['request_id_pattern'] = 'never-matches'
    path.write_text(json.dumps(config))
    out = run(world, 'route', 'fanout.explore', *world.args(), *deepseek_flags(),
              *dest_flags, '--decision-id', 'refuse-manual', *(['--brief'] if brief else []))
    no_submission(out)
    assert out['free_route']['refusal'] == 'governed_destination_unsupported'


@pytest.fixture(scope='module')
def prior_tool(tmp_path_factory):
    """The real pre-narrowing CLI writes an authentic formerly approved receipt."""
    archive = tmp_path_factory.mktemp('prior-destination-cli')
    data = subprocess.check_output(['git', 'archive', '7c8ea353bd4b45326e7d54d593dc9d36cbcfe5df',
                                    'tools/model-policy-ops'], cwd=REPO)
    subprocess.run(['tar', '-x', '-C', str(archive)], input=data, check=True)
    return archive / 'tools/model-policy-ops/model-policy-ops'


def write_prior_policy(world):
    """The archived CLI requires the policy contract and flags that existed with it."""
    policy = copy.deepcopy(world.policy)
    del policy['free_routes']
    legacy = subprocess.check_output(['git', 'show', 'c2414ded:data/model-choice-policy/operating-points.json'], cwd=REPO)
    route = json.loads(legacy)['experimental_free_routes'][0]
    route['qualified_operations'] = []
    route['t3_transport']['scope'] = 'fanout.explore_or_resolve_no_op_exact_overrides_public_only'
    policy['experimental_free_routes'] = [route]
    (world.tmp / 'policy.json').write_text(json.dumps(policy))


def prior_flags(flags, classification='public'):
    """The archived CLI took an experimental flag and a data classification; the current CLI takes neither."""
    return [*flags, '--experimental-route', '--data-classification', classification]


@pytest.mark.parametrize('brief', [False, True])
@pytest.mark.parametrize('race', [False, True])
@pytest.mark.parametrize('revoke', [False, True])
def test_authentic_public_offer_cannot_replay_submission(world, prior_tool, monkeypatch, capsys,
                                                       brief, race, revoke):
    add_opencode_fixture(world)
    flags = ['route', 'fanout.explore', *world.args(), *destination(world, DEEPSEEK_FREE),
             *deepseek_flags(), '--record', '--decision-id', 'prior-offer',
             '--parent-model', 'parent', '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    world.write()
    write_prior_policy(world)
    process = subprocess.run([sys.executable, str(prior_tool), *map(str, prior_flags(flags)), '--no-user-policy'],
                             text=True, capture_output=True, cwd=REPO)
    assert process.returncode == 0, process.stderr
    original = json.loads(process.stdout)
    assert original['launch_ready'] and original['dispatch']['mode'] == 'async'
    assert original['destination_offer']['submit'] and original['destination_offer']['how_to']
    before = world.receipts.read_bytes()
    if revoke:
        world.policy['free_routes'] = []
    world.write()
    if race:
        # Only the early lookup misses: append_receipt still uses the real locked ledger.
        monkeypatch.setattr(pd, 'find_receipt', lambda *args: None)
    else:
        def no_live_reads(*args):
            raise AssertionError('early replay read a runtime source')
        monkeypatch.setattr(pd, 'read_source', no_live_reads)
    monkeypatch.setattr(directives, 'now', lambda: datetime.fromisoformat(CLOCK.replace('Z', '+00:00')))
    monkeypatch.setattr(sys, 'argv', [str(REPO / 'tools/model-policy-ops/model-policy-ops'),
                                    *map(str, flags), '--no-user-policy', *(['--brief'] if brief else [])])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(REPO / 'tools/model-policy-ops/model-policy-ops'), run_name='__main__')
    captured = capsys.readouterr()
    assert exited.value.code == 0, captured.err
    out = json.loads(captured.out)
    no_submission(out)
    assert out['replayed'] is True
    assert out['free_route']['refusal'] == 'free_route_receipt_not_currently_authorized'
    assert world.receipts.read_bytes() == before


@pytest.mark.parametrize('replay', [False, True])
def test_nested_manual_submission_is_removed(world, replay):
    add_opencode_fixture(world)
    out = run(world, 'resolve', ORACLE_OP, *world.args(), '--proof-class', 'oracle', *deepseek_flags())
    manual = {'submit': {'command': 'worker submit'}, 'how_to': 'how-to.md',
              'launch_ready': True, 'launch_readiness_basis': 'submit by hand',
              'dispatch': {'mode': 'async'}, 'simulated_target': copy.deepcopy(out['target'])}
    out['alternatives'].append({'nested': [manual]})
    revoked = copy.deepcopy(world.policy)
    revoked['free_routes'][0]['qualified_operations'] = []
    pd.apply_free_route_policy(out, revoked, datetime.fromisoformat(CLOCK.replace('Z', '+00:00')), replay=replay)
    no_submission(out)


def test_refused_identity_survives_replay_without_catalog_or_config(world):
    add_opencode_fixture(world)
    out = run(world, 'route', 'fanout.explore', *world.args(), *destination(world, DEEPSEEK_FREE))
    no_submission(out)
    pd.apply_free_route_policy(out, {'free_routes': []},
                                       datetime.fromisoformat(CLOCK.replace('Z', '+00:00')), replay=True)
    no_submission(out)
    assert out['free_route']['targets'][0]['model'] == DEEPSEEK_FREE


def test_legacy_catalog_miss_replay_uses_structured_config_identity(world, prior_tool):
    add_opencode_fixture(world)
    world.catalog['data']['providers'][-1]['models'] = []
    flags = ['route', 'fanout.explore', *world.args(), *destination(world, DEEPSEEK_FREE),
             '--record', '--decision-id', 'old-catalog-miss',
             '--parent-model', 'parent', '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    world.write()
    write_prior_policy(world)
    process = subprocess.run([sys.executable, str(prior_tool), *map(str, prior_flags(flags)), '--no-user-policy'],
                             text=True, capture_output=True, cwd=REPO)
    assert process.returncode == 0, process.stderr
    original = json.loads(process.stdout)
    assert 'free_route' not in original and original['target'] is None
    assert original['destination_offer']['submit']
    before = world.receipts.read_bytes()
    world.policy['free_routes'] = []
    out = run(world, *flags)
    no_submission(out)
    assert out['replayed'] and world.receipts.read_bytes() == before
