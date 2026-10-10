"""`route` observation notes: two descriptive notes, each emitted only when actionable, never changing the route."""
import json
import sys
import uuid

import pytest

from test_route import NOW, REPO, stable, world  # noqa: F401  (world is a fixture)
from test_route_destinations import OP, capacity, config, entry, script

sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
import observation_notes  # noqa: E402
import observations as store  # noqa: E402
import observe  # noqa: E402

AT = '2026-10-10T00:30:00+00:00'
FLAGS = ('--purpose', 'review', '--inputs', 'github')


@pytest.fixture
def one(world, tmp_path):
    """A world with one available destination `worker`, no observe command, and an empty ledger path."""
    world.cfg = config(tmp_path, entry(capacity(tmp_path, 'cap')))
    world.ledger = world.receipts.with_name('observations.jsonl')
    return world


def with_observe_command(world, tmp_path):
    cmd = script(tmp_path, 'obs.sh', 'cat >/dev/null; echo "{}"')
    world.cfg = config(tmp_path, entry(capacity(tmp_path, 'cap2')) | {'observe_command': [str(cmd)]})


def notes_of(out):
    return out['routing']['calibration_notes'].get('observations')


def route(world, *extra):
    return world.route(OP, '--destinations', world.cfg, *FLAGS, *extra)


def decide(world, decision_id, purpose='review', recorded_at='2026-10-09T00:00:00+00:00', *extra):
    """Record a destination receipt, then pin its recorded_at (the CLI records the real clock)."""
    world.route(OP, '--destinations', world.cfg, '--purpose', purpose, '--inputs', 'github', *extra, record=decision_id)
    lines = [json.loads(line) for line in world.receipts.read_text().splitlines()]
    for line in lines:
        if line['decision_id'] == decision_id:
            line['recorded_at'] = recorded_at
    world.receipts.write_text(''.join(json.dumps(line) + '\n' for line in lines))


def run_row(world, finished='2026-10-10T00:00:00+00:00', states=None, records=None, reasons=None):
    """Append a source_run (and destination facts) through the real ledger writer."""
    states = states or {'t3': 'ok'}
    collected = {name: {'state': state, 'reason': (reasons or {}).get(name), 'subjects_checked': 0, 'observations': []}
                 for name, state in states.items()}
    if records:
        collected['destination:worker'] = {'state': 'ok', 'reason': None, 'subjects_checked': len(records), 'observations': [
            fact for request, record in records.items() for fact in observe.destination_observations('destination:worker', request, record)]}
    store.append_run(world.ledger, str(uuid.uuid4()), finished, finished, collected)


def report(outcome, *events, created='2026-10-09T12:00:00+00:00'):
    return {'found': True, 'status': 'finished', 'task_kind': 'review', 'reports': [{'outcome': outcome, 'created_at': created}],
            'events': list(events)}


def accepted(event_id=1):
    return {'event_id': event_id, 'type': 'accepted', 'at': '2026-10-09T13:00:00+00:00', 'actor_kind': 'owner', 'reason': None}


def settle(world, results, **kw):
    """Record one receipt per decision and one report each."""
    for name in results:
        decide(world, name, **kw)
    run_row(world, records={name: record for name, record in results.items()})


# ---- the evidence note ------------------------------------------------------------------------------------------------

def test_fresh_and_healthy_gives_no_key(one):
    run_row(one)
    out = route(one)
    assert 'observations' not in out['routing']['calibration_notes']


def test_absent_file_without_observe_command_gives_nothing(one):
    assert not one.ledger.exists()
    assert 'observations' not in route(one)['routing']['calibration_notes']


def test_absent_file_with_observe_command_is_missing(one, tmp_path):
    with_observe_command(one, tmp_path)
    assert notes_of(route(one)) == [{'kind': 'observations_missing', 'last_run_at': None, 'age_hours': None, 'sources': {}}]


def test_a_stale_run_is_stale_with_its_age(one):
    run_row(one, finished='2026-10-09T20:30:00+00:00', states={'t3': 'ok', 'git': 'ok'})
    assert notes_of(route(one)) == [{'kind': 'observations_stale', 'last_run_at': '2026-10-09T20:30:00+00:00', 'age_hours': 4.0,
                                     'sources': {'git': 'ok', 't3': 'ok'}}]


def test_the_age_bound_is_a_pack_param(one):
    run_row(one, finished='2026-10-09T20:30:00+00:00')
    one.policy['routing']['observation_max_age_hours'] = 5
    assert 'observations' not in route(one)['routing']['calibration_notes']


@pytest.mark.parametrize('state', ['error', 'partial'])
def test_a_source_not_ok_is_a_note(one, state):
    run_row(one, states={'t3': 'ok', 'git': state})
    assert notes_of(route(one)) == [{'kind': 'observation_source_not_ok', 'last_run_at': '2026-10-10T00:00:00+00:00', 'age_hours': 0.5,
                                     'sources': {'git': state, 't3': 'ok'}}]


def test_a_destination_without_an_observe_command_is_not_a_defect(one):
    run_row(one, states={'t3': 'ok', 'destination:worker': 'unavailable'}, reasons={'destination:worker': 'no_observe_command'})
    assert 'observations' not in route(one)['routing']['calibration_notes']


def test_an_old_run_with_only_excluded_sources_is_not_stale(one):
    run_row(one, finished='2026-10-09T18:00:00+00:00', states={'destination:worker': 'unavailable'},
            reasons={'destination:worker': 'no_observe_command'})
    assert 'observations' not in route(one)['routing']['calibration_notes']


def test_the_latest_run_is_chosen_by_finish_time_not_ledger_order(one):
    run_row(one)                                                                      # fresh and healthy, written first
    run_row(one, finished='2026-10-09T18:00:00+00:00', states={'t3': 'error'})          # older and failing, written last
    assert 'observations' not in route(one)['routing']['calibration_notes']

def test_only_the_latest_run_counts(one):
    run_row(one, finished='2026-10-09T23:00:00+00:00', states={'t3': 'error'})
    run_row(one)
    assert 'observations' not in route(one)['routing']['calibration_notes']


@pytest.mark.parametrize('body', ['{not json\n', '{"schema_version": 1, "kind": "source_run"}\n', '[]\n'])
def test_a_malformed_ledger_reads_as_missing_never_a_crash(one, body):
    one.ledger.parent.mkdir(exist_ok=True)
    one.ledger.write_text(body)
    assert notes_of(route(one)) == [{'kind': 'observations_missing', 'last_run_at': None, 'age_hours': None, 'sources': {}}]


# ---- the blocked share ----------------------------------------------------------------------------------------------

def blocked_note(out):
    found = [n for n in notes_of(out) or [] if n['kind'] == 'destination_blocked_share']
    return found[0] if found else None


def test_two_results_with_a_blocked_one_gives_nothing(one):
    settle(one, {'a': report('blocked'), 'b': report('completed')})
    assert blocked_note(route(one)) is None


def test_three_results_none_blocked_gives_nothing(one):
    settle(one, {n: report('completed') for n in 'abc'})
    assert blocked_note(route(one)) is None


def test_three_results_one_blocked_is_a_note(one):
    settle(one, {'a': report('blocked'), 'b': report('completed', accepted()), 'c': report('completed')})
    assert blocked_note(route(one)) == {
        'kind': 'destination_blocked_share', 'destination': 'worker', 'task_kind': 'review', 'window_days': 14,
        'decisions_with_result': 3, 'blocked': 1, 'completed': 2, 'accepted_by_owner': 1,
        'labels': 'completed = worker filed a report; accepted = owner disposition; neither is correctness'}


def test_many_report_events_for_one_decision_count_once_with_the_latest(one):
    for name in 'abc':
        decide(one, name)
    two = {'found': True, 'status': 'finished', 'task_kind': 'review', 'events': [],
           'reports': [{'outcome': 'blocked', 'created_at': '2026-10-09T10:00:00+00:00'},
                       {'outcome': 'completed', 'created_at': '2026-10-09T11:00:00+00:00'},
                       {'outcome': 'blocked', 'created_at': '2026-10-09T09:00:00+00:00'}]}
    run_row(one, records={'a': two, 'b': report('completed'), 'c': report('completed')})
    # a's latest report is completed: nothing is blocked.
    assert blocked_note(route(one)) is None
    later = dict(two, reports=two['reports'] + [{'outcome': 'blocked', 'created_at': '2026-10-09T12:30:00+00:00'}])
    run_row(one, finished='2026-10-10T00:10:00+00:00', records={'a': later})
    note = blocked_note(route(one))
    assert note['decisions_with_result'] == 3 and note['blocked'] == 1 and note['completed'] == 2


def test_a_relaunch_chain_counts_each_decision_once(one):
    decide(one, 'a')
    decide(one, 'b', 'review', '2026-10-09T01:00:00+00:00', '--relaunch-of', 'a', '--why', 'infra', '--reason', 'retry')
    decide(one, 'c')
    run_row(one, records={'a': report('blocked'), 'b': report('blocked'), 'c': report('completed')})
    note = blocked_note(route(one))
    assert note['decisions_with_result'] == 3 and note['blocked'] == 2 and note['completed'] == 1


def test_the_window_boundary_is_inclusive(one):
    # AT is 2026-10-10T00:30; 14 days before it is 2026-09-26T00:30.
    decide(one, 'edge', recorded_at='2026-09-26T00:30:00+00:00')
    decide(one, 'out', recorded_at='2026-09-26T00:29:59+00:00')
    decide(one, 'in1')
    decide(one, 'in2')
    run_row(one, records={'edge': report('blocked'), 'out': report('blocked'), 'in1': report('completed'), 'in2': report('completed')})
    note = blocked_note(route(one))
    assert note['decisions_with_result'] == 3 and note['blocked'] == 1


def test_the_window_is_a_pack_param(one):
    decide(one, 'old', recorded_at='2026-09-26T00:29:59+00:00')
    decide(one, 'a')
    decide(one, 'b')
    run_row(one, records={'old': report('blocked'), 'a': report('completed'), 'b': report('completed')})
    assert blocked_note(route(one)) is None
    one.policy['routing']['observation_window_days'] = 30
    assert blocked_note(route(one))['window_days'] == 30


def test_another_purpose_is_excluded(one):
    decide(one, 'x', purpose='research')
    decide(one, 'a')
    decide(one, 'b')
    run_row(one, records={'x': report('blocked'), 'a': report('completed'), 'b': report('completed')})
    assert blocked_note(route(one)) is None


def test_no_destination_chosen_gives_no_blocked_share(one):
    settle(one, {'a': report('blocked'), 'b': report('completed'), 'c': report('completed')})
    out = one.route(OP, '--destinations', one.cfg, '--purpose', 'review', '--inputs', 'local')
    assert out['routing']['destination']['chosen'] == 'model'
    assert 'observations' not in out['routing']['calibration_notes']


# ---- descriptive only -------------------------------------------------------------------------------------------------

def strip_notes(out):
    out = json.loads(json.dumps(stable(out)))
    out['routing']['calibration_notes'].pop('observations', None)
    return out


def test_notes_change_neither_ranking_target_judgment_nor_request(one, tmp_path):
    run_row(one, finished='2026-10-09T20:00:00+00:00', states={'t3': 'error'})
    for name in 'abc':
        decide(one, name)
    run_row(one, finished='2026-10-09T20:10:00+00:00', records={'a': report('blocked'), 'b': report('completed'), 'c': report('completed')})
    noted = route(one)
    assert {n['kind'] for n in notes_of(noted)} == {'observations_stale', 'destination_blocked_share'}
    one.ledger.rename(tmp_path / 'moved.jsonl')
    plain = route(one)
    assert 'observations' not in plain['routing']['calibration_notes']
    assert strip_notes(noted) == strip_notes(plain)
    for key in ('ranking', 'candidates', 'chosen', 'judgment_required', 'judgment_reasons'):
        assert json.dumps(noted['routing'].get(key), sort_keys=True) == json.dumps(plain['routing'].get(key), sort_keys=True)
    assert noted['request'] == plain['request'] and noted['target'] == plain['target']


def test_the_note_is_in_the_stored_routing_block_and_replay_still_works(one):
    run_row(one, finished='2026-10-09T20:00:00+00:00')
    first = one.route(OP, '--destinations', one.cfg, *FLAGS, record='r1')
    stored = json.loads(one.receipts.read_text().splitlines()[-1])
    assert stored['routing']['calibration_notes']['observations'][0]['kind'] == 'observations_stale'
    again = one.route(OP, '--destinations', one.cfg, *FLAGS, record='r1')
    assert again['replayed'] and again['request'] == first['request']


def test_the_defaults_are_declared_in_the_pack():
    pack = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())['routing']
    assert (pack['observation_max_age_hours'], pack['observation_window_days']) == (
        observation_notes.MAX_AGE_HOURS, observation_notes.WINDOW_DAYS)


@pytest.mark.parametrize('key', ['observation_max_age_hours', 'observation_window_days'])
def test_check_rejects_a_bad_observation_param(world, key):
    world.policy['routing'][key] = 0
    world.write()
    result, _ = world.cli('check', '--policy', world.tmp / 'policy.json', raw=True)
    assert result.returncode != 0 and key in (result.stdout + result.stderr)
