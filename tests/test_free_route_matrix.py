"""Independent 40-case matrix for the owner-authorized DeepSeek free-route trial.

The free arm can reach a caller on two CLI surfaces: the ``route`` trial *preference* (entry-gated)
and the exact ``resolve --no-op`` *resolution* (gate-authorized). This module freezes both surfaces
as a 20-case block each -- five variants x full/brief rendering x both replay paths -- for exactly
40 cases, and drives the real CLI against the reviewed data rather than against the
implementation's own notion of an entry.

The two replay paths are the ones the tool actually has: an identical call that finds its stored
receipt (``stored``) and an identical call whose receipt lookup races and recomputes against current
qualification (``race``). The ``race`` path is in-process so a missing receipt can be injected; both
paths must re-derive eligibility and neither may trust the stored row.

Refusal, revocation and security-floor coverage lives in ``test_qualified_operations.py``; this
matrix pins the *free* selection, its declared scope, and the exact-target and input gates it
depends on.
"""
import json
import runpy
import sys
from datetime import datetime

import pytest

from test_route import (REPO, DEEPSEEK_FREE, PROVENANCE, add_opencode_fixture, local_input,
                        manifest, world)  # noqa: F401
from test_route_authorization import CLOCK, run

sys.path.insert(0, str(REPO / 'tools' / 'model-policy-ops' / 'lib'))
import directives  # noqa: E402
import policy_decisions as pd  # noqa: E402

TASK_LINK = ('{"v":1,"consumer_task_id":"task-matrix","workflow_id":"wf-matrix",'
             '"stage_id":"s1","role":"maker"}')
TOOL = str(REPO / 'tools/model-policy-ops/model-policy-ops')
VANA_INPUT = manifest({'kind': 'github', 'repo': 'vana-com/minnows', 'visibility': 'private'})

# Block A -- the trial preference on `route`. Five reviewed eligibility entries.
# label, op, purpose, proof, reviewed scope, independent checker required, input manifest
ENTRIES = [
    ('source-change', 'implement.oracle-bounded', None, 'oracle',
     'small_public_source_change_full_deterministic_oracle', False,
     manifest(local_input('tools/model-policy-ops/lib/input_provenance.py'))),
    ('test-design', 'implement.oracle-bounded', 'test-design', 'judged',
     'bounded_public_test_design_checked_by_independent_checker', True,
     manifest(local_input('tests/test_input_provenance.py'))),
    ('known-source-lookup', 'docs.lookup', None, 'oracle',
     'known_public_source_lookup_structured_fact_oracle', False,
     manifest({'kind': 'github', 'repo': 'tnunamak/minnows', 'visibility': 'private'})),
    ('research-fanout', 'fanout.dollar-tight', 'research', 'judged',
     'bounded_public_research_checked_by_ci_or_independent_checker', True,
     manifest({'kind': 'github', 'repo': 'tnunamak/minnows', 'visibility': 'private'},
              local_input('tools/model-policy-ops/lib/experimental_routes.py'))),
    ('status-report', 'recover.report', None, 'judged',
     'bounded_public_status_report_checked_by_independent_checker', True,
     manifest(local_input('data/model-choice-policy/README.md', classification='docs'))),
]

# Block B -- the exact resolution on `resolve --no-op`. Five authorization gates.
# label, flags, expected refusal (None when the free target is authorized)
GATES = [
    ('published', {}, None),
    ('judged-stated', {'proof': 'judged', 'purpose': 'test-design'}, None),
    ('unclassified', {'classified': False}, 'public_classification_required'),
    ('sensitive', {'sensitive': True}, 'sensitive_inputs'),
    ('refused-input', {'inputs': VANA_INPUT}, 'input_provenance_refused_company_private_namespace'),
]

TRIAL_CASES = [
    (label, op, purpose, proof, scope, required, inputs, brief, path)
    for (label, op, purpose, proof, scope, required, inputs) in ENTRIES
    for brief in (False, True)
    for path in ('stored', 'race')
]
TRIAL_IDS = [f'trial/{case[0]}/{"brief" if case[7] else "full"}/{case[8]}' for case in TRIAL_CASES]
EXACT_CASES = [
    (label, flags, refusal, brief, path)
    for (label, flags, refusal) in GATES
    for brief in (False, True)
    for path in ('stored', 'race')
]
EXACT_IDS = [f'exact/{case[0]}/{"brief" if case[3] else "full"}/{case[4]}' for case in EXACT_CASES]

MATRIX_SIZE = len(TRIAL_CASES) + len(EXACT_CASES)
assert len(TRIAL_CASES) == 20 and len(EXACT_CASES) == 20, MATRIX_SIZE


def record_flags(decision_id):
    return ['--record', '--decision-id', decision_id, '--task-link', TASK_LINK,
            '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt']


def trial_flags(world, op, purpose, proof, inputs, *, brief, decision_id):
    argv = ['route', op, *world.args(), '--destinations', world.tmp / 'no-destinations.json',
            '--reason', 'Free-route matrix on a public task', '--data-classification', 'public',
            '--input-provenance', inputs, '--proof-class', proof, '--override', 'effort=high']
    if purpose:
        argv += ['--purpose', purpose]
    if brief:
        argv += ['--brief']
    return argv + record_flags(decision_id)


def exact_flags(world, *, classified=True, sensitive=False, proof=None, purpose=None,
                inputs=PROVENANCE, brief, decision_id=None):
    argv = ['resolve', '--no-op', *world.args(), '--destinations', world.tmp / 'no-destinations.json',
            '--reason', 'Free-route matrix on a public task', '--input-provenance', inputs,
            '--experimental-route', '--override', 'provider=opencode', '--override',
            f'model={DEEPSEEK_FREE}', '--override', 'effort=high', '--override', 'account=opencode']
    if classified:
        argv += ['--data-classification', 'public']
    if sensitive:
        argv += ['--sensitive']
    if proof:
        argv += ['--proof-class', proof]
    if purpose:
        argv += ['--purpose', purpose]
    if brief:
        argv += ['--brief']
    return argv + (record_flags(decision_id) if decision_id else [])


def in_process(monkeypatch, capsys, argv):
    """The `race` replay path: the receipt lookup misses, so current qualification must decide."""
    monkeypatch.setattr(pd, 'find_receipt', lambda *args, **kwargs: None)
    monkeypatch.setattr(directives, 'now', lambda: datetime.fromisoformat(CLOCK.replace('Z', '+00:00')))
    # `--no-user-policy` is accepted by route/check only; resolve already gets its policy from the world.
    extra = ['--no-user-policy'] if argv[0] == 'route' else []
    monkeypatch.setattr(sys, 'argv', [TOOL, *map(str, argv), *extra])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(TOOL, run_name='__main__')
    captured = capsys.readouterr()
    assert exited.value.code == 0, captured.err
    return json.loads(captured.out)


def assert_free(out):
    assert out['launch_ready'] is True
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE,
                             'options': {'variant': 'high'}}
    assert out['data_policy']['state'] == 'allowed_public_experimental'
    assert out['data_policy']['refusal'] is None
    assert out['data_policy']['input_provenance']['state'] == 'allowed'


def assert_refused(out, refusal):
    assert out['launch_ready'] is False
    assert out['target'] is None
    assert out['data_policy']['state'] == 'refused'
    assert out['data_policy']['refusal'] == refusal, out['data_policy']['refusal']


@pytest.mark.parametrize('label,op,purpose,proof,scope,required,inputs,brief,path', TRIAL_CASES,
                         ids=TRIAL_IDS)
def test_the_trial_preference_covers_route_full_brief_and_both_replay_paths(
        world, monkeypatch, capsys, label, op, purpose, proof, scope, required, inputs, brief, path):
    add_opencode_fixture(world)
    argv = trial_flags(world, op, purpose, proof, inputs, brief=brief,
                       decision_id=f'matrix-trial-{label}-{brief}')
    first = run(world, *argv)
    assert_free(first)
    if path == 'stored':
        out = run(world, *argv)
        assert out == first | {'replayed': True}
    else:
        out = in_process(monkeypatch, capsys, argv)
        assert out['replayed'] is True
        assert_free(out)

    if brief:
        assert out['selected']['account'] == 'opencode'
    else:
        block = out['routing']['trial']
        assert block['state'] == 'selected'
        assert block['selected'] == 'owner_authorized_trial'
        assert block['scope'] == scope
        assert block['independent_check_required'] is required


@pytest.mark.parametrize('label,flags,refusal,brief,path', EXACT_CASES, ids=EXACT_IDS)
def test_the_exact_resolution_covers_resolve_full_brief_and_both_replay_paths(
        world, monkeypatch, capsys, label, flags, refusal, brief, path):
    add_opencode_fixture(world)
    allowed = refusal is None
    decision_id = f'matrix-exact-{label}-{brief}' if allowed else None
    argv = exact_flags(world, brief=brief, decision_id=decision_id, **flags)
    first = run(world, *argv)
    if allowed:
        assert_free(first)
        if path == 'stored':
            out = run(world, *argv)
            assert out == first | {'replayed': True}
        else:
            out = in_process(monkeypatch, capsys, argv)
            assert out['replayed'] is True
        assert_free(out)
        assert out['data_policy']['t3_transport']['enabled'] is True
    else:
        assert_refused(first, refusal)
        out = run(world, *argv) if path == 'stored' else in_process(monkeypatch, capsys, argv)
        assert_refused(out, refusal)


def test_the_matrix_names_every_reviewed_entry_once(world):
    """The matrix is only independent if it covers the reviewed entries and nothing else."""
    from experimental_routes import eligible_operation
    route = world.policy['experimental_free_routes'][0]
    reviewed = {(e['id'], tuple(e.get('purposes') or ())) for e in route['qualified_operations']}
    covered = {(op, tuple([purpose] if purpose else ())) for _, op, purpose, *_ in ENTRIES}
    assert covered == reviewed
    for label, op, purpose, proof, *_ in ENTRIES:
        picked = eligible_operation(route, op, purpose)
        assert picked is not None, label
        assert proof in picked['proof_classes'], label
