"""`route` destinations: an ordered list of external workers that route offers before it picks a model.

Capacity commands are tiny shell scripts in a temp dir, so each probe outcome is real. The pinned route inputs come from test_route.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_route import NOW, REPO, run_tool, stable, two_runs, world  # noqa: F401  (world is a fixture)

BASELINE = '5fdc94d'
TYPED_BASELINE = '04365b20dad5f070e458134911d93e0d7fd75469'
OP = 'review.audit'
SUBMIT = {'command': 'w submit --task-kind <purpose>', 'assignment': {'request_id': '<request_id>', 'inputs': [{'github_repo': 'o/r'}], 'list': ['<purpose>', 1, None]}}


def script(tmp, name, body):
    path = tmp / name
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)
    return path


def capacity(tmp, name, available=True, reason='ok', marker=True):
    """A capacity command that prints a verdict and touches a marker, so a test can tell whether it ran."""
    out = json.dumps({'available': available, 'reason': reason, 'free': 7})
    touch = f"touch '{tmp / (name + '.ran')}'; " if marker else ''
    return script(tmp, name + '.sh', f"{touch}echo '{out}'")


def ran(tmp, name):
    return (tmp / (name + '.ran')).exists()


def entry(command, name='worker', purposes=('review', 'research', 'test-design'), timeout=5):
    return {'name': name, 'purposes': list(purposes), 'capacity_command': [str(command)], 'timeout_seconds': timeout,
            'how_to': '/abs/how-to.md', 'submit': SUBMIT}


def config(tmp, *entries, name='destinations.json'):
    path = tmp / name
    path.write_text(json.dumps({'schema_version': 1, 'destinations': list(entries)}))
    return path


def route(world, cfg, *extra, record=None, op=OP):
    return world.route(op, '--destinations', cfg, *extra, record=record)


def failing(world, cfg, *extra, record=None, op=OP):
    flags = ['--decision-id', record or 'probe']
    if record:
        flags += ['--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt']
    result, _ = world.cli('route', op, *world.args(), '--destinations', cfg, *flags, *extra)
    assert result.returncode == 1, result.stdout
    return result.stderr


def targets(value, path=''):
    """Every `target` key in the output, as (path, value)."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'target':
                found.append((path + '/target', item))
            found += targets(item, f'{path}/{key}')
    elif isinstance(value, list):
        for number, item in enumerate(value):
            found += targets(item, f'{path}/{number}')
    return found


def dest(out):
    return out['routing']['destination']


@pytest.fixture
def one(world, tmp_path):
    """A world with one available destination."""
    world.cap = capacity(tmp_path, 'cap')
    world.cfg = config(tmp_path, entry(world.cap))
    return world


# ---- rule 5d: chosen -------------------------------------------------------------------------------------------------

def test_chosen_destination_nulls_every_target_and_returns_an_offer(one):
    out = route(one, one.cfg, '--purpose', 'review', '--inputs', 'github')
    assert out['destination'] == 'worker' and out['target'] is None and out['launch_ready'] is False
    found = targets(out)
    assert len(found) > 3 and all(value is None for _, value in found), [p for p, v in found if v is not None]
    assert out['routing']['chosen']['target'] is None and out['routing']['final']['target'] is None
    offer = out['destination_offer']
    assert offer['name'] == 'worker' and offer['request_id'] == out['decision_id'] == 'probe' and offer['purpose'] == 'review'
    assert offer['how_to'] == '/abs/how-to.md'
    assert offer['capacity'] == {'available': True, 'reason': 'ok', 'free': 7}
    assert offer['submit']['command'] == 'w submit --task-kind review'
    assert offer['submit']['assignment'] == {'request_id': 'probe', 'inputs': [{'github_repo': 'o/r'}], 'list': ['review', 1, None]}
    assert dest(out)['chosen'] == 'worker' and dest(out)['considered'][0]['state'] == 'chosen'
    assert 'destination_inputs_unknown' not in [r['code'] for r in out['routing']['judgment_reasons']]


def test_inputs_unknown_adds_a_judgment_reason_and_does_not_block(one):
    out = route(one, one.cfg, '--purpose', 'test-design')
    reason = next(r for r in out['routing']['judgment_reasons'] if r['code'] == 'destination_inputs_unknown')
    assert reason['detail'] == 'the destination needs one pushed GitHub input; if inputs are local, re-run with --inputs local'
    assert out['routing']['judgment_required'] is True and out['destination'] == 'worker'


def test_models_are_still_ranked_and_kept_for_the_audit_trail(one):
    base = one.route(OP, '--purpose', 'review')
    out = route(one, one.cfg, '--purpose', 'review', '--inputs', 'github')
    assert out['routing']['chosen']['arm'] == base['routing']['chosen']['arm']
    assert out['routing']['trace'] == base['routing']['trace']


def test_override_does_not_suppress_the_destination(one):
    out = route(one, one.cfg, '--purpose', 'review', '--inputs', 'github', '--override', 'effort=high', '--reason', 'r')
    assert out['destination'] == 'worker' and out['overrides'] == {'effort': 'high'}
    assert all(value is None for _, value in targets(out))


# ---- rule 5a: facts --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('flags,reasons', [
    (['--inputs', 'local'], ['local-inputs']), (['--urgent'], ['synchronous']), (['--sensitive'], ['credential']),
    (['--inputs', 'local', '--urgent', '--sensitive'], ['local-inputs', 'synchronous', 'credential'])])
def test_facts_fall_back_to_the_model_without_probing(one, tmp_path, flags, reasons):
    out = route(one, one.cfg, '--purpose', 'review', *flags)
    assert dest(out)['chosen'] == 'model'
    assert dest(out)['fallback'] == {'destination': 'worker', 'reasons': reasons, 'source': 'facts'}
    assert out['target'] is not None and out['launch_ready'] is True and 'destination' not in out and 'destination_offer' not in out
    assert not ran(tmp_path, 'cap')


def test_facts_win_over_a_parent_skip_and_launch_facts_record_the_new_flags(one):
    out = route(one, one.cfg, '--purpose', 'review', '--inputs', 'local', '--sensitive', '--skip-destination', 'other', '--reason', 'why')
    assert dest(out)['fallback']['source'] == 'facts'
    assert out['launch_facts'] == {'schema_version': 1, 'purpose': 'review', 'inputs': 'local', 'sensitive': True}
    assert out['request']['skip_destination'] == ['other']


# ---- rule 5b: parent skip --------------------------------------------------------------------------------------------

def test_parent_skip_records_reasons_and_text_and_does_not_probe(one, tmp_path):
    out = route(one, one.cfg, '--purpose', 'review', '--skip-destination', 'synchronous', '--skip-destination', 'other',
                '--skip-destination', 'synchronous', '--reason', 'owner is waiting')
    assert dest(out)['fallback'] == {'destination': 'worker', 'reasons': ['synchronous', 'other'], 'source': 'parent', 'reason': 'owner is waiting'}
    assert out['target'] is not None and not ran(tmp_path, 'cap')


def test_skip_needs_a_reason_and_belongs_to_route(one):
    for skip in ('other', 'unavailable'):
        assert 'requires --reason' in failing(one, one.cfg, '--purpose', 'review', '--skip-destination', skip)
    result, _ = one.cli('resolve', OP, *one.args(), '--skip-destination', 'other', '--reason', 'r')
    assert result.returncode == 1 and 'route only' in result.stderr
    result, _ = one.cli('route', OP, *one.args(), '--skip-destination', 'bogus', '--reason', 'r')
    assert result.returncode == 2


# ---- rule 5c: capacity -----------------------------------------------------------------------------------------------

def test_unavailable_capacity_falls_back_with_the_probe_reason(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'full', available=False, reason='daily cap reached')))
    out = route(world, cfg, '--purpose', 'research')
    assert dest(out)['fallback'] == {'destination': 'worker', 'reasons': ['unavailable'], 'source': 'capacity', 'probe_reason': 'daily cap reached'}
    assert dest(out)['considered'][0]['capacity']['free'] == 7
    assert out['target'] is not None and out['launch_ready'] is True and 'destination' not in out


@pytest.mark.parametrize('kind,body', [
    ('invalid_json', 'echo not json'), ('invalid_json', 'true'), ('nonzero_exit', 'echo \'{"available": true, "reason": "x"}\'; exit 1'),
    ('invalid_shape', 'echo \'{"available": "yes"}\''), ('invalid_shape', 'echo \'[1]\'')])
def test_probe_errors_mean_unavailable(world, tmp_path, kind, body):
    cfg = config(tmp_path, entry(script(tmp_path, 'bad.sh', body)))
    out = route(world, cfg, '--purpose', 'review')
    assert dest(out)['fallback']['source'] == 'capacity'
    assert dest(out)['fallback']['probe_reason'] == f'capacity_probe_error: {kind}'


def test_probe_timeout_is_enforced_even_with_a_child_that_holds_the_pipe(world, tmp_path):
    cfg = config(tmp_path, entry(script(tmp_path, 'slow.sh', 'sleep 30'), timeout=0.5))
    start = time.monotonic()
    out = route(world, cfg, '--purpose', 'review')
    assert time.monotonic() - start < 15
    assert dest(out)['fallback']['probe_reason'] == 'capacity_probe_error: timeout'


def test_missing_binary_and_no_shell_and_closed_stdin(world, tmp_path):
    cfg = config(tmp_path, entry(tmp_path / 'nope'))
    assert dest(route(world, cfg, '--purpose', 'review'))['fallback']['probe_reason'] == 'capacity_probe_error: missing_binary'
    # The command is an argv list: shell syntax in an argument is never interpreted.
    echo = script(tmp_path, 'argv.sh', f"[ \"$1\" = '; touch {tmp_path}/shell' ] && echo '{{\"available\": true, \"reason\": \"argv\"}}'")
    item = entry(echo)
    item['capacity_command'].append(f'; touch {tmp_path}/shell')
    out = route(world, config(tmp_path, item, name='argv.json'), '--purpose', 'review', '--inputs', 'github')
    assert out['destination_offer']['capacity']['reason'] == 'argv' and not (tmp_path / 'shell').exists()
    eof = script(tmp_path, 'stdin.sh', 'read line; echo \'{"available": true, "reason": "eof"}\'')
    out = route(world, config(tmp_path, entry(eof), name='stdin.json'), '--purpose', 'review', '--inputs', 'github')
    assert out['destination_offer']['capacity']['reason'] == 'eof'


# ---- ordering --------------------------------------------------------------------------------------------------------

def test_first_destination_with_capacity_wins_and_later_ones_are_not_probed(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a'), name='first'), entry(capacity(tmp_path, 'b'), name='second'))
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github')
    assert out['destination'] == 'first' and ran(tmp_path, 'a') and not ran(tmp_path, 'b')


def test_unavailable_first_falls_through_to_the_second(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a', available=False, reason='busy'), name='first'),
                 entry(capacity(tmp_path, 'b'), name='second'))
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github')
    assert out['destination'] == 'second'
    assert [(c['name'], c['state']) for c in dest(out)['considered']] == [('first', 'unavailable'), ('second', 'chosen')]


def test_all_unavailable_falls_back_naming_the_first_and_listing_every_probe(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a', False, 'one'), name='first'), entry(capacity(tmp_path, 'b', False, 'two'), name='second'))
    out = route(world, cfg, '--purpose', 'review')
    assert dest(out)['fallback'] == {'destination': 'first', 'reasons': ['unavailable'], 'source': 'capacity', 'probe_reason': 'one'}
    assert [c['capacity']['reason'] for c in dest(out)['considered']] == ['one', 'two']


def test_only_destinations_that_list_the_purpose_are_considered(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a'), name='review-only', purposes=['review']),
                 entry(capacity(tmp_path, 'b'), name='research-only', purposes=['research']))
    out = route(world, cfg, '--purpose', 'research', '--inputs', 'github')
    assert out['destination'] == 'research-only' and not ran(tmp_path, 'a')


# ---- unchanged paths -------------------------------------------------------------------------------------------------

def test_unlisted_purpose_and_unknown_purpose_stay_on_the_model_and_say_why(one, tmp_path):
    for flags, reason, purpose in ((['--purpose', 'execution'], 'purpose_not_listed', 'execution'), ([], 'purpose_unknown', None)):
        out = route(one, one.cfg, *flags)
        assert dest(out) == {'chosen': 'model', 'considered': [],
                             'not_eligible': [{'name': 'worker', 'reason': reason, 'purpose': purpose}]}
        assert out['launch_ready'] is True and 'destination' not in out
        rows = [c for c in out['choice']['considered'] if c['kind'] == 'destination']
        assert [(c['candidate'], c['disposition'], c['reason']['code']) for c in rows] == [('worker', 'not_selected', reason)]
    assert not ran(tmp_path, 'cap')


def test_a_destination_without_the_purpose_is_named_even_when_another_is_chosen(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a'), name='review-only', purposes=['review']),
                 entry(capacity(tmp_path, 'b'), name='research-only', purposes=['research']))
    out = route(world, cfg, '--purpose', 'research', '--inputs', 'github')
    assert out['destination'] == 'research-only'
    assert dest(out)['not_eligible'] == [{'name': 'review-only', 'reason': 'purpose_not_listed', 'purpose': 'research'}]


def test_missing_config_changes_only_the_recorded_reason(world, tmp_path):
    plain = world.route(OP, '--purpose', 'review')
    missing = route(world, tmp_path / 'absent.json', '--purpose', 'review')
    assert stable(missing) == stable(plain) and 'skip_destination' not in plain['request']
    assert plain['routing']['destination'] == {'chosen': 'model', 'considered': [], 'not_considered': 'destinations_missing'}
    assert plain['launch_ready'] is True and 'destination' not in plain


def test_resolve_names_the_destinations_it_did_not_consider(one, tmp_path):
    result, out = one.cli('resolve', OP, *one.args(), '--destinations', one.cfg, '--purpose', 'review', '--decision-id', 'res-1')
    assert result.returncode == 0, result.stderr
    assert out['destination_skip'] == {'reason': 'explicit_resolution', 'destinations': [{'name': 'worker', 'listed_for_purpose': True}]}
    rows = [c for c in out['choice']['considered'] if c['kind'] == 'destination']
    assert [(c['candidate'], c['reason']['code']) for c in rows] == [('worker', 'explicit_resolution')]
    assert not ran(tmp_path, 'cap'), 'resolve never probes a destination'


def test_resolve_without_a_config_and_with_a_bad_one(world, tmp_path):
    result, out = world.cli('resolve', OP, *world.args(), '--destinations', tmp_path / 'absent.json', '--decision-id', 'res-2')
    assert result.returncode == 0 and out['destination_skip'] == {'reason': 'explicit_resolution', 'destinations_state': 'missing'}
    directory = tmp_path / 'a-directory'
    directory.mkdir()
    result, out = world.cli('resolve', OP, *world.args(), '--destinations', directory, '--decision-id', 'res-4')
    assert result.returncode == 0, 'an unreadable path (OSError) never blocks resolve'
    assert out['destination_skip'] == {'reason': 'explicit_resolution', 'destinations_state': 'unreadable'}
    bad = tmp_path / 'bad.json'
    bad.write_text('{')
    result, out = world.cli('resolve', OP, *world.args(), '--destinations', bad, '--decision-id', 'res-3')
    assert result.returncode == 0, 'a bad destinations file never blocks resolve'
    assert out['destination_skip'] == {'reason': 'explicit_resolution', 'destinations_state': 'unreadable'}


@pytest.mark.parametrize('text,message', [
    ('{', 'not valid JSON'), ('[]', 'schema_version 1'), ('{"schema_version": 3, "destinations": []}', 'schema_version 1'),
    ('{"schema_version": 1, "destinations": [1]}', 'must be an object')])
def test_malformed_config_is_a_clear_error(world, tmp_path, text, message):
    bad = tmp_path / 'bad.json'
    bad.write_text(text)
    assert message in failing(world, bad, '--purpose', 'review')


@pytest.mark.parametrize('field,value,message', [
    ('name', 'model', 'name'), ('purposes', ['dance'], 'purposes'), ('purposes', [], 'purposes'), ('capacity_command', ['rel'], 'absolute'),
    ('capacity_command', [], 'capacity_command'), ('timeout_seconds', 0, 'timeout_seconds'), ('timeout_seconds', 'x', 'timeout_seconds'),
    ('how_to', '', 'how_to'), ('submit', 'x', 'submit')])
def test_each_config_field_is_validated(world, tmp_path, field, value, message):
    item = entry(tmp_path / 'cap') | {field: value}
    assert message in failing(world, config(tmp_path, item), '--purpose', 'review')


def test_duplicate_names_are_rejected(world, tmp_path):
    assert 'unique' in failing(world, config(tmp_path, entry(tmp_path / 'a'), entry(tmp_path / 'b')), '--purpose', 'review')


def test_legacy_request_shape_is_unchanged_without_a_config_or_skip(world):
    out = world.route(OP)
    assert set(out['request']) == {'op', 'no_op', 'overrides', 'account', 'quota_provider', 'quota_source', 'reason', 'parent', 'escalate_from',
                                   'why', 'handoff_boundary', 'relaunch_of', 'route', 'maker', 'maker_model', 'independence', 'available', 'quota'}


# ---- simulation ------------------------------------------------------------------------------------------------------

def simulate(world, cfg, *extra):
    """A real `route --now`: the harness would otherwise lift --now into a pinned clock, so run raw."""
    result, out = world.cli('route', OP, *world.args(), '--destinations', cfg, '--decision-id', 'sim', *extra, '--now', '2026-10-10T00:30:00Z', raw=True)
    assert result.returncode == 0, result.stderr
    return out


def test_simulation_does_not_probe_and_stays_a_simulation(one, tmp_path):
    out = simulate(one, one.cfg, '--purpose', 'review')
    assert dest(out) == {'chosen': 'model', 'considered': [{'name': 'worker', 'state': 'not_probed_in_simulation'}]}
    assert not ran(tmp_path, 'cap') and out['launch_ready'] is False and 'simulation' in out and 'destination_offer' not in out
    assert all(value is None for _, value in targets(out)) and out['simulated_target'] is not None


def test_simulation_still_applies_facts_without_a_probe(one, tmp_path):
    out = simulate(one, one.cfg, '--purpose', 'review', '--urgent')
    assert dest(out)['fallback']['reasons'] == ['synchronous'] and not ran(tmp_path, 'cap')


# ---- receipts --------------------------------------------------------------------------------------------------------

def test_destination_receipt_records_replays_and_closes(one, tmp_path):
    flags = ['--purpose', 'review', '--inputs', 'github']
    out = route(one, one.cfg, *flags, record='dec1')
    assert out['target'] is None and out['destination_offer']['request_id'] == 'dec1'
    stored = json.loads(one.receipts.read_text().splitlines()[0])
    assert stored['destination'] == 'worker' and stored['routing']['destination']['chosen'] == 'worker'
    assert stored['request']['skip_destination'] == [] and len(stored['request']['destinations_sha256']) == 64
    # Replay with identical intent returns the stored receipt; it neither probes nor appends.
    (tmp_path / 'cap.ran').unlink()
    again = route(one, one.cfg, *flags, record='dec1')
    assert again['replayed'] is True and again['destination_offer'] == out['destination_offer'] and not ran(tmp_path, 'cap')
    assert len(one.receipts.read_text().splitlines()) == 1
    # A later probe result is a runtime input, not identity.
    one.cap.write_text('#!/bin/sh\necho \'{"available": false, "reason": "later"}\'\n')
    assert route(one, one.cfg, *flags, record='dec1')['replayed'] is True
    close = ['--receipts', one.receipts, '--close-id', 'c1', '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none']
    result, closed = one.cli('close', 'dec1', *close)
    assert result.returncode == 0, result.stderr
    result, follow = one.cli('followup', 'dec1', '--receipts', one.receipts, '--followup-id', 'f1', '--finding', 'no_rework_found', '--checked-scope', 'x')
    assert result.returncode == 0, result.stderr


def test_changed_intent_on_the_same_decision_id_is_rejected(one, tmp_path):
    flags = ['--purpose', 'review', '--inputs', 'github']
    route(one, one.cfg, *flags, record='dec2')
    assert 'changed inputs' in failing(one, one.cfg, *flags, '--skip-destination', 'other', '--reason', 'r', record='dec2')
    assert 'changed inputs' in failing(one, one.cfg, '--purpose', 'review', '--inputs', 'local', record='dec2')
    changed = config(tmp_path, entry(one.cap, name='renamed'), name='other.json')
    assert 'changed inputs' in failing(one, changed, *flags, record='dec2')


def test_fallback_receipts_record_too(one):
    out = route(one, one.cfg, '--purpose', 'review', '--urgent', record='dec3')
    assert out['target'] is not None and out['routing']['destination']['fallback']['source'] == 'facts'
    assert route(one, one.cfg, '--purpose', 'review', '--urgent', record='dec3')['replayed'] is True


# ---- audit -----------------------------------------------------------------------------------------------------------

def audit(world, tmp_path):
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': []}))
    result, out = world.cli('audit', '--export', export, '--receipts', world.receipts)
    assert result.returncode == 0, result.stderr
    return out


def test_audit_counts_destination_routes_and_spend_first_over_a_mixed_ledger(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'up'), purposes=['review', 'research']))
    full = config(tmp_path, entry(capacity(tmp_path, 'full', False, 'cap'), purposes=['review', 'research']), name='full.json')
    gh = ['--inputs', 'github']
    route(world, cfg, '--purpose', 'review', *gh, record='offer1')
    route(world, cfg, '--purpose', 'research', *gh, record='offer2')
    route(world, cfg, '--purpose', 'review', '--inputs', 'local', record='facts1')
    route(world, cfg, '--purpose', 'review', '--urgent', '--sensitive', record='facts2')
    route(world, cfg, '--purpose', 'review', '--skip-destination', 'other', '--reason', 'r', record='parent1')
    route(world, full, '--purpose', 'review', record='cap1')
    route(world, cfg, '--purpose', 'execution', record='unlisted', op='implement.quota-tight')
    world.route('implement.quota-tight', record='legacy')
    report = audit(world, tmp_path)
    assert report['destination_routes'] == {'worker': {'decisions': 2, 'ids': ['offer1', 'offer2']}}
    assert report['spend_first'] == {
        'eligible': 6, 'offered': 2,
        'fallback': {'facts': {'local-inputs': 1, 'synchronous': 1, 'credential': 1}, 'parent': {'other': 1}, 'capacity': {'unavailable': 1}},
        'meaning': 'descriptive; offered = destination chosen; not proof the work was submitted or used'}
    assert report['unmatched_receipts'] == ['facts1', 'facts2', 'parent1', 'cap1', 'unlisted', 'legacy']
    assert 'offer1' not in report['unmatched_receipts']


def test_audit_without_destinations_has_empty_keys(world, tmp_path):
    world.route(OP, record='plain')
    report = audit(world, tmp_path)
    assert report['destination_routes'] == {} and report['spend_first']['eligible'] == 0 and report['unmatched_receipts'] == ['plain']


# ---- determinism and old-CLI compatibility ---------------------------------------------------------------------------

def test_identical_inputs_and_probe_give_identical_output(one):
    flags = ['--purpose', 'review']
    first = route(one, one.cfg, *flags)
    second = route(one, one.cfg, *flags)
    assert stable(first) == stable(second)


def test_old_cli_reads_audits_closes_and_follows_up_a_ledger_with_test_design(one, tmp_path):
    probe = subprocess.run(['git', 'cat-file', '-e', f'{BASELINE}^{{commit}}'], cwd=REPO, capture_output=True)
    if probe.returncode:
        pytest.skip('baseline commit is not in this clone')
    old = tmp_path / 'old'
    old.mkdir()
    archive = subprocess.run(['git', 'archive', BASELINE, 'tools/model-policy-ops', 'data/model-choice-policy'], cwd=REPO, capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', str(old)], input=archive.stdout, check=True)
    tool = old / 'tools/model-policy-ops/model-policy-ops'
    route(one, one.cfg, '--purpose', 'test-design', '--inputs', 'github', record='dest')          # destination receipt
    one.route(OP, '--purpose', 'test-design', record='plain')                                       # ordinary receipt, same purpose
    ledger = ['--receipts', one.receipts]
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': []}))
    for decision in ('dest', 'plain'):
        code, out, err = run_tool(tool, 'close', decision, *ledger, '--close-id', f'c-{decision}', '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none')
        assert code == 0, err
        code, out, err = run_tool(tool, 'followup', decision, *ledger, '--followup-id', f'f-{decision}', '--finding', 'no_rework_found', '--checked-scope', 'x')
        assert code == 0, err
    code, out, err = run_tool(tool, 'audit', '--export', export, *ledger)
    assert code == 0, err
    report = json.loads(out)
    assert sorted(report['unmatched_receipts']) == ['dest', 'plain'] and 'destination_routes' not in report
    # The old CLI cannot name the new purpose, so it cannot replay such a receipt: a documented, accepted break.
    code, out, err = run_tool(tool, 'resolve', OP, '--purpose', 'test-design', *ledger, '--decision-id', 'plain')
    assert code == 2 and 'invalid choice' in err
    # The new CLI reads what the old CLI appended.
    report = audit(one, tmp_path)
    assert report['outcomes']['outcome_rows_for_unknown_decisions'] == 0 and report['destination_routes']['worker']['ids'] == ['dest']


# ---- review round 1 --------------------------------------------------------------------------------------------------

def nonnull_targets(value):
    return [path for path, item in targets(value) if item is not None]


def test_offer_never_reintroduces_a_target_from_submit_or_capacity(world, tmp_path):
    loud = {'providerInstanceId': 'i', 'model': 'm', 'options': {'target': {'model': 'deep'}}}
    cap = script(tmp_path, 'loud.sh', f"echo '{json.dumps({'available': True, 'reason': 'ok', 'target': loud, 'nested': [{'target': loud}]})}'")
    item = entry(cap) | {'submit': SUBMIT | {'target': loud, 'assignment': {'target': loud}}}
    out = route(world, config(tmp_path, item), '--purpose', 'review', '--inputs', 'github', record='loud')
    assert out['destination'] == 'worker' and len(targets(out['destination_offer'])) >= 4
    assert nonnull_targets(out) == []
    stored = json.loads(world.receipts.read_text().splitlines()[0])
    assert nonnull_targets(stored) == [] and stored['destination_offer']['capacity']['reason'] == 'ok'


def probe_fallback(world, tmp_path, body, timeout=5):
    cfg = config(tmp_path, entry(script(tmp_path, 'p.sh', body), timeout=timeout))
    start = time.monotonic()
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github')
    return out, time.monotonic() - start


def test_oversize_output_fails_closed_and_is_never_parsed_as_a_prefix(world, tmp_path):
    body = "printf '{\"available\":true}'; head -c 70000 /dev/zero | tr '\\0' ' '; echo garbage"
    out, _ = probe_fallback(world, tmp_path, body)
    assert 'destination_offer' not in out and out['target'] is not None
    assert dest(out)['fallback']['probe_reason'] == 'capacity_probe_error: output_too_large'


def test_endless_output_is_cut_off_without_unbounded_memory(world, tmp_path):
    out, seconds = probe_fallback(world, tmp_path, 'exec yes', timeout=5)
    assert dest(out)['fallback']['probe_reason'] == 'capacity_probe_error: output_too_large' and seconds < 5


def test_detached_descendant_holding_stdout_cannot_outlast_the_deadline(world, tmp_path):
    out, seconds = probe_fallback(world, tmp_path, 'setsid sleep 20 &\nsleep 20', timeout=0.5)
    assert dest(out)['fallback']['probe_reason'] == 'capacity_probe_error: timeout'
    assert seconds < 8, seconds


def test_probe_stdin_is_closed_not_inherited(world, tmp_path):
    cap = script(tmp_path, 'in.sh', 'if read line; then echo \'{"available": false, "reason": "read"}\'; else echo \'{"available": true, "reason": "eof"}\'; fi')
    cfg = config(tmp_path, entry(cap))
    world.write()
    result = subprocess.run([sys.executable, str(REPO / 'tests/pinned_clock_cli.py'), 'route', OP, *map(str, world.args()), '--destinations', str(cfg),
                             '--decision-id', 'stdin', '--purpose', 'review', '--inputs', 'github', '--json'], input='data\n', capture_output=True, text=True,
                            env=dict(os.environ, PINNED_NOW=NOW))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['destination_offer']['capacity']['reason'] == 'eof'


# ---- judgment and independence ---------------------------------------------------------------------------------------

MAKER = ('--maker-model', 'claude:claude-sonnet-5-5')


def vendor_world(world, tmp_path, vendor):
    item = entry(capacity(tmp_path, 'cap'))
    return config(tmp_path, item | ({'vendor': vendor} if vendor else {}))


def reason_map(out):
    return {r['code']: r for r in out['routing']['judgment_reasons']}


def test_model_pair_reasons_stay_visible_but_do_not_need_judgment_once_a_destination_is_chosen(world, tmp_path):
    cfg = vendor_world(world, tmp_path, 'codex')
    model = world.route(OP, '--purpose', 'review', '--inputs', 'github', *MAKER)
    assert model['routing']['judgment_required'] is True and {'pace_not_on_track', 'independence_caller_claim'} <= set(reason_map(model))
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github', *MAKER)
    reasons = reason_map(out)
    assert out['destination'] == 'worker' and {'pace_not_on_track', 'independence_caller_claim'} <= set(reasons)
    for code in ('pace_not_on_track', 'independence_caller_claim'):
        assert reasons[code]['applies_to'] == 'model_fallback' and reasons[code]['informational'] is True
    assert out['routing']['judgment_required'] is False


def test_destination_level_reasons_still_need_judgment(one):
    out = route(one, one.cfg, '--purpose', 'review')
    reasons = reason_map(out)
    assert reasons['destination_inputs_unknown']['applies_to'] == 'destination' and 'informational' not in reasons['destination_inputs_unknown']
    assert reasons['review_without_maker']['applies_to'] == 'destination' and out['routing']['judgment_required'] is True


def test_a_dispatched_model_keeps_todays_judgment(world, tmp_path):
    cfg = vendor_world(world, tmp_path, 'codex')
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'local', *MAKER)
    assert 'destination' not in out
    assert out['routing']['judgment_required'] is True
    assert all(r['applies_to'] == 'routed' and 'informational' not in r for r in out['routing']['judgment_reasons'])


def test_same_vendor_destination_is_ineligible_for_a_review(world, tmp_path):
    out = route(world, vendor_world(world, tmp_path, 'claude'), '--purpose', 'review', '--inputs', 'github', *MAKER)
    assert 'destination_offer' not in out and out['target'] is not None and not ran(tmp_path, 'cap')
    assert dest(out)['chosen'] == 'model'
    assert dest(out)['fallback'] == {'destination': 'worker', 'reasons': ['independence'], 'source': 'facts'}
    assert dest(out)['considered'] == [{'name': 'worker', 'state': 'declined_by_independence'}]


def test_different_vendor_destination_is_chosen_with_no_independence_reason(world, tmp_path):
    out = route(world, vendor_world(world, tmp_path, 'codex'), '--purpose', 'review', '--inputs', 'github', *MAKER)
    assert out['destination'] == 'worker' and 'destination_independence_unknown' not in reason_map(out)


def test_missing_vendor_or_unknown_maker_vendor_is_chosen_but_needs_judgment(world, tmp_path):
    out = route(world, vendor_world(world, tmp_path, None), '--purpose', 'review', '--inputs', 'github', *MAKER)
    reason = reason_map(out)['destination_independence_unknown']
    assert out['destination'] == 'worker' and reason['applies_to'] == 'destination' and out['routing']['judgment_required'] is True
    out = route(world, vendor_world(world, tmp_path, 'claude'), '--purpose', 'review', '--inputs', 'github')
    assert out['destination'] == 'worker' and 'destination_independence_unknown' in reason_map(out)


def test_independence_flag_applies_to_a_non_review_op(world, tmp_path):
    cfg = vendor_world(world, tmp_path, 'claude')
    out = route(world, cfg, '--purpose', 'research', '--inputs', 'github', *MAKER, '--independence', 'vendor', op='implement.standard')
    assert dest(out).get('fallback', {}).get('reasons') == ['independence']


def test_vendor_must_be_a_non_empty_string(world, tmp_path):
    assert 'vendor' in failing(world, config(tmp_path, entry(tmp_path / 'cap') | {'vendor': ''}), '--purpose', 'review')


# ---- round 2 repairs -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('claude_first', [True, False])
@pytest.mark.parametrize('probe_body', ['echo \'{"available": false, "reason": "busy"}\'', 'echo not json'])
def test_mixed_independence_decline_and_unavailable_probe_falls_back_with_both_reasons(world, tmp_path, claude_first, probe_body):
    same = entry(capacity(tmp_path, 'same'), name='same') | {'vendor': 'claude'}
    other = entry(script(tmp_path, 'other.sh', probe_body), name='other') | {'vendor': 'codex'}
    cfg = config(tmp_path, *((same, other) if claude_first else (other, same)))
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github', *MAKER)
    fallback = dest(out)['fallback']
    assert 'destination_offer' not in out and out['target'] is not None and not ran(tmp_path, 'same')
    assert fallback['destination'] == ('same' if claude_first else 'other') and fallback['source'] == 'capacity'
    assert fallback['reasons'] == ['independence', 'unavailable']
    assert fallback['by_destination'] == {'same': ['independence'], 'other': ['unavailable']}
    assert fallback['probe_reason'] in ('busy', 'capacity_probe_error: invalid_json')
    assert {c['name']: c['state'] for c in dest(out)['considered']} == {'same': 'declined_by_independence', 'other': 'unavailable'}


def test_every_probe_unavailable_keeps_the_unavailable_only_fallback(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a', False, 'one'), name='first') | {'vendor': 'codex'},
                 entry(script(tmp_path, 'b.sh', 'echo not json'), name='second') | {'vendor': 'codex'})
    fallback = dest(route(world, cfg, '--purpose', 'review', '--inputs', 'github', *MAKER))['fallback']
    assert fallback == {'destination': 'first', 'reasons': ['unavailable'], 'source': 'capacity', 'probe_reason': 'one'}


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_background_child_of_an_exited_probe_leader_is_killed(world, tmp_path):
    pidfile = tmp_path / 'child.pid'
    body = f"sleep 30 &\necho $! > '{pidfile}'\nexit 0"
    out, seconds = probe_fallback(world, tmp_path, body, timeout=1)
    assert dest(out)['fallback']['probe_reason'] == 'capacity_probe_error: timeout' and seconds < 8
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid), 'the same-group child must not outlive route'


def test_partly_unknown_maker_still_needs_the_destination_judgment(world, tmp_path):
    two_runs(world, ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'}), ('mystery', 'x-model', {}))
    cfg = vendor_world(world, tmp_path, 'codex')
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github', '--maker', 'mk')
    assert out['destination'] == 'worker'
    assert reason_map(out)['destination_independence_unknown']['applies_to'] == 'destination'
    assert out['routing']['judgment_required'] is True
    out = route(world, vendor_world(world, tmp_path, 'claude'), '--purpose', 'review', '--inputs', 'github', '--maker', 'mk')
    assert dest(out)['fallback']['reasons'] == ['independence'], 'a known run of the same vendor still excludes the destination'


def test_all_known_maker_runs_keep_the_destination_without_a_judgment_reason(world, tmp_path):
    two_runs(world, ('claudeAgent', 'claude-sonnet-5-5', {'effort': 'medium'}), ('codex', 'gpt-6.1-sol', {'reasoningEffort': 'medium'}))
    out = route(world, vendor_world(world, tmp_path, 'other-vendor'), '--purpose', 'review', '--inputs', 'github', '--maker', 'mk')
    assert out['destination'] == 'worker' and 'destination_independence_unknown' not in reason_map(out)
    out = route(world, vendor_world(world, tmp_path, 'codex'), '--purpose', 'review', '--inputs', 'github', '--maker', 'mk')
    assert dest(out)['fallback']['reasons'] == ['independence']


def test_all_unknown_maker_runs_need_the_destination_judgment(world, tmp_path):
    two_runs(world, ('mystery', 'x-model', {}), ('other-mystery', 'y-model', {}))
    out = route(world, vendor_world(world, tmp_path, 'codex'), '--purpose', 'review', '--inputs', 'github', '--maker', 'mk')
    assert out['destination'] == 'worker' and 'destination_independence_unknown' in reason_map(out)
    assert out['routing']['judgment_required'] is True


def test_independence_level_source_is_reported(world):
    assert world.route(OP)['routing']['independence']['required_source'] == 'pack'
    assert world.route(OP, '--independence', 'model')['routing']['independence']['required_source'] == 'flag'


def test_spend_first_daily_classes_are_exclusive_and_reasons_deduplicated():
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import delegation_audit

    def routed(at, block, op='review.audit'):
        return {'recorded_at': at, 'request': {'route': True, 'op': op}, 'routing': {'destination': block}}

    def resolved(at, skip):
        return {'recorded_at': at, 'request': {'route': False, 'op': 'review.audit'}, **({'destination_skip': skip} if skip else {})}

    day = '2026-10-15T01:00:00+00:00'
    rows = [
        routed(day, {'chosen': 'dot', 'considered': [{'name': 'dot', 'state': 'chosen'}]}),
        routed(day, {'chosen': 'model', 'considered': [{'name': 'dot', 'state': 'declined_by_facts'}],
                     'fallback': {'destination': 'dot', 'reasons': ['local-inputs', 'credential', 'local-inputs'], 'source': 'facts'}}),
        routed(day, {'chosen': 'model', 'considered': [], 'not_eligible': [
            {'name': 'dot', 'reason': 'purpose_not_listed', 'purpose': 'execution'},
            {'name': 'pool', 'reason': 'purpose_not_listed', 'purpose': 'execution'}]}),
        routed(day, {'chosen': 'model', 'considered': [], 'not_considered': 'destinations_missing'}),
        routed(day, {'chosen': 'model', 'considered': []}),
        resolved(day, {'reason': 'explicit_resolution', 'destinations': [{'name': 'dot', 'listed_for_purpose': True}]}),
        resolved(day, {'reason': 'explicit_resolution', 'destinations': [{'name': 'dot', 'listed_for_purpose': False}]}),
        resolved(day, {'reason': 'explicit_resolution', 'destinations_state': 'unreadable'}),
        # An offset timestamp is placed by its UTC day: 00:30+02:00 on the 16th is the 15th in UTC.
        resolved('2026-10-16T00:30:00+02:00', None),
        {'request': {'route': True}, 'routing': {}},
    ]
    first, unknown = delegation_audit.spend_first_daily(rows)
    assert first['day'] == '2026-10-15' and first['receipts'] == 9 and sum(first['classes'].values()) == first['receipts']
    assert first['classes'] == {'offered': 1, 'fallback': 1, 'not_eligible': 1, 'resolve_listed': 1, 'resolve_not_listed': 2,
                                'not_considered': 1, 'unrecorded': 2}
    assert first['eligible'] == 2 and first['offered_share_of_eligible'] == 0.5
    assert first['reasons']['fallback'] == {'facts:credential': 1, 'facts:local-inputs': 1}, 'one count per receipt and reason'
    assert first['reasons']['not_eligible'] == {'purpose_not_listed:execution:review.audit': 1}, 'two destinations, one receipt'
    assert first['reasons']['resolve_not_listed'] == {'destinations_unreadable': 1, 'purpose_not_listed': 1}
    assert unknown['day'] == 'unknown' and unknown['classes']['unrecorded'] == 1


def test_later_destinations_are_named_when_an_earlier_one_wins(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'a'), name='first'), entry(capacity(tmp_path, 'b'), name='second'))
    out = route(world, cfg, '--purpose', 'review', '--inputs', 'github')
    assert out['destination'] == 'first' and not ran(tmp_path, 'b')
    assert [(r['name'], r['state']) for r in dest(out)['considered']] == [('first', 'chosen'), ('second', 'not_probed_earlier_chosen')]
    rows = [(c['candidate'], c['reason']['code']) for c in out['choice']['considered'] if c['kind'] == 'destination']
    assert rows == [('first', 'chosen'), ('second', 'not_probed_earlier_chosen')]

def line(world, cfg, *extra, record='line-1'):
    """The default output, without the harness's --json."""
    world.write()
    flags = ['route', OP, *map(str, world.args()), '--destinations', str(cfg), *extra]
    if record:
        flags += ['--decision-id', record, '--record', '--parent-model', 'pm', '--parent-provider-instance', 'pp', '--parent-thread', 'pt']
    result = subprocess.run([sys.executable, str(REPO / 'tests/pinned_clock_cli.py'), *flags], capture_output=True, text=True,
                            env=dict(os.environ, XDG_CONFIG_HOME=str(world.tmp / 'xdg'), PINNED_NOW=NOW))
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('\n') == 1, 'one line'
    return json.loads(result.stdout), len(result.stdout)


def test_default_output_is_one_line_with_the_dispatch_fields(world, tmp_path):
    cfg = config(tmp_path, entry(capacity(tmp_path, 'cap', available=False, reason='busy')))
    out, size = line(world, cfg, '--purpose', 'review', '--inputs', 'github')
    assert set(out) >= {'decision_id', 'target', 'destination', 'launch_ready', 'dispatch', 'judgment', 'reason', 'receipt', 'more'}
    assert out['decision_id'] == 'line-1' and out['target'] and out['launch_ready'] is True
    assert out['dispatch'] == {'clientRequestId': 'line-1', 'mode': 'async'}
    assert 'worker not used: capacity unavailable' in out['reason']
    assert out['receipt'].endswith('.jsonl') and 'calibration_notes' not in out and 'routing' not in out
    full = json.loads(world.receipts.read_text().splitlines()[-1])
    assert out['target'] == full['target'] and size < len(json.dumps(full)) / 3, 'the ledger keeps the full record'


def test_default_output_for_a_hand_submit_destination_keeps_the_offer(world, tmp_path):
    out, _ = line(world, config(tmp_path, entry(capacity(tmp_path, 'cap'))), '--purpose', 'review', '--inputs', 'github')
    assert out['destination'] == 'worker' and out['target'] is None and out['launch_ready'] is False
    assert out['destination_offer']['name'] == 'worker' and 'capacity' not in out['destination_offer']
    assert out['reason'].startswith('destination worker has capacity')


def test_default_output_names_an_ineligible_destination(world, tmp_path):
    out, _ = line(world, config(tmp_path, entry(capacity(tmp_path, 'cap'))), '--purpose', 'execution', record=None)
    assert 'worker not eligible: purpose_not_listed' in out['reason'] and out['receipt'] == 'not_recorded'


def build_entry(tmp_path, **extra):
    return entry(capacity(tmp_path, 'cap'), name='dot', purposes=['review', 'execution']) | {
        'op_scope': {'execution': ['implement.oracle-bounded']}} | extra


def test_op_scope_admits_only_the_listed_op_for_that_purpose(world, tmp_path):
    cfg = config(tmp_path, build_entry(tmp_path))
    out = route(world, cfg, '--purpose', 'execution', '--inputs', 'github', '--proof-class', 'oracle', op='implement.oracle-bounded')
    assert out['destination'] == 'dot' and dest(out)['considered'][0]['state'] == 'chosen'
    other = route(world, cfg, '--purpose', 'execution', '--inputs', 'github', '--proof-class', 'judged', op='implement.standard')
    assert 'destination' not in other and dest(other)['not_eligible'] == [
        {'name': 'dot', 'reason': 'op_not_in_scope', 'purpose': 'execution'}]
    review = route(world, cfg, '--purpose', 'review', '--inputs', 'github')
    assert review['destination'] == 'dot', 'an unscoped purpose keeps every op'


def test_op_scoped_build_still_declines_local_inputs(world, tmp_path):
    out = route(world, config(tmp_path, build_entry(tmp_path)), '--purpose', 'execution', '--inputs', 'local', '--proof-class', 'oracle',
                op='implement.oracle-bounded')
    assert 'destination' not in out and dest(out)['fallback']['reasons'] == ['local-inputs']


def test_resolve_accounts_for_op_scope(world, tmp_path):
    cfg = config(tmp_path, build_entry(tmp_path))
    result, out = world.cli('resolve', 'implement.standard', *world.args(), '--destinations', cfg, '--purpose', 'execution',
                            '--proof-class', 'judged', '--decision-id', 'res-op')
    assert result.returncode == 0, result.stderr
    assert out['destination_skip']['destinations'] == [{'name': 'dot', 'listed_for_purpose': False, 'not_eligible': 'op_not_in_scope'}]
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import delegation_audit
    assert delegation_audit.receipt_class(out) == ('resolve_not_listed', ['op_not_in_scope'])


def test_op_scope_eligibility_edges(tmp_path):
    sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
    import destinations
    row = build_entry(tmp_path)
    assert destinations.eligibility(row, 'execution', None) == 'op_not_in_scope', 'resolve --no-op has no op'
    assert destinations.eligibility(row, None, 'implement.oracle-bounded') == 'purpose_unknown'
    assert destinations.eligibility(row, 'research', 'implement.standard') == 'purpose_not_listed'
    assert destinations.eligibility(row, 'review', None) is None, 'an unscoped purpose keeps every op'


def test_an_out_of_scope_op_is_never_probed_and_scoped_builds_keep_every_gate(world, tmp_path):
    cfg = config(tmp_path, build_entry(tmp_path))
    route(world, cfg, '--purpose', 'execution', '--inputs', 'github', '--proof-class', 'judged', op='implement.standard')
    assert not ran(tmp_path, 'cap')
    for flag, reason in (('--sensitive', 'credential'), ('--urgent', 'synchronous')):
        out = route(world, cfg, '--purpose', 'execution', '--inputs', 'github', '--proof-class', 'oracle', flag, op='implement.oracle-bounded')
        assert 'destination' not in out and reason in dest(out)['fallback']['reasons']


@pytest.mark.parametrize('scope', [{}, {'research': ['implement.oracle-bounded']}, {'execution': []}, {'execution': ['']}, [],
                                   {'execution': 'implement.oracle-bounded'}, {'execution': [3]}, 'execution'])
def test_op_scope_is_validated(world, tmp_path, scope):
    assert 'op_scope must map' in failing(world, config(tmp_path, build_entry(tmp_path, op_scope=scope)), '--purpose', 'review')


# ---- typed destinations (schema_version 2, kind typed-http) -----------------------------------------------------------
# Model names below are fixture data: the router only compares probe evidence with the entry's own evidence_contract.

TYPED_OP = 'review.rule-check'
SHA = 'a' * 64
SAMPLE = 'b' * 64
RULES = ['rule-1', 'rule-2']
PROOF = {'evaluated_model': 'typed-model-1', 'baseline_model': 'baseline-model-1', 'baseline_effort': 'medium',
         'typed_version': 'typed-v1', 'currency': 'USD', 'accepted_rule_ids': RULES}
# The exact capacity JSON a typed adapter prints. It reports an evaluation already made, not the live serving model.
EVIDENCE = {
    'available': True, 'op': TYPED_OP, 'evaluated_model': 'typed-model-1', 'contract_sha256': SHA, 'accepted_rule_ids': RULES,
    'evaluation': {
        'cases': 4, 'positive': 2, 'negative': 2, 'definitive': 4, 'label_disagreements': 0, 'baseline_disagreements': 0,
        'sample_sha256': SAMPLE, 'test_evidence_metadata': {'label_source': 'human-labelled', 'sample_sha256': SAMPLE},
        'versions': {'baseline_model': 'baseline-model-1', 'baseline_effort': 'medium', 'typed_version': 'typed-v1'},
        'evaluated_at': '2026-10-09T00:00:00Z', 'valid_until': '2026-10-20T00:00:00Z',
        'cost': {'basis': 'api_equivalent', 'currency': 'USD', 'scope': 'all_candidates_upper_bound', 'priced_at': '2026-10-09T00:00:00Z',
                 'per_decision': 0.002, 'baseline_per_decision': 0.02}}}


def evidence(**changes):
    value = json.loads(json.dumps(EVIDENCE))
    for path, new in changes.items():
        node = value
        *head, last = path.split('__')
        for key in head:
            node = node[key]
        node[last] = new
    return value


def typed_entry(tmp, payload=None, name='typed', **extra):
    command = script(tmp, name + '.sh', f"touch '{tmp / (name + '.ran')}'\ncat <<'EOF'\n{json.dumps(payload or EVIDENCE)}\nEOF")
    return {'name': name, 'kind': 'typed-http', 'purposes': ['review'], 'op_scope': {'review': [TYPED_OP]},
            'contracts': {TYPED_OP: {'whole_answer': 'typed', 'revision': 'r1', 'sha256': SHA}},
            'capability': {'caller_text': True, 'synchronous': True}, 'evidence_contract': PROOF,
            'capacity_command': [str(command)], 'timeout_seconds': 5, 'how_to': '/abs/typed.md',
            'submit': {'command': 'typed submit --op <op> --id <request_id>'}} | extra


def config2(tmp, *entries):
    path = tmp / 'destinations2.json'
    path.write_text(json.dumps({'schema_version': 2, 'destinations': list(entries)}))
    return path


@pytest.fixture
def typed_world(world):
    # Pin the fixture arm independently of the shipped operation's fallback.
    world.policy['operating_points'] = [o for o in world.policy['operating_points'] if o['id'] != TYPED_OP]
    world.policy['operating_points'].append(json.loads(json.dumps(next(o for o in world.policy['operating_points'] if o['id'] == OP))) | {'id': TYPED_OP})
    return world


def typed_route(world, tmp_path, *extra, payload=None, op=TYPED_OP, **entry_extra):
    cfg = config2(tmp_path, typed_entry(tmp_path, payload, **entry_extra))
    return route(world, cfg, '--purpose', 'review', *extra, op=op)


def test_typed_eligible_offer_is_a_handoff_with_no_target_or_payload(typed_world, tmp_path):
    out = typed_route(typed_world, tmp_path, '--inputs', 'github')
    assert out['destination'] == 'typed' and out['target'] is None and out['launch_ready'] is False and 'dispatch' not in out
    assert all(value is None for _, value in targets(out))
    offer = out['destination_offer']
    assert offer['submit'] == {'command': 'typed submit --op review.rule-check --id probe'}
    assert offer['typed']['contract'] == {'whole_answer': 'typed', 'revision': 'r1', 'sha256': SHA}
    assert offer['capacity']['evaluated_model'] == 'typed-model-1' and 'served_model' not in offer['capacity']
    assert offer['capacity']['evaluation']['cost']['scope'] == 'all_candidates_upper_bound'
    assert 'destination_inputs_unknown' not in [r['code'] for r in out['routing']['judgment_reasons']]


@pytest.mark.parametrize('change,code', [
    ({'evaluated_model': 'other-model'}, 'model_mismatch'),
    ({'contract_sha256': 'c' * 64}, 'contract_mismatch'),
    ({'accepted_rule_ids': ['rule-1']}, 'rule_mismatch'),
    ({'op': 'review.audit'}, 'op_mismatch'),
    ({'evaluation__valid_until': '2026-10-09T12:00:00Z'}, 'evaluation_expired'),
    ({'evaluation__evaluated_at': '2026-10-11T00:00:00Z'}, 'evaluation_expired'),
    ({'evaluation__valid_until': '2026-10-20T00:00:00'}, 'time_invalid'),
    ({'evaluation__positive': 4, 'evaluation__negative': 0}, 'not_balanced_or_definitive'),
    ({'evaluation__cases': 3, 'evaluation__negative': 1}, 'not_balanced_or_definitive'),
    ({'evaluation__definitive': 3}, 'not_balanced_or_definitive'),
    ({'evaluation__label_disagreements': 1}, 'disagreements'),
    ({'evaluation__baseline_disagreements': 1}, 'disagreements'),
    ({'evaluation__sample_sha256': 'zz'}, 'sample_unproven'),
    ({'evaluation__versions': {'baseline_model': 'baseline-model-1', 'baseline_effort': 'high', 'typed_version': 'typed-v1'}}, 'version_mismatch'),
    ({'evaluation__cost': {'basis': 'api_equivalent', 'currency': 'USD', 'scope': 'selected', 'priced_at': '2026-10-09T00:00:00Z', 'per_decision': 1, 'baseline_per_decision': 2}}, 'cost_unproven'),
    ({'evaluation': None}, 'evaluation_missing'),
])
def test_typed_evidence_must_be_current_matching_and_balanced(typed_world, tmp_path, change, code):
    out = typed_route(typed_world, tmp_path, '--inputs', 'github', payload=evidence(**change))
    assert 'destination' not in out and out['target'] is not None, 'the model arm is the fallback'
    row = dest(out)['considered'][0]
    assert row['state'] == 'evaluation_required' and row['capacity']['reason'] == f'evaluation_required: {code}'
    assert dest(out)['fallback']['reasons'] == ['evaluation_required'] and dest(out)['chosen'] == 'model'


def test_typed_unavailable_probe_stays_unavailable_and_missing_evidence_requires_evaluation(typed_world, tmp_path):
    out = typed_route(typed_world, tmp_path, '--inputs', 'github', payload={'available': False, 'reason': 'down'})
    assert dest(out)['considered'][0]['state'] == 'unavailable'
    out = typed_route(typed_world, tmp_path, '--inputs', 'github', payload={'available': True})
    assert dest(out)['considered'][0]['capacity']['reason'] == 'evaluation_required: op_mismatch'


def test_typed_rejects_review_audit_and_unscoped_purposes(typed_world, tmp_path):
    out = typed_route(typed_world, tmp_path, '--inputs', 'github', op=OP)
    assert dest(out)['not_eligible'] == [{'name': 'typed', 'reason': 'op_not_in_scope', 'purpose': 'review'}] and not ran(tmp_path, 'typed')
    out = route(typed_world, config2(tmp_path, typed_entry(tmp_path)), '--purpose', 'research', '--inputs', 'github', op=TYPED_OP)
    assert dest(out)['not_eligible'][0]['reason'] == 'purpose_not_listed' and not ran(tmp_path, 'typed')
    # No automatic routing of implementation work either.
    out = route(typed_world, config2(tmp_path, typed_entry(tmp_path)), '--purpose', 'execution', '--inputs', 'github', '--proof-class', 'judged', op='implement.standard')
    assert dest(out)['not_eligible'][0]['reason'] == 'purpose_not_listed'


@pytest.mark.parametrize('change,message', [
    ({'op_scope': {}}, 'op_scope'),
    ({'purposes': ['review', 'research']}, 'scope every purpose'),
    ({'contracts': {}}, 'contracts must declare'),
    ({'contracts': {TYPED_OP: {'whole_answer': 'partial', 'revision': 'r1', 'sha256': SHA}}}, 'contracts must declare'),
    ({'contracts': {TYPED_OP: {'whole_answer': 'typed', 'revision': 'r1', 'sha256': 'abc'}}}, 'contracts must declare'),
    ({'contracts': {TYPED_OP: {'whole_answer': 'typed', 'revision': 'r1', 'sha256': SHA}, 'review.audit': {'whole_answer': 'typed', 'revision': 'r1', 'sha256': SHA}}}, 'contracts must declare'),
    ({'target': {'providerInstanceId': 'a', 'model': 'b'}, 'brief_token': 'x <root_decision_id>', 'max_chain_failures': 1}, 'is typed'),
    ({'capability': {'caller_text': 'yes', 'synchronous': True}}, 'capability must be'),
    ({'capability': {'caller_text': True}}, 'capability must be'),
    ({'evidence_contract': {**PROOF, 'baseline_effort': ''}}, 'evidence_contract needs'),
    ({'kind': 'other'}, 'kind must be'),
])
def test_typed_entries_with_unsafe_metadata_are_refused(typed_world, tmp_path, change, message):
    cfg = config2(tmp_path, typed_entry(tmp_path) | change)
    assert message in failing(typed_world, cfg, '--purpose', 'review', op=TYPED_OP)


def test_schema_1_refuses_v2_keys_and_old_cli_rejects_schema_2(typed_world, tmp_path):
    bad = config(tmp_path, typed_entry(tmp_path))
    assert 'need schema_version 2' in failing(typed_world, bad, '--purpose', 'review', op=TYPED_OP)
    # Pin the pre-typed CLI; HEAD changes as this patch is committed.
    probe = subprocess.run(['git', 'cat-file', '-e', f'{TYPED_BASELINE}^{{commit}}'], cwd=REPO, capture_output=True)
    if probe.returncode:
        if os.environ.get('MODEL_POLICY_REQUIRE_BASELINE') == '1':
            pytest.fail('pinned pre-typed CLI is unavailable')
        pytest.skip('pinned pre-typed CLI is not in this clone')
    old = tmp_path / 'old'
    old.mkdir()
    archive = subprocess.run(['git', 'archive', TYPED_BASELINE, 'tools/model-policy-ops', 'data/model-choice-policy'], cwd=REPO, capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', str(old)], input=archive.stdout, check=True)
    typed_world.write()
    code, out, err = run_tool(old / 'tools/model-policy-ops/model-policy-ops', 'route', OP, *typed_world.args(), '--destinations', config2(tmp_path, typed_entry(tmp_path)),
                              '--decision-id', 'old')
    assert code != 0 and 'schema_version 1' in err


def test_local_inputs_need_explicit_caller_text_and_the_typed_capability(typed_world, tmp_path):
    out = typed_route(typed_world, tmp_path, '--inputs', 'local')
    assert 'destination' not in out and dest(out)['fallback']['reasons'] == ['local-inputs'] and not ran(tmp_path, 'typed')
    out = typed_route(typed_world, tmp_path, '--inputs', 'local', '--input-mode', 'caller-text')
    assert out['destination'] == 'typed' and out['launch_facts'] == {'schema_version': 1, 'purpose': 'review', 'inputs': 'local', 'input_mode': 'caller-text'}
    assert out['target'] is None and all(value is None for _, value in targets(out))
    out = typed_route(typed_world, tmp_path, '--inputs', 'local', '--input-mode', 'caller-text', capability={'caller_text': False, 'synchronous': True})
    assert 'destination' not in out and dest(out)['fallback']['reasons'] == ['local-inputs']
    for extra in (['--input-mode', 'caller-text'], ['--inputs', 'github', '--input-mode', 'caller-text']):
        assert 'input-mode' in failing(typed_world, config2(tmp_path, typed_entry(tmp_path)), '--purpose', 'review', *extra, op=TYPED_OP)
    result, _ = typed_world.cli('route', TYPED_OP, *typed_world.args(), '--destinations', config2(tmp_path, typed_entry(tmp_path)),
                                '--inputs', 'local', '--input-mode', 'tree')
    assert result.returncode == 2 and 'invalid choice' in result.stderr, 'an unknown mode is refused'
    resolved, _ = typed_world.cli('resolve', TYPED_OP, *typed_world.args(), '--inputs', 'local', '--input-mode', 'caller-text')
    assert resolved.returncode == 1 and 'input-mode' in resolved.stderr


def test_a_plain_destination_never_takes_caller_text_and_synchronous_lifts_only_urgent(typed_world, tmp_path):
    plain = entry(capacity(tmp_path, 'plain'), name='plain')
    cfg = config2(tmp_path, plain, typed_entry(tmp_path))
    out = route(typed_world, cfg, '--purpose', 'review', '--inputs', 'local', '--input-mode', 'caller-text', op=TYPED_OP)
    assert out['destination'] == 'typed' and dest(out)['considered'][0] == {'name': 'plain', 'state': 'declined_by_facts', 'reasons': ['local-inputs']}
    assert not ran(tmp_path, 'plain')
    urgent = route(typed_world, cfg, '--purpose', 'review', '--inputs', 'github', '--urgent', op=TYPED_OP)
    assert urgent['destination'] == 'typed' and dest(urgent)['considered'][0]['reasons'] == ['synchronous']
    both = typed_route(typed_world, tmp_path, '--inputs', 'github', '--urgent', capability={'caller_text': True, 'synchronous': False})
    assert 'destination' not in both and dest(both)['fallback']['reasons'] == ['synchronous']


@pytest.mark.parametrize('flags,key', [(['--sensitive'], 'sensitive'), (['--skip-destination', 'other', '--reason', 'parent says no'], 'skip')])
def test_sensitive_and_parent_skip_never_bypass(typed_world, tmp_path, flags, key):
    out = typed_route(typed_world, tmp_path, '--inputs', 'local', '--input-mode', 'caller-text', '--urgent', *flags)
    assert 'destination' not in out and not ran(tmp_path, 'typed')
    assert dest(out)['fallback']['source'] == ('facts' if key == 'sensitive' else 'parent')
    cfg = config2(tmp_path, typed_entry(tmp_path))
    result, simulated = typed_world.cli('route', TYPED_OP, *typed_world.args(), '--destinations', cfg, '--decision-id', 'sim', '--purpose', 'review',
                                        '--inputs', 'local', '--input-mode', 'caller-text', *flags, '--now', '2026-10-10T00:30:00Z', raw=True)
    assert result.returncode == 0, result.stderr
    assert 'destination' not in simulated and not ran(tmp_path, 'typed')


def test_vendor_independence_is_not_assumed_for_a_typed_worker(typed_world, tmp_path):
    out = typed_route(typed_world, tmp_path, '--inputs', 'github', '--maker-model', 'codex:gpt-6.1-sol')
    assert out['destination'] == 'typed' and 'vendor' not in {k: v for k, v in out['destination_offer'].items() if v}
    assert 'destination_independence_unknown' in [r['code'] for r in out['routing']['judgment_reasons']] and out['routing']['judgment_required'] is True
    same = typed_route(typed_world, tmp_path, '--inputs', 'github', '--maker-model', 'codex:gpt-6.1-sol', vendor='codex')
    assert 'destination' not in same and dest(same)['considered'][0]['state'] == 'declined_by_independence'
    other = typed_route(typed_world, tmp_path, '--inputs', 'github', '--maker-model', 'codex:gpt-6.1-sol', vendor='someone-else')
    assert other['destination'] == 'typed' and 'destination_independence_unknown' not in [r['code'] for r in other['routing']['judgment_reasons']]


def test_no_probe_in_simulation_resolve_or_replay(typed_world, tmp_path):
    cfg = config2(tmp_path, typed_entry(tmp_path))
    result, sim = typed_world.cli('route', TYPED_OP, *typed_world.args(), '--destinations', cfg, '--decision-id', 'sim', '--purpose', 'review',
                                  '--inputs', 'github', '--now', '2026-10-10T00:30:00Z', raw=True)
    assert result.returncode == 0, result.stderr
    assert dest(sim)['considered'][0]['state'] == 'not_probed_in_simulation' and sim['target'] is None and not ran(tmp_path, 'typed')
    result, out = typed_world.cli('resolve', TYPED_OP, *typed_world.args(), '--destinations', cfg, '--purpose', 'review', '--decision-id', 'res')
    assert result.returncode == 0, result.stderr
    assert out['destination_skip']['destinations'] == [{'name': 'typed', 'listed_for_purpose': True}] and not ran(tmp_path, 'typed')
    first = route(typed_world, cfg, '--purpose', 'review', '--inputs', 'github', record='rec', op=TYPED_OP)
    (tmp_path / 'typed.ran').unlink()
    again = route(typed_world, cfg, '--purpose', 'review', '--inputs', 'github', record='rec', op=TYPED_OP)
    assert again['replayed'] is True and again['destination_offer'] == first['destination_offer']
    assert not ran(tmp_path, 'typed')


def test_v1_config_behaves_as_before_beside_the_v2_tests(world, tmp_path):
    cap = capacity(tmp_path, 'cap')
    out = route(world, config(tmp_path, entry(cap)), '--purpose', 'review', '--inputs', 'local')
    assert 'destination' not in out and dest(out)['fallback']['reasons'] == ['local-inputs'] and 'typed' not in json.dumps(out)
    urgent = route(world, config(tmp_path, entry(cap)), '--purpose', 'review', '--inputs', 'github', '--urgent')
    assert dest(urgent)['fallback']['reasons'] == ['synchronous']
