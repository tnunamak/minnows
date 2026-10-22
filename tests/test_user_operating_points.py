"""Per-user operating-point overrides: the overlay may set an op's default arm, its alternates, its default purpose
and the reason its xhigh/max arms need. The pack's evidence does not change, and explicit flags still win."""
import json
import sys

import pytest

from test_route import REPO, World
from test_route_destinations import capacity, config, entry

sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
import user_policy  # noqa: E402

OP = 'implement.standard'
LUNA_MAX = {'provider': 'codex', 'model': 'gpt-6-luna', 'effort': 'max', 'mode': 'standard'}
HAIKU_MAX = {'provider': 'claude', 'model': 'claude-haiku-5-5', 'effort': 'max', 'basis': 'unvalidated',
             'evidence_refs': [], 'known_gaps': ['test row: no measurement at max']}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def overlay(world, operating_points, name='user-policy.json'):
    path = world.tmp / name
    path.write_text(json.dumps({'schema_version': 1, 'routing': {}, 'operating_points': operating_points}))
    path.chmod(0o600)
    return path


def arms(out):
    return {(row['arm']['provider'], row['arm']['model']) for row in out['routing']['trace']}


def destination_config(tmp_path, *purposes):
    return config(tmp_path, entry(capacity(tmp_path, 'dot'), name='dot', purposes=purposes))


def test_overlay_replaces_the_default_arm_and_records_provenance(world):
    path = overlay(world, {OP: {'expands_to': LUNA_MAX, 'reason': 'test: standard implementation at max'}})
    out = world.route(OP, '--user-policy', path)
    chosen = out['routing']['chosen']
    assert chosen['arm'] == {'provider': 'codex', 'model': 'gpt-6-luna', 'effort': 'max'}
    assert chosen['selection_basis'] == 'primary'
    assert out['routing']['user_policy']['operating_points'] == [OP]


def test_overlay_replaces_the_alternates(world):
    path = overlay(world, {OP: {'expands_to': LUNA_MAX, 'candidates': [HAIKU_MAX], 'reason': 'test'}})
    out = world.route(OP, '--user-policy', path)
    assert arms(out) == {('codex', 'gpt-6-luna'), ('claude', 'claude-haiku-5-5')}


def test_overlay_purpose_sends_a_review_to_the_destination_and_an_explicit_flag_wins(world, tmp_path):
    cfg = destination_config(tmp_path, 'review', 'research')
    path = overlay(world, {'review.audit': {'purpose': 'review'}})
    out = world.route('review.audit', '--proof-class', 'judged', '--user-policy', path, '--destinations', cfg)
    assert out['destination'] == 'dot'
    assert out['routing']['user_policy']['operating_points'] == ['review.audit']
    without = world.route('review.audit', '--proof-class', 'judged', '--destinations', cfg)
    assert without.get('destination') != 'dot', 'the destination needs a purpose, and the flag was not given'
    explicit = world.route('review.audit', '--proof-class', 'judged', '--user-policy', path, '--destinations', cfg,
                           '--purpose', 'execution')
    assert explicit.get('destination') != 'dot', 'an explicit --purpose wins over the overlay default'


def test_an_overlay_reason_lets_a_max_arm_route_without_a_reason_flag(world):
    path = overlay(world, {OP: {'expands_to': LUNA_MAX, 'reason': 'owner default'}})
    out = world.route(OP, '--user-policy', path)
    assert out['routing']['chosen']['arm']['effort'] == 'max'


def test_no_user_policy_ignores_an_overlay_in_the_default_location(world):
    default = world.tmp / 'xdg' / 'model-policy'
    default.mkdir(parents=True)
    (default / 'user-policy.json').write_text(json.dumps({'schema_version': 1, 'routing': {}, 'operating_points': {
        OP: {'expands_to': LUNA_MAX, 'reason': 'test'}}}))
    (default / 'user-policy.json').chmod(0o600)
    assert world.route(OP)['routing']['chosen']['arm']['model'] == 'gpt-6-luna'
    assert world.route(OP, '--no-user-policy')['routing']['chosen']['arm']['model'] != 'gpt-6-luna'


def test_the_merge_changes_only_the_overridden_op_and_leaves_the_pack_alone():
    path = REPO / 'data/model-choice-policy/operating-points.json'
    pack = json.loads(path.read_text())
    merged = user_policy.merge(pack, {}, {OP: {'expands_to': LUNA_MAX, 'reason': 'test'}})
    before = {op['id']: op for op in pack['operating_points']}
    after = {op['id']: op for op in merged['operating_points']}
    assert after[OP]['expands_to'] == LUNA_MAX and after[OP]['candidates'] == before[OP]['candidates']
    assert {k: v for k, v in after.items() if k != OP} == {k: v for k, v in before.items() if k != OP}
    assert merged['routing'] == pack['routing']
    assert pack == json.loads(path.read_text()), 'merge must not mutate the pack it was given'


@pytest.mark.parametrize('operating_points,text', [
    ({'implement.nope': {'purpose': 'review'}}, 'unknown operating point'),
    ({OP: {}}, 'must be a non-empty object'),
    ({OP: {'cost': 1}}, 'keys cost are not overridable'),
    ({OP: {'expands_to': {'provider': 'codex', 'model': 'gpt-6-luna'}, 'reason': 'x'}}, 'expands_to.effort must be a non-empty string'),
    ({OP: {'purpose': 'vibes'}}, 'purpose must be one of'),
    ({OP: {'candidates': [{'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'primary'}]}}, 'task_benchmark_prior or unvalidated'),
    ({OP: {'expands_to': LUNA_MAX}}, 'an xhigh or max effort needs a reason'),
    ({OP: {'candidates': [{'provider': 'claude', 'model': 'claude-haiku-5-5', 'effort': 'low', 'basis': 'task_benchmark_prior',
                           'evidence_refs': [], 'known_gaps': []}]}}, 'an overlay alternate must use basis unvalidated'),
    ({OP: {'expands_to': LUNA_MAX, 'reason': '  '}}, 'reason must be a non-empty string'),
    ([], 'operating_points must be an object'),
])
def test_malformed_overrides_are_refused_by_check_and_route(world, operating_points, text):
    path = overlay(world, operating_points)
    result, _ = world.cli('check', *world.args(), '--user-policy', path)
    assert result.returncode == 1 and text in result.stderr, result.stderr
    result, out = world.cli('route', OP, *world.args(), '--decision-id', 'bad', '--proof-class', 'judged', '--user-policy', path)
    assert out is None and result.returncode == 1 and text in result.stderr, result.stderr
