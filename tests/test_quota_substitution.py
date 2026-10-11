"""`route` quota-aware substitution. Inputs are pinned: a scrubbed 2026-10-11T00:23Z `clawmeter --json` capture.

In it the Codex 7d window is at 58% and projected near 105%, and the three Claude accounts are projected near
87% (`claude-work`), 72% (`claudeAgent`) and 29% (`claude-alt`). Each test changes one input and runs the real CLI.
"""
import fcntl
import json
import os
import subprocess
import sys
import time

import pytest

import test_route
from test_route import REPO, World, codes, set_forecast, stable
from test_route_destinations import capacity, config, entry as worker

FIX = REPO / 'tests/fixtures/route-live-20261011'
NOW = '2026-10-11T00:25:00Z'
THRESHOLDS = {'activate_projected_pct': 95, 'release_projected_pct': 90}
TOOL = REPO / 'tools/model-policy-ops/model-policy-ops'
AFFECTED = [('review.audit', 'claude-opus-5-5', 'high', ()),
            ('fanout.explore', 'claude-opus-5-5', 'medium', ()),
            ('implement.oracle-bounded', 'claude-sonnet-5-5', 'medium', ('--proof-class', 'oracle')),
            ('ui.computer-use', 'claude-opus-5-5', 'medium', ())]
CLAUDE = {'claudeAgent': 'default', 'claude-work': 'work', 'claude-alt': 'alt'}


class QuotaWorld(World):
    """The route-live-20261011 snapshot, the 20261010 catalog, no failure events, and the pack with substitution on."""

    def __init__(self, tmp):
        super().__init__(tmp)
        self.quota = json.loads((FIX / 'clawmeter.json').read_text())
        self.accounts = json.loads((FIX / 'accounts.json').read_text())
        self.events = []
        self.policy['routing']['quota_substitution'] = dict(THRESHOLDS)
        self.state = tmp / 'state/quota-substitution.json'

    def args(self):
        return super().args() + ['--quota-state', self.state]

    def set_claude(self, value, *instances):
        for instance in instances or CLAUDE:
            set_forecast(self, 'claude', CLAUDE[instance], '7d All', value)

    def codex(self, value):
        set_forecast(self, 'openai', None, '7d', value)

    def stored(self):
        return json.loads(self.state.read_text())['accounts']


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(test_route, 'NOW', NOW)
    return QuotaWorld(tmp_path)


def block(out):
    return out['routing']['quota_substitution']


def active(out):
    return {k: v['state'] for k, v in block(out)['active_accounts'].items()}


def pick(out):
    chosen = out['routing']['chosen']
    return chosen['instance'], chosen['arm']['model']


# ---- thresholds and hysteresis ----------------------------------------------------------------------------------------

def test_threshold_crossing_with_hysteresis(world):
    steps = [(94.9, {}, 'codex'), (95, {'codex': 'activated'}, 'claude'), (92, {'codex': 'held_by_hysteresis'}, 'claude'),
             (90, {'codex': 'held_by_hysteresis'}, 'claude'), (89.9, {'codex': 'released'}, 'codex'), (92, {}, 'codex')]
    for projected, expected, vendor in steps:
        world.codex(projected)
        out = world.route('review.audit')
        assert active(out) == expected, projected
        assert out['routing']['chosen']['arm']['provider'] == vendor, projected
        assert block(out)['substituted'] is (vendor == 'claude')


def test_active_row_carries_projection_window_and_since(world):
    out = world.route('review.audit')
    assert block(out)['active_accounts'] == {'codex': {'projected_pct': 105.12, 'window': '7d', 'state': 'activated', 'since': NOW.replace('Z', '+00:00')}}
    assert block(out)['thresholds'] == THRESHOLDS
    world.codex(92)
    held = block(world.route('review.audit'))['active_accounts']['codex']
    assert held['state'] == 'held_by_hysteresis' and held['since'] == NOW.replace('Z', '+00:00'), 'since is the activation time'


def test_each_account_has_its_own_state(world):
    world.set_claude(96, 'claude-alt')
    out = world.route('review.audit')
    assert active(out) == {'claude-alt': 'activated', 'codex': 'activated'}
    assert set(world.stored()) == {'claude-alt', 'codex'}
    assert pick(out) == ('claudeAgent', 'claude-opus-5-5')


# ---- state file ------------------------------------------------------------------------------------------------------

def test_state_file_is_created_private_and_updated_only_on_a_transition(world):
    assert not world.state.exists()
    out = world.route('review.audit')
    assert block(out)['state'] == 'missing' and block(out)['state_written'] is True
    assert stat_mode(world.state) == 0o600 and stat_mode(world.state.parent) == 0o700
    assert world.stored() == {'codex': {'since': NOW.replace('Z', '+00:00'), 'window': '7d'}}
    before = world.state.stat()
    again = block(world.route('review.audit'))
    assert again['state'] == 'loaded' and again['state_written'] is False
    assert world.state.stat().st_mtime_ns == before.st_mtime_ns, 'no transition, no write'
    world.codex(80)
    released = block(world.route('review.audit'))
    assert released['state_written'] is True and world.stored() == {}
    assert list(world.state.parent.glob('*.tmp')) == [], 'the temporary file is replaced, not left behind'


def test_default_state_file_sits_beside_the_receipts(world):
    (world.tmp / 'state').mkdir(mode=0o700)
    world.state = world.receipts.with_name('quota-substitution.json')
    args = [a for a in world.args() if a not in ('--quota-state', world.state)]
    result, out = world.cli('route', 'review.audit', *args, '--decision-id', 'p')
    assert result.returncode == 0, result.stderr
    assert world.state.exists()


@pytest.mark.parametrize('content', ['{not json', '[]', '{"schema_version": 2, "accounts": {}}', '{"schema_version": 1, "accounts": {"codex": {"since": "x"}}}',
                                      '{"schema_version": 1, "accounts": {"codex": {"since": "yesterday", "window": "7d"}}}'])
def test_corrupt_state_is_not_rewritten_and_decides_without_memory(world, content):
    world.state.parent.mkdir(mode=0o700)
    world.state.write_text(content)
    world.state.chmod(0o600)
    world.codex(92)
    out = world.route('review.audit')
    assert block(out)['state'] == 'unreadable' and block(out)['state_written'] is False and block(out)['state_issue']
    assert active(out) == {} and pick(out)[0] == 'codex', 'without memory 92 is below the activation threshold'
    world.codex(96)
    out = world.route('review.audit')
    assert active(out) == {'codex': 'activated'} and pick(out)[0] != 'codex'
    assert world.state.read_text() == content, 'a corrupt state is never rewritten'


def test_state_that_others_can_write_or_a_symlink_is_unreadable_and_untouched(world, tmp_path):
    world.state.parent.mkdir(mode=0o700)
    target = tmp_path / 'elsewhere.json'
    target.write_text('{"schema_version": 1, "accounts": {}}')
    world.state.symlink_to(target)
    out = world.route('review.audit')
    assert block(out)['state'] == 'unreadable' and block(out)['state_written'] is False
    assert world.state.is_symlink() and target.read_text() == '{"schema_version": 1, "accounts": {}}'
    world.state.unlink()
    world.state.write_text('{"schema_version": 1, "accounts": {}}')
    world.state.chmod(0o664)
    out = world.route('review.audit')
    assert block(out)['state'] == 'unreadable' and block(out)['state_issue'] == 'group_or_world_writable'
    assert stat_mode(world.state) == 0o664


def test_state_in_a_directory_that_others_can_write_is_not_written(world):
    world.state.parent.mkdir(mode=0o700)
    world.state.parent.chmod(0o777)
    out = world.route('review.audit')
    assert block(out)['state'] == 'unreadable' and block(out)['state_written'] is False and not world.state.exists()
    assert active(out) == {'codex': 'activated'}


def test_simulation_never_writes_state_but_reads_it(world):
    sim = ['--now', NOW]
    result, out = world.cli('route', 'review.audit', *world.args(), *sim, raw=True)
    assert result.returncode == 0, result.stderr
    assert block(out)['state'] == 'missing' and block(out)['state_written'] is False
    assert active(out) == {'codex': 'activated'} and out['simulation']
    assert not world.state.exists() and not world.state.with_name('quota-substitution.json.lock').exists()
    world.route('review.audit')
    stored = world.state.read_bytes()
    world.codex(92)
    result, out = world.cli('route', 'review.audit', *world.args(), *sim, raw=True)
    assert active(out) == {'codex': 'held_by_hysteresis'}, 'a simulation reads the memory'
    world.codex(50)
    result, out = world.cli('route', 'review.audit', *world.args(), *sim, raw=True)
    assert active(out) == {'codex': 'released'} and world.state.read_bytes() == stored


def test_state_is_read_modify_written_under_an_exclusive_lock(world):
    world.route('review.audit')
    world.codex(80)
    world.write()
    lock = open(world.state.with_name('quota-substitution.json.lock'), 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    env = dict(os.environ, XDG_CONFIG_HOME=str(world.tmp / 'xdg'), PINNED_NOW=NOW)
    process = subprocess.Popen([sys.executable, str(REPO / 'tests/pinned_clock_cli.py'), 'route', 'review.audit', *map(str, world.args()),
                                '--decision-id', 'locked'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    time.sleep(1.5)
    assert process.poll() is None, 'route waits for the lock'
    assert 'codex' in world.stored()
    lock.close()
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert json.loads(stdout)['routing']['quota_substitution']['active_accounts']['codex']['state'] == 'released'
    assert world.stored() == {}


def test_state_path_must_differ_from_the_other_ledgers(world):
    for name in ('decisions.jsonl', 'directives.jsonl', 'outcomes.jsonl', 'observations.jsonl'):
        world.state = world.receipts.with_name(name)
        result, _ = world.cli('route', 'review.audit', *world.args(), '--decision-id', 'p')
        assert result.returncode == 1 and 'name the same file' in result.stderr, (name, result.stderr)
    result, _ = world.cli('close', 'x', '--quota-state', world.tmp / 'q.json')
    assert result.returncode == 1 and 'applies to route only' in result.stderr


# ---- substitution -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('op, model, effort, extra', AFFECTED)
def test_codex_constrained_gives_a_claude_alternate_on_the_preferred_account(world, op, model, effort, extra):
    out = world.route(op, *extra)
    chosen = out['routing']['chosen']
    assert (chosen['instance'], chosen['arm']['model'], chosen['arm']['effort']) == ('claudeAgent', model, effort)
    assert chosen['arm']['provider'] == 'claude' and chosen['basis'] == 'unvalidated'
    assert chosen['selection_basis'] == 'quota_substitution_exception' and chosen['evidence'] == 'unvalidated'
    assert 'unvalidated_candidate' not in codes(out)
    assert out['target']['providerInstanceId'] == 'claudeAgent' and out['target']['model'] == model
    moved = block(out)
    assert moved['substituted'] is True and moved['authorized_by'] == 'pack routing.quota_substitution (owner directive)'
    assert moved['from']['instance'] == 'codex' and moved['from']['projected_pct'] == 105.12
    assert moved['to'] == {'instance': 'claudeAgent', 'model': model, 'effort': effort, 'projected_pct': 72.4}
    assert moved['reason'] and out['routing']['routed']['account'] == 'claudeAgent'
    assert 'quota_substitution_unavailable' not in codes(out)


def test_quota_tight_stays_on_its_claude_primary(world):
    out = world.route('implement.quota-tight')
    assert pick(out)[1] == 'claude-sonnet-5-5' and chosen_basis(out) == 'primary'
    assert block(out)['substituted'] is False and 'from' not in block(out)
    assert out['routing']['judgment_required'] is False


def test_other_judgment_reasons_still_apply_to_a_substitute(world):
    assert codes(world.route('implement.oracle-bounded')) == ['requires_unknown']
    assert codes(world.route('implement.oracle-bounded', '--proof-class', 'oracle')) == []
    assert codes(world.route('review.audit')) == ['review_without_maker']


def test_a_prior_candidate_keeps_its_basis(world):
    out = world.route('fanout.dollar-tight')
    assert chosen_basis(out) == 'evidence_prior' and block(out)['substituted'] is False, 'at 105% the normal pace rank already prefers Claude'
    world.codex(96)
    out = world.route('fanout.dollar-tight')
    chosen = out['routing']['chosen']
    assert chosen['arm']['provider'] == 'claude' and chosen['basis'] == 'task_benchmark_prior' and chosen['evidence'] == 'prior'
    assert chosen['selection_basis'] == 'quota_substitution_exception' and chosen['prior']['board'] == 'AutomationBench'


def test_substitution_needs_a_constrained_best_pair(world):
    world.codex(80)
    for op, *_ in AFFECTED:
        out = world.route(op, '--proof-class', 'oracle')
        assert pick(out)[0] == 'codex' and block(out)['substituted'] is False and active(out) == {}


def test_unavailable_when_every_claude_account_is_constrained_too(world):
    world.set_claude(96)
    out = world.route('review.audit')
    assert pick(out) == ('codex', 'gpt-6.1-sol') and block(out)['substituted'] is False
    reason = next(r for r in out['routing']['judgment_reasons'] if r['code'] == 'quota_substitution_unavailable')
    assert reason['applies_to'] == 'routed' and out['routing']['judgment_required'] is True
    assert reason['detail']['constrained'] == {'claude-alt': {'projected_pct': 96, 'window': '7d All'}, 'claude-work': {'projected_pct': 96, 'window': '7d All'},
                                              'claudeAgent': {'projected_pct': 96, 'window': '7d All'}, 'codex': {'projected_pct': 105.12, 'window': '7d'}}
    out = world.route('implement.quota-tight')
    assert pick(out) == ('claude-alt', 'claude-sonnet-5-5') and 'quota_substitution_unavailable' in codes(out), 'a constrained Claude primary has no substitute either'


def test_one_unconstrained_claude_account_is_enough(world):
    world.set_claude(96, 'claudeAgent', 'claude-alt')
    out = world.route('review.audit')
    assert pick(out) == ('claude-work', 'claude-opus-5-5') and block(out)['to']['projected_pct'] == 87.01


def test_candidate_order_comes_before_account_preference(world):
    # opus (candidate 1) on its worst-ranked account still beats sonnet (candidate 2) on the best-ranked one
    world.accounts['accounts']['claude-work']['substitution_rank'] = 0
    world.accounts['accounts']['claudeAgent']['substitution_rank'] = 5
    out = world.route('review.audit')
    assert pick(out) == ('claude-work', 'claude-opus-5-5')


# ---- account preference and the hard filters ---------------------------------------------------------------------------

def test_substitution_rank_orders_accounts_lower_first(world):
    ranks = {'claudeAgent': 2, 'claude-alt': 1, 'claude-work': 0}
    for instance, rank in ranks.items():
        world.accounts['accounts'][instance]['substitution_rank'] = rank
    assert pick(world.route('review.audit')) == ('claude-work', 'claude-opus-5-5')
    world.accounts['accounts']['claude-work']['substitution_rank'] = 9
    assert pick(world.route('review.audit')) == ('claude-alt', 'claude-opus-5-5')


def test_without_a_rank_the_lowest_projection_wins_then_the_instance_id(world):
    for entry in world.accounts['accounts'].values():
        entry.pop('substitution_rank', None)
    assert pick(world.route('review.audit')) == ('claude-alt', 'claude-opus-5-5')
    world.set_claude(50)
    assert pick(world.route('review.audit')) == ('claude-alt', 'claude-opus-5-5'), 'equal projections: instance ID'
    world.accounts['accounts']['claude-work']['substitution_rank'] = 0
    assert pick(world.route('review.audit')) == ('claude-work', 'claude-opus-5-5'), 'a ranked account comes before an unranked one'


def test_bad_substitution_rank_fails_closed(world):
    world.accounts['accounts']['claudeAgent']['substitution_rank'] = 'first'
    result, _ = world.cli('route', 'review.audit', *world.args(), '--decision-id', 'p')
    assert result.returncode == 1 and 'substitution_rank' in result.stderr


def test_billing_filter_still_applies(world):
    world.accounts['accounts']['claudeAgent']['billing'] = 'metered'
    assert pick(world.route('review.audit')) == ('claude-alt', 'claude-opus-5-5')
    for instance in CLAUDE:
        world.accounts['accounts'][instance]['billing'] = 'metered'
    out = world.route('review.audit')
    assert pick(out)[0] == 'codex' and 'quota_substitution_unavailable' in codes(out)


def test_avoid_directive_still_applies(world):
    world.add_directive('no-rank-zero', 'avoid', 'account=claudeAgent', until='2026-10-12T00:00:00Z', recorded_at='2026-10-11T00:00:00+00:00')
    assert pick(world.route('review.audit')) == ('claude-alt', 'claude-opus-5-5')
    world.add_directive('no-claude', 'avoid', 'provider=claude', until='2026-10-12T00:00:00Z', recorded_at='2026-10-11T00:00:00+00:00')
    out = world.route('review.audit')
    assert pick(out)[0] == 'codex' and 'quota_substitution_unavailable' in codes(out)


def test_exhausted_account_is_not_a_target(world):
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'default')
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['utilization'] = 100
    assert pick(world.route('review.audit')) == ('claude-alt', 'claude-opus-5-5')


def test_independence_still_applies(world):
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5-5')
    assert pick(out)[0] == 'codex', 'vendor independence removes every Claude pair, so there is no substitute'
    assert 'quota_substitution_unavailable' in codes(out)
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5-5', '--independence', 'model')
    assert pick(out) == ('claudeAgent', 'claude-sonnet-5-5'), 'model independence removes opus only'
    assert out['routing']['chosen']['selection_basis'] == 'quota_substitution_exception'
    assert 'independence_requested_only' in codes(out) and 'quota_substitution_unavailable' not in codes(out)
    out = world.route('review.audit', '--maker-model', 'claude:claude-sonnet-5-5', '--independence', 'family')
    assert pick(out) == ('claudeAgent', 'claude-opus-5-5'), 'family independence removes the maker\'s line (sonnet) and keeps opus'
    assert out['routing']['chosen']['selection_basis'] == 'quota_substitution_exception'


# ---- unknown or stale quota ----------------------------------------------------------------------------------------------

def test_unknown_account_is_not_a_target_even_when_it_would_rank_first(world):
    for entry in world.accounts['accounts'].values():
        entry.pop('substitution_rank', None)
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'alt')
    next(w for w in source['usage']['windows'] if w['name'] == '7d All')['resets_at'] = '2026-10-10T00:00:00Z'
    out = world.route('review.audit')
    assert pick(out) == ('claudeAgent', 'claude-opus-5-5'), 'claude-alt is the lowest projection but its window expired'
    assert 'claude-alt' in block(out)['unknown_accounts'] and 'claude-alt' not in block(out)['active_accounts']


def test_no_forecast_is_unknown_and_never_headroom(world):
    del world.quota['providers']['claude']['sources']
    out = world.route('review.audit')
    assert pick(out)[0] == 'codex', 'no Claude account has a known projection'
    assert sorted(block(out)['unknown_accounts']) == ['claude-alt', 'claude-work', 'claudeAgent']
    assert 'quota_substitution_unavailable' in codes(out)


def test_an_activated_account_with_unknown_quota_stays_constrained_until_a_known_release(world):
    """Spec: constrained until a KNOWN projection falls below release. Unknown quota is not a release and not headroom."""
    world.route('review.audit')
    assert 'codex' in world.stored()
    world.quota['providers']['openai']['usage']['windows'][0]['resets_at'] = '2026-10-10T00:00:00Z'
    out = world.route('review.audit')
    assert pick(out)[0] != 'codex' and active(out) == {'codex': 'held_unknown'} and block(out)['unknown_accounts'] == ['codex']
    assert 'codex' in world.stored(), 'unknown quota erases nothing'
    world.quota['providers']['openai']['usage']['windows'][0]['resets_at'] = '2026-10-13T00:00:00Z'
    world.codex(92)
    assert active(world.route('review.audit')) == {'codex': 'held_by_hysteresis'}


def test_missing_quota_source_makes_every_account_unknown(world):
    world.quota = {}
    out = world.route('review.audit')
    assert block(out)['active_accounts'] == {} and pick(out)[0] == 'codex' and block(out)['substituted'] is False
    assert not world.state.exists() or world.stored() == {}


# ---- destinations, overrides, receipts ----------------------------------------------------------------------------------------

def test_a_dot_eligible_review_with_github_inputs_still_goes_to_the_destination(world):
    cfg = config(world.tmp, worker(capacity(world.tmp, 'cap'), name='worker'))
    flags = ['--destinations', cfg, '--purpose', 'review', '--inputs', 'github']
    out = world.route('review.audit', *flags)
    world.policy['routing'].pop('quota_substitution')
    off = world.route('review.audit', *flags)
    assert out['destination'] == 'worker' and out['routing']['destination'] == off['routing']['destination']
    assert out['target'] is None and out['launch_ready'] is False and out['destination_offer'] == off['destination_offer']
    assert block(out)['substituted'] is True, 'the model fallback still records the substitution'
    assert 'quota_substitution' not in off['routing']


def test_override_to_a_constrained_account_adds_a_final_reason(world):
    out = world.route('review.audit', '--override', 'provider=codex', '--override', 'model=gpt-6.1-sol', '--override', 'account=codex', '--reason', 'owner call')
    final = [r for r in out['routing']['judgment_reasons'] if r['code'] == 'final_account_quota_constrained']
    assert len(final) == 1 and final[0]['applies_to'] == 'final'
    assert final[0]['detail'] == {'instance': 'codex', 'projected_pct': 105.12, 'window': '7d'}
    assert out['routing']['routed']['account'] == 'claudeAgent' and out['routing']['final']['account'] == 'codex'
    out = world.route('review.audit', '--override', 'account=claude-work', '--reason', 'owner call')
    assert 'final_account_quota_constrained' not in codes(out)


def test_override_pair_keeps_the_final_pair_rules(world):
    out = world.route('review.audit', '--override', 'account=claude-work', '--reason', 'owner call')
    assert out['routing']['final']['account'] == 'claude-work'
    assert 'unvalidated_candidate' in [r['code'] for r in out['routing']['judgment_reasons'] if r['applies_to'] == 'final'], 'the substitution exception covers the routed pair only'


def test_replay_returns_the_stored_receipt_and_leaves_the_state_alone(world):
    first = world.route('review.audit', record='keep')
    assert block(first)['substituted'] is True
    assert 'quota_state' not in first['request'] and 'quota_substitution' not in first['request']
    world.codex(10)
    world.state.unlink()
    replay = world.route('review.audit', record='keep')
    assert replay['replayed'] is True and stable(replay) == stable(first) | {'replayed': True}
    assert block(replay) == block(first) and not world.state.exists(), 'a replay does not evaluate the state'
    other = world.route('review.audit', '--quota-state', world.tmp / 'state/other.json', record='keep')
    assert block(other) == block(first), 'the state path is not part of the decision identity'


def test_feature_off_without_the_pack_param_changes_nothing(world):
    world.policy['routing'].pop('quota_substitution')
    out = world.route('review.audit')
    assert 'quota_substitution' not in out['routing'] and pick(out) == ('codex', 'gpt-6.1-sol')
    assert not world.state.exists() and not world.state.with_name('quota-substitution.json.lock').exists()
    assert 'unvalidated_candidate' not in codes(out)


# ---- the pack check ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value, ok', [
    ({'activate_projected_pct': 95, 'release_projected_pct': 90}, True),
    ({'activate_projected_pct': 200, 'release_projected_pct': 0.5}, True),
    ({'activate_projected_pct': 200.5, 'release_projected_pct': 90}, False),
    ({'activate_projected_pct': 90, 'release_projected_pct': 90}, False),
    ({'activate_projected_pct': 90, 'release_projected_pct': 95}, False),
    ({'activate_projected_pct': 95, 'release_projected_pct': 0}, False),
    ({'activate_projected_pct': 95, 'release_projected_pct': -1}, False),
    ({'activate_projected_pct': 95}, False),
    ({'activate_projected_pct': 95, 'release_projected_pct': 90, 'extra': 1}, False),
    ({'activate_projected_pct': '95', 'release_projected_pct': 90}, False),
    ({'activate_projected_pct': True, 'release_projected_pct': 0.5}, False),
    ([], False), (None, False)])
def test_check_validates_the_thresholds(world, value, ok):
    world.policy['routing']['quota_substitution'] = value
    world.write()
    result = subprocess.run([str(TOOL), '--policy', str(world.tmp / 'policy.json'), 'check'], capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert 'quota_substitution' in result.stderr


def test_the_shipped_pack_enables_substitution_with_the_agreed_thresholds():
    pack = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
    assert pack['routing']['quota_substitution'] == THRESHOLDS


# ---- the pinned live snapshot ----------------------------------------------------------------------------------------------------

def test_pinned_live_snapshot_selects_the_lowest_ranked_unconstrained_claude_account(world):
    quota = world.quota['providers']
    assert quota['openai']['forecast']['windows']['7d']['projected_pct'] == 105.12
    assert quota['openai']['usage']['windows'][0]['utilization'] == 58
    assert sorted(s['forecast']['windows']['7d All']['projected_pct'] for s in quota['claude']['sources']) == [28.95, 72.4, 87.01]
    for op, model, effort, extra in AFFECTED:
        out = world.route(op, *extra)
        assert pick(out) == ('claudeAgent', model), op
        assert out['routing']['chosen']['selection_basis'] == 'quota_substitution_exception'
        assert block(out)['to']['projected_pct'] == 72.4 and active(out) == {'codex': 'activated'}
    for entry in world.accounts['accounts'].values():
        entry.pop('substitution_rank', None)
    for op, model, effort, extra in AFFECTED:
        assert pick(world.route(op, *extra)) == ('claude-alt', model), op


def chosen_basis(out):
    return out['routing']['chosen']['selection_basis']


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


def test_a_healthy_account_is_preferred_over_a_rate_limit_demoted_one_for_the_same_arm():
    qs = lib_module('quota_substitution') if 'lib_module' in globals() else __import__('quota_substitution')
    sub = {'state': {'known': {'a-demoted': 10.0, 'b-healthy': 60.0}, 'constrained': set()},
           'ranks': {'a-demoted': 0, 'b-healthy': 5}, 'thresholds': {'activate_projected_pct': 95, 'release_projected_pct': 90}}
    rows = [{'instance': 'a-demoted', 'candidate': 1, 'rank': {'availability': 2}},
            {'instance': 'b-healthy', 'candidate': 1, 'rank': {'availability': 0}}]
    assert qs.substitute(rows, sub)['instance'] == 'b-healthy', 'availability comes before substitution_rank and projection'
    rows[0]['rank']['availability'] = 0
    assert qs.substitute(rows, sub)['instance'] == 'a-demoted', 'with equal availability, substitution_rank decides'



def test_a_fifo_at_the_state_path_reads_as_unreadable_without_blocking(world):
    world.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.mkfifo(world.state, 0o600)
    started = time.time()
    out = world.route('review.audit')
    assert time.time() - started < 15, 'a FIFO with no writer must not block route'
    assert block(out)['state'] == 'unreadable' and block(out)['state_written'] is False
    assert active(out) == {'codex': 'activated'}


def test_a_simulation_does_not_trust_memory_in_an_unsafe_directory(world):
    world.route('review.audit')                      # stores the codex activation in a safe directory
    world.state.parent.chmod(0o777)
    world.codex(92)                                  # inside the band: only memory could hold it
    result, out = world.cli('route', 'review.audit', *world.args(), '--now', NOW, raw=True)
    assert result.returncode == 0, result.stderr
    assert block(out)['state'] == 'unreadable' and active(out) == {}, 'unsafe location: decide without memory'
    world.state.parent.chmod(0o700)
