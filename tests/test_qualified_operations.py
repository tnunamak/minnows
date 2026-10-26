"""Qualified normal free-route boundaries, using the real CLI and locked receipt ledger.

The DeepSeek V4.1 Flash Free declaration in `free_routes` is the only zero-price route. Its two qualified
operations need public inputs, oracle proof and the high effort. The retired legacy experimental declaration
survives only as a refusal: a stored legacy identity is never re-authorized, and the retired flag refuses.
"""
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
OWNER_OPS = [('implement.oracle-bounded', 'oracle'), ('implement.quota-tight', 'judged'),
             ('fanout.dollar-tight', 'judged'), ('fanout.explore', 'judged')]
CLOCK_AT = datetime.fromisoformat(CLOCK.replace('Z', '+00:00'))


def flags(world, op, command='route', proof='oracle', effort='high'):
    selected = deepseek_flags(classification='public')
    selected[selected.index('effort=high')] = 'effort=' + effort
    return [command, op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
            *selected, *(['--proof-class', proof] if proof else [])]


def normal(world):
    return world.policy['free_routes'][0]


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('command', ['route', 'resolve'])
def test_qualified_public_oracle_high_returns_exact_target(world, op, command):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, command))
    assert out['launch_ready'] is True
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE,
                             'options': {'variant': 'high'}}
    assert out['data_policy']['state'] == 'allowed_public_normal'
    assert out['data_policy']['basis'] == 'qualified_free' and out['data_policy']['refusal'] is None
    if command == 'route':
        assert out['routing']['final']['billing'] == 'free'


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
        assert out['data_policy']['refusal'] == 'proof_class_not_declared_for_normal_route'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('effort', ['low', 'max', 'banana'])
def test_only_benchmarked_effort_qualifies(world, op, effort):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, effort=effort))
    refused(out)
    if effort in ('low', 'max'):
        assert out['data_policy']['refusal'] == 'effort_not_declared_for_normal_route'


@pytest.mark.parametrize('op', ['review.audit', 'implement.standard', 'advisor.deep', 'recover.report'])
def test_other_ops_do_not_inherit_qualification(world, op):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, proof='judged'))
    refused(out)
    assert out['data_policy']['refusal'] == 'operation_not_declared_for_normal_route'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('case', ['unknown', 'sensitive-class', 'sensitive-flag', 'account-alias',
                                 'provider-alias', 'sibling', 'future-evidence', 'missing-evidence',
                                 'missing-account'])
def test_qualification_keeps_security_floor(world, op, case):
    add_opencode_fixture(world)
    argv = flags(world, op)
    route = normal(world)
    if case == 'unknown':
        argv.remove('--data-classification')
        argv.remove('public')
    elif case == 'sensitive-class':
        argv[argv.index('public')] = 'sensitive'
    elif case == 'sensitive-flag':
        argv.append('--sensitive')
    elif case == 'account-alias':
        world.catalog['data']['providers'][-1]['instanceId'] = 'opencode-alias'
        argv[argv.index('account=opencode')] = 'account=opencode-alias'
    elif case == 'provider-alias':
        world.catalog['data']['providers'][-1]['driver'] = 'alias'
        argv[argv.index('provider=opencode')] = 'provider=alias'
    elif case == 'sibling':
        argv[argv.index('model=' + DEEPSEEK_FREE)] = 'model=opencode/paid-model'
    elif case == 'future-evidence':
        route['status'] = 'free_as_of_2026-10-16'
        route['evidence'][0]['accessed'] = '2026-10-16'
    elif case == 'missing-evidence':
        route['evidence'] = []
    elif case == 'missing-account':
        index = argv.index('account=opencode')
        del argv[index - 1:index + 1]
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    if case == 'missing-evidence':
        assert process.returncode == 1 and not out
        assert 'evidence' in process.stderr
    else:
        assert process.returncode == 0, process.stderr
        refused(out)
        if case == 'future-evidence':
            assert out['data_policy']['refusal'] == 'normal_route_evidence_from_future_or_invalid'
        if case == 'sensitive-flag':
            assert out['data_policy']['refusal'] == 'sensitive_inputs'
        if case in ('unknown', 'sensitive-class'):
            assert out['data_policy']['refusal'] == 'public_classification_required'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('missing', ['provider', 'model', 'effort', 'reason'])
def test_qualification_requires_every_explicit_override_and_reason(world, op, missing):
    add_opencode_fixture(world)
    argv = flags(world, op)
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
    assert out['data_policy']['refusal'] == 'governed_destination_unsupported'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('race', [False, True])
@pytest.mark.parametrize('revoke', [None, 'operation', 'declaration'])
def test_current_qualification_is_required_on_both_replay_paths(world, op, race, revoke, monkeypatch, capsys):
    add_opencode_fixture(world)
    argv = [*flags(world, op), '--record', '--decision-id', 'qualified', '--parent-model', 'parent',
            '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    first = run(world, *argv)
    assert first['launch_ready']
    ledger = world.receipts.read_bytes()
    if revoke == 'operation':
        normal(world)['operations'] = [entry for entry in normal(world)['operations'] if entry['id'] != op]
    elif revoke == 'declaration':
        world.policy['free_routes'] = []
    world.write()
    if race:
        monkeypatch.setattr(pd, 'find_receipt', lambda *args: None)
    else:
        monkeypatch.setattr(pd, 'read_source', lambda *args: pytest.fail('early replay read live runtime data'))
    monkeypatch.setattr(directives, 'now', lambda: CLOCK_AT)
    tool = str(Path(__file__).resolve().parents[1] / 'tools/model-policy-ops/model-policy-ops')
    monkeypatch.setattr(sys, 'argv', [tool, *full_record(argv), '--no-user-policy'])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(tool, run_name='__main__')
    captured = capsys.readouterr()
    assert exited.value.code == 0, captured.err
    out = json.loads(captured.out)
    if revoke:
        refused(out)
        assert out['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'
    else:
        assert out == first | {'replayed': True}
    assert world.receipts.read_bytes() == ledger


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('change', ['operation-removed', 'proof-changed', 'effort-changed'])
def test_actual_target_replay_uses_current_proof_and_effort_eligibility(world, op, change):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op))
    policy = copy.deepcopy(world.policy)
    declared = policy['free_routes'][0]
    if change == 'operation-removed':
        declared['operations'] = [item for item in declared['operations'] if item['id'] != op]
    else:
        entry = next(item for item in declared['operations'] if item['id'] == op)
        if change == 'proof-changed':
            entry['proof_classes'] = ['judged']
        else:
            entry['efforts'] = ['low']
    pd.apply_experimental_route_policy(out, policy, CLOCK_AT, replay=True)
    refused(out)
    assert out['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'


def test_resolve_without_an_op_is_not_authorized_for_the_free_route(world):
    add_opencode_fixture(world)
    out = run(world, *flags(world, '--no-op', 'resolve', proof=None))
    refused(out)
    assert out['data_policy']['refusal'] == 'operation_not_declared_for_normal_route'


def test_an_owner_authorized_operation_still_needs_a_declared_proof_class(world):
    add_opencode_fixture(world)
    out = run(world, *flags(world, 'fanout.explore', proof='oracle'))
    refused(out)
    assert out['data_policy']['refusal'] == 'proof_class_not_declared_for_normal_route'


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


def test_a_stored_legacy_identity_is_refused_on_replay_even_with_the_normal_declaration(world):
    add_opencode_fixture(world)
    out = run(world, *flags(world, OPS[0]))
    assert out['data_policy']['state'] == 'allowed_public_normal'
    # A receipt recorded under the retired legacy contract: same identity, legacy kind, legacy request shape.
    out['data_policy']['state'] = 'allowed_public_experimental'
    out['request']['experimental_route'] = True
    pd.apply_experimental_route_policy(out, world.policy, CLOCK_AT, replay=True)
    refused(out)
    assert out['data_policy']['refusal'] == 'experimental_route_receipt_not_currently_authorized'


def test_the_retired_flag_refuses_a_fresh_request(world):
    add_opencode_fixture(world)
    out = run(world, *flags(world, OPS[0]), '--experimental-route')
    refused(out)
    assert out['data_policy']['refusal'] == 'model_not_declared_for_experimental_route'


def test_a_retired_experimental_key_in_the_pack_is_an_error(world):
    add_opencode_fixture(world)
    world.policy['experimental_free_routes'] = []
    process, out = world.cli(*flags(world, OPS[0]), evaluation_time=CLOCK)
    assert process.returncode == 1 and out is None
    assert 'experimental_free_routes is retired' in process.stderr


# ---- resolve and the four DeepSeek-first ops ------------------------------------------------------------------------------
# Automatic `resolve` never picks the free route. An exact, reasoned operator override on public input may name it,
# as it may for the qualified ops; without public input, or with --sensitive, the override is refused.

@pytest.mark.parametrize('op,proof', OWNER_OPS)
def test_automatic_resolve_never_returns_the_free_route(world, op, proof):
    add_opencode_fixture(world)
    out = run(world, 'resolve', op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
              '--data-classification', 'public', '--proof-class', proof)
    assert DEEPSEEK_FREE not in json.dumps(out)
    assert (out.get('target') or {}).get('providerInstanceId') != 'opencode'


@pytest.mark.parametrize('op,proof', OWNER_OPS)
def test_an_explicit_public_override_on_resolve_is_accepted(world, op, proof):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, 'resolve', proof=proof))
    assert out['launch_ready'] is True and out['data_policy']['refusal'] is None
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE, 'options': {'variant': 'high'}}


@pytest.mark.parametrize('op,proof', OWNER_OPS)
@pytest.mark.parametrize('classification,sensitive,refusal', [
    (None, False, 'public_classification_required'),
    ('sensitive', False, 'public_classification_required'),
    ('public', True, 'sensitive_inputs'),
])
def test_an_explicit_override_on_resolve_without_public_input_is_refused(world, op, proof, classification, sensitive, refusal):
    add_opencode_fixture(world)
    selected = deepseek_flags(classification=classification, sensitive=sensitive)
    out = run(world, 'resolve', op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
              *selected, '--proof-class', proof)
    refused(out)
    assert out['data_policy']['refusal'] == refusal
