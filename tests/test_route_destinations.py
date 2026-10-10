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

def test_unlisted_purpose_and_unknown_purpose_stay_on_the_model(one, tmp_path):
    for flags in (['--purpose', 'execution'], []):
        out = route(one, one.cfg, *flags)
        assert dest(out) == {'chosen': 'model', 'considered': []} and out['launch_ready'] is True and 'destination' not in out
    assert not ran(tmp_path, 'cap')


def test_missing_config_changes_nothing(world, tmp_path):
    plain = world.route(OP, '--purpose', 'review')
    missing = route(world, tmp_path / 'absent.json', '--purpose', 'review')
    assert stable(missing) == stable(plain) and 'destination' not in plain['routing'] and 'skip_destination' not in plain['request']


@pytest.mark.parametrize('text,message', [
    ('{', 'not valid JSON'), ('[]', 'schema_version 1'), ('{"schema_version": 2, "destinations": []}', 'schema_version 1'),
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
                             '--decision-id', 'stdin', '--purpose', 'review', '--inputs', 'github'], input='data\n', capture_output=True, text=True,
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
