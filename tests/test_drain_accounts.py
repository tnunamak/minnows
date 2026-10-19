"""`routing.drain_accounts`: a user's one-off wish to burn an account's weekly allowance before its reset.

Inputs are the pinned 2026-10-11T00:25Z capture of `test_quota_substitution`. In it codex is at 58% use and projected
105% (so substitution holds it away), and `claude-alt` is at 3% use. Each test changes one input and runs the real CLI.
"""
import json
import sys

import pytest

import test_quota_bands as qb
import test_quota_substitution as qs
import test_route
from test_quota_substitution import NOW, QuotaWorld, active, block, pick
from test_route import REPO, codes

sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
import drain  # noqa: E402

ENTRY = {'until_current_pct': 95, 'expires_at': '2026-10-22T00:00:00Z', 'reason': 'reset credit: use the week up, fire it, use it again'}


def drain_of(**changes):
    return {'codex': ENTRY | changes}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    return QuotaWorld(tmp_path)


def set_use(world, provider, source, window, value):
    row = world.quota['providers'][provider]
    if source:
        row = next(s for s in row['sources'] if s['source']['id'] == source)
    next(w for w in row['usage']['windows'] if w['name'] == window)['utilization'] = value


def drained(world, entries):
    world.policy['routing']['drain_accounts'] = entries


def test_without_an_entry_a_high_projection_substitutes_away(world):
    out = world.route('review.audit')
    assert pick(out)[0] != 'codex' and block(out)['substituted'] is True
    assert 'drain_accounts' not in out['routing'] and 'drain_account_active' not in codes(out)


def test_an_active_entry_keeps_the_projected_account(world):
    drained(world, drain_of())
    out = world.route('review.audit')
    assert pick(out) == ('codex', 'gpt-6.1-sol')
    assert block(out)['substituted'] is False and active(out) == {}, 'the drained account is not constrained'
    assert out['routing']['chosen']['pace'] == 'on_track' and out['launch_ready'] is True
    assert out['routing']['rank_keys'][:2] == ['eligibility', 'drain_account']


def test_the_receipt_records_the_entry_and_its_reason(world):
    drained(world, drain_of())
    out = world.route('review.audit', record='drain')
    row = out['routing']['drain_accounts']
    assert row['chosen_drained'] is True
    assert row['accounts']['codex'] == ENTRY | {'current_pct': 58, 'applies': True, 'why': 'active'}
    reason = next(r for r in out['routing']['judgment_reasons'] if r['code'] == 'drain_account_active')
    assert reason['applies_to'] == 'routed' and reason['informational'] is True
    assert reason['detail'] == {'instance': 'codex', 'reason': ENTRY['reason'], 'until_current_pct': 95, 'current_pct': 58,
                                'expires_at': ENTRY['expires_at']}
    assert out['routing']['params']['drain_accounts'] == drain_of()


def test_a_drained_account_ranks_ahead_of_a_lower_projection(world):
    # claude-work has the highest Claude projection (87%); draining it still ranks it first among the qualified pairs
    oracle = ('--proof-class', 'oracle')
    assert pick(world.route('implement.oracle-bounded', *oracle))[0] != 'claude-work'
    drained(world, {'claude-work': ENTRY})
    out = world.route('implement.oracle-bounded', *oracle)
    assert pick(out)[0] == 'claude-work' and out['routing']['drain_accounts']['chosen_drained'] is True


def test_the_evidence_class_still_outranks_a_drained_account(world):
    # the Claude pairs for review.audit are class 1 (unvalidated candidates); draining one does not promote it over codex
    world.codex(50)
    drained(world, {'claude-alt': ENTRY})
    out = world.route('review.audit')
    assert pick(out)[0] == 'codex' and out['routing']['drain_accounts']['chosen_drained'] is False


def test_the_entry_stops_at_the_threshold(world):
    drained(world, drain_of())
    set_use(world, 'openai', None, '7d', 94.9)
    assert pick(world.route('review.audit'))[0] == 'codex'
    set_use(world, 'openai', None, '7d', 95)
    out = world.route('review.audit')
    assert pick(out)[0] != 'codex' and block(out)['substituted'] is True and active(out) == {'codex': 'activated'}
    assert out['routing']['drain_accounts']['accounts']['codex'] | {} == ENTRY | {'current_pct': 95, 'applies': False, 'why': 'threshold_reached'}
    assert 'drain_account_active' not in codes(out)


def test_the_entry_stops_at_expiry(world):
    drained(world, drain_of(expires_at='2026-10-11T00:25:01Z'))
    assert pick(world.route('review.audit'))[0] == 'codex'
    drained(world, drain_of(expires_at=NOW))
    out = world.route('review.audit')
    assert pick(out)[0] != 'codex' and block(out)['substituted'] is True
    assert out['routing']['drain_accounts']['accounts']['codex']['why'] == 'expired'
    assert 'drain_account_active' not in codes(out)


def test_an_inactive_entry_changes_nothing_but_the_record(world):
    plain = world.route('review.audit')
    drained(world, drain_of(expires_at='2026-10-01T00:00:00Z'))
    out = world.route('review.audit')
    for key in ('chosen', 'target', 'launch_ready'):
        assert out[key] == plain[key] if key in plain else key not in out
    assert {k: v for k, v in block(out).items() if not k.startswith('state')} == {k: v for k, v in block(plain).items() if not k.startswith('state')}
    assert [r['code'] for r in out['routing']['judgment_reasons']] == [r['code'] for r in plain['routing']['judgment_reasons']]


def test_unknown_use_never_applies_an_entry(world):
    drained(world, drain_of())
    set_use(world, 'openai', None, '7d', None)
    out = world.route('review.audit')
    assert out['routing']['drain_accounts']['accounts']['codex']['applies'] is False
    assert pick(out)[0] != 'codex'


def test_an_avoid_directive_still_removes_the_drained_account(world):
    drained(world, drain_of())
    world.add_directive('avoid-codex', 'avoid', 'account=codex', until='2026-10-12T00:00:00Z')
    out = world.route('review.audit')
    assert next(t for t in out['routing']['trace'] if t.get('instance') == 'codex')['removed_by'] == 'avoid_directive'
    assert pick(out)[0] != 'codex' and out['routing']['drain_accounts']['chosen_drained'] is False


def test_independence_still_removes_the_drained_account(world):
    drained(world, {'claude-alt': ENTRY})
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5-5')
    assert pick(out)[0] == 'codex', 'vendor independence removes every Claude pair'


def test_exhausted_quota_still_removes_the_drained_account(world):
    drained(world, drain_of())
    next(w for w in world.quota['providers']['openai']['usage']['windows'] if w['name'] == '7d')['state'] = 'exhausted'
    set_use(world, 'openai', None, '7d', 100)
    out = world.route('review.audit')
    assert pick(out)[0] != 'codex'


def test_an_override_is_not_changed_by_an_entry(world):
    drained(world, {'claude-alt': ENTRY})
    out = world.route('review.audit', '--override', 'provider=codex', '--override', 'model=gpt-6.1-sol', '--override', 'account=codex',
                      '--reason', 'caller intent')
    assert out['target']['providerInstanceId'] == 'codex'


# ---- forecast bands: a drained account is not a wait ------------------------------------------------------------------

@pytest.fixture
def banded(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    return qb.BandWorld(tmp_path)


def test_banded_without_headroom_waits_but_an_active_entry_launches(banded):
    out = qb.route(banded)
    assert out['launch_ready'] is False and out['waiting']['state'] == 'no_forecast_headroom'
    banded.policy['routing']['drain_accounts'] = {'claude-alt': ENTRY}
    out = qb.route(banded)
    assert qb.pick(out) == ('claude-alt', 'claude-sonnet-5-5')
    assert out['launch_ready'] is True and 'waiting' not in out
    assert out['routing']['quota']['claude-alt']['binding']['headroom'] <= 0, 'the projection is shown as read'
    assert 'drain_account_active' in codes(out)


def test_banded_entry_at_threshold_waits_again(banded):
    banded.policy['routing']['drain_accounts'] = {'claude-alt': ENTRY | {'until_current_pct': 3}}
    out = qb.route(banded)
    assert out['launch_ready'] is False and out['waiting']['state'] == 'no_forecast_headroom'


def test_banded_missing_forecast_still_drains_on_current_use(banded):
    source = next(s for s in banded.quota['providers']['claude']['sources'] if s['source']['id'] == 'alt')
    del source['forecast']['windows']['7d All']
    assert qb.route(banded)['launch_ready'] is False, 'without an entry an unknown forecast waits'
    banded.policy['routing']['drain_accounts'] = {'claude-alt': ENTRY}
    out = qb.route(banded)
    assert qb.pick(out) == ('claude-alt', 'claude-sonnet-5-5') and out['launch_ready'] is True
    assert out['routing']['drain_accounts']['accounts']['claude-alt']['current_pct'] == 3
    banded.policy['routing']['drain_accounts'] = {'claude-alt': ENTRY | {'until_current_pct': 3}}
    assert qb.route(banded)['launch_ready'] is False


def test_the_summary_matches_an_instance_id_that_contains_a_colon():
    from datetime import datetime, timezone
    import routing
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    status = drain.status(ENTRY, 10, now)
    block = routing.drain_summary({'drain': {'codex:personal': ENTRY}, 'now': now}, {'codex:personal': status}, {'instance': 'codex:personal'})
    assert block['chosen_drained'] is True and block['accounts']['codex:personal']['applies'] is True


# ---- validation -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value, message', [
    ([], 'must be a non-empty object keyed by provider instance ID'),
    ({}, 'must be a non-empty object keyed by provider instance ID'),
    ({'': ENTRY}, 'every key must be a non-empty provider instance ID'),
    ({'a': 'x'}, 'must be an object with exactly until_current_pct, expires_at and reason'),
    ({'a': {'until_current_pct': 95, 'expires_at': ENTRY['expires_at']}}, 'must be an object with exactly'),
    ({'a': ENTRY | {'extra': 1}}, 'must be an object with exactly'),
    ({'a': ENTRY | {'until_current_pct': 0}}, 'until_current_pct must be a number with 0 < until_current_pct <= 100'),
    ({'a': ENTRY | {'until_current_pct': 101}}, 'until_current_pct must be a number'),
    ({'a': ENTRY | {'until_current_pct': True}}, 'until_current_pct must be a number'),
    ({'a': ENTRY | {'until_current_pct': '95'}}, 'until_current_pct must be a number'),
    ({'a': ENTRY | {'expires_at': '2026-10-22'}}, 'expires_at must be an ISO 8601 time with a zone'),
    ({'a': ENTRY | {'expires_at': '2026-10-22T00:00:00'}}, 'expires_at must be an ISO 8601 time with a zone'),
    ({'a': ENTRY | {'expires_at': 5}}, 'expires_at must be an ISO 8601 time with a zone'),
    ({'a': ENTRY | {'reason': ' '}}, 'reason must be a non-empty string'),
    ({'a': ENTRY | {'reason': None}}, 'reason must be a non-empty string')])
def test_validation_errors(value, message):
    errors = drain.check(value)
    assert errors and any(message in e for e in errors), errors


def test_a_valid_entry_and_an_absent_key_pass():
    assert drain.check({'a': ENTRY, 'b': ENTRY | {'until_current_pct': 100.0}}) == [] and drain.check(None, present=False) == []


def test_the_overlay_accepts_the_key_and_a_bad_value_fails_closed(world, tmp_path):
    good = tmp_path / 'good.json'
    good.write_text(json.dumps({'schema_version': 1, 'routing': {'drain_accounts': drain_of()}}))
    good.chmod(0o600)
    out = world.route('review.audit', '--user-policy', good)
    assert pick(out)[0] == 'codex' and out['routing']['user_policy']['keys'] == ['drain_accounts']
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({'schema_version': 1, 'routing': {'drain_accounts': {'codex': ENTRY | {'until_current_pct': 0}}}}))
    bad.chmod(0o600)
    result, parsed = world.cli('route', 'review.audit', *world.args(), '--decision-id', 'probe', '--user-policy', bad)
    assert parsed is None and result.returncode == 1 and 'until_current_pct must be a number' in result.stderr


def test_the_shipped_pack_carries_no_entry():
    routing = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text()).get('routing', {})
    assert 'drain_accounts' not in routing
