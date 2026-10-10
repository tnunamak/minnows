"""`route` tests. Inputs are pinned: a scrubbed 2026-10-10 snapshot of quota, catalog and T3 failure events.

Live state is never read. Each test changes one input at a time and runs the real CLI.
"""
import copy
import json
import os
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools/model-policy-ops/model-policy-ops'
FIX = REPO / 'tests/fixtures/route-live-20261010'
NOW = '2026-10-10T00:30:00Z'
PREFIX = 'orchestration_v2_projection_'
sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))

import route_inputs  # noqa: E402
from route_inputs import classify_failure  # noqa: E402
from runtime_sources import quota_forecast  # noqa: E402


def soon(**delta):
    """An RFC 3339 time relative to the real clock, for `directive add`, which records the real clock and refuses --now."""
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%SZ')


def load(name):
    return json.loads((FIX / name).read_text())


def apply_overlay(policy, overlay):
    policy = copy.deepcopy(policy)
    policy['routing'] = overlay['routing']
    for op in policy['operating_points']:
        if op['id'] in overlay['candidates']:
            op['candidates'] = overlay['candidates'][op['id']]
    return policy


def build_db(path, events, items=(), runs=()):
    db = sqlite3.connect(path)
    db.execute(f'CREATE TABLE {PREFIX}turn_items (turn_item_id TEXT, thread_id TEXT, run_id TEXT, type TEXT, status TEXT, updated_at TEXT, payload_json TEXT)')
    db.execute(f'CREATE TABLE {PREFIX}runs (run_id TEXT, provider_instance_id TEXT, payload_json TEXT)')
    for number, event in enumerate(events):
        run = f'run-{number}'
        db.execute(f'INSERT INTO {PREFIX}runs VALUES (?,?,?)', (run, event['instance'], '{}'))
        payload = {'startedAt': event['startedAt'], 'title': 'Provider error', 'failure': {'class': event['class'], 'message': event['message']}}
        db.execute(f'INSERT INTO {PREFIX}turn_items VALUES (?,?,?,?,?,?,?)',
                   (f'turn-item:provider:x:native-item:terminal-failure%3A{number}', 't', run, 'error', event['status'], event['startedAt'], json.dumps(payload)))
    for item in items:
        db.execute(f'INSERT INTO {PREFIX}turn_items VALUES (?,?,?,?,?,?,?)', item)
    for run in runs:
        db.execute(f'INSERT INTO {PREFIX}runs VALUES (?,?,?)', run)
    db.commit()
    db.close()


def event(instance, message, status='failed', klass='provider_error', at='2026-10-10T00:20:00.000Z'):
    return {'instance': instance, 'status': status, 'startedAt': at, 'class': klass, 'message': message}


RATE = 'Claude API rate limit reached. Try again later.'
AUTH = "Claude could not authenticate. For subscription login, run `claude auth login`"


class World:
    """Pinned inputs in a temp dir, with one mutable copy of each so a test changes one thing."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.quota = load('clawmeter.json')
        self.catalog = load('t3-models.json')
        self.events = load('t3-failures.json')['events']
        self.accounts = load('accounts.json')
        self.policy = apply_overlay(json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text()), load('pack-overlay.json'))
        self.db_extra = {'items': [], 'runs': []}
        self.with_db = True
        self.model_catalog = None
        self.receipts = tmp / 'state/decisions.jsonl'

    def write(self):
        for name, value in (('quota.json', self.quota), ('catalog.json', self.catalog), ('accounts.json', self.accounts), ('policy.json', self.policy)):
            (self.tmp / name).write_text(json.dumps(value))
        db = self.tmp / 't3.sqlite'
        db.unlink(missing_ok=True)
        build_db(db, self.events, **self.db_extra)

    def args(self):
        found = ['--policy', self.tmp / 'policy.json', '--available', self.tmp / 'catalog.json', '--quota', self.tmp / 'quota.json',
                 '--accounts', self.tmp / 'accounts.json', '--receipts', self.receipts, '--now', NOW]
        if self.with_db:
            found += ['--db', self.tmp / 't3.sqlite']
        if self.model_catalog:
            found += ['--model-catalog', self.model_catalog]
        return found

    def cli(self, *args):
        self.write()
        result = subprocess.run([str(TOOL), *map(str, args)], capture_output=True, text=True)
        return result, json.loads(result.stdout) if result.stdout.startswith('{') else None

    def route(self, op, *extra, record=None):
        flags = ['--decision-id', record or 'probe']
        if record:
            flags += ['--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt']
        result, out = self.cli('route', op, *self.args(), *flags, *extra)
        assert result.returncode == 0, result.stderr
        return out

    def directive(self, *args):
        result, out = self.cli('directive', *args, '--receipts', self.receipts, '--now', NOW)
        assert result.returncode == 0, result.stderr
        return out

    def add_directive(self, name, effect, *match, until='2026-10-11T00:00:00Z', source='owner', recorded_at='2026-10-10T00:00:00+00:00'):
        """Append an add row with a pinned clock. `directive add` records the real clock, so a pinned-time test writes the row itself."""
        row = {'schema_version': 1, 'kind': 'add', 'id': name, 'effect': effect, 'source': source, 'match': dict(m.split('=', 1) for m in match),
               'until': datetime.fromisoformat(until.replace('Z', '+00:00')).isoformat(), 'reason': 'test', 'recorded_at': recorded_at}
        path = self.receipts.with_name('directives.jsonl')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(path, 'a') as stream:
            stream.write(json.dumps(row) + '\n')
        os.chmod(path, 0o600)
        return row


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def chosen(out):
    return out['routing']['chosen']


def codes(out):
    return [r['code'] for r in out['routing']['judgment_reasons']]


def entry(out, instance, candidate=None):
    rows = [t for t in out['routing']['trace'] if t['instance'] == instance and (candidate is None or t['candidate'] == candidate)]
    assert len(rows) == 1, rows
    return rows[0]


def quota_of(out, row):
    """The full quota view that a trace row cites."""
    return out['routing']['quota'][row['quota']['ref']]


def set_forecast(world, provider, source, window, value):
    row = world.quota['providers'][provider]
    if source:
        row = next(s for s in row['sources'] if s['source']['id'] == source)
    row['forecast']['windows'][window]['projected_pct'] = value


def stable(out):
    return {k: v for k, v in out.items() if k != 'recorded_at'}


# ---- quota reader -------------------------------------------------------------------------------------------------

def test_quota_forecast_reads_source_and_provider_level_and_rejects_bad_shapes():
    quota = load('clawmeter.json')
    assert quota_forecast(quota, 'claude', 'alt')['7d All'] == 104.06
    assert quota_forecast(quota, 'claude', 'work')['7d All'] == 57.52
    assert quota_forecast(quota, 'openai')['7d'] == 134.44
    assert quota_forecast(quota, 'claude') is None, 'a provider with sources must be read through a source'
    assert quota_forecast(quota, 'claude', 'nope') is None
    assert quota_forecast(quota, 'missing') is None
    quota['providers']['openai']['forecast']['windows']['7d']['projected_pct'] = 'high'
    assert quota_forecast(quota, 'openai') == {}


# ---- AC4: pinned live snapshot --------------------------------------------------------------------------------------

def test_snapshot_quota_tight_chooses_sonnet_on_a_subscription_account_that_is_not_alt(world):
    out = world.route('implement.quota-tight')
    pick = chosen(out)
    assert pick['arm']['model'] == 'claude-sonnet-5-5' and pick['instance'] == 'claudeAgent'
    assert out['target'] == {'model': 'claude-sonnet-5-5', 'options': {'effort': 'medium'}, 'providerInstanceId': 'claudeAgent'}
    assert pick['selection_basis'] == 'primary' and pick['pace'] == 'on_track'
    assert out['routing']['judgment_required'] is False
    assert entry(out, 'claude-alt', 0)['removed_by'] == 'quota_exhausted'
    assert entry(out, 'claude-work', 0)['rank']['projected_pct'] == 57.52
    assert entry(out, 'codex', 1)['rank']['pace'] == 2, 'codex 7d is projected at 134.44 in the snapshot'


def test_snapshot_review_with_a_sonnet_maker_does_not_choose_sonnet(world):
    world.route('implement.quota-tight', record='maker1')
    out = world.route('review.audit', '--maker', 'maker1')
    assert chosen(out)['arm']['model'] == 'gpt-6.1-sol' and chosen(out)['instance'] == 'codex'
    claude = [t for t in out['routing']['trace'] if t['arm']['provider'] == 'claude' and t['instance'] == 'claudeAgent']
    assert {t['removed_by'] for t in claude} == {'independence'}
    assert out['routing']['independence']['maker']['provenance'] == 'receipt_only'
    assert out['routing']['independence']['maker']['vendor'] == {'value': 'claude', 'basis': 't3_driver_kind'}
    assert out['routing']['independence']['maker']['served_model'] == 'unattested'
    assert codes(out) == ['pace_not_on_track'], 'vendor independence rests on the T3 driver kind and needs no judgment'


def test_snapshot_review_with_avoid_codex_directive_chooses_no_codex_pair(world):
    world.add_directive('no-codex', 'avoid', 'provider=codex')
    out = world.route('review.audit')
    assert chosen(out)['arm']['provider'] == 'claude'
    assert all(t['removed_by'] == 'avoid_directive' for t in out['routing']['trace'] if t['arm']['provider'] == 'codex')
    assert out['routing']['directives_applied'] == ['no-codex']
    assert chosen(out)['basis'] == 'unvalidated' and set(codes(out)) == {'unvalidated_candidate', 'review_without_maker'}


def test_snapshot_exhausted_account_and_metered_instances_are_never_chosen(world):
    for op in ('implement.quota-tight', 'implement.standard', 'fanout.explore', 'recover.report'):
        out = world.route(op)
        assert chosen(out)['instance'] not in ('claude-alt', 'claude-api', 'claude-api-alt')
    out = world.route('implement.standard')
    assert entry(out, 'claude-alt')['removed_by'] == 'quota_exhausted'
    assert entry(out, 'claude-api')['removed_by'] == 'billing_not_subscription'
    assert entry(out, 'claude-api-alt')['detail'] == {'billing': 'metered'}


# ---- filters --------------------------------------------------------------------------------------------------------

def test_filter_not_runnable_when_model_or_effort_is_missing(world):
    for provider in world.catalog['data']['providers']:
        if provider['driver'] == 'claudeAgent':
            provider['models'] = [m for m in provider['models'] if m['slug'] != 'claude-sonnet-5-5']
    out = world.route('implement.quota-tight')
    assert entry(out, 'claudeAgent', 0)['detail'] == {'state': 'unavailable_model'}
    assert chosen(out)['arm']['provider'] == 'codex', 'the task_benchmark_prior candidate is the fallback'


def test_filter_billing_unknown_and_unmapped_are_excluded_like_metered(world):
    del world.accounts['accounts']['claudeAgent']
    del world.accounts['accounts']['claude-work']['billing']
    world.accounts['accounts']['claude-work']['billing'] = 'subscription'
    out = world.route('implement.quota-tight')
    assert entry(out, 'claudeAgent', 0)['removed_by'] == 'billing_not_subscription'
    assert entry(out, 'claudeAgent', 0)['detail'] == {'billing': 'unmapped'}
    assert chosen(out)['instance'] == 'claude-work'


def test_filter_allow_metered_directive_passes_a_metered_pair(world):
    world.add_directive('metered-ok', 'allow-metered', 'account=claude-api', 'op=implement.quota-tight')
    out = world.route('implement.quota-tight')
    assert entry(out, 'claude-api', 0)['removed_by'] is None
    assert entry(out, 'claude-api-alt', 0)['removed_by'] == 'billing_not_subscription'
    assert 'metered-ok' in out['routing']['directives_applied']


def test_filter_unmapped_account_with_allow_metered_still_has_unknown_pace(world):
    del world.accounts['accounts']['claude-api']
    world.add_directive('open', 'allow-metered', 'account=claude-api')
    row = entry(world.route('implement.quota-tight'), 'claude-api', 0)
    assert row['removed_by'] is None and row['quota']['pace'] == 'unknown' and row['quota']['pace_reason'] == 'unmapped_account'


def test_filter_every_pair_removed_gives_a_null_target_and_judgment(world):
    for account in ('claudeAgent', 'claude-work'):
        world.quota['providers']['claude']['sources'][[s['source']['id'] for s in world.quota['providers']['claude']['sources']].index('default' if account == 'claudeAgent' else 'work')]['usage']['windows'][1]['utilization'] = 100
    world.quota['providers']['openai']['usage']['windows'][0]['utilization'] = 100
    out = world.route('implement.quota-tight')
    assert out['target'] is None and out['launch_ready'] is False and chosen(out) is None
    assert out['selection']['state'] == 'no_route_target'
    assert codes(out) == ['no_eligible_pair'] and out['routing']['judgment_required'] is True


def test_filter_stale_quota_is_not_exhaustion_and_is_unknown_pace(world):
    source = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'alt')
    source['usage']['windows'][1]['resets_at'] = '2026-10-10T00:00:00Z'
    row = entry(world.route('implement.quota-tight'), 'claude-alt', 0)
    assert row['removed_by'] is None and row['quota']['pace'] == 'unknown'


def test_filter_requires_known_mismatch_removes_and_unknown_needs_judgment(world):
    world.catalog['data']['providers'] = [p for p in world.catalog['data']['providers'] if p['driver'] != 'codex']
    out = world.route('implement.oracle-bounded')
    assert chosen(out)['arm']['model'] == 'claude-sonnet-5-5'
    assert codes(out) == ['requires_unknown', 'unvalidated_candidate']
    out = world.route('implement.oracle-bounded', '--proof-class', 'oracle')
    assert codes(out) == ['unvalidated_candidate']
    out = world.route('implement.oracle-bounded', '--proof-class', 'judged')
    assert entry(out, 'claudeAgent')['removed_by'] == 'requires_not_met' and chosen(out) is None


# ---- directives -----------------------------------------------------------------------------------------------------

def test_directive_expiry_is_ignored_and_listed(world):
    world.add_directive('soon', 'avoid', 'provider=codex', until='2026-10-10T00:40:00Z')
    assert world.route('implement.quota-tight')['routing']['directives_applied'] == ['soon']
    later = world.route('implement.quota-tight', '--now', '2026-10-10T01:00:00Z')
    assert later['routing']['directives_applied'] == []
    listing = world.directive('list')
    assert listing['directives'][0]['state'] == 'active'
    result, out = world.cli('directive', 'list', '--receipts', world.receipts, '--now', '2026-10-10T01:00:00Z')
    assert out['directives'][0]['state'] == 'expired'


def test_directive_end_and_validation(world):
    world.add_directive('d1', 'prefer', 'account=claude-work')
    ended = world.directive('end', 'd1', '--reason', 'done')
    assert ended['state'] == 'ended' and ended['ended']['reason'] == 'done'
    assert world.directive('list')['directives'][0]['state'] == 'ended'
    for args, text in ((['add', '--id', 'x', '--effect', 'avoid', '--match', 'provider=codex', '--reason', 'r', '--source', 'owner'], '--until is required'),
                       (['add', '--id', 'x', '--effect', 'avoid', '--match', 'provider=codex', '--until', soon(days=1), '--reason', 'r'], '--source is required'),
                       (['add', '--id', 'x', '--effect', 'avoid', '--match', 'provider=codex', '--until', '2020-01-01T00:00:00Z', '--reason', 'r', '--source', 'owner'], 'future'),
                       (['add', '--id', 'x', '--effect', 'avoid', '--match', 'colour=red', '--until', soon(days=1), '--reason', 'r', '--source', 'owner'], '--match'),
                       (['add', '--id', 'd1', '--effect', 'avoid', '--match', 'provider=codex', '--until', soon(days=1), '--reason', 'r', '--source', 'owner'], 'already used'),
                       (['end', 'd1', '--reason', 'again'], 'no open directive'),
                       (['end', 'missing', '--reason', 'r'], 'no open directive')):
        result, _ = world.cli('directive', *args, '--receipts', world.receipts)
        assert result.returncode == 1 and text in result.stderr, (args, result.stderr)


def test_directive_file_is_private_append_only_and_refuses_symlinks(world, tmp_path):
    world.add_directive('d1', 'avoid', 'provider=codex')
    path = world.receipts.with_name('directives.jsonl')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]['kind'] == 'add' and rows[0]['source'] == 'owner'
    link = tmp_path / 'other/directives.jsonl'
    link.parent.mkdir(mode=0o700)
    link.symlink_to(path)
    result, _ = world.cli('directive', 'list', '--receipts', link.parent / 'decisions.jsonl')
    assert result.returncode == 1 and 'symlink' in result.stderr
    shared = tmp_path / 'shared'
    shared.mkdir(mode=0o770)
    shared.chmod(0o770)
    result, _ = world.cli('directive', 'add', '--id', 'x', '--effect', 'avoid', '--match', 'provider=codex', '--until', soon(days=1),
                          '--reason', 'r', '--source', 'owner', '--receipts', shared / 'decisions.jsonl')
    assert result.returncode == 1 and 'writable' in result.stderr
    result, _ = world.cli('directive', 'list', '--receipts', world.receipts, '--directives', world.receipts)
    assert result.returncode == 1 and 'same file' in result.stderr


def test_directive_prefer_is_a_rank_key_after_eligibility_and_never_defeats_a_filter(world):
    world.add_directive('work', 'prefer', 'account=claude-work')
    assert chosen(world.route('implement.quota-tight'))['instance'] == 'claude-work'
    world.add_directive('alt', 'prefer', 'account=claude-alt')
    out = world.route('implement.quota-tight')
    assert entry(out, 'claude-alt', 0)['removed_by'] == 'quota_exhausted' and chosen(out)['instance'] == 'claude-work'
    world.add_directive('metered', 'prefer', 'account=claude-api')
    assert entry(world.route('implement.quota-tight'), 'claude-api', 0)['removed_by'] == 'billing_not_subscription'
    world.add_directive('opus', 'prefer', 'model=claude-opus-5-5', 'op=review.audit')
    out = world.route('review.audit', '--maker-model', 'claude:claude-haiku-5-5', '--independence', 'model')
    assert chosen(out)['arm']['model'] == 'gpt-6.1-sol', 'prefer cannot lift an unvalidated candidate above an eligible primary'


def test_directive_authorize_keeps_basis_unvalidated_and_labels_the_exception(world):
    world.add_directive('auth', 'authorize', 'op=review.audit', 'model=claude-opus-5-5', source='owner')
    world.add_directive('avoid-sol', 'avoid', 'model=gpt-6.1-sol')
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol')
    pick = chosen(out)
    assert pick['arm']['model'] == 'claude-opus-5-5' and pick['basis'] == 'unvalidated'
    assert pick['selection_basis'] == 'authorized_exception' and pick['evidence'] == 'unvalidated'
    assert pick['authorized_by'] == [{'id': 'auth', 'source': 'owner', 'until': '2026-10-11T00:00:00+00:00'}]
    assert 'unvalidated_candidate' not in codes(out)
    assert pick['known_gaps'], 'authorization does not remove the gaps'


def test_directive_authorize_outranks_an_unauthorized_unvalidated_candidate(world):
    world.add_directive('auth', 'authorize', 'op=fanout.explore', 'model=claude-sonnet-5-5')
    world.catalog['data']['providers'] = [p for p in world.catalog['data']['providers'] if p['driver'] != 'codex']
    out = world.route('fanout.explore')
    assert chosen(out)['arm']['model'] == 'claude-sonnet-5-5' and chosen(out)['selection_basis'] == 'authorized_exception'
    assert entry(out, 'claudeAgent', 1)['rank']['eligibility'] == 1


def test_directive_conflict_avoid_wins_and_is_reported(world):
    world.add_directive('want', 'prefer', 'account=claude-work')
    world.add_directive('refuse', 'avoid', 'account=claude-work')
    out = world.route('implement.quota-tight')
    assert entry(out, 'claude-work', 0)['removed_by'] == 'avoid_directive'
    assert out['routing']['conflicts'] == [{'candidate': 0, 'instance': 'claude-work', 'avoid': ['refuse'], 'overridden': ['want'], 'resolution': 'avoid wins'}]
    assert 'directive_conflict' in codes(out)


# ---- rank keys ------------------------------------------------------------------------------------------------------

def test_rank_eligibility_comes_before_pack_order_and_pace(world):
    out = world.route('fanout.explore')
    assert chosen(out)['arm']['model'] == 'gpt-6.1-sol'
    assert [t['rank']['eligibility'] for t in out['routing']['trace'] if t['removed_by'] is None and t['candidate'] > 0] == [1] * 4


def test_rank_pace_orders_on_track_unknown_at_risk(world):
    set_forecast(world, 'claude', 'default', '7d All', 120)
    set_forecast(world, 'claude', 'work', '7d All', 10)
    out = world.route('implement.quota-tight')
    assert chosen(out)['instance'] == 'claude-work'
    del world.accounts['accounts']['claude-work']['quota_provider']
    out = world.route('implement.quota-tight')
    assert entry(out, 'claude-work', 0)['rank']['pace'] == 1 and entry(out, 'claudeAgent', 0)['rank']['pace'] == 2
    assert chosen(out)['instance'] == 'claude-work', 'unknown ranks above at_risk, below on_track'


def test_rank_availability_demotes_after_three_rate_limit_or_transport_events(world):
    world.events += [event('claudeAgent', RATE), event('claudeAgent', 'Connection error.'), event('claudeAgent', 'stream disconnected before completion')]
    out = world.route('implement.quota-tight')
    assert entry(out, 'claudeAgent', 0)['availability']['class'] == 'demoted'
    assert entry(out, 'claudeAgent', 0)['availability']['events'] == {'rate_limit': 1, 'transport': 2}
    assert chosen(out)['instance'] == 'claude-work' and entry(out, 'claude-work', 0)['availability']['class'] == 'healthy'


def test_rank_availability_two_events_do_not_demote(world):
    world.events += [event('claudeAgent', RATE), event('claudeAgent', RATE)]
    out = world.route('implement.quota-tight')
    assert chosen(out)['instance'] == 'claudeAgent' and chosen(out)['availability'] == 'healthy'


def test_rank_availability_events_outside_the_lookback_do_not_count(world):
    world.events += [event('claudeAgent', RATE, at='2026-10-09T22:00:00.000Z') for _ in range(5)]
    assert chosen(world.route('implement.quota-tight'))['instance'] == 'claudeAgent'


def test_rank_load_balancing_prefers_the_lower_projection_then_instance_id(world):
    assert chosen(world.route('implement.quota-tight'))['instance'] == 'claudeAgent'
    set_forecast(world, 'claude', 'default', '7d All', 90)
    out = world.route('implement.quota-tight')
    assert chosen(out)['instance'] == 'claude-work'
    assert out['routing']['rank_keys'][-2].endswith('load-balancing heuristic)')
    set_forecast(world, 'claude', 'work', '7d All', 90)
    assert chosen(world.route('implement.quota-tight'))['instance'] == 'claude-work', 'equal projections fall to the instance ID order'


def test_rank_binding_window_is_the_highest_projection_among_the_pace_windows(world):
    row = next(s for s in world.quota['providers']['claude']['sources'] if s['source']['id'] == 'default')
    row['usage']['windows'].append({'name': '7d', 'display_name': '7 days', 'utilization': 10, 'resets_at': '2026-10-16T02:00:00Z'})
    row['forecast']['windows']['7d'] = {'projected_pct': 150, 'indicator': '150%'}
    out = world.route('implement.quota-tight')
    row = entry(out, 'claudeAgent', 0)
    binding = quota_of(out, row)
    assert binding['pace'] == 'at_risk' and binding['binding']['window'] == '7d' and binding['binding']['remaining_pct'] == 90
    assert row['quota']['pace'] == 'at_risk' and row['quota']['projected_pct'] == 150


def test_quota_output_records_fetched_at_cache_and_resets_at(world):
    out = world.route('implement.quota-tight')
    quota = quota_of(out, entry(out, 'claudeAgent', 0))
    assert quota['fetched_at'].startswith('2026-10-09T19:26') and quota['cache']['hit'] is True and quota['source_id'] == 'default'
    window = next(w for w in quota['windows'] if w['name'] == '7d All')
    assert window['resets_at'] and window['remaining_pct'] == 95 and window['projected_pct'] == 37.43


# ---- judgment triggers ---------------------------------------------------------------------------------------------

def test_judgment_pace_not_on_track(world):
    set_forecast(world, 'claude', 'default', '7d All', 120)
    set_forecast(world, 'claude', 'work', '7d All', 120)
    out = world.route('implement.quota-tight')
    assert chosen(out)['arm']['provider'] == 'claude'
    assert 'pace_not_on_track' in codes(out)


def test_judgment_unvalidated_candidate_and_primary_without_judgment(world):
    out = world.route('implement.standard')
    assert codes(out) == [] and chosen(out)['arm']['model'] == 'claude-opus-5-5'
    world.add_directive('no-claude', 'avoid', 'provider=claude')
    out = world.route('implement.standard')
    assert chosen(out)['arm']['model'] == 'gpt-6.1-sol' and 'unvalidated_candidate' in codes(out)


def test_judgment_auth_config_events_do_not_demote_and_need_judgment(world):
    world.events += [event('claudeAgent', AUTH)] * 4
    out = world.route('implement.quota-tight')
    assert chosen(out)['instance'] == 'claudeAgent' and chosen(out)['availability'] == 'healthy'
    assert codes(out) == ['auth_config_events']
    assert out['routing']['judgment_reasons'][0]['detail'] == {'instance': 'claudeAgent', 'count': 4}


def test_judgment_other_failure_classes_are_reported_only(world):
    world.events += [event('claudeAgent', 'This content was flagged for possible cybersecurity risk.')] * 4
    world.events += [event('claudeAgent', 'Claude API unknown.')] * 4
    out = world.route('implement.quota-tight')
    assert codes(out) == [] and entry(out, 'claudeAgent', 0)['availability']['events'] == {'content_policy': 4, 'unknown': 4}


def test_judgment_cancellations_and_recoveries_are_not_failures(world):
    base = stable(world.route('implement.quota-tight'))
    world.events += [event('claudeAgent', RATE, status='cancelled', klass='cancelled')] * 5
    world.events += [event('claudeAgent', RATE, status='interrupted')] * 5
    world.events += [event('claudeAgent', RATE, status='completed')] * 5
    world.events += [event('claudeAgent', RATE, klass='interrupted')] * 5
    assert stable(world.route('implement.quota-tight')) == base


def test_judgment_review_without_maker(world):
    assert 'review_without_maker' in codes(world.route('review.audit'))
    assert 'review_without_maker' not in codes(world.route('implement.standard'))


def test_judgment_maker_unknown_and_caller_claim(world):
    out = world.route('review.audit', '--maker-model', 'claude:claude-sonnet-5-5')
    assert out['routing']['independence']['maker']['provenance'] == 'caller_claim'
    assert 'independence_caller_claim' in codes(out)
    world.route('implement.quota-tight', record='maker-null', )
    receipts = [json.loads(line) for line in world.receipts.read_text().splitlines()]
    receipts[0]['target'] = None
    world.receipts.write_text(json.dumps(receipts[0]) + '\n')
    out = world.route('review.audit', '--maker', 'maker-null')
    assert out['routing']['independence']['maker']['provenance'] == 'unknown'
    assert 'independence_maker_unknown' in codes(out)


def test_judgment_newer_ga_model_is_informational_and_keeps_the_target(world, tmp_path):
    catalog = {'models': [{'id': 'claude-sonnet-5-5', 'family': 'claude-sonnet', 'tier': 'sonnet', 'status': 'ga', 'released': '2026-09-28'},
                          {'id': 'claude-sonnet-5', 'family': 'claude-sonnet', 'tier': 'sonnet', 'status': 'ga', 'released': '2026-10-20'}]}
    (tmp_path / 'models.json').write_text(json.dumps(catalog))
    world.model_catalog = tmp_path / 'models.json'
    out = world.route('implement.quota-tight')
    reason = out['routing']['judgment_reasons']
    assert [r['code'] for r in reason] == ['newer_ga_model_not_in_pack'] and reason[0]['informational'] is True
    assert out['target'] is not None and out['routing']['judgment_required'] is False, 'informational reasons do not set judgment'
    catalog['models'][1]['status'] = 'preview'
    (tmp_path / 'models.json').write_text(json.dumps(catalog))
    assert codes(world.route('implement.quota-tight')) == []


def test_candidate_missing_from_catalog_falls_back_with_judgment_only_when_unvalidated(world):
    for provider in world.catalog['data']['providers']:
        if provider['driver'] == 'codex':
            provider['models'] = [m for m in provider['models'] if m['slug'] != 'gpt-6.1-sol']
    out = world.route('review.audit')
    assert chosen(out)['arm']['model'] == 'claude-opus-5-5' and 'unvalidated_candidate' in codes(out)
    assert entry(out, 'codex', 0)['detail'] == {'state': 'unavailable_model'}
    for provider in world.catalog['data']['providers']:
        if provider['driver'] == 'codex':
            provider['models'] = [m for m in provider['models'] if m['slug'] != 'gpt-6-luna']
    out = world.route('fanout.dollar-tight')
    assert chosen(out)['arm']['model'] == 'claude-sonnet-5-5' and chosen(out)['selection_basis'] == 'evidence_prior'
    assert chosen(out)['evidence'] == 'prior' and chosen(out)['prior']['harness_matched'] is False
    assert codes(out) == []


# ---- independence and maker provenance ------------------------------------------------------------------------------

def maker_item(decision_id, target, instance, model, options, child='child-1'):
    payload = {'toolName': 'mcp__t3_code__delegate_task', 'startedAt': '2026-10-10T00:10:00Z',
               'input': {'clientRequestId': decision_id, 'target': target}, 'output': {'childRunId': child}}
    return (f'item-{decision_id}', 't', 'parent-run', 'dynamic_tool', 'completed', '2026-10-10T00:10:00Z', json.dumps(payload)), \
        (child, instance, json.dumps({'modelSelection': {'model': model, 'options': options}}))


def test_provenance_t3_run_config_when_the_child_run_matches_the_receipt_target(world):
    out = world.route('implement.quota-tight', record='maker1')
    item, run = maker_item('maker1', out['target'], 'claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'})
    world.db_extra = {'items': [item], 'runs': [run]}
    assert world.route('review.audit', '--maker', 'maker1')['routing']['independence']['maker']['provenance'] == 't3_run_config'
    item, run = maker_item('maker1', out['target'], 'claudeAgent', 'claude-opus-5-5', {'effort': 'medium'})
    world.db_extra = {'items': [item], 'runs': [run]}
    maker = world.route('review.audit', '--maker', 'maker1')['routing']['independence']['maker']
    assert maker['provenance'] == 'unknown' and maker['issue'] == 'child_run_differs_from_receipt'


def test_provenance_receipt_only_without_db_and_unknown_maker_receipt_is_an_error(world):
    world.route('implement.quota-tight', record='maker1')
    world.with_db = False
    assert world.route('review.audit', '--maker', 'maker1')['routing']['independence']['maker']['provenance'] == 'receipt_only'
    result, _ = world.cli('route', 'review.audit', *world.args(), '--maker', 'nobody')
    assert result.returncode == 1 and 'maker receipt not found' in result.stderr
    result, _ = world.cli('route', 'review.audit', *world.args(), '--maker', 'maker1', '--maker-model', 'claude:x')
    assert result.returncode == 1 and 'exclusive' in result.stderr


def survivors(out, provider):
    return {t['arm']['model'] for t in out['routing']['trace'] if t['removed_by'] is None and t['arm']['provider'] == provider}


def test_independence_vendor_level_removes_the_makers_vendor(world):
    out = world.route('review.audit', '--maker-model', 'claude:claude-sonnet-5-5', '--independence', 'vendor')
    assert survivors(out, 'claude') == set() and survivors(out, 'codex') == {'gpt-6.1-sol'}
    assert {t['detail']['why'] for t in out['routing']['trace'] if t['removed_by'] == 'independence'} == {'same_vendor'}


def test_independence_model_level_is_requested_only_within_one_vendor(world):
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5-5', '--independence', 'model')
    assert survivors(out, 'claude') == {'claude-sonnet-5-5'} and survivors(out, 'codex') == {'gpt-6.1-sol'}
    assert {t['detail']['why'] for t in out['routing']['trace'] if t['removed_by'] == 'independence'} == {'same_model'}
    assert chosen(out)['arm']['provider'] == 'codex', 'the eligible primary outranks the unvalidated Sonnet'
    assert 'independence_requested_only' not in codes(out)
    world.add_directive('no-codex', 'avoid', 'provider=codex')
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5-5', '--independence', 'model')
    assert chosen(out)['arm']['model'] == 'claude-sonnet-5-5' and 'independence_requested_only' in codes(out)


def test_independence_family_level_uses_the_model_catalog_family(world):
    out = world.route('review.audit', '--maker-model', 'claude:claude-haiku-5-5', '--independence', 'family')
    kept = [t for t in out['routing']['trace'] if t['removed_by'] is None and t['arm']['provider'] == 'claude']
    assert {t['arm']['model'] for t in kept} == {'claude-opus-5-5', 'claude-sonnet-5-5'}
    assert all(t['requested_only_independence'] for t in kept)
    out = world.route('review.audit', '--maker-model', 'claude:claude-opus-5', '--independence', 'family')
    same = {t['arm']['model'] for t in out['routing']['trace'] if t['removed_by'] == 'independence'}
    assert same == {'claude-opus-5-5'}


def test_independence_default_comes_from_the_pack_for_review_only(world):
    out = world.route('review.audit', '--maker-model', 'claude:claude-sonnet-5-5')
    assert out['routing']['independence']['required'] == 'vendor'
    out = world.route('implement.standard', '--maker-model', 'claude:claude-sonnet-5-5')
    assert out['routing']['independence']['required'] is None and chosen(out)['arm']['provider'] == 'claude'


# ---- availability inputs degrade to unknown ---------------------------------------------------------------------------

def test_availability_unknown_without_db_missing_table_or_changed_schema(world, tmp_path):
    world.with_db = False
    out = world.route('implement.quota-tight')
    assert out['routing']['inputs']['failures'] | {} == {'state': 'unknown', 'reason': 'not_supplied', 'lookback_minutes': 60, 'unjoined': None}
    assert entry(out, 'claudeAgent', 0)['availability']['class'] == 'unknown'
    world.with_db = True
    empty = tmp_path / 'empty.sqlite'
    sqlite3.connect(empty).close()
    result, out = world.cli('route', 'implement.quota-tight', *world.args()[:-2], '--db', empty)
    assert result.returncode == 0 and out['routing']['inputs']['failures']['reason'] == 'turn_items_table_absent'
    odd = tmp_path / 'odd.sqlite'
    db = sqlite3.connect(odd)
    db.execute(f'CREATE TABLE {PREFIX}turn_items (turn_item_id TEXT)')
    db.execute(f'CREATE TABLE {PREFIX}runs (run_id TEXT)')
    db.commit()
    db.close()
    result, out = world.cli('route', 'implement.quota-tight', *world.args()[:-2], '--db', odd)
    assert result.returncode == 0 and out['routing']['inputs']['failures']['reason'] == 'schema_mismatch'
    result, out = world.cli('route', 'implement.quota-tight', *world.args()[:-2], '--db', tmp_path / 'absent.sqlite')
    assert result.returncode == 0 and out['routing']['inputs']['failures']['reason'] == 'database_unreadable'


def test_availability_unknown_never_outranks_healthy(world):
    world.with_db = False
    unknown = entry(world.route('implement.quota-tight'), 'claudeAgent', 0)['rank']['availability']
    world.with_db = True
    healthy = entry(world.route('implement.quota-tight'), 'claudeAgent', 0)['rank']['availability']
    assert (healthy, unknown) == (0, 1)


@pytest.mark.parametrize('message,klass', [
    ('Claude API rate limit reached. Try again later.', 'rate_limit'),
    ('Connection error.', 'transport'), ('The provider event stream closed unexpectedly.', 'transport'),
    ('stream disconnected before completion: Connection refused (os error 111)', 'transport'),
    ("Claude could not authenticate. For subscription login", 'auth_config'),
    ('No conversation found with session ID: SESSION', 'auth_config'),
    ('Insufficient context allowance for the provider handoff.', 'auth_config'),
    ('Claude is still running background agents or commands', 'auth_config'),
    ('This content was flagged for possible cybersecurity risk.', 'content_policy'),
    ('Claude API unknown.', 'unknown'), ('Provider turn failed.', 'unknown'), (None, 'unknown')])
def test_failure_classes(message, klass):
    assert classify_failure(message) == klass


def test_pinned_snapshot_failure_events_are_classified_without_crash(world):
    out = world.route('implement.quota-tight')
    alt = entry(out, 'claude-alt')['quota']
    assert alt['exhausted'] is True
    events = [e['message'] for e in world.events]
    assert sum(classify_failure(m) == 'rate_limit' for m in events) > 10


# ---- receipts, replay, override, audit -------------------------------------------------------------------------------

def test_receipt_is_a_valid_resolve_receipt_with_route_fields(world):
    out = world.route('implement.quota-tight', record='r1')
    stored = json.loads(world.receipts.read_text().splitlines()[0])
    assert stored['request']['route'] is True and stored['request']['maker'] is None
    assert stored['routing']['router_version'] == 1 and len(stored['routing']['params_sha256']) == 64
    assert stored['routing']['routed'] == {'arm': {'effort': 'medium', 'model': 'claude-sonnet-5-5', 'provider': 'claude'}, 'account': 'claudeAgent'}
    assert stored['routing']['final']['overridden'] is False and stored['routing']['evidence']['label'] == 'descriptive_only'
    assert stored['routing']['calibration_notes']['applies_changes'] is False
    assert stored['target'] == out['target'] and stored['launch_ready'] is True
    assert stored['selection']['arm']['mode'] == 'standard'
    assert stat.S_IMODE(world.receipts.stat().st_mode) == 0o600


def test_replay_returns_the_stored_receipt_and_does_not_route_again(world):
    first = world.route('implement.quota-tight', record='r1')
    set_forecast(world, 'claude', 'default', '7d All', 120)
    set_forecast(world, 'claude', 'work', '7d All', 10)
    again = world.route('implement.quota-tight', record='r1')
    assert again['replayed'] is True and again['target'] == first['target']
    result, _ = world.cli('route', 'implement.quota-tight', *world.args(), '--decision-id', 'r1', '--record', '--parent-model', 'pm',
                          '--parent-provider-instance', 'pp', '--parent-thread', 'pt', '--maker-model', 'claude:x')
    assert result.returncode == 1 and 'changed inputs' in result.stderr
    result, _ = world.cli('resolve', 'implement.quota-tight', *world.args(), '--decision-id', 'r1', '--record', '--parent-model', 'pm',
                          '--parent-provider-instance', 'pp', '--parent-thread', 'pt')
    assert result.returncode == 1 and 'changed inputs' in result.stderr


def test_old_receipts_without_route_fields_still_replay(world):
    args = ['--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt', '--decision-id', 'old', '--record']
    result, first = world.cli('resolve', 'implement.standard', *world.args(), *args)
    assert result.returncode == 0 and 'route' not in first['request'] and 'routing' not in first
    result, second = world.cli('resolve', 'implement.standard', *world.args(), *args)
    assert second['replayed'] is True


def test_route_is_deterministic_apart_from_timestamps(world):
    runs = [stable(world.route('review.audit', '--maker-model', 'claude:claude-sonnet-5-5')) for _ in range(3)]
    assert json.dumps(runs[0], sort_keys=True) == json.dumps(runs[1], sort_keys=True) == json.dumps(runs[2], sort_keys=True)
    world.write()
    first = subprocess.run([str(TOOL), 'route', 'implement.quota-tight', *map(str, world.args()), '--decision-id', 'same'], capture_output=True, text=True).stdout
    second = subprocess.run([str(TOOL), 'route', 'implement.quota-tight', *map(str, world.args()), '--decision-id', 'same'], capture_output=True, text=True).stdout
    strip = lambda text: {k: v for k, v in json.loads(text).items() if k != 'recorded_at'}
    assert strip(first) == strip(second)


def test_override_records_the_routed_and_the_final_choice(world):
    result, _ = world.cli('route', 'implement.quota-tight', *world.args(), '--override', 'account=claude-work')
    assert result.returncode == 1 and 'require --reason' in result.stderr
    out = world.route('implement.quota-tight', '--override', 'account=claude-work', '--reason', 'owner wants Work', record='ov1')
    routing = out['routing']
    assert routing['routed']['account'] == 'claudeAgent' and routing['final']['account'] == 'claude-work' and routing['final']['overridden'] is True
    assert out['target']['providerInstanceId'] == 'claude-work' and out['overrides'] == {'account': 'claude-work'}
    out = world.route('implement.quota-tight', '--override', 'provider=codex', '--override', 'model=gpt-6-luna', '--override', 'effort=high',
                      '--reason', 'oracle task')
    assert out['target']['providerInstanceId'] == 'codex' and out['routing']['final']['arm']['model'] == 'gpt-6-luna'
    assert out['routing']['routed']['arm']['model'] == 'claude-sonnet-5-5'


def test_route_refuses_options_that_it_decides(world):
    for flag in (['--no-op'], ['--account-hint', 'codex'], ['--escalate-from', 'x'], ['--quota-provider', 'claude']):
        result, _ = world.cli('route', 'implement.quota-tight', *world.args(), *flag)
        assert result.returncode == 1 and 'route takes an op ID only' in result.stderr


def test_route_receipt_joins_an_export_in_audit_and_counts_route_coverage(world, tmp_path):
    out = world.route('implement.quota-tight', record='r1')
    export = {'schema_version': 1, 'delegations': [{'call_id': 'call-1', 'thread_id': 'pt', 'timestamp': '2099-01-01T00:00:00Z',
                                                      'input': {'clientRequestId': 'r1', 'target': out['target']}, 'output': {'childRunId': 'child-1'}}]}
    (tmp_path / 'export.json').write_text(json.dumps(export))
    result, audit = world.cli('audit', '--export', tmp_path / 'export.json', '--receipts', world.receipts, '--thread', 'pt')
    assert result.returncode == 0, result.stderr
    assert audit['request_match']['count'] == 1 and audit['delegations'][0]['request_match'] is True
    assert audit['route_coverage']['route'] == 1 and audit['route_coverage']['rate'] == 1.0
    export['delegations'].append(dict(export['delegations'][0], call_id='call-2', input={'clientRequestId': 'unknown', 'target': out['target']}))
    (tmp_path / 'export.json').write_text(json.dumps(export))
    result, audit = world.cli('audit', '--export', tmp_path / 'export.json', '--receipts', world.receipts, '--thread', 'pt')
    assert audit['route_coverage'] == audit['route_coverage'] | {'route': 1, 'resolve': 0, 'no_receipt': 1, 'denominator': 2, 'rate': 0.5}


def test_route_receipt_closes_followups_and_escalates(world):
    world.route('implement.quota-tight', record='r1')
    result, closed = world.cli('close', 'r1', '--receipts', world.receipts, '--close-id', 'c1', '--outcome', 'rejected', '--judged-by', 'independent',
                               '--check', 'independent_judged', '--evidence', json.dumps({'type': 'pr', 'repo': 'a/b', 'number': 1}))
    assert result.returncode == 0, result.stderr
    result, escalated = world.cli('resolve', 'implement.standard', *world.args(), '--escalate-from', 'r1', '--why', 'judged_insufficient',
                                  '--reason', 'review rejected it', '--handoff-boundary', 'new child', '--decision-id', 'r2', '--record',
                                  '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt')
    assert result.returncode == 0, result.stderr
    assert escalated['escalation']['from'] == 'r1'


def test_evidence_snapshot_is_descriptive_and_never_reorders(world):
    world.route('implement.quota-tight', record='r1')
    before = world.route('implement.quota-tight')
    ev = before['routing']['evidence']
    assert ev['label'] == 'descriptive_only' and ev['arm']['decisions'] == 1 and ev['arm']['closed'] == 0
    world.cli('close', 'r1', '--receipts', world.receipts, '--close-id', 'c1', '--outcome', 'rejected', '--judged-by', 'independent',
              '--check', 'independent_judged', '--evidence', json.dumps({'type': 'pr', 'repo': 'a/b', 'number': 1}))
    after = world.route('implement.quota-tight')
    assert after['routing']['evidence']['arm']['by_check']['independent_judged']['outcomes'] == {'rejected': 1}
    assert after['routing']['chosen'] == before['routing']['chosen'] and after['routing']['trace'] == before['routing']['trace']
    assert after['routing']['calibration_notes']['checked_rejections'] == [
        {'arm': {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}, 'check': 'independent_judged', 'rejected': 1}]
    other = world.route('review.audit')
    assert other['routing']['evidence']['arm']['decisions'] == 0, 'evidence is keyed on the exact op and arm'


# ---- pack validation ------------------------------------------------------------------------------------------------

def check(world, mutate):
    mutate(world.policy)
    result, _ = world.cli('--policy', world.tmp / 'policy.json', 'check')
    return result


def test_check_accepts_a_pack_with_and_without_the_new_fields(world):
    assert check(world, lambda p: None).returncode == 0
    legacy = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
    for op in legacy['operating_points']:
        op.pop('candidates', None)
    legacy.pop('routing', None)
    (world.tmp / 'legacy.json').write_text(json.dumps(legacy))
    assert subprocess.run([str(TOOL), '--policy', str(world.tmp / 'legacy.json'), 'check'], capture_output=True).returncode == 0


@pytest.mark.parametrize('mutate,text', [
    (lambda p: p['operating_points'][0]['candidates'].append({'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'primary'}), 'task_benchmark_prior or unvalidated'),
    (lambda p: p['operating_points'][0]['candidates'].append({'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'benchmark_at_least_primary'}), 'task_benchmark_prior or unvalidated'),
    (lambda p: p['operating_points'][0]['candidates'].append({'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'task_benchmark_prior'}), 'needs prior'),
    (lambda p: p['operating_points'][0]['candidates'].append({'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'unvalidated', 'prior': {}}), 'prior is only for'),
    (lambda p: p['operating_points'][0]['candidates'].append({'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'unvalidated', 'requires': {'proof_class': ['x']}}), 'requires.proof_class'),
    (lambda p: p['operating_points'][0].update(candidates=[]), 'non-empty list'),
    (lambda p: p['routing'].update(failure_demote_count=0), 'positive integer'),
    (lambda p: p['routing'].update(independence_default='best'), 'independence_default'),
    (lambda p: p['routing'].update(pace_windows='7d'), 'pace_windows'),
    (lambda p: p['routing'].update(surprise=1), 'unknown keys'),
    (lambda p: p['routing'].update(directive_max_days=0), 'positive integer'),
    (lambda p: p['routing'].update(lineage_key={'codex': 'generation'}), 'lineage_key'),
    (lambda p: p['routing'].update(lineage_key={}), 'lineage_key'),
])
def test_check_rejects_malformed_candidates_and_routing(world, mutate, text):
    # The first op in the overlay that has candidates is the one at index 0 after sorting; pick it explicitly.
    index = next(i for i, op in enumerate(world.policy['operating_points']) if op['id'] == 'implement.quota-tight')
    swap = world.policy['operating_points']
    swap[0], swap[index] = swap[index], swap[0]
    result = check(world, mutate)
    assert result.returncode == 1 and text in result.stderr, result.stderr


def test_task_benchmark_prior_requires_a_cited_same_effort_board(world):
    def weaken(policy):
        op = next(o for o in policy['operating_points'] if o['id'] == 'implement.quota-tight')
        op['candidates'][0]['prior']['effort_matched'] = False
    assert 'board_cited_for_op true, effort_matched true' in check(world, weaken).stderr


# ---- calibrate ------------------------------------------------------------------------------------------------------

def fake_receipt(decision_id, op, arm, account, reason, overrides, recorded='2026-10-09T12:00:00+00:00'):
    target = {'providerInstanceId': account, 'model': arm['model'], 'options': {'effort': arm['effort']}} if account else None
    return {'schema_version': 1, 'decision_id': decision_id, 'recorded_at': recorded, 'reason': reason, 'overrides': overrides,
            'request': {'op': op, 'overrides': overrides, 'route': None}, 'selection': {'arm': arm}, 'target': target,
            'parent': {'thread_id': 't', 'model': 'm', 'provider_instance_id': 'p'}}


def test_calibrate_proposes_from_override_clusters_changes_no_file_and_says_so(world):
    sonnet = {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}
    haiku = {'provider': 'claude', 'model': 'claude-haiku-5-5', 'effort': 'medium'}
    rows = [fake_receipt(f'a{i}', 'implement.quota-tight', sonnet, 'claude-alt', 'use the alternate account first', {'account': 'claude-alt'}) for i in range(3)]
    rows += [fake_receipt(f'b{i}', 'review.audit', haiku, 'claude-alt', 'cross-vendor judge', {'model': 'claude-haiku-5-5'}) for i in range(2)]
    rows += [fake_receipt('c0', 'review.audit', haiku, 'claude-work', 'single', {'account': 'claude-work'})]
    world.receipts.parent.mkdir(mode=0o700)
    world.receipts.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    world.write()
    snapshot = {p: p.read_bytes() for p in world.tmp.rglob('*') if p.is_file()}
    result, out = world.cli('calibrate', *world.args())
    assert result.returncode == 0, result.stderr
    assert snapshot == {p: p.read_bytes() for p in world.tmp.rglob('*') if p.is_file() and p in snapshot}
    assert set(snapshot) == {p for p in world.tmp.rglob('*') if p.is_file()}
    assert out['applies_changes'] is False
    clusters = out['override_clusters']
    assert [(c['op'], c['final']['account'], c['count']) for c in clusters] == [('implement.quota-tight', 'claude-alt', 3), ('review.audit', 'claude-alt', 2)]
    assert clusters[0]['proposals'][0] == {'kind': 'directive', 'effect': 'prefer', 'match': {'op': 'implement.quota-tight', 'model': 'claude-sonnet-5-5', 'account': 'claude-alt'},
                                           'note': 'needs --until and --source'}
    assert clusters[1]['proposals'][0]['kind'] == 'pack_candidate' and clusters[1]['proposals'][0]['basis'] == 'unvalidated'
    assert clusters[0]['reasons'] == [{'reason': 'use the alternate account first', 'count': 3}]
    assert out['coverage']['decisions'] == 6 and out['coverage']['overrides'] == 6
    assert out['quality_signals']['label'] == 'descriptive_only'
    assert out['availability']['state'] == 'observed'


def test_calibrate_reports_stale_pack_entries(world, tmp_path):
    world.policy['operating_points'][0]['expands_to']['model'] = 'claude-opus-4-8'
    world.policy['operating_points'][0].pop('candidates', None)
    (tmp_path / 'models.json').write_text(json.dumps({'models': [{'id': 'claude-opus-4-8', 'family': 'claude-opus', 'tier': 'opus', 'status': 'ga', 'released': '2026-05-28'},
                                                                  {'id': 'claude-opus-5', 'family': 'claude-opus', 'tier': 'opus', 'status': 'ga', 'released': '2026-07-24'}]}))
    world.model_catalog = tmp_path / 'models.json'
    result, out = world.cli('calibrate', *world.args())
    assert result.returncode == 0, result.stderr
    assert out['stale_pack']['state'] == 'partial', 'the two-model catalog does not know the other pack models'
    assert 'claude-opus-4-8' in out['stale_pack']['missing_from_live_catalog'] and out['stale_pack']['unchecked']
    assert all(u['reason'] == 'line_unknown' for u in out['stale_pack']['unchecked'])
    assert out['stale_pack']['newer_ga_not_in_pack'] == [{'pack_model': 'claude-opus-4-8', 'newer_model': 'claude-opus-5', 'released': '2026-07-24', 'line': ['claude', 'claude-opus']}]


def test_calibrate_since_filters_receipts(world):
    arm = {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}
    rows = [fake_receipt(f'old{i}', 'implement.quota-tight', arm, 'claude-alt', 'r', {'account': 'claude-alt'}, recorded='2026-10-01T00:00:00+00:00') for i in range(2)]
    rows += [fake_receipt('new0', 'implement.quota-tight', arm, 'claude-alt', 'r', {'account': 'claude-alt'})]
    world.receipts.parent.mkdir(mode=0o700)
    world.receipts.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    result, out = world.cli('calibrate', *world.args())
    assert out['override_clusters'][0]['count'] == 3
    result, out = world.cli('calibrate', *world.args(), '--since', '2026-10-05T00:00:00Z')
    assert out['override_clusters'] == [] and out['coverage']['decisions'] == 1


# ---- AC2: legacy commands against the baseline code ------------------------------------------------------------------

BASELINE = '82f2b80'


def baseline_tree(tmp_path):
    probe = subprocess.run(['git', 'cat-file', '-e', f'{BASELINE}^{{commit}}'], cwd=REPO, capture_output=True)
    if probe.returncode:
        pytest.skip('baseline commit is not in this clone')
    target = tmp_path / 'baseline'
    target.mkdir()
    archive = subprocess.run(['git', 'archive', BASELINE, 'tools/model-policy-ops', 'data/model-choice-policy'], cwd=REPO, capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', str(target)], input=archive.stdout, check=True)
    return target


def run_tool(tool, *args):
    result = subprocess.run([str(tool), *map(str, args)], capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_legacy_commands_are_byte_identical_to_the_baseline(world, tmp_path):
    base = baseline_tree(tmp_path)
    old_tool = base / 'tools/model-policy-ops/model-policy-ops'
    pack = base / 'data/model-choice-policy/operating-points.json'
    world.write()
    receipts_old, receipts_new = tmp_path / 'old/decisions.jsonl', tmp_path / 'new/decisions.jsonl'
    common = ['--available', tmp_path / 'catalog.json', '--quota', tmp_path / 'quota.json', '--policy', pack]
    for args in (['check'], ['list'], ['show', 'review.audit'], ['show', 'nope']):
        assert run_tool(old_tool, *common[-2:], *args) == run_tool(TOOL, *common[-2:], *args), args
    for op in ('implement.quota-tight', 'review.audit', 'fanout.dollar-tight'):
        for extra in ([], ['--account-hint', 'claude-work', '--quota-provider', 'claude', '--quota-source', 'work'], ['--override', 'effort=high', '--reason', 'r']):
            old = run_tool(old_tool, 'resolve', op, *common, *extra, '--receipts', receipts_old, '--decision-id', 'same')
            new = run_tool(TOOL, 'resolve', op, *common, *extra, '--receipts', receipts_new, '--decision-id', 'same')
            strip = lambda r: (r[0], {k: v for k, v in json.loads(r[1] or '{}').items() if k not in ('recorded_at', 'policy_path')}, r[2])
            assert strip(old) == strip(new), (op, extra)
    flags = ['--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt', '--decision-id', 'rec1', '--purpose', 'review']
    old = run_tool(old_tool, 'resolve', 'review.audit', *common, *flags, '--account-hint', 'codex', '--receipts', receipts_old)
    new = run_tool(TOOL, 'resolve', 'review.audit', *common, *flags, '--account-hint', 'codex', '--receipts', receipts_new)
    assert json.loads(old[1])['request'] == json.loads(new[1])['request'] and old[0] == new[0] == 0
    stored = lambda path: {k: v for k, v in json.loads(path.read_text().splitlines()[0]).items() if k not in ('recorded_at', 'policy_path')}
    assert stored(receipts_old) == stored(receipts_new)
    close = ['--close-id', 'c1', '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none']
    a = run_tool(old_tool, 'close', 'rec1', '--receipts', receipts_old, *close)
    b = run_tool(TOOL, 'close', 'rec1', '--receipts', receipts_new, *close)
    clean = lambda r: {k: v for k, v in json.loads(r[1]).items() if k not in ('recorded_at', 'policy_sha256')}
    assert a[0] == b[0] == 0 and clean(a) == clean(b)
    export = {'schema_version': 1, 'delegations': [{'call_id': 'c', 'thread_id': 'pt', 'timestamp': '2099-01-01T00:00:00Z',
                                                      'input': {'clientRequestId': 'rec1', 'target': json.loads(new[1])['target']}, 'output': {}}]}
    (tmp_path / 'export.json').write_text(json.dumps(export))
    a = run_tool(old_tool, 'audit', '--export', tmp_path / 'export.json', '--receipts', receipts_old, '--thread', 'pt')
    b = run_tool(TOOL, 'audit', '--export', tmp_path / 'export.json', '--receipts', receipts_new, '--thread', 'pt')
    audit_old, audit_new = json.loads(a[1]), json.loads(b[1])
    audit_old['delegations'][0].pop('receipt_before_call', None)
    audit_new['delegations'][0].pop('receipt_before_call', None)
    coverage = audit_new.pop('route_coverage')
    assert coverage['resolve'] == 1 and coverage['route'] == 0, 'audit adds route_coverage and nothing else'
    for report in (audit_old, audit_new):
        report['delegations'][0]['decision_outcome']['current'].pop('recorded_at')
    assert audit_old == audit_new


def test_unrecognized_extra_positional_still_fails_for_legacy_commands():
    for args in (['show', 'a', 'b'], ['list', 'x', 'y'], ['check', 'a', 'b']):
        code, _, err = run_tool(TOOL, *args)
        assert code == 2 and 'unrecognized arguments' in err


# ---- review repairs (checker report r1) -----------------------------------------------------------------------------

def maker_run(world, instance, model, options, op='implement.quota-tight'):
    """Record a maker receipt, then add a T3 child run for it that was launched as given."""
    out = world.route(op, record='maker1')
    item, run = maker_item('maker1', out['target'], instance, model, options)
    world.db_extra = {'items': [item], 'runs': [run]}
    return out


def maker_of(out):
    return out['routing']['independence']['maker']


def test_f1_child_run_on_another_vendor_gives_the_run_vendor_model_and_a_judgment(world):
    maker_run(world, 'codex', 'gpt-6.1-sol', {'effort': 'medium'})
    out = world.route('review.audit', '--maker', 'maker1')
    maker = maker_of(out)
    assert maker['provenance'] == 'unknown' and maker['issue'] == 'child_run_differs_from_receipt'
    assert maker['vendor'] == {'value': 'codex', 'basis': 't3_driver_kind'}, 'the vendor is the run instance driver kind, not the receipt vendor'
    assert maker['requested']['model'] == 'gpt-6.1-sol' and maker['requested']['provider'] == 'codex'
    codex = [t for t in out['routing']['trace'] if t['arm']['provider'] == 'codex']
    assert {t['removed_by'] for t in codex} == {'independence'}, 'the checker is not the maker vendor'
    assert chosen(out)['arm']['provider'] == 'claude' and 'independence_maker_unknown' in codes(out)


def test_f1_effort_only_difference_keeps_provenance_and_is_reported_apart(world):
    maker_run(world, 'claudeAgent', 'claude-sonnet-5-5', {'effort': 'high'})
    out = world.route('review.audit', '--maker', 'maker1')
    maker = maker_of(out)
    assert maker['provenance'] == 't3_run_config' and 'issue' not in maker
    assert maker['effort_mismatch'] == {'receipt': 'medium', 'run': 'high'}
    assert 'independence_maker_unknown' not in codes(out)


def test_f1_run_on_an_instance_outside_the_catalog_is_unknown_and_filters_nothing(world):
    maker_run(world, 'ghost-instance', 'claude-sonnet-5-5', {'effort': 'medium'})
    out = world.route('review.audit', '--maker', 'maker1')
    assert maker_of(out)['provenance'] == 'unknown' and maker_of(out)['vendor']['value'] is None
    assert not [t for t in out['routing']['trace'] if t['removed_by'] == 'independence']
    assert 'independence_maker_unknown' in codes(out)


def test_f1_unknown_maker_receipt_without_a_target_still_raises_the_reason(world):
    world.route('implement.quota-tight', record='maker-null')
    receipts = [json.loads(line) for line in world.receipts.read_text().splitlines()]
    receipts[0]['target'] = None
    world.receipts.write_text(json.dumps(receipts[0]) + '\n')
    out = world.route('review.audit', '--maker', 'maker-null')
    assert not [t for t in out['routing']['trace'] if t['removed_by'] == 'independence'] and 'independence_maker_unknown' in codes(out)


def removed_models(out, provider, by='independence'):
    return {t['arm']['model'] for t in out['routing']['trace'] if t['arm']['provider'] == provider and t['removed_by'] == by}


def test_f2_openai_family_is_the_tier_line_across_generations(world):
    out = world.route('review.audit', '--maker-model', 'codex:gpt-5.6-sol', '--independence', 'family')
    assert removed_models(out, 'codex') == {'gpt-6.1-sol'}, 'gpt-6.1-sol is on the same sol line as gpt-5.6-sol'
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6-luna', '--independence', 'family')
    assert removed_models(out, 'codex') == set() and chosen(out)['arm']['model'] == 'gpt-6.1-sol', 'luna is another line'
    assert 'independence_requested_only' in codes(out)


def test_f2_a_line_that_is_unknown_cannot_pass_family_independence(world):
    world.policy['routing']['lineage_key'] = {'claude': 'family'}
    out = world.route('review.audit', '--maker-model', 'codex:gpt-5.6-sol', '--independence', 'family')
    assert removed_models(out, 'codex') == {'gpt-6.1-sol'}
    assert {t['detail']['why'] for t in out['routing']['trace'] if t['removed_by'] == 'independence'} == {'line_unknown'}
    assert 'independence_line_unknown' in codes(out)
    world.policy['routing']['lineage_key'] = {'claude': 'family', 'codex': 'tier'}
    out = world.route('review.audit', '--maker-model', 'codex:gpt-9-sol', '--independence', 'family')
    assert removed_models(out, 'codex') == {'gpt-6.1-sol'}, 'a maker model that the model catalog lacks has no line'
    assert 'independence_line_unknown' in codes(out)


def test_f3_freshness_uses_the_line_key_for_openai_and_claude():
    catalog = route_inputs.read_model_catalog(REPO / 'data/model-catalog/models.json')
    lineage = {'claude': 'family', 'codex': 'tier'}
    policy = {'operating_points': [{'id': 'x', 'expands_to': {'provider': 'codex', 'model': 'gpt-5.6-sol', 'effort': 'high'}},
                                   {'id': 'y', 'expands_to': {'provider': 'claude', 'model': 'claude-opus-5', 'effort': 'high'}}]}
    live = [{'driver': 'codex', 'models': [{'slug': 'gpt-5.6-sol'}, {'slug': 'gpt-6-luna'}, {'slug': 'gpt-6-sol'}, {'slug': 'gpt-6.1-sol'}]},
            {'driver': 'claudeAgent', 'models': [{'slug': 'claude-opus-5'}, {'slug': 'claude-opus-5-5'}]}]
    newer = route_inputs.freshness(policy, live, catalog, lineage)['newer_ga_not_in_pack']
    assert {(n['pack_model'], n['newer_model']) for n in newer} == {('gpt-5.6-sol', 'gpt-6-sol'), ('gpt-5.6-sol', 'gpt-6.1-sol'), ('claude-opus-5', 'claude-opus-5-5')}
    assert route_inputs.freshness(policy, live, catalog, {'claude': 'family'})['newer_ga_not_in_pack'][0]['pack_model'] == 'claude-opus-5'
    assert len(route_inputs.freshness(policy, live, catalog, {'claude': 'family'})['newer_ga_not_in_pack']) == 1, 'no mapping, no line, never newer'
    assert route_inputs.freshness(policy, live, catalog, lineage)['state'] == 'checked'
    assert route_inputs.freshness(policy, live, catalog, lineage)['unchecked'] == []


def test_r5_a_pack_model_with_no_known_line_is_listed_unchecked_and_the_state_is_partial():
    catalog = route_inputs.read_model_catalog(REPO / 'data/model-catalog/models.json')
    policy = {'operating_points': [{'id': 'x', 'expands_to': {'provider': 'codex', 'model': 'gpt-5.6-sol', 'effort': 'high'}},
                                   {'id': 'y', 'expands_to': {'provider': 'claude', 'model': 'claude-opus-5', 'effort': 'high'}}]}
    live = [{'driver': 'codex', 'models': [{'slug': 'gpt-5.6-sol'}]}, {'driver': 'claudeAgent', 'models': [{'slug': 'claude-opus-5'}]}]
    fresh = route_inputs.freshness(policy, live, catalog, {'claude': 'family'})
    assert fresh['state'] == 'partial'
    assert fresh['unchecked'] == [{'provider': 'codex', 'model': 'gpt-5.6-sol', 'reason': 'line_unknown'}]
    assert route_inputs.freshness(policy, live, catalog, None)['state'] == 'partial'
    assert route_inputs.freshness(policy, live, {}, {'claude': 'family'})['state'] == 'unknown'


def test_r5_the_live_pack_has_every_model_checked(world):
    out = world.route('review.audit')
    assert out['routing']['inputs']['freshness'] == 'checked'


def test_f3_route_flags_a_newer_openai_generation_for_the_chosen_model(world):
    world.policy['operating_points'][next(i for i, o in enumerate(world.policy['operating_points']) if o['id'] == 'review.audit')]['expands_to']['model'] = 'gpt-5.6-sol'
    for provider in world.catalog['data']['providers']:
        if provider['driver'] == 'codex':
            provider['models'] += [dict(provider['models'][0], slug=slug) for slug in ('gpt-5.6-sol', 'gpt-6-sol')]
    out = world.route('review.audit', '--maker-model', 'claude:claude-sonnet-5-5')
    assert chosen(out)['arm']['model'] == 'gpt-5.6-sol'
    assert any(r['code'] == 'newer_ga_model_not_in_pack' and r['detail']['newer_model'] == 'gpt-6-sol' for r in out['routing']['judgment_reasons'])


def test_f4_override_to_a_metered_account_records_billing_and_raises_judgment(world):
    out = world.route('implement.quota-tight', '--override', 'account=claude-api', '--reason', 'owner', record='ov')
    final = out['routing']['final']
    assert out['target']['providerInstanceId'] == 'claude-api', 'the override stays allowed'
    assert final['billing'] == 'metered' and final['pace'] == 'unknown'
    assert [b['filter'] for b in final['filters_bypassed']] == ['billing_not_subscription']
    assert 'final_metered_without_directive' in codes(out) and out['routing']['judgment_required'] is True
    world.add_directive('metered-ok', 'allow-metered', 'account=claude-api')
    out = world.route('implement.quota-tight', '--override', 'account=claude-api', '--reason', 'owner')
    assert out['routing']['final']['filters_bypassed'] == [] and 'final_metered_without_directive' not in codes(out)


def test_f4_override_to_an_avoided_or_exhausted_account_raises_judgment(world):
    world.add_directive('no-work', 'avoid', 'account=claude-work')
    out = world.route('implement.quota-tight', '--override', 'account=claude-work', '--reason', 'owner')
    assert out['target']['providerInstanceId'] == 'claude-work' and 'final_avoided' in codes(out)
    assert [b['filter'] for b in out['routing']['final']['filters_bypassed']] == ['avoid_directive']
    out = world.route('implement.quota-tight', '--override', 'account=claude-alt', '--reason', 'owner')
    assert 'final_quota_exhausted' in codes(out) and out['routing']['final']['filters_bypassed'][0]['filter'] == 'quota_exhausted'


def test_f4_a_route_without_override_records_a_clean_final(world):
    out = world.route('implement.quota-tight')
    final = out['routing']['final']
    assert final['filters_bypassed'] == [] and final['billing'] == 'subscription' and final['pace'] == 'on_track'
    assert out['routing']['quota'][final['quota_ref']]['pace'] == 'on_track'


def test_f5_maker_model_provider_must_be_a_known_provider(world):
    result, _ = world.cli('route', 'review.audit', *world.args(), '--maker-model', 'openai:gpt-6.1-sol', '--decision-id', 'q')
    assert result.returncode == 1 and 'not a known provider' in result.stderr and 'codex' in result.stderr
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol')
    assert maker_of(out)['vendor'] == {'value': 'codex', 'basis': 'caller_claim'}


def add_args(name, *match, until=None):
    until = until or soon(days=1)
    return ['add', '--id', name, '--effect', 'avoid', *[a for m in match for a in ('--match', m)], '--until', until, '--reason', 'test', '--source', 'owner']


def directive_cli(world, *args):
    now = [] if args[0] == 'add' else ['--now', NOW]
    return world.cli('directive', *args, '--receipts', world.receipts, *now, '--policy', world.tmp / 'policy.json',
                     '--available', world.tmp / 'catalog.json', '--accounts', world.tmp / 'accounts.json')


def test_f6_directive_add_rejects_unknown_provider_model_and_op(world):
    for match in ('provider=openai', 'model=gpt-9-nope', 'op=review.nope'):
        result, _ = directive_cli(world, *add_args('bad', match))
        assert result.returncode == 1 and match in result.stderr, (match, result.stderr)
    assert not world.receipts.with_name('directives.jsonl').exists()
    for match in ('provider=codex', 'model=gpt-5.6-sol', 'op=review.audit'):
        result, _ = directive_cli(world, *add_args(match.replace('=', '-'), match))
        assert result.returncode == 0, result.stderr


def test_f6_unknown_account_warns_and_is_still_recorded(world):
    result, out = directive_cli(world, *add_args('acct', 'account=codex-typo'))
    assert result.returncode == 0 and 'codex-typo' in out['warnings'][0]
    result, out = directive_cli(world, *add_args('acct-ok', 'account=codex'))
    assert result.returncode == 0 and 'warnings' not in out


def test_f6_route_reports_active_directives_that_matched_no_pair(world):
    world.add_directive('hits', 'avoid', 'provider=codex')
    world.add_directive('elsewhere', 'prefer', 'op=fanout.explore')
    out = world.route('review.audit')
    assert out['routing']['directives_applied'] == ['hits'] and out['routing']['directives_unmatched'] == ['elsewhere']


def test_f6_directive_lifetime_is_capped_by_the_pack(world):
    result, _ = directive_cli(world, *add_args('long', 'provider=codex', until=soon(days=14, hours=1)))
    assert result.returncode == 1 and 'directive_max_days' in result.stderr and '14' in result.stderr
    result, _ = directive_cli(world, *add_args('limit', 'provider=codex', until=soon(days=13)))
    assert result.returncode == 0, result.stderr


def test_f6_directives_file_that_others_can_write_is_refused_everywhere(world):
    world.add_directive('d1', 'avoid', 'provider=codex')
    path = world.receipts.with_name('directives.jsonl')
    os.chmod(path, 0o660)
    for args in (['route', 'review.audit', *world.args(), '--decision-id', 'q'], ['calibrate', *world.args()]):
        result, _ = world.cli(*args)
        assert result.returncode == 1 and 'group- or world-writable' in result.stderr, args
    for args in (['list'], ['end', 'd1', '--reason', 'x'], add_args('d2', 'provider=codex')):
        result, _ = directive_cli(world, *args)
        assert result.returncode == 1 and 'group- or world-writable' in result.stderr, args
    os.chmod(path, 0o600)
    assert directive_cli(world, 'list')[0].returncode == 0


def test_f7_candidates_are_alternates_and_expands_to_is_always_the_primary(world):
    index = next(i for i, o in enumerate(world.policy['operating_points']) if o['id'] == 'implement.quota-tight')
    world.policy['operating_points'][index]['expands_to']['model'] = 'claude-opus-5-5'
    assert check(world, lambda p: None).returncode == 0, 'editing expands_to alone keeps the pack valid'
    out = world.route('implement.quota-tight')
    primary = [t for t in out['routing']['trace'] if t['candidate'] == 0]
    assert {t['arm']['model'] for t in primary} == {'claude-opus-5-5'} and {t['basis'] for t in primary} == {'primary'}
    result, resolved = world.cli('resolve', 'implement.quota-tight', *world.args(), '--decision-id', 'legacy')
    assert result.returncode == 0 and resolved['selection']['arm']['model'] == 'claude-opus-5-5'
    result = check(world, lambda p: p['operating_points'][index]['candidates'].append({'provider': 'claude', 'model': 'm', 'effort': 'low', 'basis': 'primary'}))
    assert result.returncode == 1 and 'task_benchmark_prior or unvalidated' in result.stderr


def test_f9_vendor_from_the_receipt_arm_is_labelled_so(world):
    world.route('implement.quota-tight', record='m1')
    world.catalog['data']['providers'] = [p for p in world.catalog['data']['providers'] if p.get('instanceId', p.get('providerInstanceId')) != 'claudeAgent']
    maker = maker_of(world.route('review.audit', '--maker', 'm1'))
    assert maker['vendor'] == {'value': 'claude', 'basis': 'receipt_arm'} and maker['provenance'] == 'receipt_only'


def test_f10_unjoined_failure_events_make_availability_unknown_for_every_instance(world):
    world.events = world.events + [event(None, RATE)]
    out = world.route('implement.quota-tight')
    assert out['routing']['inputs']['failures']['unjoined'] == 1
    survivors_ = [t for t in out['routing']['trace'] if t['removed_by'] is None]
    assert survivors_ and {t['availability']['class'] for t in survivors_} == {'unknown'}
    assert {t['availability']['reason'] for t in survivors_} == {'unjoined_failure_events'}
    assert not hasattr(route_inputs, 'ATTEMPT_COLUMNS')


def test_f11_directives_equal_to_receipts_names_the_directives_flag(world):
    result, _ = world.cli('directive', 'list', '--receipts', world.receipts, '--directives', world.receipts, '--now', NOW)
    assert result.returncode == 1 and '--receipts and --directives name the same file' in result.stderr


def test_f11_trace_keeps_one_quota_view_per_account_not_one_per_row(world):
    out = world.route('review.audit', record='size')
    assert world.receipts.stat().st_size < 18000, 'was 18556 bytes with the full quota view in every row'
    for row in out['routing']['trace']:
        assert set(row['quota']) == {'pace', 'pace_reason', 'exhausted', 'projected_pct', 'ref'}
        assert row['quota']['ref'] in out['routing']['quota']


def test_ac7_1_one_codex_forecast_changes_from_on_track_to_at_risk(world):
    """AC7.1 as written: change only the Codex 7d forecast and assert the three outcomes together."""
    set_forecast(world, 'openai', None, '7d', 50)
    base = {op: world.route(op) for op in ('implement.quota-tight', 'fanout.dollar-tight', 'review.audit')}
    assert chosen(base['fanout.dollar-tight'])['arm']['model'] == 'gpt-6-luna' and chosen(base['fanout.dollar-tight'])['pace'] == 'on_track'
    assert chosen(base['review.audit'])['arm']['model'] == 'gpt-6.1-sol' and 'pace_not_on_track' not in codes(base['review.audit'])
    set_forecast(world, 'openai', None, '7d', 134.44)
    tight, dollar, review = (world.route(op) for op in ('implement.quota-tight', 'fanout.dollar-tight', 'review.audit'))
    assert chosen(tight)['arm']['provider'] == 'claude' and chosen(tight) == chosen(base['implement.quota-tight'])
    assert chosen(dollar)['arm']['model'] == 'claude-sonnet-5-5' and chosen(dollar)['basis'] == 'task_benchmark_prior'
    assert chosen(dollar)['selection_basis'] == 'evidence_prior' and chosen(dollar)['evidence'] == 'prior'
    assert dollar['routing']['judgment_required'] is False and codes(dollar) == []
    assert chosen(review)['arm']['model'] == 'gpt-6.1-sol' and chosen(review)['pace'] == 'at_risk'
    assert 'pace_not_on_track' in codes(review) and review['routing']['judgment_required'] is True
    opus = [t for t in review['routing']['trace'] if t['arm']['model'] == 'claude-opus-5-5' and t['removed_by'] is None]
    assert opus and {t['basis'] for t in opus} == {'unvalidated'} and all(t['rank']['eligibility'] == 1 for t in opus)


def test_review_op_with_a_maker_and_no_independence_default_uses_vendor(world):
    del world.policy['routing']['independence_default']
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol')
    assert out['routing']['independence']['required'] == 'vendor'
    assert removed_models(out, 'codex') == {'gpt-6.1-sol'} and chosen(out)['arm']['provider'] == 'claude'
    out = world.route('implement.standard', '--maker-model', 'claude:claude-sonnet-5-5')
    assert out['routing']['independence']['required'] is None, 'a non-review op needs --independence'
    assert not [t for t in out['routing']['trace'] if t['removed_by'] == 'independence']


def test_calibrate_clusters_by_op_arm_account_and_overridden_fields_without_classifying_reasons(world):
    sonnet = {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}
    rows = [fake_receipt(f'a{i}', 'implement.quota-tight', sonnet, 'claude-alt', 'use the alternate account first', {'account': 'claude-alt'}) for i in range(2)]
    rows += [fake_receipt(f'b{i}', 'implement.quota-tight', sonnet, 'claude-alt', 'owner prefers the alternate account', {'account': 'claude-alt', 'model': 'claude-sonnet-5-5'}) for i in range(2)]
    world.receipts.parent.mkdir(mode=0o700)
    world.receipts.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    result, out = world.cli('calibrate', *world.args())
    assert result.returncode == 0, result.stderr
    clusters = {tuple(c['overridden_fields']): c for c in out['override_clusters']}
    assert set(clusters) == {('account',), ('account', 'model')} and all(c['count'] == 2 for c in clusters.values())
    assert clusters[('account',)]['reasons'] == [{'reason': 'use the alternate account first', 'count': 2}]
    assert clusters[('account', 'model')]['reasons'] == [{'reason': 'owner prefers the alternate account', 'count': 2}]
    assert not any('class' in key or 'category' in key for c in clusters.values() for key in c)


def close(world, decision_id, close_id, outcome, check='none', judged='parent', *extra):
    evidence = ['--evidence', json.dumps({'type': 'pr', 'repo': 'a/b', 'number': 1})] if check != 'none' else []
    result, _ = world.cli('close', decision_id, '--receipts', world.receipts, '--close-id', close_id, '--outcome', outcome,
                          '--judged-by', judged, '--check', check, *evidence, *extra)
    assert result.returncode == 0, result.stderr


def record_with_purpose(world, decision_id, purpose=None):
    extra = ['--purpose', purpose] if purpose else []
    world.route('implement.quota-tight', *extra, record=decision_id)


def test_outcome_counts_are_split_by_launch_purpose_and_carry_the_scope_statement(world):
    for decision_id, purpose in (('p1', 'review'), ('p2', 'review'), ('p3', 'execution'), ('p4', None)):
        record_with_purpose(world, decision_id, purpose)
    close(world, 'p1', 'c1', 'accepted')
    close(world, 'p2', 'c2', 'rejected', 'independent_judged', 'independent')
    close(world, 'p3', 'c3', 'accepted')
    scope = ('a close describes the delegated result only. For purpose=review it describes the review (findings accepted), '
             'not the success of the reviewed work or the cost of the whole procedure.')
    out = world.route('implement.quota-tight')
    evidence = out['routing']['evidence']
    assert evidence['outcome_scope'] == scope
    arm = evidence['arm']
    assert arm['by_outcome'] == {'accepted': 2, 'rejected': 1} and arm['rejections'] == 1
    assert arm['by_purpose'] == {'review': {'decisions': 2, 'closed': 2, 'by_outcome': {'accepted': 1, 'rejected': 1}, 'rejections': 1},
                                 'execution': {'decisions': 1, 'closed': 1, 'by_outcome': {'accepted': 1}, 'rejections': 0},
                                 'unknown': {'decisions': 1, 'closed': 0, 'by_outcome': {}, 'rejections': 0}}
    result, report = world.cli('calibrate', *world.args())
    assert result.returncode == 0, result.stderr
    assert report['quality_signals']['outcome_scope'] == scope
    signal = next(a for a in report['quality_signals']['arms'] if a['op'] == 'implement.quota-tight')
    assert signal['by_purpose'] == arm['by_purpose']


def test_abandoned_by_choice_and_superseded_are_never_rejections_or_arm_failures(world):
    for decision_id in ('a1', 'a2', 'a3', 'a4'):
        record_with_purpose(world, decision_id, 'review')
    close(world, 'a1', 'c1', 'abandoned_by_choice', 'independent_judged', 'independent')
    close(world, 'a2', 'c2', 'superseded', 'independent_judged', 'independent')
    close(world, 'a3', 'c3', 'rejected', 'independent_judged', 'independent')
    close(world, 'a4', 'c4', 'rejected')
    close(world, 'a4', 'c5', 'abandoned_by_choice', 'none', 'parent', '--supersedes', 'c4', '--reason', 'task dropped')
    out = world.route('implement.quota-tight')
    arm = out['routing']['evidence']['arm']
    assert arm['by_outcome'] == {'abandoned_by_choice': 2, 'rejected': 1, 'superseded': 1}
    assert arm['rejections'] == 1 and arm['by_purpose']['review']['rejections'] == 1
    assert out['routing']['calibration_notes']['checked_rejections'] == [
        {'arm': {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}, 'check': 'independent_judged', 'rejected': 1}]
    result, report = world.cli('calibrate', *world.args())
    assert report['quality_signals']['not_arm_failures'] == ['abandoned_by_choice', 'superseded']


# --- round 2 (R2): the directive cap holds on write and on read ---------------

def test_r2_directive_add_refuses_now_and_records_the_real_clock(world):
    result, _ = directive_cli(world, *add_args('back', 'provider=codex'), '--now', '2099-01-01T00:00:00Z')
    assert result.returncode == 1 and '--now is not allowed' in result.stderr
    assert not world.receipts.with_name('directives.jsonl').exists()
    result, out = directive_cli(world, *add_args('real', 'provider=codex'))
    assert result.returncode == 0
    recorded = datetime.fromisoformat(out['recorded_at'])
    assert abs((datetime.now(timezone.utc) - recorded).total_seconds()) < 60


def test_r2_a_row_beyond_the_cap_is_ignored_reported_and_listed_rejected(world):
    world.add_directive('hand', 'allow-metered', 'account=claude-api', until='2099-01-01T00:00:00Z')
    out = world.route('implement.quota-tight', '--override', 'account=claude-api', '--reason', 'x')
    rejected = out['routing']['directives_rejected']
    assert [r['id'] for r in rejected] == ['hand'] and 'directive_max_days' in rejected[0]['reason']
    assert out['routing']['directives_applied'] == [] and out['routing']['inputs']['directives']['active'] == []
    assert 'final_metered_without_directive' in codes(out), 'the metered override is not authorized by the rejected row'
    listed = directive_cli(world, 'list')[1]['directives'][0]
    assert listed['state'] == 'rejected' and 'directive_max_days' in listed['rejected_because']


def test_r2_a_row_recorded_after_the_evaluation_time_is_ignored(world):
    world.add_directive('future', 'avoid', 'provider=codex', until='2026-10-10T20:00:00Z', recorded_at='2026-10-10T10:00:00+00:00')
    out = world.route('review.audit')
    assert [r['id'] for r in out['routing']['directives_rejected']] == ['future']
    assert out['routing']['directives_rejected'][0]['reason'] == 'recorded_after_evaluation_time'
    assert chosen(out)['arm']['provider'] == 'codex', 'the avoid row grants nothing and blocks nothing'


def test_r2_a_row_inside_the_cap_is_not_reported_rejected(world):
    world.add_directive('fine', 'avoid', 'provider=codex')
    assert world.route('review.audit')['routing']['directives_rejected'] == []


# --- round 2 (R1): a maker decision can start more than one child run --------

def two_runs(world, first, second):
    out = world.route('implement.quota-tight', record='mk')
    item1, run1 = maker_item('mk', out['target'], *first, child='child-a')
    item2, run2 = maker_item('mk', out['target'], *second, child='child-b')
    world.db_extra = {'items': [item1, ('item-mk-2',) + item2[1:]], 'runs': [run1, run2]}


def test_r1_two_child_runs_on_different_vendors_are_unknown_and_filter_on_both(world):
    two_runs(world, ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'}), ('codex', 'gpt-6.1-sol', {'reasoningEffort': 'medium'}))
    out = world.route('review.audit', '--maker', 'mk')
    maker = maker_of(out)
    assert maker['provenance'] == 'unknown' and maker['issue'] == 'multiple_child_runs'
    assert [(r['instance'], r['provider'], r['model']) for r in maker['runs']] == [('claudeAgent', 'claude', 'claude-sonnet-5-5'), ('codex', 'codex', 'gpt-6.1-sol')]
    removed = {(t['arm']['provider'], t['removed_by']) for t in out['routing']['trace'] if t['removed_by'] == 'independence'}
    assert removed == {('claude', 'independence'), ('codex', 'independence')}, 'both makers\' vendors are removed'
    assert chosen(out) is None and codes(out) == ['no_eligible_pair'] and out['routing']['judgment_required'] is True


def test_r1_two_child_runs_that_agree_keep_run_config_and_report_effort_apart(world):
    two_runs(world, ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'}), ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'high'}))
    maker = maker_of(world.route('review.audit', '--maker', 'mk'))
    assert maker['provenance'] == 't3_run_config' and 'issue' not in maker
    assert maker['effort_mismatch']['receipt'] == 'medium' and sorted(maker['effort_mismatch']['run']) == ['high', 'medium']
    assert maker['vendor'] == {'value': 'claude', 'basis': 't3_driver_kind'}


def test_r1_two_child_runs_on_one_vendor_filter_family_and_model_against_each(world):
    two_runs(world, ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'}), ('claudeAgent', 'claude-opus-5-5', {'effort': 'medium'}))
    out = world.route('review.audit', '--maker', 'mk', '--independence', 'model')
    claude_models = {t['arm']['model'] for t in out['routing']['trace'] if t['arm']['provider'] == 'claude' and t['removed_by'] == 'independence'}
    assert {'claude-sonnet-5-5', 'claude-opus-5-5'} <= claude_models


def test_r1_two_child_runs_always_raise_the_maker_unknown_reason_when_a_target_survives(world):
    two_runs(world, ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'}), ('codex', 'gpt-6.1-sol', {'reasoningEffort': 'medium'}))
    out = world.route('review.audit', '--maker', 'mk', '--independence', 'model')
    assert chosen(out) is not None and 'independence_maker_unknown' in codes(out) and out['routing']['judgment_required'] is True


# --- round 2 (R3, R4): an override's reasons describe the final pair --------------------------------------------------

def tagged(out, code):
    return [r['applies_to'] for r in out['routing']['judgment_reasons'] if r['code'] == code]


def test_r3_an_override_to_a_pair_that_cannot_run_has_no_target_and_needs_judgment(world):
    out = world.route('implement.quota-tight', '--override', 'model=claude-nonexistent-9', '--reason', 'x')
    assert out['target'] is None and out['routing']['judgment_required'] is True
    assert 'final_target_null' in codes(out) and tagged(out, 'final_target_null') == ['final']
    assert 'final_not_runnable' in codes(out)


def test_r3_a_provider_switch_with_no_account_has_no_target_and_needs_judgment(world):
    out = world.route('review.audit', '--override', 'provider=claude', '--override', 'model=claude-opus-5-5', '--reason', 'x')
    assert out['target'] is None and 'final_target_null' in codes(out) and out['routing']['judgment_required'] is True


def test_r4_every_reason_says_which_pair_it_applies_to(world):
    out = world.route('review.audit', '--override', 'effort=high', '--reason', 'x')
    assert out['routing']['judgment_reasons'] and all(r['applies_to'] in ('routed', 'final') for r in out['routing']['judgment_reasons'])
    out = world.route('review.audit')
    assert {r['applies_to'] for r in out['routing']['judgment_reasons']} == {'routed'}


def test_r4_judgment_follows_the_final_pair_and_the_routed_reasons_stay_visible(world):
    set_forecast(world, 'openai', None, '7d', 50)
    set_forecast(world, 'claude', 'default', '7d All', 150)
    world.add_directive('want-claude', 'prefer', 'account=claudeAgent')
    plain = world.route('implement.quota-tight')
    assert chosen(plain)['pace'] == 'at_risk' and plain['routing']['judgment_required'] is True
    out = world.route('implement.quota-tight', '--override', 'provider=codex', '--override', 'model=gpt-6.1-sol', '--override', 'account=codex',
                      '--reason', 'x')
    assert tagged(out, 'pace_not_on_track') == ['routed'], 'the routed pair stays visible'
    assert out['routing']['final']['pace'] == 'on_track' and out['routing']['judgment_required'] is False


def test_r4_an_override_to_an_unvalidated_arm_raises_the_final_unvalidated_reason(world):
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol', '--override', 'model=claude-opus-5-5', '--override', 'provider=claude',
                      '--override', 'account=claude-work', '--reason', 'x')
    assert 'final' in tagged(out, 'unvalidated_candidate') and out['routing']['judgment_required'] is True


def test_r4_an_override_to_a_model_outside_the_pack_is_unvalidated(world):
    out = world.route('implement.quota-tight', '--override', 'model=claude-opus-4-8', '--override', 'account=claude-work', '--reason', 'x')
    assert tagged(out, 'unvalidated_candidate') == ['final'] and out['routing']['judgment_required'] is True


def test_an_override_that_only_changes_effort_is_unvalidated(world):
    out = world.route('implement.quota-tight', '--override', 'effort=low', '--override', 'account=claude-work', '--reason', 'x')
    assert out['target']['options'] == {'effort': 'low'}
    assert tagged(out, 'unvalidated_candidate') == ['final'] and out['routing']['judgment_required'] is True
    same = world.route('implement.quota-tight', '--override', 'effort=medium', '--override', 'account=claude-work', '--reason', 'x')
    assert 'unvalidated_candidate' not in codes(same), 'the exact pack arm keeps its basis'


def test_r4_an_override_to_the_makers_vendor_raises_the_independence_reasons(world):
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol', '--override', 'provider=codex', '--override', 'model=gpt-6.1-sol',
                      '--override', 'account=codex', '--reason', 'x')
    assert tagged(out, 'final_independence_bypassed') == ['final'] and tagged(out, 'independence_caller_claim') == ['routed', 'final']
    assert [b['filter'] for b in out['routing']['final']['filters_bypassed']] == ['independence']
    assert out['routing']['judgment_required'] is True


def test_r4_a_final_pair_that_passes_independence_at_the_model_level_is_requested_only(world):
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol', '--independence', 'model', '--override', 'provider=codex',
                      '--override', 'model=gpt-6-luna', '--override', 'account=codex', '--reason', 'x')
    assert 'final' in tagged(out, 'independence_requested_only')


def test_b_independence_names_where_the_required_level_came_from(world):
    assert world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol')['routing']['independence']['required_source'] == 'pack'
    assert world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol', '--independence', 'model')['routing']['independence']['required_source'] == 'flag'
    assert world.route('implement.quota-tight')['routing']['independence'] == {'required': None, 'required_source': None, 'maker': None}
    del world.policy['routing']['independence_default']
    out = world.route('review.audit', '--maker-model', 'codex:gpt-6.1-sol')
    assert out['routing']['independence']['required'] == 'vendor' and out['routing']['independence']['required_source'] == 'mechanism_default'
