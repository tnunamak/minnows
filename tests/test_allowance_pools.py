"""Allowance pools: free and prepaid arms that route first, spend a daily or weekly cap, and fall through when it is gone.

The pack declares the pools under `free_routes[].allowance`. Every test changes one input and runs the real CLI
against the pinned capture. No model is called.
"""
import json
import sys
from datetime import datetime, timedelta

import pytest

import test_route
from test_normal_free_routes import PUBLIC, JUDGED, free_rows
from test_quota_bands import ORACLE, BandWorld
from test_quota_substitution import NOW
from test_route import DEEPSEEK_FREE, REPO, add_opencode_fixture, event

sys.path.insert(0, str(REPO / 'tools' / 'model-policy-ops' / 'lib'))
import allowance  # noqa: E402
from normal_free_routes import check_normal_free_routes  # noqa: E402

GEMINI = 'gemini-3.8-flash'
SONNET = 'claude-sonnet-4-6'
OP = 'implement.oracle-bounded'
DEEPSEEK_LIMIT = 'Free usage limit reached. Connect a provider or subscribe to OpenCode Go to keep going, or switch models.'
ANTIGRAVITY_LIMIT = 'Usage Limit Reached. Your weekly limit resets in 3 days, 4 hours.'
EVENT_AT = '2026-10-11T00:15:00.000Z'
LEVELS = [{'id': level} for level in ('low', 'medium', 'high')]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    found = BandWorld(tmp_path)
    add_opencode_fixture(found)
    found.catalog['data']['providers'].append({'driver': 'antigravity', 'enabled': True, 'instanceId': 'antigravity', 'models': [
        {'slug': model, 'options': [{'id': 'thinking_level', 'values': LEVELS}]} for model in (GEMINI, SONNET)]})
    for route in found.policy['free_routes']:  # date every declaration before the pinned capture
        route['status'] = route['status'].split('_as_of_')[0] + '_as_of_2026-10-10'
        for source in route['evidence']:
            source['accessed'] = '2026-10-10'
    return found


def route(world, *extra, op=OP, proof=ORACLE, clock=NOW):
    result, out = world.cli('route', op, *world.args(), *proof, '--decision-id', 'allowance', *extra, evaluation_time=clock)
    assert result.returncode == 0, result.stderr
    return out


def chosen_model(out):
    return (out['routing']['chosen'] or {}).get('arm', {}).get('model')


def removed(out, model, instance):
    rows = [t for t in out['routing']['trace'] if t['arm']['model'] == model and t.get('instance') == instance]
    return rows[0]['removed_by'] if rows else 'no row'


def plus(clock, **delta):
    moment = datetime.fromisoformat(clock.replace('Z', '+00:00')) + timedelta(**delta)
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


# ---- routing choice ------------------------------------------------------------------------------------------------------

def test_public_bounded_work_goes_to_free_deepseek_first(world):
    out = route(world, *PUBLIC)
    assert chosen_model(out) == DEEPSEEK_FREE
    assert out['target'] == {'providerInstanceId': 'opencode', 'model': DEEPSEEK_FREE, 'options': {'variant': 'high'}}
    assert removed(out, GEMINI, 'antigravity') is None, 'the prepaid arm stays an eligible fallback'


def test_work_without_a_public_assertion_goes_to_antigravity_not_deepseek(world):
    out = route(world)
    assert chosen_model(out) == GEMINI
    assert out['target'] == {'providerInstanceId': 'antigravity', 'model': GEMINI, 'options': {'thinking_level': 'medium'}}
    assert free_rows(out) == [], 'DeepSeek needs the public assertion: it adds no candidate without it'
    assert out['routing']['chosen']['selection_basis'] == 'owner_authorized_route'
    assert out['data_policy']['state'] == 'allowed_public_normal' and out['data_policy']['provider'] == 'antigravity'


@pytest.mark.parametrize('extra', [('--sensitive',), ('--data-classification', 'sensitive')])
def test_sensitive_work_never_goes_to_a_free_or_prepaid_arm(world, extra):
    out = route(world, *extra)
    assert all(row['arm']['provider'] not in ('opencode', 'antigravity') for row in out['routing']['trace'])
    assert out['target'] is None or out['target']['providerInstanceId'] not in ('opencode', 'antigravity')


def test_first_pass_review_takes_the_free_arm_and_the_prepaid_arm_otherwise(world):
    public = route(world, *PUBLIC, '--maker-model', 'claude:claude-opus-5-5', op='review.first-pass', proof=JUDGED)
    assert chosen_model(public) == DEEPSEEK_FREE
    private = route(world, '--maker-model', 'claude:claude-opus-5-5', op='review.first-pass', proof=JUDGED)
    assert chosen_model(private) == GEMINI


# ---- cross-vendor review rules still hold ---------------------------------------------------------------------------------

def test_a_review_never_shares_a_vendor_with_its_maker(world):
    out = route(world, '--maker-model', f'antigravity:{GEMINI}', op='review.first-pass', proof=JUDGED)
    assert removed(out, GEMINI, 'antigravity') == 'independence'
    assert chosen_model(out) != GEMINI


def test_a_claude_model_served_by_antigravity_still_counts_as_claude(world):
    out = route(world, '--maker-model', f'antigravity:{SONNET}', op='review.audit', proof=JUDGED)
    claude_rows = [t for t in out['routing']['trace'] if t['arm']['provider'] == 'claude' and t.get('instance')]
    assert claude_rows and all(t['removed_by'] is not None for t in claude_rows)
    assert any(t['removed_by'] == 'independence' for t in claude_rows)
    assert out['routing']['chosen']['arm']['provider'] == 'codex'


def test_a_free_arm_maker_is_checked_by_a_different_vendor(world):
    out = route(world, '--maker-model', f'opencode:{DEEPSEEK_FREE}', op='review.audit', proof=JUDGED)
    assert out['routing']['independence']['required'] == 'vendor'
    assert out['routing']['chosen']['arm']['provider'] != 'opencode'


# ---- exhaustion and fall-through ---------------------------------------------------------------------------------------------

def test_a_deepseek_limit_message_exhausts_the_pool_until_midnight_utc_and_routing_falls_through(world):
    world.events = [event('opencode', DEEPSEEK_LIMIT, at=EVENT_AT, model=DEEPSEEK_FREE)]
    out = route(world, *PUBLIC)
    assert removed(out, DEEPSEEK_FREE, 'opencode') == 'allowance_exhausted'
    spent = next(t for t in out['routing']['trace'] if t['arm']['model'] == DEEPSEEK_FREE)
    assert spent['detail'] == {'pool': 'opencode/opencode/daily', 'exhausted_until': '2026-10-12T00:00:00+00:00'}
    assert chosen_model(out) == GEMINI, 'the next arm serves'


def test_exhaustion_survives_a_restart_and_expires_at_the_known_reset(world):
    world.events = [event('opencode', DEEPSEEK_LIMIT, at=EVENT_AT, model=DEEPSEEK_FREE)]
    route(world, *PUBLIC)
    stored = json.loads(world.receipts.with_name('allowance-exhaustion.json').read_text())
    assert stored['pools']['opencode/opencode/daily']['until'] == '2026-10-12T00:00:00+00:00'
    world.events = []  # the T3 failure left the lookback: only the stored state remains
    assert removed(route(world, *PUBLIC), DEEPSEEK_FREE, 'opencode') == 'allowance_exhausted'
    assert removed(route(world, *PUBLIC, clock='2026-10-11T23:59:59Z'), DEEPSEEK_FREE, 'opencode') == 'allowance_exhausted'
    after = route(world, *PUBLIC, clock='2026-10-12T00:00:01Z')
    assert removed(after, DEEPSEEK_FREE, 'opencode') is None and chosen_model(after) == DEEPSEEK_FREE


def test_a_weekly_limit_uses_the_providers_own_reset_and_falls_through_to_the_other_pool(world):
    world.events = [event('antigravity', ANTIGRAVITY_LIMIT, at=EVENT_AT, model=GEMINI)]
    out = route(world)
    spent = next(t for t in out['routing']['trace'] if t['arm']['model'] == GEMINI)
    assert spent['removed_by'] == 'allowance_exhausted'
    assert spent['detail']['exhausted_until'] == '2026-10-14T04:15:00+00:00'
    assert chosen_model(out) == SONNET, 'the Claude and GPT pool is separate and still has allowance'
    assert out['target']['options'] == {'thinking_level': 'medium'}
    world.events = []
    assert chosen_model(route(world, clock='2026-10-14T04:14:00Z')) == SONNET
    assert chosen_model(route(world, clock='2026-10-14T04:16:00Z')) == GEMINI


def test_every_pool_spent_falls_back_to_a_paid_arm(world):
    world.events = [event('opencode', DEEPSEEK_LIMIT, at=EVENT_AT, model=DEEPSEEK_FREE),
                    event('antigravity', ANTIGRAVITY_LIMIT, at=EVENT_AT, model=GEMINI),
                    event('antigravity', ANTIGRAVITY_LIMIT, at=EVENT_AT, model=SONNET)]
    out = route(world, *PUBLIC)
    free = [t for t in out['routing']['trace'] if t['arm']['provider'] in ('opencode', 'antigravity') and t.get('instance')]
    assert free and all(t['removed_by'] == 'allowance_exhausted' for t in free)
    assert out['routing']['chosen']['arm']['provider'] in ('codex', 'claude')


def test_a_limit_without_a_model_exhausts_only_an_unambiguous_pool(world):
    world.events = [event('opencode', DEEPSEEK_LIMIT, at=EVENT_AT), event('antigravity', ANTIGRAVITY_LIMIT, at=EVENT_AT)]
    out = route(world, *PUBLIC)
    assert removed(out, DEEPSEEK_FREE, 'opencode') == 'allowance_exhausted'
    assert removed(out, GEMINI, 'antigravity') is None, 'antigravity has two pools: a guess could spend the wrong one'


def test_a_limit_message_in_new_wording_still_exhausts_the_pool(world):
    world.events = [event('antigravity', 'You have reached your weekly quota limit for this plan.', at=EVENT_AT, model=GEMINI)]
    out = route(world)
    assert removed(out, GEMINI, 'antigravity') == 'allowance_exhausted'
    assert removed(out, SONNET, 'antigravity') is None, 'the other pool of the account keeps its allowance'


def test_an_unrelated_rate_limit_does_not_exhaust_a_pool(world):
    world.events = [event('opencode', 'Claude API rate limit reached. Try again later.', at=EVENT_AT, model=DEEPSEEK_FREE)]
    assert removed(route(world, *PUBLIC), DEEPSEEK_FREE, 'opencode') is None


def test_the_simulation_clock_reads_but_never_writes_the_state(world):
    world.events = [event('opencode', DEEPSEEK_LIMIT, at=EVENT_AT, model=DEEPSEEK_FREE)]
    result, out = world.cli('route', OP, *world.args(), *ORACLE, *PUBLIC, '--decision-id', 'sim', '--now', NOW, raw=True)
    assert result.returncode == 0, result.stderr
    assert removed(out, DEEPSEEK_FREE, 'opencode') == 'allowance_exhausted'
    assert not world.receipts.with_name('allowance-exhaustion.json').exists()


def test_an_override_onto_an_exhausted_pool_reports_it(world):
    world.events = [event('opencode', DEEPSEEK_LIMIT, at=EVENT_AT, model=DEEPSEEK_FREE)]
    out = route(world, *PUBLIC, '--override', 'provider=opencode', '--override', f'model={DEEPSEEK_FREE}', '--override', 'effort=high',
                '--override', 'account=opencode', '--reason', 'explicit public free-route request')
    assert any(r['code'] == 'final_allowance_exhausted' and r['informational'] for r in out['routing']['judgment_reasons'])


# ---- the allowance command -------------------------------------------------------------------------------------------------------

def run_allowance(world, *args, clock=NOW):
    result, out = world.cli('allowance', *args, '--policy', world.tmp / 'policy.json', '--receipts', world.receipts, evaluation_time=clock)
    return result, out


def test_the_caller_can_record_a_limit_and_the_router_honours_it(world):
    result, out = run_allowance(world, 'limit', '--account', 'antigravity', '--pool', 'gemini', '--message', ANTIGRAVITY_LIMIT)
    assert result.returncode == 0, result.stderr
    assert out['pool'] == 'antigravity/antigravity/gemini' and out['exhausted_until'] == '2026-10-14T04:25:00+00:00'
    assert removed(route(world), GEMINI, 'antigravity') == 'allowance_exhausted'
    result, status = run_allowance(world, 'status')
    rows = {row['pool']: row for row in status['pools']}
    assert rows['antigravity/antigravity/gemini']['state'] == 'exhausted'
    assert rows['antigravity/antigravity/claude-gpt']['state'] == 'available'
    assert rows['opencode/opencode/daily']['period'] == 'daily_utc'
    result, cleared = run_allowance(world, 'clear', '--account', 'antigravity', '--pool', 'gemini')
    assert cleared['cleared'] is True and removed(route(world), GEMINI, 'antigravity') is None


def test_recording_a_limit_for_an_undeclared_pool_fails(world):
    result, _ = run_allowance(world, 'limit', '--account', 'antigravity', '--pool', 'nope', '--message', ANTIGRAVITY_LIMIT)
    assert result.returncode == 1 and 'no declared allowance pool' in result.stderr


def test_a_corrupt_state_file_stops_routing_instead_of_hiding_a_dead_pool(world):
    path = world.receipts.with_name('allowance-exhaustion.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{not json')
    world.write()
    result, _ = world.cli('route', OP, *world.args(), *ORACLE, *PUBLIC, '--decision-id', 'corrupt')
    assert result.returncode == 1 and 'allowance state' in result.stderr


# ---- reset arithmetic and declaration checks ---------------------------------------------------------------------------------------

def at(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


@pytest.mark.parametrize('declared,message,now,expected', [
    ({'period': 'daily_utc'}, None, '2026-10-11T23:59:00Z', '2026-10-12T00:00:00+00:00'),
    ({'period': 'daily_utc'}, None, '2026-10-12T00:00:00Z', '2026-10-13T00:00:00+00:00'),
    ({'period': 'weekly', 'anchor': '2026-11-05T17:35:12Z'}, None, '2026-11-06T00:00:00Z', '2026-11-12T17:35:12+00:00'),
    ({'period': 'weekly', 'anchor': '2026-11-05T17:35:12Z'}, None, '2026-11-12T17:35:12Z', '2026-11-19T17:35:12+00:00'),
    ({'period': 'weekly', 'anchor': '2026-11-05T17:35:12Z'}, 'Usage Limit Reached. It resets in 2 days', '2026-11-06T00:00:00Z', '2026-11-08T00:00:00+00:00'),
    ({'period': 'daily_utc'}, 'Usage Limit Reached, resets in 6 days, 23 hours.', '2026-11-05T12:00:00Z', '2026-11-12T11:00:00+00:00'),
])
def test_next_reset(declared, message, now, expected):
    assert allowance.next_reset(declared, at(now), message).isoformat() == expected


def declared_route(**allowance_fields):
    route = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())['free_routes'][2]
    route['allowance'] = {**route['allowance'], **allowance_fields}
    return route


@pytest.mark.parametrize('fields,problem', [
    ({'period': 'monthly'}, 'period must be one of'),
    ({'anchor': None, 'period': 'weekly'}, 'anchor must be a zoned ISO time'),
    ({'limit_markers': []}, 'limit_markers must be a non-empty list'),
    ({'limit_markers': ['Usage Limit Reached']}, 'lowercase message fragments'),
    ({'pool': 'Has Space'}, 'pool must be a lowercase slug'),
])
def test_a_bad_allowance_declaration_fails_the_check(fields, problem):
    errors = check_normal_free_routes([declared_route(**fields)])
    assert any(problem in error for error in errors), errors


def test_the_shipped_pack_declares_each_pool_once_with_a_distinct_key():
    routes = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())['free_routes']
    keys = sorted(allowance.pools(routes))
    assert keys == ['antigravity/antigravity/claude-gpt', 'antigravity/antigravity/gemini', 'opencode/opencode/daily']
    assert check_normal_free_routes(routes) == []
