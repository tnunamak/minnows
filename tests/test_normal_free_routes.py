"""Normal (non-experimental) zero-price routes: selected as ordinary candidates under their declared scope.

The declaration is the pack's `free_routes`: exact provider/model/account, a zero source price, the public
floor, and one basis per operation. Every test changes one input and runs the real CLI. No model is called.
"""
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import test_route
from test_quota_bands import ORACLE, BandWorld
from test_quota_substitution import NOW
from test_route import DEEPSEEK_FREE, RATE, add_opencode_fixture, all_target_values, event
from test_route_destinations import capacity, config, entry

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools' / 'model-policy-ops' / 'lib'))
from normal_free_routes import check_normal_free_routes  # noqa: E402

OP = 'implement.oracle-bounded'
PUBLIC = ('--data-classification', 'public')
JUDGED = ('--proof-class', 'judged')
LATER = '2026-10-16T01:11:00Z'  # after the pinned capture is stale, inside the declared evidence window
FREE_TARGET = {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE, 'options': {'variant': 'high'}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    found = BandWorld(tmp_path)
    add_opencode_fixture(found)
    return found


def normal(world):
    return world.policy['free_routes'][0]


def current_at_pinned_capture(world):
    """Date the observation and its sources before the pinned 2026-10-11 capture so one clock serves both."""
    route = normal(world)
    route['status'] = 'free_as_of_2026-10-10'
    for item in route['evidence']:
        item['accessed'] = '2026-10-10'


def route_at(world, clock, *extra, op=OP, proof=ORACLE, record=None):
    flags = ['--decision-id', record or 'normal', *(['--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp',
                                                    '--parent-thread', 'pt'] if record else [])]
    result, out = world.cli('route', op, *world.args(), *proof, *flags, *extra, evaluation_time=clock)
    assert result.returncode == 0, result.stderr
    return out


def exact_flags(*, effort='high', account='opencode', model=DEEPSEEK_FREE):
    return ('--override', 'provider=opencode', '--override', f'model={model}', '--override', f'effort={effort}',
            '--override', f'account={account}', '--reason', 'explicit public free-route request')


def selected(out):
    return out['target'] == FREE_TARGET and out['launch_ready'] is True and 'waiting' not in out


def not_dispatched(out):
    return out['target'] is None and out['launch_ready'] is False


def free_rows(out):
    return [row for row in out['routing']['trace']
            if row['arm']['provider'] == 'opencode' and row['arm']['model'] == DEEPSEEK_FREE]


# ---- selected as an ordinary candidate -----------------------------------------------------------------------------------

def test_the_normal_route_is_selected_without_any_flag(world):
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out)
    chosen = out['routing']['chosen']
    assert chosen['arm'] == {'provider': 'opencode', 'model': DEEPSEEK_FREE, 'effort': 'high'}
    assert chosen['basis'] == 'qualified_free' and chosen['selection_basis'] == 'qualified_free_route'
    assert chosen['evidence'] == 'qualified'
    assert free_rows(out)[0]['rank']['price_class'] == 0
    assert out['routing']['final']['billing'] == 'free' and out['routing']['final']['overridden'] is False
    assert out['request']['overrides'] == {} and 'experimental_route' not in out['request']
    assert out['data_policy']['state'] == 'allowed_public_normal'
    assert out['data_policy']['basis'] == 'qualified_free' and out['data_policy']['refusal'] is None
    assert out['routing']['judgment_required'] is False


def test_the_normal_route_beats_a_healthy_subscription(world):
    current_at_pinned_capture(world)
    world.codex(100)  # below its band: a real subscription alternative with headroom
    out = route_at(world, NOW, *PUBLIC)
    assert selected(out)
    assert out['routing']['chosen']['arm']['provider'] == 'opencode'
    assert next(t for t in out['routing']['trace'] if t.get('instance') == 'codex')['removed_by'] is None


def test_the_normal_route_is_not_displaced_by_a_drain_preference(world):
    current_at_pinned_capture(world)
    world.policy['routing']['drain_accounts'] = {
        'codex': {'until_current_pct': 95, 'expires_at': '2026-10-22T00:00:00Z', 'reason': 'burn the week'}}
    out = route_at(world, NOW, *PUBLIC)
    assert selected(out)
    codex = next(t for t in out['routing']['trace'] if t.get('instance') == 'codex' and t['removed_by'] is None)
    assert codex['quota']['pace'] == 'on_track' and codex['rank']['drain'] == 0
    assert out['routing']['chosen']['arm']['provider'] == 'opencode'


def test_an_explicit_override_on_the_normal_declaration_is_normal(world):
    out = route_at(world, LATER, *PUBLIC)
    assert len(free_rows(out)) == 1 and out['data_policy']['state'] == 'allowed_public_normal'
    result, manual = world.cli('route', OP, *world.args(), *ORACLE, '--decision-id', 'manual-normal',
                               *test_route.deepseek_flags(classification='public'), evaluation_time=LATER)
    assert result.returncode == 0, result.stderr
    assert manual['data_policy']['state'] == 'allowed_public_normal'


# ---- the declared scope --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('extra,op,proof', [
    ((), OP, ORACLE),
    (('--data-classification', 'sensitive'), OP, ORACLE),
    (PUBLIC + ('--sensitive',), OP, ORACLE),
    (PUBLIC, 'docs.lookup', JUDGED),
    (PUBLIC, 'implement.quota-tight', ORACLE),
])
def test_public_oracle_and_a_declared_operation_are_required(world, extra, op, proof):
    out = route_at(world, LATER, *extra, op=op, proof=proof)
    assert not_dispatched(out) and not free_rows(out)


@pytest.mark.parametrize('change,state', [
    (lambda model: model.update(options=[]), 'unknown_effort_surface'),
    (lambda model: model['options'][0].update(id='effort'), 'unknown_effort_surface'),
    (lambda model: model['options'][0].update(values=[{'id': 'low'}, {'id': 'max'}]), 'unsupported_effort'),
    (lambda model: model['options'].append(dict(model['options'][0])), 'unknown_effort_surface'),
])
def test_the_declared_effort_option_and_value_are_exact(world, change, state):
    opencode = world.catalog['data']['providers'][-1]
    change(opencode['models'][0])
    out = route_at(world, LATER, *PUBLIC)
    row = free_rows(out)[0]
    assert row['removed_by'] == 'not_runnable' and row['detail']['state'] == state
    assert not_dispatched(out)


def test_only_the_declared_account_can_carry_the_normal_route(world):
    second = json.loads(json.dumps(world.catalog['data']['providers'][-1]))
    second['instanceId'] = 'opencode-alt'
    world.catalog['data']['providers'].append(second)
    world.accounts['accounts']['opencode-alt'] = {'billing': 'subscription', 'quota_provider': None, 'quota_source': None}
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out)
    assert not any(t.get('instance') == 'opencode-alt' for t in free_rows(out))
    refused = route_at(world, LATER, *PUBLIC, *exact_flags(account='opencode-alt'))
    assert not_dispatched(refused)
    assert refused['data_policy']['refusal'] == 'account_not_declared_for_normal_route'


def test_a_missing_catalog_account_removes_the_route(world):
    world.catalog['data']['providers'].pop()
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out)
    assert free_rows(out)[0]['removed_by'] == 'no_catalog_account'


# ---- explicit selection --------------------------------------------------------------------------------------------------

def test_an_exact_reasoned_override_can_select_the_normal_target(world):
    out = route_at(world, LATER, *PUBLIC, *exact_flags())
    assert selected(out) and out['routing']['final']['overridden'] is True
    assert out['data_policy']['state'] == 'allowed_public_normal'
    assert out['data_policy']['basis'] == 'qualified_free'


@pytest.mark.parametrize('extra,refusal', [
    (('--data-classification', 'sensitive'), 'public_classification_required'),
    (PUBLIC + ('--sensitive',), 'sensitive_inputs'),
])
def test_an_override_cannot_expand_the_public_floor(world, extra, refusal):
    out = route_at(world, LATER, *extra, *exact_flags())
    assert not_dispatched(out)
    assert out['data_policy']['state'] == 'refused' and out['data_policy']['refusal'] == refusal


@pytest.mark.parametrize('proof,effort,refusal,op', [
    (JUDGED, 'high', 'proof_class_not_declared_for_normal_route', 'docs.lookup'),
    (ORACLE, 'low', 'effort_not_declared_for_normal_route', OP),
])
def test_an_override_cannot_use_an_undeclared_proof_or_effort(world, proof, effort, refusal, op):
    out = route_at(world, LATER, *PUBLIC, *exact_flags(effort=effort), op=op, proof=proof)
    assert not_dispatched(out)
    assert out['data_policy']['refusal'] == refusal


def test_an_override_needs_a_reason(world):
    result, out = world.cli('route', OP, *world.args(), *ORACLE, *PUBLIC, *exact_flags()[:-2],
                            evaluation_time=LATER)
    assert result.returncode == 1 and out is None
    assert 'require --reason' in result.stderr


def test_an_override_needs_every_exact_field(world):
    flags = list(exact_flags())
    index = flags.index('effort=high')
    del flags[index - 1:index + 1]
    out = route_at(world, LATER, *PUBLIC, *flags)
    assert not_dispatched(out)
    assert out['data_policy']['refusal'] == 'exact_normal_override_required'


def test_the_free_account_does_not_make_another_model_free(world):
    out = route_at(world, LATER, *PUBLIC, *exact_flags(model='opencode/paid-model'))
    assert not_dispatched(out) and out['data_policy']['state'] == 'refused'
    assert out['data_policy']['refusal'] == 'model_not_declared_for_experimental_route'


# ---- bounded review is an unvalidated alternative ------------------------------------------------------------------------

def test_bounded_review_is_an_unvalidated_alternative_not_an_automatic_target(world):
    current_at_pinned_capture(world)
    out = route_at(world, NOW, *PUBLIC, op='review.rule-check', proof=JUDGED)
    assert out['routing']['chosen']['arm']['provider'] == 'claude'
    row = free_rows(out)[0]
    assert row['basis'] == 'unvalidated' and row['removed_by'] is None
    assert row['selection_basis'] is None and row['eligibility_reasons'] == ['unvalidated_candidate']
    assert row['rank']['eligibility'] == 1 and row['rank']['price_class'] == 1


def test_an_unvalidated_normal_target_is_never_authorized_automatically(world):
    current_at_pinned_capture(world)
    world.catalog['data']['providers'] = [world.catalog['data']['providers'][-1]]  # only the free arm remains
    out = route_at(world, NOW, *PUBLIC, op='review.rule-check', proof=JUDGED)
    assert out['routing']['chosen']['basis'] == 'unvalidated'
    assert not_dispatched(out) and out['data_policy']['state'] == 'refused'
    assert out['data_policy']['refusal'] == 'exact_normal_override_required'


def test_an_exact_reasoned_override_selects_the_unvalidated_review(world):
    current_at_pinned_capture(world)
    out = route_at(world, NOW, *PUBLIC, *exact_flags(), op='review.rule-check', proof=JUDGED)
    assert selected(out) and out['routing']['final']['overridden'] is True
    assert out['data_policy']['state'] == 'allowed_public_normal'
    assert out['data_policy']['basis'] == 'unvalidated'


def test_broad_audit_does_not_receive_the_normal_alternative(world):
    out = route_at(world, NOW, *PUBLIC, op='review.audit', proof=JUDGED)
    assert not free_rows(out)


# ---- a destination offer is decided first (dot-first) --------------------------------------------------------------------

def test_a_destination_offer_is_decided_before_the_normal_route(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'dot'), name='dot', purposes=('review',)))
    offered = route_at(world, LATER, *PUBLIC, '--destinations', cfg, '--purpose', 'review', '--inputs', 'github')
    assert offered['destination'] == 'dot' and offered['launch_ready'] is False
    assert FREE_TARGET not in all_target_values(offered)
    assert free_rows(offered)[0]['removed_by'] is None, 'the free pair stays an eligible fallback, never a target'
    unlisted = route_at(world, LATER, *PUBLIC, '--destinations', cfg, '--purpose', 'execution', '--inputs', 'github')
    assert selected(unlisted), 'the same request for a purpose the destination does not list takes the normal route'


# ---- cooldown, telemetry, funding and revocation -------------------------------------------------------------------------

def test_a_cooling_normal_route_waits_and_is_reevaluated_after_the_cooldown(world):
    current_at_pinned_capture(world)
    world.events = [event('opencode', RATE, at=at) for at in
                    ('2026-10-11T00:10:00.000Z', '2026-10-11T00:15:00.000Z', '2026-10-11T00:20:00.000Z')]
    cooling = route_at(world, NOW, *PUBLIC)
    assert not_dispatched(cooling)
    row = free_rows(cooling)[0]
    assert row['removed_by'] == 'cooling_down' and row['detail']['retry_after'] == '2026-10-11T01:10:00+00:00'
    assert not_dispatched(route_at(world, '2026-10-11T01:10:00Z', *PUBLIC))
    assert selected(route_at(world, '2026-10-11T01:10:01Z', *PUBLIC))


def test_missing_telemetry_is_unknown_and_reported(world):
    world.with_db = False
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out) and out['routing']['chosen']['availability'] == 'unknown'
    row = free_rows(out)[0]
    assert row['availability']['class'] == 'unknown' and row['availability']['reason']


def test_the_declared_free_tuple_is_not_withheld_for_account_funding(world):
    world.accounts['accounts']['opencode'] = {'billing': 'metered', 'quota_provider': None, 'quota_source': None,
                                              'funding': {'verified_until': None}}
    out = route_at(world, LATER, *PUBLIC)
    assert selected(out) and out['routing']['final']['billing'] == 'free'


def test_the_normal_route_has_no_artificial_expiry(world):
    assert 'expires_at_utc' not in normal(world)
    out = route_at(world, '2027-01-01T00:00:00Z', *PUBLIC)
    assert selected(out)


def test_removing_the_operation_revokes_forward_availability(world):
    normal(world)['operations'] = []
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out) and not free_rows(out)


def test_an_owner_avoid_directive_wins_over_the_normal_route(world):
    world.add_directive('avoid-free', 'avoid', f'model={DEEPSEEK_FREE}', until='2026-10-17T00:00:00Z',
                        recorded_at='2026-10-16T00:00:00+00:00')
    out = route_at(world, LATER, *PUBLIC)
    assert not_dispatched(out)
    assert free_rows(out)[0]['removed_by'] == 'avoid_directive'


def test_checker_independence_removes_the_normal_route(world):
    out = route_at(world, LATER, *PUBLIC, '--maker-model', f'opencode:{DEEPSEEK_FREE}', '--independence', 'vendor')
    assert not_dispatched(out)
    assert free_rows(out)[0]['removed_by'] == 'independence'


# ---- receipts, replay, simulation ----------------------------------------------------------------------------------------

def test_a_normal_receipt_replays_and_is_refused_once_the_declaration_is_removed(world):
    first = route_at(world, LATER, *PUBLIC, record='normal-1')
    assert selected(first)
    assert route_at(world, LATER, *PUBLIC, record='normal-1')['replayed'] is True
    world.policy['free_routes'] = []
    revoked = route_at(world, LATER, *PUBLIC, record='normal-1')
    assert revoked['target'] is None and revoked['launch_ready'] is False
    assert revoked['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'
    assert all(target is None for target in all_target_values(revoked))


def test_a_simulation_never_launches_or_records_the_normal_route(world):
    result, out = world.cli('route', OP, *world.args(), *ORACLE, *PUBLIC, '--now', LATER, raw=True)
    assert result.returncode == 0, result.stderr
    assert out['target'] is None and out['launch_ready'] is False
    assert out['simulated_target'] == FREE_TARGET and 'dispatch' not in out
    assert not world.receipts.exists()


# ---- the shipped declaration and its shared validator ---------------------------------------------------------------------

VALIDATOR = None


def load_validator():
    global VALIDATOR
    if VALIDATOR is None:
        path = REPO / 'scripts' / 'validate_data_pack.py'
        spec = importlib.util.spec_from_file_location('normal_route_data_pack_validator', path)
        VALIDATOR = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = VALIDATOR
        spec.loader.exec_module(VALIDATOR)
    return VALIDATOR


BASE_ROUTE = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())['free_routes'][0]


def changed_route(change):
    route = copy.deepcopy(BASE_ROUTE)
    change(route)
    return route


def test_the_pack_ships_an_exact_normal_scope_without_experimental_fields():
    route = BASE_ROUTE
    assert 'expires_at_utc' not in route and 't3_transport' not in route and 'expiry_basis' not in route
    operations = {item['id']: item for item in route['operations']}
    assert set(operations) == {'implement.oracle-bounded', 'docs.lookup', 'review.rule-check'}
    for op in ('implement.oracle-bounded', 'docs.lookup'):
        assert operations[op]['basis'] == 'qualified_free'
        assert operations[op]['proof_classes'] == ['oracle'] and operations[op]['efforts'] == ['high']
        assert operations[op]['evidence_basis'] == 'bounded_gate_evidence'
    review = operations['review.rule-check']
    assert review['basis'] == 'unvalidated' and review['proof_classes'] == ['judged']
    assert review['efforts'] == ['high'] and review['evidence_basis'] == 'classification_probes_only'
    assert set(route['known_risks']) == {'shared_server_host_tools', 'injected_t3_thread_read_private_thread_access'}
    for item in route['operations']:
        assert (REPO / 'data/model-choice-policy' / item['evidence_ref']).is_file()


def test_an_empty_operation_list_is_a_valid_revocation():
    assert check_normal_free_routes([changed_route(lambda route: route.update(operations=[]))]) == []


@pytest.mark.parametrize('change', [
    lambda route: route.update(cost_usd=1),
    lambda route: route.update(status='expired_as_of_2026-10-15'),
    lambda route: route.pop('effort_option'),
    lambda route: route['data_policy'].update(classification_required='sensitive'),
    lambda route: route['data_policy'].update(sensitive_flag_forces_refusal=False),
    lambda route: route['data_policy']['prohibited_inputs'].remove('PDPP_data'),
    lambda route: route.pop('known_risks'),
    lambda route: route['known_risks'].remove('shared_server_host_tools'),
    lambda route: route['evidence'][0].update(accessed='2026-02-30'),
    lambda route: route.pop('operations'),
    lambda route: route['operations'].append(copy.deepcopy(route['operations'][0])),
    lambda route: route['operations'][0].update(proof_classes=['judged']),
    lambda route: route['operations'][0].update(evidence_basis='classification_probes_only'),
    lambda route: route['operations'][0].update(evidence_ref='missing.json'),
    lambda route: route['operations'][2].update(proof_classes=['oracle']),
    lambda route: route['operations'][2].update(evidence_basis='bounded_gate_evidence'),
    lambda route: route['operations'][2].update(extra='arbitrary'),
])
def test_the_normal_contract_rejects_a_price_privacy_or_evidence_violation(change):
    assert check_normal_free_routes([changed_route(change)])


def test_both_pack_checkers_use_the_same_normal_route_contract(tmp_path):
    pack_dir = tmp_path / 'model-choice-policy'
    shutil.copytree(REPO / 'data/model-choice-policy', pack_dir)
    policy_path = pack_dir / 'operating-points.json'
    policy = json.loads(policy_path.read_text())
    policy['free_routes'][0]['data_policy']['classification_required'] = 'sensitive'
    policy_path.write_text(json.dumps(policy))

    shared_errors = check_normal_free_routes(policy['free_routes'])
    validator = load_validator()
    errors = validator.Errors()
    validator.validate_policy_pack(pack_dir, errors)
    assert all(any(message in item for item in errors.items) for message in shared_errors)

    result = subprocess.run([str(REPO / 'tools/model-policy-ops/model-policy-ops'), 'check',
                             '--policy', str(policy_path), '--no-user-policy'],
                            text=True, capture_output=True, cwd=REPO)
    assert result.returncode == 1
    assert all(message in result.stderr for message in shared_errors)


def test_a_missing_evidence_file_is_rejected_by_both_checkers(tmp_path):
    pack_dir = tmp_path / 'model-choice-policy'
    shutil.copytree(REPO / 'data/model-choice-policy', pack_dir)
    policy_path = pack_dir / 'operating-points.json'
    policy = json.loads(policy_path.read_text())
    policy['free_routes'][0]['operations'][0]['evidence_ref'] = 'evidence/missing.json'
    policy_path.write_text(json.dumps(policy))

    validator = load_validator()
    errors = validator.Errors()
    validator.validate_policy_pack(pack_dir, errors)
    assert any('evidence_ref evidence/missing.json is missing' in item for item in errors.items)

    result = subprocess.run([str(REPO / 'tools/model-policy-ops/model-policy-ops'), 'check',
                             '--policy', str(policy_path), '--no-user-policy'],
                            text=True, capture_output=True, cwd=REPO)
    assert result.returncode == 1
    assert 'evidence_ref evidence/missing.json is missing' in result.stderr


# Regression: zero source price schedules budget; it does not revoke an owner's preference.
@pytest.mark.parametrize('selector', ['account', 'model'])
def test_explicit_owner_preference_precedes_qualified_free_price(world, selector):
    current_at_pinned_capture(world)
    world.codex(100)
    primary = next(op for op in world.policy['operating_points'] if op['id'] == OP)['expands_to']
    target = 'account=codex' if selector == 'account' else f"model={primary['model']}"
    world.add_directive('prefer-subscription', 'prefer', target,
                        until='2026-10-12T00:00:00Z', recorded_at='2026-10-10T00:00:00+00:00')
    out = route_at(world, NOW, *PUBLIC)
    assert out['routing']['chosen']['instance'] == 'codex'
    assert out['routing']['chosen']['arm']['model'] == primary['model']
    assert out['launch_ready'] is True
    assert free_rows(out)[0]['removed_by'] is None


def test_normal_replay_refuses_an_obsolete_option_key_without_rewriting_receipt(world):
    first = route_at(world, LATER, *PUBLIC, record='option-binding')
    assert selected(first)
    saved = world.receipts.read_bytes()
    normal(world)['effort_option'] = 'reasoningEffort'
    refused = route_at(world, LATER, *PUBLIC, record='option-binding')
    assert refused['replayed'] is True
    assert not_dispatched(refused)
    assert refused['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'
    assert all(target is None for target in all_target_values(refused))
    assert world.receipts.read_bytes() == saved
    normal(world)['effort_option'] = 'variant'
    restored = route_at(world, LATER, *PUBLIC, record='option-binding')
    assert restored == first | {'replayed': True}
    assert world.receipts.read_bytes() == saved


@pytest.mark.parametrize('location', [('target',), ('selection', 'target'), ('routing', 'final', 'target')])
@pytest.mark.parametrize('options', [{}, {'variant': 'high', 'effort': 'high'}, {'variant': 'low'}])
def test_every_actual_normal_target_binding_is_checked(world, location, options):
    import policy_decisions as decisions
    first = route_at(world, LATER, *PUBLIC, record='actual-binding')
    assert selected(first)
    saved = world.receipts.read_bytes()
    replay = copy.deepcopy(first)
    node = replay
    for key in location:
        node = node[key]
    node['options'] = options
    decisions.apply_experimental_route_policy(
        replay, world.policy, datetime.fromisoformat(LATER.replace('Z', '+00:00')), replay=True)
    assert not_dispatched(replay)
    assert replay['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'
    assert all(target is None for target in all_target_values(replay))
    assert world.receipts.read_bytes() == saved


def test_record_time_replay_rechecks_the_current_option_binding(world, monkeypatch):
    import io
    import runpy
    from contextlib import redirect_stdout
    from datetime import datetime
    import policy_decisions as decisions

    first = route_at(world, LATER, *PUBLIC, record='binding-race')
    assert selected(first)
    saved = world.receipts.read_bytes()
    normal(world)['effort_option'] = 'reasoningEffort'
    world.write()
    # World.cli starts a subprocess. Load the same real CLI in-process so this
    # read-boundary fault is observable, without replacing execute or append.
    cli = runpy.run_path(str(test_route.TOOL), run_name='binding_race_cli')
    calls, appended = [], []
    original_find = decisions.find_receipt
    original_append = decisions.append_receipt

    def initial_miss(path, decision_id):
        assert path == world.receipts and decision_id == 'binding-race'
        assert original_find(path, decision_id) is not None
        calls.append((path, decision_id))
        return None

    def observe_append(*args, **kwargs):
        recorded = original_append(*args, **kwargs)
        appended.append(copy.deepcopy(recorded))
        return recorded

    monkeypatch.setattr(decisions, 'find_receipt', initial_miss)
    monkeypatch.setattr(decisions, 'append_receipt', observe_append)
    monkeypatch.setattr(decisions.directive_store, 'now',
                        lambda: datetime.fromisoformat(LATER.replace('Z', '+00:00')))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(world.tmp / 'xdg'))
    args = test_route.full_record(list(map(str, [
        'route', OP, *world.args(), *ORACLE, *PUBLIC, '--no-user-policy',
        '--decision-id', 'binding-race', '--record', '--parent-model', 'pm',
        '--parent-provider-instance', 'pp', '--parent-thread', 'pt'])))
    monkeypatch.setattr(sys, 'argv', [str(test_route.TOOL), *args])
    output = io.StringIO()
    with redirect_stdout(output):
        assert cli['main']() == 0
    refused = json.loads(output.getvalue())
    assert calls == [(world.receipts, 'binding-race')], 'the initial receipt lookup must miss'
    assert len(appended) == 1 and appended[0]['replayed'] is True
    assert appended[0]['target'] == FREE_TARGET, 'the real append must find the old variant receipt'
    assert refused['replayed'] is True
    assert not_dispatched(refused)
    assert refused['data_policy']['refusal'] == 'normal_route_receipt_not_currently_authorized'
    assert all(target is None for target in all_target_values(refused))
    assert world.receipts.read_bytes() == saved
    monkeypatch.setattr(decisions, 'find_receipt', original_find)
    normal(world)['effort_option'] = 'variant'
    assert route_at(world, LATER, *PUBLIC, record='binding-race') == first | {'replayed': True}
    assert world.receipts.read_bytes() == saved
