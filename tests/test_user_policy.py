"""The per-user overlay: it enables quota substitution and tunes observation limits, and nothing else.

Inputs are the pinned route-live-20261011 snapshot. The shipped pack does not enable substitution, so each
test starts with it off and adds the overlay it needs.
"""
import hashlib
import json
import sys

import pytest

import test_quota_substitution as qs
from test_quota_substitution import THRESHOLDS, TOOL, block, pick
from test_route import REPO, World
from test_route_destinations import capacity, config, entry
from test_route_observation_notes import notes_of, route, run_row

sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
from runtime_sources import digest  # noqa: E402

OVERLAY = {'schema_version': 1, 'routing': {'quota_substitution': THRESHOLDS}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(qs.test_route, 'NOW', qs.NOW)
    found = qs.QuotaWorld(tmp_path)
    found.policy['routing'].pop('quota_substitution')
    return found


@pytest.fixture
def one(tmp_path):
    """The observation-notes world: the plain pack, one available destination and an empty ledger path."""
    found = World(tmp_path)
    found.cfg = config(tmp_path, entry(capacity(tmp_path, 'cap')))
    found.ledger = found.receipts.with_name('observations.jsonl')
    return found


def write(world, value, name='user-policy.json', mode=0o600):
    path = world.tmp / name
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(mode)
    return path


def route_fails(world, *flags):
    result, out = world.cli('route', 'review.audit', *world.args(), '--decision-id', 'probe', *flags)
    assert out is None and result.returncode == 1 and result.stderr.startswith('FAIL'), result.stdout + result.stderr
    return result.stderr


def test_no_overlay_means_substitution_is_off(world):
    out = world.route('review.audit')   # the default path (under the test XDG_CONFIG_HOME) is absent
    assert pick(out) == ('codex', 'gpt-6.1-sol') and 'quota_substitution' not in out['routing']
    assert out['routing']['user_policy'] is None and out['effective_policy_sha256'] == out['policy_sha256']
    assert not world.state.exists()


def test_the_default_path_is_read_when_no_flag_is_given(world):
    default = world.tmp / 'xdg/model-policy/user-policy.json'
    default.parent.mkdir(parents=True)
    default.write_text(json.dumps(OVERLAY))
    default.chmod(0o600)
    out = world.route('review.audit')
    assert out['routing']['user_policy']['path'] == str(default) and block(out)['substituted'] is True


def test_an_overlay_enables_substitution_and_records_provenance(world):
    path = write(world, OVERLAY)
    out = world.route('review.audit', '--user-policy', path)
    assert pick(out) == ('claudeAgent', 'claude-opus-5-5') and block(out)['substituted'] is True
    assert block(out)['thresholds'] == THRESHOLDS and block(out)['authorized_by'] == f'user policy overlay {path}'
    assert out['routing']['user_policy'] == {'path': str(path), 'resolved_path': str(path), 'keys': ['quota_substitution'],
                                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    pack = json.loads((world.tmp / 'policy.json').read_text())
    assert out['policy_sha256'] == digest(pack), 'policy_sha256 stays the pack hash'
    assert out['effective_policy_sha256'] != out['policy_sha256']
    assert 'user_policy' not in out['request'] and 'quota_substitution' not in out['request']


def test_a_pack_enabled_substitution_names_the_pack(world):
    world.policy['routing']['quota_substitution'] = dict(THRESHOLDS)
    out = world.route('review.audit')
    assert block(out)['authorized_by'] == 'pack routing.quota_substitution' and out['routing']['user_policy'] is None


def test_the_overlay_replaces_a_pack_key_whole(world):
    world.policy['routing']['quota_substitution'] = {'activate_projected_pct': 50, 'release_projected_pct': 40}
    out = world.route('review.audit', '--user-policy', write(world, OVERLAY))
    assert block(out)['thresholds'] == THRESHOLDS


@pytest.mark.parametrize('name, body', [
    ('unknown key', {'schema_version': 1, 'routing': {'candidates': []}}),
    ('evidence key', {'schema_version': 1, 'routing': {'independence_default': 'none'}}),
    ('unknown top key', {'schema_version': 1, 'routing': {}, 'extra': 1}),
    ('bad schema', {'schema_version': 2, 'routing': {}}),
    ('bad thresholds', {'schema_version': 1, 'routing': {'quota_substitution': {'activate_projected_pct': 80, 'release_projected_pct': 90}}}),
    ('bad int', {'schema_version': 1, 'routing': {'observation_window_days': 0}}),
    ('not an object', ['x']),
    ('malformed', '{nope')])
def test_a_bad_overlay_fails_closed(world, name, body):
    assert 'user policy' in route_fails(world, '--user-policy', write(world, body))


def test_loose_permissions_fail_closed(world):
    for mode in (0o664, 0o622, 0o666):
        assert 'writable' in route_fails(world, '--user-policy', write(world, OVERLAY, mode=mode))


def test_a_directory_is_not_a_regular_file(world):
    (world.tmp / 'dir.json').mkdir()
    assert 'regular file' in route_fails(world, '--user-policy', world.tmp / 'dir.json')


def test_a_symlink_to_a_safe_file_is_followed(world):
    (world.tmp / 'deployed').mkdir()
    target = write(world, OVERLAY, 'deployed/user-policy.json')
    link = world.tmp / 'link.json'
    link.symlink_to(target)
    out = world.route('review.audit', '--user-policy', link)
    assert block(out)['substituted'] is True and block(out)['authorized_by'] == f'user policy overlay {link}'
    assert out['routing']['user_policy']['path'] == str(link) and out['routing']['user_policy']['resolved_path'] == str(target)


def test_a_dangling_symlink_fails_closed(world):
    link = world.tmp / 'dangling.json'
    link.symlink_to(world.tmp / 'nowhere.json')
    assert 'user policy' in route_fails(world, '--user-policy', link)


def test_a_symlink_to_a_loose_file_fails_closed(world):
    (world.tmp / 'deployed').mkdir()
    link = world.tmp / 'link.json'
    link.symlink_to(write(world, OVERLAY, 'deployed/user-policy.json', mode=0o666))
    assert 'writable' in route_fails(world, '--user-policy', link)


def test_no_user_policy_ignores_a_present_overlay(world):
    (world.tmp / 'xdg/model-policy').mkdir(parents=True)
    write(world, OVERLAY, 'xdg/model-policy/user-policy.json')
    assert pick(world.route('review.audit'))[0] == 'claudeAgent', 'the default-path overlay applies'
    out = world.route('review.audit', '--no-user-policy')
    assert pick(out) == ('codex', 'gpt-6.1-sol') and out['routing']['user_policy'] is None
    write(world, {'schema_version': 1, 'routing': {'candidates': []}}, 'xdg/model-policy/user-policy.json')
    assert world.route('review.audit', '--no-user-policy')['routing']['user_policy'] is None, 'a bad overlay is not even read'


def test_the_two_flags_conflict(world):
    result, _ = world.cli('check', '--policy', world.tmp / 'policy.json', '--user-policy', write(world, OVERLAY), '--no-user-policy', raw=True)
    assert result.returncode == 2


def test_check_validates_the_overlay(world):
    good, bad = write(world, OVERLAY), write(world, {'schema_version': 1, 'routing': {'candidates': []}}, 'bad.json')
    ok, _ = world.cli('check', '--policy', world.tmp / 'policy.json', '--user-policy', good, raw=True)
    assert ok.returncode == 0 and 'PASS' in ok.stdout
    broken, _ = world.cli('check', '--policy', world.tmp / 'policy.json', '--user-policy', bad, raw=True)
    assert broken.returncode == 1 and 'not user-tunable' in broken.stderr
    skipped, _ = world.cli('check', '--policy', world.tmp / 'policy.json', '--user-policy', bad, '--no-user-policy', raw=True)
    assert skipped.returncode == 2
    missing, _ = world.cli('check', '--policy', world.tmp / 'policy.json', '--user-policy', world.tmp / 'absent.json', raw=True)
    assert missing.returncode == 1 and 'no such file' in missing.stderr, 'a named overlay that is missing is an error'
    default, _ = world.cli('check', '--policy', world.tmp / 'policy.json', raw=True)
    assert default.returncode == 0, 'a missing default overlay means none'


def test_a_replayed_receipt_is_unchanged_by_a_later_overlay(world):
    first = world.route('review.audit', record='keep')
    assert 'quota_substitution' not in first['routing'] and first['routing']['user_policy'] is None
    replay = world.route('review.audit', '--user-policy', write(world, OVERLAY), record='keep')
    assert replay['replayed'] is True and replay['routing'] == first['routing'] and replay['request'] == first['request']


def test_a_receipt_records_the_overlay(world):
    path = write(world, OVERLAY)
    world.route('review.audit', '--user-policy', path, record='kept')
    stored = json.loads(world.receipts.read_text().splitlines()[-1])
    assert stored['routing']['user_policy']['keys'] == ['quota_substitution'] and stored['effective_policy_sha256'] != stored['policy_sha256']


def test_observation_limits_apply(one):
    run_row(one, finished='2026-10-09T20:30:00+00:00')
    assert notes_of(route(one))[0]['kind'] == 'observations_stale'
    path = one.tmp / 'user-policy.json'
    path.write_text(json.dumps({'schema_version': 1, 'routing': {'observation_max_age_hours': 5}}))
    path.chmod(0o600)
    out = route(one, '--user-policy', path)
    assert 'observations' not in out['routing']['calibration_notes'] and out['routing']['user_policy']['keys'] == ['observation_max_age_hours']


def test_an_explicitly_named_missing_overlay_is_an_error(world, tmp_path):
    route_fails(world, '--user-policy', tmp_path / 'typo-user-policy.json')
