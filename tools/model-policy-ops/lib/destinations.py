"""Destinations: external workers that `route` offers before it picks a model. A destination is an adapter, not a chooser."""
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from outcomes import PURPOSES
from runtime_sources import digest

SKIP_REASONS = ('local-inputs', 'synchronous', 'credential', 'unavailable', 'other')
MAX_PROBE_BYTES = 64 * 1024
CHUNK = 8192
KILL_GRACE = 1.0
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
        if 'vendor' in entry and (not isinstance(entry['vendor'], str) or not entry['vendor'].strip()):
            raise ValueError(f'{where}.vendor must be a non-empty string when present')
        if not isinstance(entry.get('submit'), dict):
            raise ValueError(f'{where}.submit must be an object')
    return rows, {'state': 'loaded', 'sha256': digest(rows)}


def probe_error(kind):
    return {'available': False, 'reason': f'capacity_probe_error: {kind}'}


def stop(process):
    """Kill the probe's session, close the read pipe and reap with a bounded wait. A detached descendant may live on; it cannot block us."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
    process.stdout.close()
    try:
        process.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        pass


def read_limited(process, deadline):
    """(bytes, None), or (None, 'timeout' | 'output_too_large'). Streams in chunks, so memory stays below the limit plus one chunk."""
    chunks, size = [], 0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None, 'timeout'
            if not selector.select(left):
                continue
            chunk = os.read(process.stdout.fileno(), CHUNK)
            if not chunk:
                return b''.join(chunks), None
            size += len(chunk)
            if size > MAX_PROBE_BYTES:
                return None, 'output_too_large'
            chunks.append(chunk)


def probe(entry):
    """Run the capacity command: no shell, stdin closed, output and time bounded. Every failure maps to available false.

    The total time is the timeout plus `KILL_GRACE`. Output past `MAX_PROBE_BYTES` fails closed; a truncated prefix is never parsed.
    """
    try:
        process = subprocess.Popen(entry['capacity_command'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
    except FileNotFoundError:
        return probe_error('missing_binary')
    except OSError:
        return probe_error('os_error')
    deadline = time.monotonic() + entry['timeout_seconds']
    try:
        stdout, failure = read_limited(process, deadline)
        if failure is None:
            try:
                process.wait(timeout=max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                failure = 'timeout'
    finally:
        stop(process)
    if failure:
        return probe_error(failure)
    if process.returncode != 0:
        return probe_error('nonzero_exit')
    try:
        value = json.loads(stdout.decode('utf-8'))
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


def same_vendor(entry, independence):
    """True when the destination provably shares the maker's vendor and the required level is `vendor`."""
    return bool(independence) and independence['level'] == 'vendor' and entry.get('vendor') in independence['maker_vendors']


def independence_unknown(entry, independence):
    """True when a required independence cannot be checked: no destination vendor, no known maker vendor, or a level finer than vendor."""
    return bool(independence) and (not entry.get('vendor') or not independence['maker_vendors'] or independence['level'] != 'vendor')


def decide(entries, *, purpose, facts, skip, reason, request_id, simulate, independence=None, run_probe=probe):
    """Return (`routing.destination` block, offer or None). Facts first, then the parent's skip, then independence, then capacity.

    `independence` is None when none is required, else {level, maker_vendors}.
    """
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
    considered = [{'name': e['name'], 'state': 'declined_by_independence'} for e in listed if same_vendor(e, independence)]
    listed = [e for e in listed if not same_vendor(e, independence)]
    if not listed:
        return block | {'considered': considered,
                        'fallback': {'destination': first, 'reasons': ['independence'], 'source': 'facts'}}, None
    for entry in listed:
        capacity = run_probe(entry)
        if capacity['available']:
            considered.append({'name': entry['name'], 'state': 'chosen', 'capacity': capacity})
            offer = {'name': entry['name'], 'request_id': request_id, 'purpose': purpose, 'how_to': entry['how_to'],
                     'submit': substitute(entry['submit'], {'<request_id>': request_id, '<purpose>': purpose}), 'capacity': capacity,
                     'vendor': entry.get('vendor')}
            return block | {'chosen': entry['name'], 'considered': considered}, offer
        considered.append({'name': entry['name'], 'state': 'unavailable', 'capacity': capacity})
    return block | {'considered': considered,
                    'fallback': {'destination': first, 'reasons': ['unavailable'], 'source': 'capacity',
                                 'probe_reason': considered[0]['capacity'].get('reason')}}, None


INPUTS_UNKNOWN = {'code': 'destination_inputs_unknown', 'applies_to': 'destination',
                  'detail': 'the destination needs one pushed GitHub input; if inputs are local, re-run with --inputs local'}
INDEPENDENCE_UNKNOWN = {'code': 'destination_independence_unknown', 'applies_to': 'destination',
                        'detail': 'the destination vendor or the maker vendor is unknown, or the level is finer than vendor; independence cannot be checked'}
