"""Destinations: external workers that `route` offers before it picks a model. A destination is an adapter, not a chooser."""
import json
import os
import signal
import subprocess
from pathlib import Path

from outcomes import PURPOSES
from runtime_sources import digest

SKIP_REASONS = ('local-inputs', 'synchronous', 'credential', 'unavailable', 'other')
MAX_PROBE_BYTES = 64 * 1024
MAX_TIMEOUT = 60


def default_destinations_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'model-policy/destinations.json'


def read_destinations(path):
    """Ordered destination list. A missing file means none; a malformed file is an error."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [], {'state': 'missing', 'sha256': None}
    except ValueError as error:
        raise ValueError('destinations file is not valid JSON') from error
    rows = value.get('destinations') if isinstance(value, dict) else None
    if not isinstance(rows, list) or value.get('schema_version') != 1:
        raise ValueError('destinations file needs schema_version 1 and a "destinations" list')
    seen = set()
    for number, entry in enumerate(rows):
        where = f'destinations[{number}]'
        if not isinstance(entry, dict):
            raise ValueError(f'{where} must be an object')
        name = entry.get('name')
        if not isinstance(name, str) or not name.strip() or name == 'model' or name in seen:
            raise ValueError(f'{where}.name must be a unique non-empty string other than "model"')
        seen.add(name)
        purposes = entry.get('purposes')
        if not isinstance(purposes, list) or not purposes or any(p not in PURPOSES for p in purposes):
            raise ValueError(f'{where}.purposes must be a non-empty list drawn from {", ".join(PURPOSES)}')
        command = entry.get('capacity_command')
        if (not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command)
                or not os.path.isabs(command[0])):
            raise ValueError(f'{where}.capacity_command must be a list of strings that starts with an absolute path')
        timeout = entry.get('timeout_seconds')
        if type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError(f'{where}.timeout_seconds must be a number in (0, {MAX_TIMEOUT}]')
        if not isinstance(entry.get('how_to'), str) or not entry['how_to']:
            raise ValueError(f'{where}.how_to must be a non-empty string')
        if not isinstance(entry.get('submit'), dict):
            raise ValueError(f'{where}.submit must be an object')
    return rows, {'state': 'loaded', 'sha256': digest(rows)}


def probe_error(kind):
    return {'available': False, 'reason': f'capacity_probe_error: {kind}'}


def probe(entry):
    """Run the capacity command: no shell, stdin closed, timeout enforced. Every failure maps to available false."""
    try:
        process = subprocess.Popen(entry['capacity_command'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return probe_error('missing_binary')
    except OSError:
        return probe_error('os_error')
    try:
        stdout, _ = process.communicate(timeout=entry['timeout_seconds'])
    except subprocess.TimeoutExpired:
        # The whole session is killed, so a grandchild cannot keep the pipe open.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.communicate()
        return probe_error('timeout')
    if process.returncode != 0:
        return probe_error('nonzero_exit')
    try:
        value = json.loads(stdout[:MAX_PROBE_BYTES].decode('utf-8'))
    except ValueError:
        return probe_error('invalid_json')
    if not isinstance(value, dict) or type(value.get('available')) is not bool:
        return probe_error('invalid_shape')
    return value


def substitute(value, mapping):
    if isinstance(value, str):
        for token, text in mapping.items():
            value = value.replace(token, text)
        return value
    if isinstance(value, dict):
        return {k: substitute(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, mapping) for v in value]
    return value


def fact_reasons(facts):
    """Skip reasons that the recorded launch facts imply."""
    facts = facts or {}
    return [reason for reason, applies in (('local-inputs', facts.get('inputs') == 'local'),
                                           ('synchronous', facts.get('urgent') is True),
                                           ('credential', facts.get('sensitive') is True)) if applies]


def decide(entries, *, purpose, facts, skip, reason, request_id, simulate, run_probe=probe):
    """Return (`routing.destination` block, offer or None). Facts first, then the parent's skip, then capacity."""
    listed = [e for e in entries if purpose in e['purposes']]
    block = {'chosen': 'model', 'considered': []}
    if not listed:
        return block, None
    first = listed[0]['name']

    def declined(state):
        return [{'name': e['name'], 'state': state} for e in listed]

    implied = fact_reasons(facts)
    if implied:
        return block | {'considered': declined('declined_by_facts'),
                        'fallback': {'destination': first, 'reasons': implied, 'source': 'facts'}}, None
    if skip:
        return block | {'considered': declined('declined_by_parent'),
                        'fallback': {'destination': first, 'reasons': list(skip), 'source': 'parent', 'reason': reason}}, None
    if simulate:
        return block | {'considered': declined('not_probed_in_simulation')}, None
    considered = []
    for entry in listed:
        capacity = run_probe(entry)
        if capacity['available']:
            considered.append({'name': entry['name'], 'state': 'chosen', 'capacity': capacity})
            offer = {'name': entry['name'], 'request_id': request_id, 'purpose': purpose, 'how_to': entry['how_to'],
                     'submit': substitute(entry['submit'], {'<request_id>': request_id, '<purpose>': purpose}), 'capacity': capacity}
            return block | {'chosen': entry['name'], 'considered': considered}, offer
        considered.append({'name': entry['name'], 'state': 'unavailable', 'capacity': capacity})
    return block | {'considered': considered,
                    'fallback': {'destination': first, 'reasons': ['unavailable'], 'source': 'capacity',
                                 'probe_reason': considered[0]['capacity'].get('reason')}}, None


INPUTS_UNKNOWN = {'code': 'destination_inputs_unknown', 'applies_to': 'routed',
                  'detail': 'the destination needs one pushed GitHub input; if inputs are local, re-run with --inputs local'}
