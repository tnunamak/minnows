"""Private append-only receipts, with locked identity and escalation checks."""
import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path


def read_receipts(path):
    try:
        with Path(path).open() as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return [json.loads(line) for line in stream if line.strip()]
    except FileNotFoundError:
        return []
    except ValueError as error:
        raise ValueError('malformed receipt JSONL') from error


def numbered_rows(lines, name):
    """Parse JSONL into (line_number, row) pairs. Failure names the file and line."""
    rows = []
    for number, line in enumerate(lines, 1):
        if line.strip():
            try:
                rows.append((number, json.loads(line)))
            except ValueError as error:
                raise ValueError(f'{name} line {number}: not valid JSON') from error
    return rows


def find_receipt(path, decision_id):
    matches = [r for r in read_receipts(path) if r['decision_id'] == decision_id]
    if len(matches) > 1:
        raise ValueError('duplicate receipt decision ID')
    return matches[0] if matches else None


def validate_id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', value):
        raise ValueError('decision ID must be 1–128 letters, digits, dots, underscores, colons or hyphens')



TASK_ROLES = ('planning', 'maker', 'checker', 'repair', 'escalation', 'rework', 'other')
CHOICE_DISPOSITIONS = ('selected', 'selected_waiting', 'not_selected', 'rejected')
COST_KEY = 'predicted_total_cost_per_accepted_result'


def metadata_object(raw, name, limit=8192):
    """Bounded caller JSON, validated before opening any writable state."""
    if not isinstance(raw, str) or len(raw) > limit:
        raise ValueError(f'{name} must be bounded JSON (at most {limit} characters)')
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f'{name} contains duplicate keys')
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, RecursionError) as error:
        raise ValueError(f'{name} must be a JSON object with unique keys') from error
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be a JSON object')
    return value


def metadata_id(value, name):
    if not isinstance(value, str):
        raise ValueError(f'{name} must be an ID string')
    validate_id(value)


def metadata_text(value, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > 280
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f'{name} must be one line of 1-280 characters')


def validate_task_link(value):
    keys = {'v', 'consumer_task_id', 'workflow_id', 'stage_id', 'role'}
    if not isinstance(value, dict) or set(value) != keys or type(value.get('v')) is not int or value['v'] != 1:
        raise ValueError('task_link requires exactly v:1, consumer_task_id, workflow_id, stage_id and role')
    for key in ('consumer_task_id', 'workflow_id', 'stage_id'):
        metadata_id(value[key], key)
    if value['role'] not in TASK_ROLES:
        raise ValueError('task_link role is not supported')
    return value


def validate_choice_reason(value):
    if not isinstance(value, dict) or not {'code'} <= set(value) <= {'code', 'detail'}:
        raise ValueError('choice reason requires code and optional detail')
    metadata_id(value['code'], 'choice reason code')
    if 'detail' in value:
        metadata_text(value['detail'], 'choice reason detail')


def unknown_cost():
    return {'state': 'unknown', 'value': None,
            'reason': 'complete workflow cost and accepted-result evidence are not available'}


def validate_choice(value):
    if (not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1
            or not {'v', 'reason'} <= set(value) <= {'v', 'reason', 'workflow_candidate', 'schedule', 'considered'}):
        raise ValueError('choice requires v:1 and reason; optional workflow_candidate, schedule and considered')
    validate_choice_reason(value['reason'])
    if 'workflow_candidate' in value:
        metadata_id(value['workflow_candidate'], 'workflow_candidate')
    if 'schedule' in value:
        schedule = value['schedule']
        if (not isinstance(schedule, dict) or set(schedule) != {'state', 'candidate', 'reason'}
                or schedule['state'] not in ('immediate', 'selected_waiting')):
            raise ValueError('choice schedule requires state (immediate|selected_waiting), candidate and reason')
        metadata_id(schedule['candidate'], 'schedule candidate')
        validate_choice_reason(schedule['reason'])
    entries = value.get('considered', [])
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError('choice considered must be a list of at most 16 parent-considered workflows')
    seen = set()
    for entry in entries:
        required = {'kind', 'candidate', 'disposition', 'reason'}
        if (not isinstance(entry, dict) or not required <= set(entry) <= required | {COST_KEY}
                or entry['kind'] != 'workflow' or entry['disposition'] not in CHOICE_DISPOSITIONS):
            raise ValueError('parent considered entries require kind:workflow, candidate, disposition and reason')
        metadata_id(entry['candidate'], 'workflow candidate')
        if entry['candidate'] in seen:
            raise ValueError('duplicate parent workflow candidate')
        seen.add(entry['candidate'])
        validate_choice_reason(entry['reason'])
        if COST_KEY in entry:
            cost = entry[COST_KEY]
            if (not isinstance(cost, dict) or set(cost) != {'state', 'value', 'reason'}
                    or cost['state'] != 'unknown' or cost['value'] is not None):
                raise ValueError('choice v1 permits only unknown predictions with null value')
            metadata_text(cost['reason'], 'unknown cost reason')
    return value


def receipt_extensions(args):
    result = {}
    for key, validator in (('task_link', validate_task_link), ('choice', validate_choice)):
        raw = getattr(args, key, None)
        if raw is not None:
            result[key] = validator(metadata_object(raw, '--' + key.replace('_', '-')))
    return result


def capture_choice(receipt, caller=None):
    """Describe only evaluated selections and explicit caller workflows. Never route or dispatch."""
    considered = []

    def add(kind, disposition, code, pointer, **facts):
        considered.append({'kind': kind, 'disposition': disposition,
                           'reason': {'code': code, 'source': pointer},
                           COST_KEY: unknown_cost(), **facts})

    block = receipt.get('routing')
    if block:
        chosen, final = block.get('chosen') or {}, block.get('final') or {}
        destination = block.get('destination') or {}
        destination_selected = destination.get('chosen', 'model') != 'model'
        for index, row in enumerate(block.get('trace', [])):
            picked = (row.get('candidate') == chosen.get('candidate')
                      and row.get('instance') == chosen.get('instance') and row.get('arm') == chosen.get('arm'))
            matches_final = row.get('arm') == final.get('arm') and row.get('instance') == final.get('account')
            if row.get('removed_by'):
                disposition, code = 'filtered', row['removed_by']
            elif picked and final.get('overridden') and not matches_final:
                disposition, code = 'overridden', 'explicit_override'
            elif (matches_final if final.get('overridden') else picked):
                disposition = 'model_fallback' if destination_selected else 'selected'
                code = 'explicit_override' if final.get('overridden') else 'routing_choice'
            else:
                disposition, code = 'not_selected', 'routing_rank'
            add('dispatch_arm', disposition, code, f'#/routing/trace/{index}',
                candidate=row.get('candidate'), basis=row.get('basis'))
        # Explicit overrides are assessed by the router even when absent from its candidate trace.
        if final.get('overridden'):
            add('dispatch_arm', 'model_fallback' if destination_selected else
                'selected' if final.get('target') is not None else 'unresolved',
                'explicit_override', '#/routing/final')
        for index, row in enumerate(destination.get('considered', [])):
            add('destination', 'selected' if row['state'] == 'chosen' else 'not_selected',
                row['state'], f'#/routing/destination/considered/{index}', candidate=row['name'])
        for index, row in enumerate(destination.get('not_eligible', [])):
            add('destination', 'not_selected', row['reason'], f'#/routing/destination/not_eligible/{index}', candidate=row['name'])
        reason = {'code': 'routing_evaluation', 'source': '#/routing'}
    else:
        add('dispatch_arm', 'selected' if receipt['selection'].get('target') is not None else 'unresolved',
            receipt['selection']['state'], '#/selection')
        for index, entry in enumerate(receipt.get('alternatives', [])):
            add('dispatch_arm', 'alternative', entry['selection']['state'], f'#/alternatives/{index}/selection')
        for index, row in enumerate((receipt.get('destination_skip') or {}).get('destinations', [])):
            add('destination', 'not_selected', 'explicit_resolution', f'#/destination_skip/destinations/{index}', candidate=row['name'])
        reason = {'code': 'explicit_resolution', 'source': '#/selection'}
    value = {'v': 1, 'reason': reason, 'considered': considered}
    if caller is not None:
        # Keep the exact supplied metadata for replay; derived entries are never caller identity.
        value['caller'] = caller
        value['reason'] = caller['reason'] | {'source': '#/choice/caller/reason'}
        for key in ('workflow_candidate', 'schedule'):
            if key in caller:
                value[key] = caller[key]
        for index, entry in enumerate(caller.get('considered', [])):
            considered.append(entry | {'reason': entry['reason'] | {'source': f'#/choice/caller/considered/{index}'},
                                       COST_KEY: entry.get(COST_KEY, unknown_cost())})
        named = {item['candidate'] for item in caller.get('considered', [])}
        schedule = caller.get('schedule') or {}
        for candidate, pointer, disposition in (
                (schedule.get('candidate'), '#/choice/caller/schedule', schedule.get('state')),
                (caller.get('workflow_candidate'), '#/choice/caller/workflow_candidate', 'selected')):
            if candidate and candidate not in named:
                add('workflow', 'selected_waiting' if disposition == 'selected_waiting' else 'selected',
                    'caller_choice', pointer, candidate=candidate)
                named.add(candidate)
        if (caller.get('schedule') or {}).get('state') == 'selected_waiting' or any(
                item['disposition'] == 'selected_waiting' for item in caller.get('considered', [])):
            value['warning'] = ('caller claim only; old launchers do not enforce selected_waiting; '
                                'the parent must withhold dispatch; no queue is created')
    return value


def supplied_extensions(receipt):
    supplied = {}
    if 'task_link' in receipt:
        supplied['task_link'] = validate_task_link(receipt['task_link'])
    if isinstance(receipt.get('choice'), dict) and 'caller' in receipt['choice']:
        supplied['choice'] = validate_choice(receipt['choice']['caller'])
    return supplied


def caller_intent(request):
    # Runtime observations are pinned evidence, not caller identity. Accept old receipts.
    return {k: v for k, v in request.items() if k not in ('available', 'quota') and not (k == 'relaunch_of' and v is None)}


def replay_receipt(old, request, launch_facts=None, extensions=None):
    # Launch facts live outside `request`, so a 4ce5a3e CLI replays new receipts with base flags.
    if caller_intent(old['request']) != caller_intent(request) or old.get('launch_facts') != launch_facts:
        raise ValueError('decision ID already recorded with changed inputs; use a new ID')
    for key, supplied in (extensions or {}).items():
        stored = old.get(key)
        if key == 'choice':
            stored = stored.get('caller') if isinstance(stored, dict) else None
        if stored != supplied:
            raise ValueError(f'decision ID already recorded with changed or absent {key}; use a new ID')
    return old | {'replayed': True}


def validate_escalation_source(rows, source_id):
    by_id = {r['decision_id']: r for r in rows}

    def relaunch_root(decision_id):
        seen = set()
        while True:
            if decision_id in seen or decision_id not in by_id:
                raise ValueError('invalid or missing receipt lineage')
            seen.add(decision_id)
            row = by_id[decision_id]
            if not row.get('relaunch'):
                return row
            decision_id = row['relaunch']['from']

    source = relaunch_root(source_id)
    if source.get('escalation') or any(
        r.get('escalation') and relaunch_root(r['escalation']['from'])['decision_id'] == source['decision_id']
        for r in rows
    ):
        raise ValueError('one linked escalation allowed across relaunch lineage; parent judgment requires a new explicit override')


@contextmanager
def locked_private_stream(path):
    """Yield an exclusively locked, private append-only JSONL stream without loading its rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o022:
        raise ValueError('receipt parent directory must not be group- or world-writable')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        os.fchmod(stream.fileno(), 0o600)
        yield stream


@contextmanager
def locked_private_rows(path, validate=None):
    """Yield (stream, rows) for an exclusive, private, append-only JSONL file.

    `validate(numbered_rows, name)` runs before the caller sees any row and may raise.
    """
    with locked_private_stream(path) as stream:
        numbered = numbered_rows(stream, Path(path).name)
        if validate:
            validate(numbered, Path(path).name)
        yield stream, [row for _, row in numbered]


def append_row(stream, row):
    stream.seek(0, os.SEEK_END)
    stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
    stream.flush()
    os.fsync(stream.fileno())


def append_receipt(path, receipt, extensions=None):
    validate_id(receipt['decision_id'])
    supplied = supplied_extensions(receipt)
    if extensions is None:
        extensions = supplied
    elif any(supplied.get(key) != value for key, value in extensions.items()):
        raise ValueError('receipt extensions do not match supplied metadata')
    with locked_private_rows(path) as (stream, rows):
        for old in rows:
            if old['decision_id'] == receipt['decision_id']:
                return replay_receipt(old, receipt['request'], receipt.get('launch_facts'), extensions)
        for link in ('escalation', 'relaunch'):
            if receipt.get(link):
                source = receipt[link]['from']
                validate_id(source)
                if source == receipt['decision_id'] or not any(r['decision_id'] == source for r in rows):
                    raise ValueError(f'{link} source receipt not found or self-linked')
        escalation = receipt.get('escalation')
        if escalation:
            validate_escalation_source(rows, escalation['from'])
        append_row(stream, receipt)
    return receipt
