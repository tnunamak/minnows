"""Owner-authorized free-route trial boundaries, using the real CLI and locked receipt ledger.

The 2026-10-15 expansion makes the free route the preferred normal candidate for checked
cheap-tier work on the reviewed scopes. These tests pin the gates that keep that preference
honest: an operation must be reviewed, the proof must be checked, the effort must be one the
entry and the live catalog both allow, the input manifest must be allowed, and every fallback
to a paid arm must state its reason.
"""
import copy
import json
import runpy
import sys
from datetime import datetime
from pathlib import Path

import pytest

from test_route import DEEPSEEK_FREE, PROVENANCE, RATE, add_opencode_fixture, deepseek_flags, event, world  # noqa: F401
from test_route_authorization import CLOCK, destination, refused, run
import directives
import policy_decisions as pd


OPS = ['implement.oracle-bounded', 'docs.lookup']
TASK_LINK = '{"v":1,"consumer_task_id":"task-qualified","workflow_id":"wf-qualified","stage_id":"s1","role":"maker"}'
VANA_MANIFEST = '{"v":1,"inputs":[{"kind":"github","repo":"vana-com/private-thing","visibility":"public"}]}'


def flags(world, op, command='route', proof='oracle', effort='high', provenance=PROVENANCE):
    """The historical exact-override call: still authorized, but no longer the only way in."""
    selected = deepseek_flags(classification='public', provenance=provenance)
    selected[selected.index('effort=high')] = 'effort=' + effort
    return [command, op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
            *selected, *(['--proof-class', proof] if proof else [])]


def trial_flags(world, op, command='route', proof='oracle', effort='high', provenance=PROVENANCE, extra=()):
    """Only the gates the trial itself needs: no --experimental-route, no provider/model/account pin."""
    argv = [command, op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
            '--reason', 'Preferred-route trial on a public task', '--data-classification', 'public']
    if provenance:
        argv += ['--input-provenance', provenance]
    if proof:
        argv += ['--proof-class', proof]
    if effort:
        argv += ['--override', 'effort=' + effort]
    return argv + list(extra)


def trial(out):
    return out['routing']['trial']


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('command', ['route', 'resolve'])
def test_qualified_public_oracle_high_returns_exact_target(world, op, command):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, command))
    assert out['launch_ready'] is True
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE,
                             'options': {'variant': 'high'}}
    assert out['data_policy']['state'] == 'allowed_public_experimental'
    if command == 'route':
        assert out['routing']['final']['billing'] == 'free'


@pytest.mark.parametrize('op', OPS)
def test_trial_is_preferred_without_exact_overrides_or_the_opt_in_flag(world, op):
    add_opencode_fixture(world)
    argv = trial_flags(world, op)
    assert '--experimental-route' not in argv
    assert not any(isinstance(a, str) and a.startswith('provider=') for a in argv)
    out = run(world, *argv)
    assert out['launch_ready'] is True
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE,
                             'options': {'variant': 'high'}}
    assert trial(out)['state'] == 'selected'
    assert trial(out)['selected'] == 'owner_authorized_trial'
    assert trial(out)['provenance']['state'] == 'allowed'
    assert out['routing']['final']['billing'] == 'free'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('command', ['route', 'resolve'])
@pytest.mark.parametrize('proof', [None, 'none'])
def test_missing_or_unchecked_proof_cannot_dispatch(world, op, command, proof):
    add_opencode_fixture(world)
    process, out = world.cli(*flags(world, op, command, proof), evaluation_time=CLOCK)
    if command == 'route' and op == 'implement.oracle-bounded':
        assert process.returncode == 1 and '--proof-class' in process.stderr
    else:
        assert process.returncode == 0, process.stderr
        refused(out)
        assert out['data_policy']['refusal'] == 'qualified_operation_requires_allowed_proof'


def test_a_source_change_does_not_take_a_judged_proof(world):
    add_opencode_fixture(world)
    process, out = world.cli(*trial_flags(world, 'implement.oracle-bounded', proof='judged'), evaluation_time=CLOCK)
    assert process.returncode == 1 and out is None and '--proof-class' in process.stderr


def test_a_judged_scope_requires_an_independent_judge_and_says_so_at_route_time(world):
    add_opencode_fixture(world)
    for op, extra in (('docs.lookup', []),
                      ('implement.oracle-bounded', ['--purpose', 'test-design']),
                      ('recover.report', [])):
        argv = trial_flags(world, op, proof='judged', extra=extra)
        out = run(world, *argv)
        assert trial(out)['state'] == 'selected', (op, trial(out))
        assert trial(out)['independent_check_required'] is True
        assert trial(out)['close_requirements'] == ['close', 'repairs', 'independent_judge_check']
        assert out['target']['model'] == DEEPSEEK_FREE
    out = run(world, *trial_flags(world, 'docs.lookup', proof='oracle'))
    assert trial(out)['independent_check_required'] is False
    assert trial(out)['close_requirements'] == ['close', 'repairs']


def test_the_test_design_purpose_uses_its_own_entry_and_scope(world):
    """A stated purpose selects the purpose entry, not the op's general entry."""
    add_opencode_fixture(world)
    general = run(world, *trial_flags(world, 'implement.oracle-bounded', proof='oracle'))
    assert trial(general)['state'] == 'selected'
    assert trial(general)['scope'] == 'small_public_source_change_full_deterministic_oracle'
    assert trial(general)['independent_check_required'] is False
    scoped = run(world, *trial_flags(world, 'implement.oracle-bounded', proof='judged',
                                     extra=['--purpose', 'test-design']))
    assert trial(scoped)['state'] == 'selected'
    assert trial(scoped)['scope'] == 'bounded_public_test_design_checked_by_independent_checker'
    assert trial(scoped)['independent_check_required'] is True
    # The purpose entry admits judged only, so an oracle declaration still falls back to a paid arm.
    refused_entry = run(world, *trial_flags(world, 'implement.oracle-bounded', proof='oracle',
                                            extra=['--purpose', 'test-design']))
    assert trial(refused_entry)['selected'] is None
    assert trial(refused_entry)['reason'] == 'proof_class_not_eligible'
    assert refused_entry['target']['model'] != DEEPSEEK_FREE


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
def test_every_reviewed_effort_qualifies(world, op, effort):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, effort=effort))
    assert out['launch_ready'] is True
    assert out['target']['options'] == {'variant': effort}


@pytest.mark.parametrize('op', OPS)
def test_unreviewed_effort_names_its_refusal(world, op):
    add_opencode_fixture(world)
    out = run(world, *trial_flags(world, op, effort='banana'))
    assert out['launch_ready'] is False and out['target'] is None
    assert trial(out)['state'] == 'not_qualified'
    assert trial(out)['reason'] == 'effort_not_allowed'
    assert trial(out)['selected'] is None


def test_research_purpose_accepts_a_judged_checker_but_other_or_absent_purposes_do_not(world):
    add_opencode_fixture(world)
    research = run(world, *trial_flags(world, 'fanout.dollar-tight', proof='judged',
                                      extra=['--purpose', 'research']))
    assert trial(research)['state'] == 'selected'
    assert research['target']['model'] == DEEPSEEK_FREE
    for purpose in ('execution', None):
        extra = ['--purpose', purpose] if purpose else []
        out = run(world, *trial_flags(world, 'fanout.dollar-tight', proof='judged', extra=extra))
        assert trial(out)['state'] == 'not_qualified'
        assert trial(out)['reason'] == 'purpose_not_qualified'
        assert out['target']['model'] != DEEPSEEK_FREE


@pytest.mark.parametrize('op', ['review.audit', 'implement.standard', 'advisor.deep', 'ui.computer-use'])
def test_other_ops_do_not_inherit_qualification(world, op):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, proof='judged'))
    refused(out)
    assert out['data_policy']['refusal'] == 'operation_not_qualified'


@pytest.mark.parametrize('proof', ['oracle', 'judged'])
def test_a_bounded_status_report_is_qualified_for_its_checked_scope_only(world, proof):
    add_opencode_fixture(world)
    out = run(world, *trial_flags(world, 'recover.report', proof=proof))
    assert trial(out)['state'] == 'selected'
    assert trial(out)['scope'] == 'bounded_public_status_report_checked_by_independent_checker'
    assert out['target']['model'] == DEEPSEEK_FREE
    out = run(world, *trial_flags(world, 'recover.report', proof='oracle', effort='medium'))
    assert trial(out)['state'] == 'not_qualified'
    assert trial(out)['reason'] == 'effort_not_allowed'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('case', ['unknown', 'sensitive-class', 'sensitive-flag', 'account-alias',
                                 'provider-alias', 'sibling', 'expired', 'missing-evidence',
                                 'disabled-transport', 'missing-expiry'])
def test_qualification_keeps_security_floor(world, op, case):
    add_opencode_fixture(world)
    argv = flags(world, op)
    route = world.policy['experimental_free_routes'][0]
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
        assert out['data_policy']['refusal'] == {
            'account-alias': 'account_alias_not_allowed',
            'provider-alias': 'provider_alias_not_allowed',
            'sibling': 'model_not_declared_for_experimental_route',
        }.get(case, out['data_policy']['refusal'])


@pytest.mark.parametrize('op', OPS)
def test_an_effort_override_alone_keeps_the_trial_selected(world, op):
    add_opencode_fixture(world)
    out = run(world, *trial_flags(world, op, effort='max'))
    assert trial(out)['state'] == 'selected'
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE,
                             'options': {'variant': 'max'}}


@pytest.mark.parametrize('op', OPS)
def test_the_free_route_is_bound_to_its_declared_account(world, op):
    add_opencode_fixture(world)
    argv = [*trial_flags(world, op), '--override', 'provider=opencode', '--override', f'model={DEEPSEEK_FREE}',
            '--override', 'account=ghost']
    out = run(world, *argv)
    refused(out)
    assert out['data_policy']['refusal'] == 'account_alias_not_allowed'
    assert trial(out)['selected'] is None


@pytest.mark.parametrize('op', OPS)
def test_missing_input_provenance_falls_back_with_a_stated_reason(world, op):
    add_opencode_fixture(world)
    out = run(world, *trial_flags(world, op, provenance=None))
    assert trial(out)['state'] == 'not_selected'
    assert trial(out)['reason'] == 'input_provenance_required'
    assert trial(out)['selected'] is None
    assert out['target']['model'] != DEEPSEEK_FREE


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('manifest', [
    VANA_MANIFEST,
    '{"v":1,"inputs":[{"kind":"local","path":"/home/someone/.ssh/id_rsa","classification":"code"}]}',
    '{"v":1,"inputs":[{"kind":"local","path":"../secrets/pem","classification":"code"}]}',
    '{"v":1,"inputs":[{"kind":"local","path":"config.json","classification":"code","contains":["PDPP_data"]}]}',
    '{"v":1,"inputs":[{"kind":"github","repo":"other/thing","visibility":"private"}]}',
])
def test_refused_input_manifest_falls_back_with_a_stated_reason(world, op, manifest):
    add_opencode_fixture(world)
    out = run(world, *trial_flags(world, op, provenance=manifest))
    assert trial(out)['state'] == 'not_selected'
    assert trial(out)['reason'].startswith('input_provenance_refused_')
    assert trial(out)['selected'] is None
    assert out['target']['model'] != DEEPSEEK_FREE


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('manifest', [None, VANA_MANIFEST])
def test_a_pinned_free_target_is_refused_without_an_allowed_manifest(world, op, manifest):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op, provenance=manifest))
    refused(out)
    assert out['data_policy']['refusal'].startswith('input_provenance_required' if manifest is None
                                                    else 'input_provenance_refused_')


@pytest.mark.parametrize('op', OPS)
def test_an_exclusion_change_is_honored_rather_than_relaxed(world, op):
    add_opencode_fixture(world)
    world.policy['experimental_free_routes'][0]['input_policy']['excluded_github_owners'] = ['tnunamak', 'vana-com']
    world.write()
    out = run(world, *trial_flags(world, op))
    assert trial(out)['reason'] == 'input_provenance_refused_company_private_namespace'
    assert out['target']['model'] != DEEPSEEK_FREE


@pytest.mark.parametrize('reason', ['sensitive_inputs', 'public_classification_required',
                                    'explicit_provider_model_account_override', 'proof_class_not_eligible',
                                    'purpose_not_qualified'])
def test_each_fall_back_reason_is_visible_in_the_trial_block(world, reason):
    add_opencode_fixture(world)
    if reason == 'sensitive_inputs':
        argv = [*trial_flags(world, 'docs.lookup'), '--sensitive']
    elif reason == 'public_classification_required':
        argv = [a for a in trial_flags(world, 'docs.lookup') if a not in ('--data-classification', 'public')]
    elif reason == 'explicit_provider_model_account_override':
        argv = [*trial_flags(world, 'docs.lookup'), '--override', 'provider=claude',
                '--override', 'model=claude/claude-haiku-5-5']
    elif reason == 'proof_class_not_eligible':
        argv = trial_flags(world, 'docs.lookup', proof='none')
    else:
        argv = trial_flags(world, 'fanout.dollar-tight', proof='judged', extra=['--purpose', 'execution'])
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    assert process.returncode == 0, process.stderr
    assert trial(out)['selected'] is None
    assert trial(out)['reason'] == reason
    assert out['target'] is None or out['target']['model'] != DEEPSEEK_FREE


@pytest.mark.parametrize('lost', ['catalog', 'demoted'])
def test_a_trial_candidate_lost_at_the_routing_boundary_is_named_not_claimed(world, lost):
    add_opencode_fixture(world)
    if lost == 'catalog':
        models = world.catalog['data']['providers'][-1]['models']
        world.catalog['data']['providers'][-1]['models'] = [m for m in models if m['slug'] != DEEPSEEK_FREE]
    else:
        world.events += [event('opencode', RATE, at='2026-10-15T00:20:00.000Z')] * 3
    out = run(world, *trial_flags(world, 'docs.lookup'))
    assert trial(out)['state'] == 'not_selected'
    assert trial(out)['reason'] == {'catalog': 'candidate_removed_not_runnable',
                                    'demoted': 'availability_demoted'}[lost]
    assert trial(out)['selected'] is None
    assert out['target']['model'] != DEEPSEEK_FREE
    assert 'pending_closes' not in trial(out)


def test_an_ordinary_operation_carries_no_trial_block(world):
    add_opencode_fixture(world)
    out = run(world, *trial_flags(world, 'review.audit', proof='judged'))
    assert 'trial' not in out['routing']


@pytest.mark.parametrize('op', OPS)
def test_generic_destination_remains_refused_for_qualified_ops(world, op):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op), *destination(world, DEEPSEEK_FREE))
    refused(out)
    assert out['data_policy']['refusal'] == 'governed_destination_unsupported'


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('race', [False, True])
@pytest.mark.parametrize('revoke', [None, 'operation', 'proof', 'provenance'])
def test_current_qualification_is_required_on_both_replay_paths(world, op, race, revoke, monkeypatch, capsys):
    add_opencode_fixture(world)
    argv = [*flags(world, op), '--record', '--decision-id', 'qualified', '--task-link', TASK_LINK,
            '--parent-model', 'parent', '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    first = run(world, *argv)
    assert first['launch_ready']
    ledger = world.receipts.read_bytes()
    if revoke:
        route = world.policy['experimental_free_routes'][0]
        if revoke == 'operation':
            route['qualified_operations'] = [entry for entry in route['qualified_operations'] if entry['id'] != op]
        elif revoke == 'proof':
            next(entry for entry in route['qualified_operations'] if entry['id'] == op)['proof_classes'] = []
        else:
            route['input_policy']['excluded_github_owners'] = ['tnunamak', 'vana-com']
    world.write()
    if race:
        monkeypatch.setattr(pd, 'find_receipt', lambda *args: None)
    else:
        monkeypatch.setattr(pd, 'read_source', lambda *args: pytest.fail('early replay read live runtime data'))
    monkeypatch.setattr(directives, 'now', lambda: datetime.fromisoformat(CLOCK.replace('Z', '+00:00')))
    tool = str(Path(__file__).resolve().parents[1] / 'tools/model-policy-ops/model-policy-ops')
    monkeypatch.setattr(sys, 'argv', [tool, *map(str, argv), '--no-user-policy'])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(tool, run_name='__main__')
    captured = capsys.readouterr()
    assert exited.value.code == 0, captured.err
    out = json.loads(captured.out)
    if revoke:
        refused(out)
        assert out['data_policy']['refusal'] == 'experimental_route_receipt_not_currently_authorized'
    else:
        assert out == first | {'replayed': True}
    assert world.receipts.read_bytes() == ledger


@pytest.mark.parametrize('op', OPS)
@pytest.mark.parametrize('change', ['proof-removed', 'proof-narrowed', 'effort', 'scope'])
def test_actual_target_replay_uses_current_proof_and_effort_eligibility(world, op, change):
    add_opencode_fixture(world)
    out = run(world, *flags(world, op))
    policy = copy.deepcopy(world.policy)
    entry = next(item for item in policy['experimental_free_routes'][0]['qualified_operations'] if item['id'] == op)
    if change.startswith('proof'):
        entry['proof_classes'] = [] if change == 'proof-removed' else ['judged']
    elif change == 'effort':
        entry['efforts'] = []
    else:
        entry['scope'] = 'open_ended_research'
    pd.apply_experimental_route_policy(out, policy, datetime.fromisoformat(CLOCK.replace('Z', '+00:00')), replay=True)
    refused(out)


@pytest.mark.parametrize('effort', ['low', 'max'])
def test_exploratory_paths_retain_catalog_validated_efforts(world, effort):
    add_opencode_fixture(world)
    for command, op in [('route', 'fanout.explore'), ('resolve', '--no-op')]:
        out = run(world, *flags(world, op, command, proof=None, effort=effort))
        assert out['target']['options'] == {'variant': effort}


@pytest.mark.parametrize('op', OPS)
def test_changed_recorded_proof_cannot_be_replaced_on_replay(world, op):
    add_opencode_fixture(world)
    argv = [*flags(world, op), '--record', '--decision-id', 'proof-identity', '--task-link', TASK_LINK,
            '--parent-model', 'parent', '--parent-provider-instance', 'parent', '--parent-thread', 'parent']
    assert run(world, *argv)['launch_ready']
    ledger = world.receipts.read_bytes()
    argv[argv.index('oracle')] = 'judged'
    process, out = world.cli(*argv, evaluation_time=CLOCK)
    assert process.returncode == 1 and out is None and 'changed inputs' in process.stderr
    assert world.receipts.read_bytes() == ledger
