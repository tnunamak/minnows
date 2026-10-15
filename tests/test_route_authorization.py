"""Actual dispatch authorization and historical replay regression tests (offline CLI)."""
import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from test_route import (REPO, DEEPSEEK_FREE, add_opencode_fixture, all_target_values,
                        deepseek_flags, world)  # noqa: F401

CLOCK = '2026-10-15T00:30:00Z'
SIBLING = 'opencode/muse-spark-1.3-contributor-free'


def destination(world, model, account='opencode', driver=None):
    target = {'providerInstanceId': account, 'model': model}
    if driver:
        target['driverKind'] = driver
    path = world.tmp / 'destinations.json'
    path.write_text(json.dumps({'schema_version': 1, 'destinations': [{
        'name': 'public-worker', 'purposes': ['research'],
        'capacity_command': ['/usr/bin/printf', '{"available":true,"reason":"ok"}'],
        'timeout_seconds': 5, 'how_to': 'how-to.md', 'submit': {'command': 'worker submit'},
        'target': target, 'brief_token': 'Research <root_decision_id>', 'max_chain_failures': 1,
    }]}))
    return ['--destinations', path, '--purpose', 'research', '--inputs', 'github']


def sibling(world):
    world.catalog['data']['providers'][-1]['models'].append({
        'slug': SIBLING, 'options': [{'id': 'variant', 'values': [{'id': 'high'}]}]})


def run(world, *args):
    process, result = world.cli(*args, evaluation_time=CLOCK)
    assert process.returncode == 0, process.stderr
    return result


def refused(out):
    assert out['launch_ready'] is False and out['target'] is None
    assert out['data_policy']['state'] == 'refused'
    assert all(target is None for target in all_target_values(out))
    assert 'dispatch' not in out


@pytest.mark.parametrize('case', ['unclassified', 'sensitive', 'expired', 'allowed-to-sibling', 'ordinary-to-sibling'])
def test_r1_actual_destination_requires_its_own_authorization(world, case):
    add_opencode_fixture(world)
    sibling(world)
    model = SIBLING if case.endswith('sibling') else DEEPSEEK_FREE
    flags = deepseek_flags(classification='public') if case == 'allowed-to-sibling' else []
    if case == 'sensitive':
        flags += ['--data-classification', 'sensitive']
    if case == 'expired':
        world.policy['experimental_free_routes'][0]['expires_at_utc'] = '2026-10-14T00:00:00Z'
    op = 'implement.standard' if case == 'ordinary-to-sibling' else 'fanout.explore'
    out = run(world, 'route', op, *world.args(), '--proof-class', 'judged',
              *destination(world, model), *flags)
    refused(out)


@pytest.mark.parametrize('change', ['removed', 'account', 'model', 'provider', 'expired', 'expiry-boundary'])
def test_r2_replay_requires_current_exact_authorization(world, change):
    add_opencode_fixture(world)
    # Record against the real clock so expiry-boundary replay is tested without a simulation.
    now = datetime.now(timezone.utc)
    route = world.policy['experimental_free_routes'][0]
    route['expires_at_utc'] = (now + timedelta(days=1)).isoformat()
    flags = ['resolve', '--no-op', *world.args(), *deepseek_flags(classification='public'),
             '--record', '--decision-id', 'current-exact', '--parent-model', 'parent',
             '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    first = run(world, *flags)
    assert first['launch_ready'] is True
    assert run(world, *flags) == first | {'replayed': True}
    if change == 'removed':
        world.policy['experimental_free_routes'] = []
    elif change in ('account', 'model', 'provider'):
        route[change] = 'revoked-' + route[change]
    elif change == 'expiry-boundary':
        route['expires_at_utc'] = now.isoformat()
    else:
        route['expires_at_utc'] = '2026-10-14T00:00:00Z'
    out = run(world, *flags)
    assert out['replayed'] is True
    refused(out)
    assert out['data_policy']['refusal'] == 'experimental_route_receipt_not_currently_authorized'


def test_r2_genuine_legacy_sibling_receipt_is_refused(world):
    add_opencode_fixture(world)
    sibling(world)
    world.write()
    archive = world.tmp / 'historical'
    archive.mkdir()
    data = subprocess.check_output(['git', 'archive', 'f25a92c', 'tools/model-policy-ops'], cwd=REPO)
    subprocess.run(['tar', '-x', '-C', str(archive)], input=data, check=True)
    flags = ['resolve', '--no-op', *world.args(), '--override', 'provider=opencode', '--override',
             f'model={SIBLING}', '--override', 'account=opencode', '--override', 'effort=high',
             '--reason', 'Legacy public fixture', '--record', '--decision-id', 'legacy-sibling',
             '--parent-model', 'parent', '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    old = subprocess.run([sys.executable, str(archive / 'tools/model-policy-ops/model-policy-ops'),
                          *map(str, flags)], text=True, capture_output=True, cwd=REPO)
    assert old.returncode == 0, old.stderr
    original = json.loads(old.stdout)
    assert original['launch_ready'] is True and 'data_policy' not in original
    assert original['target']['model'] == SIBLING
    world.policy['experimental_free_routes'] = []
    out = run(world, *flags)
    assert out['replayed'] is True
    refused(out)


@pytest.mark.parametrize('command', ['route', 'resolve'])
@pytest.mark.parametrize('option', ['variant', 'effort', 'reasoningEffort', 'thinking'])
def test_r3_unrelated_effort_surface_matches_baseline(world, command, option):
    world.catalog['data']['providers'].append({'driver': 'other', 'instanceId': 'other', 'models': [{
        'slug': 'other/unrelated', 'options': [{'id': option, 'values': [{'id': 'high'}]}]}]})
    arm = {'provider': 'other', 'model': 'other/unrelated', 'effort': 'high'}
    flags = [part for key, value in arm.items() for part in ('--override', f'{key}={value}')]
    flags += ['--override', 'account=other', '--reason', 'Unrelated surface parity']
    out = run(world, command, *(['fanout.explore'] if command == 'route' else ['--no-op']), *world.args(), *flags)
    # Use actual original source as the compatibility oracle, not a copy of the implementation.
    import types
    baseline = types.ModuleType('baseline_runtime_sources')
    source = subprocess.check_output(['git', 'show', '5f09f01:tools/model-policy-ops/lib/runtime_sources.py'], cwd=REPO, text=True)
    exec(compile(source, 'baseline_runtime_sources.py', 'exec'), baseline.__dict__)
    expected = baseline.resolve_arm(arm, world.catalog, {'state': 'snapshot'}, 'other')
    assert out['selection']['state'] == expected['state']
    assert out['target'] == expected['target']
    assert out['launch_ready'] is (option != 'variant')
    assert 'data_policy' not in out


def test_r1_exact_destination_retains_options_free_dispatch_contract(world):
    add_opencode_fixture(world)
    out = run(world, 'route', 'fanout.explore', *world.args(), *deepseek_flags(classification='public'),
              *destination(world, DEEPSEEK_FREE))
    assert out['launch_ready'] is True
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE}
    assert out['data_policy']['state'] == 'allowed_public_experimental'
    assert out['dispatch']['mode'] == 'async'


def test_r1_unrelated_destination_does_not_inherit_fallback_policy(world):
    add_opencode_fixture(world)
    provider = world.catalog['data']['providers'][0]
    account = provider.get('providerInstanceId', provider.get('instanceId'))
    model = provider['models'][0].get('id', provider['models'][0].get('slug'))
    out = run(world, 'route', 'fanout.explore', *world.args(), *deepseek_flags(classification='public'),
              *destination(world, model, account))
    assert out['launch_ready'] is True and out['target']['providerInstanceId'] == account
    assert 'data_policy' not in out
    assert out['dispatch']['mode'] == 'async'


@pytest.mark.parametrize('location', ['selection', 'alternatives', 'routing'])
@pytest.mark.parametrize('replay', [False, True])
def test_all_nested_dispatch_targets_share_the_gate(world, location, replay):
    from policy_decisions import apply_experimental_route_policy
    add_opencode_fixture(world)
    allowed = run(world, 'resolve', '--no-op', *world.args(), *deepseek_flags(classification='public'))
    nested = {'target': {'providerInstanceId': 'opencode', 'model': SIBLING, 'options': {'variant': 'high'}},
              'dispatch': {'mode': 'async'}}
    if location == 'alternatives':
        allowed[location] = [nested]
    elif location == 'routing':
        allowed[location] = {'nested': nested, 'judgment_reasons': []}
    else:
        allowed[location] = nested
    apply_experimental_route_policy(allowed, world.policy, datetime.fromisoformat(CLOCK.replace('Z', '+00:00')), replay=replay)
    refused(allowed)
    assert all('dispatch' not in node for node in nested_nodes(allowed))


def nested_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_nodes(child)


def test_stored_governed_identity_survives_complete_policy_removal(world):
    from policy_decisions import apply_experimental_route_policy
    add_opencode_fixture(world)
    out = run(world, 'resolve', '--no-op', *world.args(), *deepseek_flags(classification='public'))
    # Provider/model names are data: stored governance must also cover future names.
    identity = {'provider': 'another', 'model': 'another/model', 'account': 'another-account', 'effort': 'high'}
    out['data_policy'] = {'route': 'a-different-governed-model', 'targets': [identity]}
    out['selection'] = {'arm': {key: identity[key] for key in ('provider', 'model', 'effort')},
                        'target': {'providerInstanceId': identity['account'], 'model': identity['model'], 'options': {'variant': 'high'}}}
    out['target'] = copy.deepcopy(out['selection']['target'])
    apply_experimental_route_policy(out, {'experimental_free_routes': []}, datetime.now(timezone.utc), replay=True)
    refused(out)


def test_destination_sensitive_flag_refuses_even_with_public_exact_overrides(world):
    add_opencode_fixture(world)
    out = run(world, 'route', 'fanout.explore', *world.args(), *deepseek_flags(classification='public', sensitive=True),
              *destination(world, DEEPSEEK_FREE))
    refused(out)
    assert out['data_policy']['refusal'] == 'sensitive_inputs'


@pytest.mark.parametrize('driver', ['opencode', 'other'])
def test_destination_alias_cannot_use_fallback_authorization(world, driver):
    add_opencode_fixture(world)
    world.catalog['data']['providers'].append({'driver': driver, 'instanceId': 'alias',
                                               'models': [{'slug': DEEPSEEK_FREE}]})
    out = run(world, 'route', 'fanout.explore', *world.args(), *deepseek_flags(classification='public'),
              *destination(world, DEEPSEEK_FREE, 'alias', driver))
    refused(out)
    assert out['data_policy']['account'] == 'alias'


def test_destination_replay_revocation_clears_dispatch_instructions(world):
    add_opencode_fixture(world)
    flags = ['route', 'fanout.explore', *world.args(), *deepseek_flags(classification='public'),
             *destination(world, DEEPSEEK_FREE), '--record', '--decision-id', 'destination-replay',
             '--parent-model', 'parent', '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    first = run(world, *flags)
    assert first['launch_ready'] is True and first['dispatch']['mode'] == 'async'
    assert run(world, *flags) == first | {'replayed': True}
    world.policy['experimental_free_routes'] = []
    out = run(world, *flags)
    assert out['replayed'] is True
    refused(out)
