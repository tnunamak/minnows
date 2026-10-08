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


def caller_intent(request):
    # Runtime observations are pinned evidence, not caller identity. Accept old receipts.
    return {k: v for k, v in request.items() if k not in ('available', 'quota') and not (k == 'relaunch_of' and v is None)}


def replay_receipt(old, request, launch_facts=None):
    # Launch facts live outside `request`, so a 4ce5a3e CLI replays new receipts with base flags.
    if caller_intent(old['request']) != caller_intent(request) or old.get('launch_facts') != launch_facts:
        raise ValueError('decision ID already recorded with changed inputs; use a new ID')
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
def locked_private_rows(path, validate=None):
    """Yield (stream, rows) for an exclusive, private, append-only JSONL file.

    `validate(numbered_rows, name)` runs before the caller sees any row and may raise.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o022:
        raise ValueError('receipt parent directory must not be group- or world-writable')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        os.fchmod(stream.fileno(), 0o600)
        numbered = numbered_rows(stream, path.name)
        if validate:
            validate(numbered, path.name)
        yield stream, [row for _, row in numbered]


def append_row(stream, row):
    stream.seek(0, os.SEEK_END)
    stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
    stream.flush()
    os.fsync(stream.fileno())


def append_receipt(path, receipt):
    validate_id(receipt['decision_id'])
    with locked_private_rows(path) as (stream, rows):
        for old in rows:
            if old['decision_id'] == receipt['decision_id']:
                return replay_receipt(old, receipt['request'], receipt.get('launch_facts'))
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
