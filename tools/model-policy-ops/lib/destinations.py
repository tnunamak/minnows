"""Destinations: external workers that `route` offers before it picks a model. A destination is an adapter, not a chooser."""
import json
import math
import os
import re
import selectors
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from outcomes import PURPOSES
from runtime_sources import digest, provider_instance, providers, runnable

# The one name contract: config, receipts and the observation ledger all use it.
NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,31}')
NAME_TEXT = '^[a-z0-9][a-z0-9_-]{0,31}$'
SKIP_REASONS = ('local-inputs', 'synchronous', 'credential', 'unavailable', 'other')
MAX_PROBE_BYTES = 64 * 1024
MAX_STDIN_BYTES = 32 * 1024
CHUNK = 8192
KILL_GRACE = 1.0
MAX_TIMEOUT = 60
HEX64 = re.compile(r'[0-9a-f]{64}')
TYPED = 'typed-http'
# Keys that only mean something in a schema 2 file; a schema 1 file refuses them rather than half-honoring them.
V2_KEYS = ('kind', 'contracts', 'capability', 'evidence_contract')
INPUT_MODES = ('caller-text',)
# Typed evidence is projected field by field. Free text from a probe (reasons, labels, extras) is never stored.
LABEL_SOURCES = ('human-labelled', 'oracle-labelled', 'model-reviewed-synthetic')
PROBE_ERROR_KINDS = ('missing_binary', 'os_error', 'timeout', 'output_too_large', 'nonzero_exit', 'invalid_json', 'invalid_shape')
UNAVAILABLE_REASONS = tuple(f'capacity_probe_error: {kind}' for kind in PROBE_ERROR_KINDS) + ('adapter_reported_unavailable',)
COST_SCOPE = 'all_candidates_sample_mean'
# The sample mean is not a bound on the selected rule set's cost, so those per-decision costs are reported as unknown (null).
UNKNOWN_COSTS = ('selected_rule_set_per_decision', 'baseline_selected_rule_set_per_decision')


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
    version = value.get('schema_version') if isinstance(value, dict) else None
    if not isinstance(rows, list) or version not in (1, 2):
        raise ValueError('destinations file needs schema_version 1 or 2 and a "destinations" list')
    seen = set()
    for number, entry in enumerate(rows):
        where = f'destinations[{number}]'
        if not isinstance(entry, dict):
            raise ValueError(f'{where} must be an object')
        name = entry.get('name')
        if not isinstance(name, str) or not NAME.fullmatch(name) or name == 'model' or name in seen:
            raise ValueError(f'{where}.name must be a unique name matching {NAME_TEXT} other than "model"')
        seen.add(name)
        purposes = entry.get('purposes')
        if not isinstance(purposes, list) or not purposes or any(p not in PURPOSES for p in purposes):
            raise ValueError(f'{where}.purposes must be a non-empty list drawn from {", ".join(PURPOSES)}')
        command_argv(entry | {'capacity_command': entry.get('capacity_command')}, 'capacity_command', where)
        if 'observe_command' in entry:
            command_argv(entry, 'observe_command', where)
        timeout = entry.get('timeout_seconds')
        if type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT:
            raise ValueError(f'{where}.timeout_seconds must be a number in (0, {MAX_TIMEOUT}]')
        if not isinstance(entry.get('how_to'), str) or not entry['how_to']:
            raise ValueError(f'{where}.how_to must be a non-empty string')
        if 'vendor' in entry and (not isinstance(entry['vendor'], str) or not entry['vendor'].strip()):
            raise ValueError(f'{where}.vendor must be a non-empty string when present')
        if not isinstance(entry.get('submit'), dict):
            raise ValueError(f'{where}.submit must be an object')
        if version == 1 and any(k in entry for k in V2_KEYS):
            raise ValueError(f'{where} uses {", ".join(k for k in V2_KEYS if k in entry)}, which need schema_version 2')
        validate_target(entry, where)
        validate_op_scope(entry, where)
        validate_typed(entry, where)
    # observe_command belongs to `observe`; adding it must not change the identity that a route request records.
    return rows, {'state': 'loaded', 'sha256': digest([{k: v for k, v in e.items() if k != 'observe_command'} for e in rows])}


def validate_op_scope(entry, where):
    """Optional `op_scope`: {PURPOSE: [OP, ...]}. A listed purpose is eligible only for those ops; other purposes are unscoped."""
    if 'op_scope' not in entry:
        return
    scope = entry['op_scope']
    if (not isinstance(scope, dict) or not scope
            or any(purpose not in entry['purposes'] or not isinstance(ops, list) or not ops
                   or not all(isinstance(op, str) and op for op in ops) for purpose, ops in scope.items())):
        raise ValueError(f'{where}.op_scope must map listed purposes to non-empty lists of op IDs')


def is_typed(entry):
    return entry.get('kind') == TYPED


def validate_typed(entry, where):
    """Schema 2 `kind: typed-http`: a whole-answer typed judgment worker. It offers a handoff, never a model target.

    `op_scope` must cover every listed purpose, and its ops are exactly the ops `contracts` declares `whole_answer: typed`
    ({op: {whole_answer, revision, sha256}}). `capability` is {caller_text, synchronous} booleans. `evidence_contract` is what
    a probe's labelled evaluation must equal: the evaluated model, the baseline model and effort, the typed version, accepted
    rule IDs and the cost currency. No model name is known to the router; all of them come from here."""
    if 'kind' in entry and entry['kind'] != TYPED:
        raise ValueError(f'{where}.kind must be "{TYPED}" when present')
    if not is_typed(entry):
        if any(k in entry for k in V2_KEYS[1:]):
            raise ValueError(f'{where} declares contracts, capability or evidence_contract without kind "{TYPED}"')
        return
    if any(k in entry for k in ('target', 'brief_token', 'max_chain_failures', 'request_id_pattern')):
        raise ValueError(f'{where} is typed: no target, brief_token, max_chain_failures or request_id_pattern')
    scope = entry.get('op_scope')
    if not isinstance(scope, dict) or set(scope) != set(entry['purposes']):
        raise ValueError(f'{where}.op_scope must scope every purpose of a typed destination')
    contracts = entry.get('contracts')
    scoped_ops = {op for ops in scope.values() for op in ops}
    if (not isinstance(contracts, dict) or set(contracts) != scoped_ops
            or not all(isinstance(c, dict) and set(c) == {'whole_answer', 'revision', 'sha256'} and c['whole_answer'] == 'typed'
                       and isinstance(c['revision'], str) and c['revision'] and isinstance(c['sha256'], str)
                       and HEX64.fullmatch(c['sha256']) for c in contracts.values())):
        raise ValueError(f'{where}.contracts must declare each scoped op as {{whole_answer: "typed", revision, sha256: 64 hex}} and nothing else')
    capability = entry.get('capability')
    if (not isinstance(capability, dict) or set(capability) != {'caller_text', 'synchronous'}
            or not all(type(v) is bool for v in capability.values())):
        raise ValueError(f'{where}.capability must be {{caller_text, synchronous}} booleans')
    proof = entry.get('evidence_contract')
    texts = ('evaluated_model', 'baseline_model', 'baseline_effort', 'typed_version', 'currency')
    if (not isinstance(proof, dict) or set(proof) != set(texts) | {'accepted_rule_ids'}
            or not all(isinstance(proof[k], str) and proof[k].strip() for k in texts)
            or not isinstance(proof['accepted_rule_ids'], list) or not proof['accepted_rule_ids']
            or not all(isinstance(r, str) and r for r in proof['accepted_rule_ids'])):
        raise ValueError(f'{where}.evidence_contract needs non-empty {", ".join(texts)} and accepted_rule_ids')


def eligibility(entry, purpose, op):
    """None when the entry may take this purpose and op, else the reason it may not."""
    if purpose is None:
        return 'purpose_unknown'
    if purpose not in entry['purposes']:
        return 'purpose_not_listed'
    scoped = (entry.get('op_scope') or {}).get(purpose)
    if scoped is not None and op not in scoped:
        return 'op_not_in_scope'
    return None


ROOT = '<root_decision_id>'
TARGET_KEYS = ('providerInstanceId', 'model', 'driverKind')


def validate_target(entry, where):
    """Optional dispatch target. With `target`, a chosen destination returns a normal T3 target instead of a hand-submit
    offer only. `brief_token` (holding `<root_decision_id>` once) is the brief's required first line, and
    `max_chain_failures` is how many routes to this destination one relaunch chain may hold before it is declined.
    `request_id_pattern` (optional) is the root decision IDs the destination accepts as its request ID."""
    keys = ('target', 'brief_token', 'max_chain_failures', 'request_id_pattern')
    if not any(k in entry for k in keys):
        return
    target = entry.get('target')
    if (not isinstance(target, dict) or set(target) - set(TARGET_KEYS)
            or not all(isinstance(target.get(k), str) and target[k] for k in ('providerInstanceId', 'model'))
            or ('driverKind' in target and not (isinstance(target['driverKind'], str) and target['driverKind']))):
        raise ValueError(f'{where}.target must be {{providerInstanceId, model, driverKind?}} with non-empty strings and no options')
    token = entry.get('brief_token')
    if not isinstance(token, str) or token.count(ROOT) != 1 or '\n' in token or len(token) > 200:
        raise ValueError(f'{where}.brief_token must be one line holding {ROOT} exactly once')
    limit = entry.get('max_chain_failures')
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError(f'{where}.max_chain_failures must be an integer from 1 to 10')
    if 'request_id_pattern' in entry:
        try:
            re.compile(entry['request_id_pattern'])
        except (TypeError, re.error):
            raise ValueError(f'{where}.request_id_pattern must be a regular expression') from None


def not_considered(state):
    """`routing.destination` when no destination config was read: the state says why none was considered."""
    return {'chosen': 'model', 'considered': [], 'not_considered': f'destinations_{state}'}


def resolve_skip(entries, meta, purpose, op=None):
    """Why a `resolve` receipt names no destination: resolve is an explicit choice and never consults one."""
    if meta['state'] != 'loaded':
        return {'reason': 'explicit_resolution', 'destinations_state': meta['state']}
    return {'reason': 'explicit_resolution',
            'destinations': [{'name': e['name'], 'listed_for_purpose': eligibility(e, purpose, op) is None}
                             | ({'not_eligible': eligibility(e, purpose, op)} if eligibility(e, purpose, op) else {}) for e in entries]}


def catalog_target(available, target):
    """True when the live catalog lists the target's instance, driver (when given) and model, and both can run a child task now (the rule model arms use)."""
    for row in providers(available):
        if provider_instance(row) != target['providerInstanceId']:
            continue
        driver = row.get('driverKind', row.get('driver'))
        if 'driverKind' in target and driver != target['driverKind']:
            return False
        if not runnable(row):
            return False
        return any(m.get('id', m.get('slug')) == target['model'] and runnable(m) for m in row.get('models', []) if isinstance(m, dict))
    return False


def chain(rows, relaunch_of, name):
    """(root decision ID, routes to `name` in the relaunch chain that ends at `relaunch_of`). Each one is a failed attempt:
    the caller relaunches only after the previous child did not deliver."""
    by_id = {r.get('decision_id'): r for r in rows}
    root, failures, seen, current = None, 0, set(), relaunch_of
    while current and current not in seen and current in by_id:
        seen.add(current)
        row = by_id[current]
        failures += row.get('destination') == name
        root = current
        current = (row.get('relaunch') or {}).get('from')
    return root, failures


def command_argv(entry, key, where):
    command = entry[key]
    if (not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command)
            or not os.path.isabs(command[0])):
        raise ValueError(f'{where}.{key} must be a list of strings that starts with an absolute path')


def probe_error(kind):
    return {'available': False, 'reason': f'capacity_probe_error: {kind}'}


def stop(process):
    """Kill the probe's session, close the read pipe and reap with a bounded wait. A detached descendant may live on; it cannot block us."""
    # The group outlives an exited leader when a child holds the pipe, so kill it whatever the leader's state.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()
    process.stdout.close()
    try:
        process.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        pass


def read_limited(process, deadline, limit=MAX_PROBE_BYTES):
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
            if size > limit:
                return None, 'output_too_large'
            chunks.append(chunk)


def run_bounded(argv, timeout, stdin_bytes=None, limit=MAX_PROBE_BYTES, env=None):
    """Run a command: no shell, output and time bounded. Returns (stdout, returncode, failure).

    `failure` is one of missing_binary, os_error, timeout, output_too_large, or None; a non-zero exit is not a failure
    here, the caller reads `returncode`. Stdin is closed, or carries `stdin_bytes` (small: written once before reading).
    The total time is the timeout plus `KILL_GRACE`. Output past `limit` fails closed; a truncated prefix is never returned.
    """
    if stdin_bytes is not None and len(stdin_bytes) > MAX_STDIN_BYTES:
        raise ValueError('stdin too large for one write')
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL if stdin_bytes is None else subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0, env=env)
    except FileNotFoundError:
        return None, None, 'missing_binary'
    except OSError:
        return None, None, 'os_error'
    deadline = time.monotonic() + timeout
    try:
        if stdin_bytes is not None:
            try:
                process.stdin.write(stdin_bytes)
            except OSError:
                pass  # the command exited without reading; its exit status and output say what happened
            finally:
                process.stdin.close()
        stdout, failure = read_limited(process, deadline, limit)
        if failure is None:
            try:
                process.wait(timeout=max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                failure = 'timeout'
    finally:
        stop(process)
    return stdout, process.returncode, failure


def reject_constant(name):
    raise ValueError(f'nonstandard JSON constant: {name}')


def probe(entry):
    """Run the capacity command. Every failure maps to available false.

    The total time is the timeout plus `KILL_GRACE`. Output past `MAX_PROBE_BYTES` fails closed; a truncated prefix is never parsed.
    """
    stdout, returncode, failure = run_bounded(entry['capacity_command'], entry['timeout_seconds'])
    if failure:
        return probe_error(failure)
    if returncode != 0:
        return probe_error('nonzero_exit')
    try:
        # A typed adapter's numbers feed a cost claim: Infinity, -Infinity and NaN are not JSON and fail closed.
        value = json.loads(stdout.decode('utf-8'), **({'parse_constant': reject_constant} if is_typed(entry) else {}))
    except ValueError:
        return probe_error('invalid_json')
    if not isinstance(value, dict) or type(value.get('available')) is not bool:
        return probe_error('invalid_shape')
    return value


def aware_time(value):
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if isinstance(value, str) and value.endswith('Z') else value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def count(value, minimum):
    return type(value) is int and value >= minimum


def price(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:   # a valid JSON integer too large for a float is not a usable price
        return False


def typed_unavailable(capacity):
    """Only `available: false` and an enumerated reason survive; an adapter's own text and extra fields never do."""
    reason = capacity.get('reason')
    return {'available': False, 'reason': reason if reason in UNAVAILABLE_REASONS else 'adapter_reported_unavailable'}


def typed_evidence(entry, op, capacity, now, until=None):
    """(None, summary) when the probe carries current, matching evidence for this op, else (reason_code, None).

    The probe only reports an evaluation already made; it is not a live attestation of the serving model. Time comes from
    the caller's evaluation clock, which is the real clock for every dispatchable route (a simulation never probes).
    `until` is that clock plus the monotonic time the probe took: the evidence must be current at both ends, so an offer
    never expires while it is being validated. The summary is rebuilt from validated fields only; nothing else is echoed."""
    proof, contract = entry['evidence_contract'], entry['contracts'][op]
    if capacity.get('available') is not True:
        return 'unavailable', None
    if capacity.get('op') != op:
        return 'op_mismatch', None
    if capacity.get('evaluated_model') != proof['evaluated_model']:
        return 'model_mismatch', None
    if capacity.get('contract_sha256') != contract['sha256']:
        return 'contract_mismatch', None
    if capacity.get('accepted_rule_ids') != proof['accepted_rule_ids']:
        return 'rule_mismatch', None
    evaluation = capacity.get('evaluation')
    if not isinstance(evaluation, dict):
        return 'evaluation_missing', None
    cases, positive, negative = (evaluation.get(k) for k in ('cases', 'positive', 'negative'))
    if not (count(cases, 4) and count(positive, 2) and count(negative, 2) and positive + negative == cases
            and evaluation.get('definitive') == cases):
        return 'not_balanced_or_definitive', None
    if any(type(evaluation.get(k)) is not int or evaluation[k] != 0 for k in ('label_disagreements', 'baseline_disagreements')):
        return 'disagreements', None
    metadata = evaluation.get('test_evidence_metadata')
    sample = evaluation.get('sample_sha256')
    if (not isinstance(sample, str) or not HEX64.fullmatch(sample) or not isinstance(metadata, dict)
            or metadata.get('sample_sha256') != sample or metadata.get('label_source') not in LABEL_SOURCES):
        return 'sample_unproven', None
    versions = {'baseline_model': proof['baseline_model'], 'baseline_effort': proof['baseline_effort'], 'typed_version': proof['typed_version']}
    if evaluation.get('versions') != versions:
        return 'version_mismatch', None
    cost = evaluation.get('cost')
    if (not isinstance(cost, dict) or cost.get('basis') != 'api_equivalent' or cost.get('currency') != proof['currency']
            or cost.get('scope') != COST_SCOPE or not price(cost.get('per_decision')) or not price(cost.get('baseline_per_decision'))
            or any(key not in cost or cost[key] is not None for key in UNKNOWN_COSTS)
            or aware_time(cost.get('priced_at')) is None):
        return 'cost_unproven', None
    evaluated_at, valid_until = aware_time(evaluation.get('evaluated_at')), aware_time(evaluation.get('valid_until'))
    if evaluated_at is None or valid_until is None or valid_until <= evaluated_at:
        return 'time_invalid', None
    if now is None or now.tzinfo is None:
        return 'clock_unavailable', None
    if not evaluated_at <= now < valid_until or not (until or now) < valid_until:
        return 'evaluation_expired', None
    projected = {
        **{key: evaluation[key] for key in ('cases', 'positive', 'negative', 'definitive', 'label_disagreements', 'baseline_disagreements')},
        'sample_sha256': sample, 'test_evidence_metadata': {'label_source': metadata['label_source'], 'sample_sha256': sample},
        'versions': versions, 'evaluated_at': evaluation['evaluated_at'], 'valid_until': evaluation['valid_until'],
        'cost': {'basis': 'api_equivalent', 'currency': proof['currency'], 'scope': COST_SCOPE, 'priced_at': cost['priced_at'],
                 'per_decision': cost['per_decision'], 'baseline_per_decision': cost['baseline_per_decision'],
                 **dict.fromkeys(UNKNOWN_COSTS)}}
    return None, {'available': True, 'op': op, 'evaluated_model': proof['evaluated_model'], 'contract_sha256': contract['sha256'],
                  'accepted_rule_ids': list(proof['accepted_rule_ids']), 'evaluation': projected}


def historical_handoff(result):
    """A replayed typed receipt is a record, not a live offer: its evidence was current only when it was validated.

    Returns a copy for output; the stored receipt is never touched. The offer moves to `historical_destination_offer`
    without its submit command, so nothing under the normal dispatch keys can be run from an old receipt. Other
    destinations and receipts without a typed offer come back unchanged."""
    offer = result.get('destination_offer')
    if not isinstance(offer, dict) or 'typed' not in offer:
        return result
    held = {key: value for key, value in offer.items() if key != 'submit'} | {'historical': True}
    changed = {key: value for key, value in result.items() if key != 'destination_offer'}
    return changed | {'historical_destination_offer': held, 'historical_non_actionable': True, 'launch_ready': False,
                      'launch_readiness_basis': 'historical typed destination offer; evidence was current only when validated; re-route for a current offer'}


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


def fact_reasons(facts, entry=None):
    """Skip reasons that the recorded launch facts imply for this entry (none given: a plain destination).

    Only a typed destination can lift a reason, and only its own declared capability: `caller_text` lifts local inputs for
    the exact `input_mode` caller-text, `synchronous` lifts urgent. Credential and every parent skip never lift."""
    facts = facts or {}
    mode = facts.get('input_mode')
    if mode is not None and mode not in INPUT_MODES:
        raise ValueError(f'unknown input mode: {mode}')
    capability = (entry or {}).get('capability') if entry is not None and is_typed(entry) else None
    capability = capability or {}
    local = facts.get('inputs') == 'local' and not (capability.get('caller_text') is True and mode == 'caller-text')
    urgent = facts.get('urgent') is True and capability.get('synchronous') is not True
    return [reason for reason, applies in (('local-inputs', local), ('synchronous', urgent),
                                           ('credential', facts.get('sensitive') is True)) if applies]


def by_destination(considered):
    """Each declined or probed destination's own reason(s), by name: a fact keeps its fact, a probe keeps its state."""
    out = {}
    for row in considered:
        if row['state'] == 'declined_by_facts':
            out[row['name']] = row['reasons']
        elif row['state'] == 'declined_by_independence':
            out[row['name']] = ['independence']
        elif row['state'] == 'declined_by_chain':
            out[row['name']] = ['chain_failures']
        elif row['state'] in ('unavailable', 'evaluation_required'):
            out[row['name']] = [row['state']]
    return out


def same_vendor(entry, independence):
    """True when the destination provably shares the maker's vendor and the required level is `vendor`."""
    return bool(independence) and independence['level'] == 'vendor' and entry.get('vendor') in independence['maker_vendors']


def independence_unknown(entry, independence):
    """True when a required independence cannot be checked: no destination vendor, a maker vendor that is unknown for any run, or a level finer than vendor."""
    return bool(independence) and (not entry.get('vendor') or not independence['maker_complete'] or independence['level'] != 'vendor')


def decide(entries, *, purpose, facts, skip, reason, request_id, simulate, independence=None, run_probe=probe, lineage=None, op=None, now=None, monotonic=time.monotonic, at_monotonic=None):
    """Return (`routing.destination` block, offer or None). Facts first, then the parent's skip, then independence, then capacity.

    `independence` is None when none is required, else {level, maker_vendors, maker_complete}; `maker_complete` is false when any maker run has an unknown vendor.
    """
    listed = [e for e in entries if eligibility(e, purpose, op) is None]
    block = {'chosen': 'model', 'considered': []}
    # Every destination the purpose or op excludes is named with the reason, so no receipt skips one silently.
    unlisted = [{'name': e['name'], 'reason': eligibility(e, purpose, op), 'purpose': purpose}
                for e in entries if eligibility(e, purpose, op) is not None]
    if unlisted:
        block['not_eligible'] = unlisted
    if not listed:
        return block, None
    first = listed[0]['name']

    def declined(state):
        return [{'name': e['name'], 'state': state} for e in listed]

    implied = {e['name']: fact_reasons(facts, e) for e in listed}
    pre = []
    if all(implied.values()):
        return block | {'considered': declined('declined_by_facts'),
                        'fallback': {'destination': first, 'reasons': implied[first], 'source': 'facts'}}, None
    if any(implied.values()):
        # Only a typed destination's own capability lifts a fact: the others are named, the rest go on.
        pre = [{'name': e['name'], 'state': 'declined_by_facts', 'reasons': implied[e['name']]} for e in listed if implied[e['name']]]
        listed = [e for e in listed if not implied[e['name']]]
        first = listed[0]['name']
    if skip:
        return block | {'considered': pre + declined('declined_by_parent'),
                        'fallback': {'destination': first, 'reasons': list(skip), 'source': 'parent', 'reason': reason}}, None
    if simulate:
        return block | {'considered': pre + declined('not_probed_in_simulation')}, None
    # Only a destination that shares the maker's vendor is an independence decline; a fact decline keeps its own reason.
    independence_declined = [e['name'] for e in listed if same_vendor(e, independence)]
    considered = pre + [{'name': name, 'state': 'declined_by_independence'} for name in independence_declined]
    listed = [e for e in listed if not same_vendor(e, independence)]
    if not listed:
        fallback = {'destination': first, 'reasons': ['independence'], 'source': 'facts'}
        if pre:
            fallback['by_destination'] = by_destination(considered)
        return block | {'considered': considered, 'fallback': fallback}, None
    # `now` was sampled earlier by the caller (before runtime sources and quota reads); `at_monotonic` is the monotonic
    # reading taken with it, so the time between that sample and this probe also counts against the evidence.
    started = at_monotonic if at_monotonic is not None else monotonic()
    spent = {}
    for entry in listed:
        root, failures = (lineage or (lambda name: (None, 0)))(entry['name'])
        if 'max_chain_failures' in entry and failures >= entry['max_chain_failures']:
            spent[entry['name']] = failures
            considered.append({'name': entry['name'], 'state': 'declined_by_chain', 'chain_failures': failures})
    listed = [e for e in listed if e['name'] not in spent]
    if not listed:
        reasons = (['independence'] if independence_declined else []) + ['chain_failures']
        fallback = {'destination': first, 'reasons': reasons, 'source': 'chain', 'chain_failures': spent}
        if pre or independence_declined:
            fallback['by_destination'] = by_destination(considered)
        return block | {'considered': considered, 'fallback': fallback}, None
    for position, entry in enumerate(listed):
        capacity = run_probe(entry)
        if is_typed(entry) and not capacity['available']:
            capacity = typed_unavailable(capacity)
        elif is_typed(entry):
            # A typed worker is healthy only with current evidence that equals its declared contract; else evaluation is required.
            until = now + timedelta(seconds=max(monotonic() - started, 0)) if now is not None and now.tzinfo is not None else now
            problem, summary = typed_evidence(entry, op, capacity, now, until)
            capacity = summary if summary else {'available': False, 'reason': f'evaluation_required: {problem}'}
            if summary is None:
                considered.append({'name': entry['name'], 'state': 'evaluation_required', 'capacity': capacity})
                continue
        if capacity['available']:
            considered.append({'name': entry['name'], 'state': 'chosen', 'capacity': capacity})
            # Later destinations are named, not probed: the first with capacity wins.
            considered += [{'name': later['name'], 'state': 'not_probed_earlier_chosen'} for later in listed[position + 1:]]
            offer = {'name': entry['name'], 'request_id': request_id, 'purpose': purpose, 'how_to': entry['how_to'],
                     'submit': substitute(entry['submit'], {'<request_id>': request_id, '<purpose>': purpose}), 'capacity': capacity,
                     'vendor': entry.get('vendor')}
            if is_typed(entry):
                offer['submit'] = substitute(offer['submit'], {'<op>': op})
                offer['typed'] = {'op': op, 'contract': dict(entry['contracts'][op]), 'caller_text': entry['capability']['caller_text'],
                                  'synchronous': entry['capability']['synchronous'], 'evidence': 'current',
                                  'handoff': 'typed submit command and contract; the router calls nothing and returns no model target'}
            if 'target' in entry:
                root = (lineage or (lambda name: (None, 0)))(entry['name'])[0] or request_id
                pattern = entry.get('request_id_pattern')
                offer['_dispatch'] = {'target': dict(entry['target']), 'root_decision_id': root,
                                      'brief_first_line': entry['brief_token'].replace(ROOT, root),
                                      'root_accepted': pattern is None or re.fullmatch(pattern, root) is not None}
            return block | {'chosen': entry['name'], 'considered': considered}, offer
        considered.append({'name': entry['name'], 'state': 'unavailable', 'capacity': capacity})
    probed = [row for row in considered if row['state'] in ('unavailable', 'evaluation_required')]
    states = {row['state'] for row in probed}
    kinds = [k for k in ('unavailable', 'evaluation_required') if k in states]
    fallback = {'destination': first, 'reasons': kinds, 'source': 'capacity', 'probe_reason': probed[0]['capacity'].get('reason')}
    if independence_declined or pre:
        # Mixed destinations: each declined or probed one names its own reason, and independence only if it was declined for it.
        fallback['by_destination'] = by_destination(considered)
        if independence_declined:
            fallback['reasons'] = ['independence'] + kinds
    return block | {'considered': considered, 'fallback': fallback}, None


INPUTS_UNKNOWN = {'code': 'destination_inputs_unknown', 'applies_to': 'destination',
                  'detail': 'the destination needs one pushed GitHub input; if inputs are local, re-run with --inputs local'}
INDEPENDENCE_UNKNOWN = {'code': 'destination_independence_unknown', 'applies_to': 'destination',
                        'detail': 'the destination vendor or the maker vendor is unknown, or the level is finer than vendor; independence cannot be checked'}
