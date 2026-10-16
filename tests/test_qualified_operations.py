"""Offline qualification boundaries, using the real CLI and locked receipt ledger."""
import copy
import json
import runpy
import sys
from datetime import datetime
from pathlib import Path

import pytest

from cli_args import full_record

from test_route import DEEPSEEK_FREE, add_opencode_fixture, deepseek_flags, world  # noqa: F401
from test_route_authorization import CLOCK, destination, refused, run
import directives
import policy_decisions as pd


OPS = ['implement.oracle-bounded', 'docs.lookup']


def flags(world, op, command='route', proof='oracle', effort='high'):
    selected = deepseek_flags()
    selected[selected.index('effort=high')] = 'effort=' + effort
    return [command, op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
            *selected, *(['--proof-class', proof] if proof else [])]


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('command', ['route', 'resolve'])
def test_qualified_oracle_high_returns_exact_target(world, op, command):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, command))
    assert out['launch_ready'] is True
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE,
                             'options': {'variant': 'high'}}
    assert out['free_route']['state'] == 'allowed'
    if command == 'route':
        assert out['routing']['final']['billing'] == 'free'


@pytest.mark.parametrize('op', OPS)
def test_route_prefers_the_free_arm_for_oracle_checked_work_without_overrides(world, op):
    add_opencode_fixture(world)
    out = run(world, 'route', op, *world.args(), '--proof-class', 'oracle')
    assert out['launch_ready'] is True and out['target']['model'] == DEEPSEEK_FREE
    assert out['routing']['chosen']['basis'] == 'measured_free' and out['routing']['final']['overridden'] is False
    assert out['free_route']['state'] == 'allowed'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('command', ['route', 'resolve'])
@pytest.mark.parametrize('proof', [None, 'judged', 'none'])
def test_missing_or_nonoracle_proof_cannot_dispatch(world, op, command, proof):
    add_opencode_fixture(world)
    process, out = world.cli(*flags(world, op, command, proof), evaluation_time=CLOCK)
    if command == 'route' and op == 'implement.oracle-bounded':
        assert process.returncode == 1 and '--proof-class' in process.stderr
    else:
        assert process.returncode == 0, process.stderr
        refused(out)
        assert out['free_route']['refusal'] == 'qualified_operation_requires_oracle_proof'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('effort', ['low', 'max', 'banana'])
def test_only_benchmarked_effort_qualifies(world, op, effort):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, effort=effort))
    refused(out)


@pytest.mark.parametrize('op', ['review.audit', 'implement.standard', 'advisor.deep', 'recover.report'])
def test_other_ops_do_not_inherit_qualification(world, op):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, proof='judged'))
    refused(out)
    assert out['free_route']['refusal'] == 'operation_not_qualified'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('case', ['account-alias', 'provider-alias', 'sibling', 'expired', 'missing-evidence',
                                 'disabled-transport', 'missing-expiry'])
def test_qualification_keeps_identity_and_availability_floor(world, op, case):
    add_opencode_fixture(world)
    argv = flags(world, op, 'resolve')
    route = world.policy['free_routes'][0]
    if case == 'account-alias':
        world.catalog['data']['providers'][-1]['instanceId'] = 'opencode-alias'
        argv[argv.index('account=opencode')] = 'account=opencode-alias'
    elif case == 'provider-alias':
        world.catalog['data']['providers'][-1]['driver'] = 'alias'
        argv[argv.index('provider=opencode')] = 'provider=alias'
    elif case == 'sibling':
        argv[argv.index('model=' + DEEPSEEK_FREE)] = 'model=opencode/paid-model'
    elif case == 'expired':
        route['expires_at_utc'] = '2026-10-14T00:00:00Z'
    elif case == 'missing-evidence':
        route['evidence'] = []
    elif case == 'missing-expiry':
        route.pop('expires_at_utc')
    elif case == 'disabled-transport':
        route['t3_transport']['enabled'] = False
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    if case in ('missing-evidence', 'missing-expiry', 'disabled-transport'):
        assert process.returncode == 1 and not out
    else:
        assert process.returncode == 0, process.stderr
        refused(out)


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('missing', ['provider', 'model', 'reason'])
def test_resolve_requires_every_explicit_override_and_reason(world, op, missing):
    add_opencode_fixture(world)
    argv = flags(world, op, 'resolve')
    index = argv.index('--reason') if missing == 'reason' else next(
        n - 1 for n, value in enumerate(argv) if isinstance(value, str) and value.startswith(missing + '='))
    del argv[index:index + 2]
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    if missing == 'reason':
        assert process.returncode == 1 and out is None and 'require --reason' in process.stderr
    else:
        assert process.returncode == 0, process.stderr
        refused(out)


@pytest.mark.parametrize('op', OPS)
def test_generic_destination_remains_refused_for_qualified_ops(world, op):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op), *destination(world, DEEPSEEK_FREE))
    refused(out)
    assert out['free_route']['refusal'] == 'governed_destination_unsupported'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('race', [False, True])
@pytest.mark.parametrize('revoke', [None, 'operation', 'proof'])
def test_current_qualification_is_required_on_both_replay_paths(world, op, race, revoke, monkeypatch, capsys):
    add_opencode_fixture(world)
    argv = [*flags(world, op), '--record', '--decision-id', 'qualified', '--parent-model', 'parent',
            '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    first = run(world, *argv)
    assert first['launch_ready']
    ledger = world.receipts.read_bytes()
    if revoke:
        route = world.policy['free_routes'][0]
        if revoke == 'operation':
            route['qualified_operations'] = [entry for entry in route['qualified_operations'] if entry['id'] != op]
        else:
            next(entry for entry in route['qualified_operations'] if entry['id'] == op)['proof_classes'] = []
    world.write()
    if race:
        monkeypatch.setattr(pd, 'find_receipt', lambda *args: None)
    else:
        monkeypatch.setattr(pd, 'read_source', lambda *args: pytest.fail('early replay read live runtime data'))
    monkeypatch.setattr(directives, 'now', lambda: datetime.fromisoformat(CLOCK.replace('Z', '+00:00')))
    tool = str(Path(__file__).resolve().parents[1] / 'tools/model-policy-ops/model-policy-ops')
    monkeypatch.setattr(sys, 'argv', [tool, *full_record(argv), '--no-user-policy'])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(tool, run_name='__main__')
    captured = capsys.readouterr()
    assert exited.value.code == 0, captured.err
    out = json.loads(captured.out)
    if revoke:
        refused(out)
        assert out['free_route']['refusal'] == 'free_route_receipt_not_currently_authorized'
    else:
        assert out == first | {'replayed': True}
    assert world.receipts.read_bytes() == ledger


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('change', ['proof-removed', 'proof-changed', 'effort', 'scope'])
def test_actual_target_replay_uses_current_proof_and_effort_eligibility(world, op, change):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op))
    policy = copy.deepcopy(world.policy)
    entry = next(item for item in policy['free_routes'][0]['qualified_operations'] if item['id'] == op)
    if change.startswith('proof'):
        entry['proof_classes'] = [] if change == 'proof-removed' else ['judged']
    elif change == 'effort':
        entry['efforts'] = []
    else:
        entry['scope'] = 'open_ended_research'
    pd.apply_free_route_policy(out, policy, datetime.fromisoformat(CLOCK.replace('Z', '+00:00')), replay=True)
    refused(out)


@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
def test_unmeasured_paths_are_not_eligible_at_any_effort(world, effort):
    add_opencode_fixture(world)
    for command, op in [('route', 'fanout.explore'), ('resolve', '--no-op')]:
        out = run(world, *flags(world, op, command, proof=None, effort=effort))
        refused(out)
        assert out['free_route']['refusal'] == 'operation_not_qualified'


@pytest.mark.parametrize('op', OPS)
def test_changed_recorded_proof_cannot_be_replaced_on_replay(world, op):
    add_opencode_fixture(world)
    argv = [*flags(world, op), '--record', '--decision-id', 'proof-identity', '--parent-model', 'parent',
            '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    assert run(world, *argv)['launch_ready']
    ledger = world.receipts.read_bytes()
    argv[argv.index('oracle')] = 'judged'
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    assert process.returncode == 1 and out is None and 'changed inputs' in process.stderr
    assert world.receipts.read_bytes() == ledger
