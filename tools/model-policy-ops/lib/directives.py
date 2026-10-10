"""Time-bounded owner directives. Private append-only JSONL, beside the receipts.

A directive is a temporary instruction: avoid, prefer, authorize an unvalidated candidate, or
allow a metered account. None is permanent. Standing rules belong in the policy pack.
"""
import fcntl
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from decision_receipts import append_row, locked_private_rows, numbered_rows, validate_id

EFFECTS = ('avoid', 'prefer', 'authorize', 'allow-metered')
SOURCES = ('owner', 'lead')
MATCH_KEYS = ('provider', 'model', 'account', 'op')
MAX_REASON = 280


def now():
    return datetime.now(timezone.utc)


def parse_time(value, name):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        raise ValueError(f'{name} must be an RFC 3339 timestamp with a timezone') from None
    if parsed.tzinfo is None:
        raise ValueError(f'{name} must be an RFC 3339 timestamp with a timezone')
    return parsed.astimezone(timezone.utc)


def parse_match(items):
    match = {}
    for item in items:
        key, sep, value = item.partition('=')
        if not sep or key not in MATCH_KEYS or not value.strip() or key in match:
            raise ValueError(f'--match takes unique KEY=VALUE with KEY one of {", ".join(MATCH_KEYS)}')
        match[key] = value
    if not match:
        raise ValueError('a directive needs at least one --match KEY=VALUE')
    return match


def reason_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REASON or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'--reason must be one line of 1-{MAX_REASON} characters; no prompts or secrets')
    return value


def check_rows(numbered, name='directives.jsonl'):
    """Fail closed on the first bad row or broken lineage. Nothing is skipped or repaired."""
    added = {}
    ended = set()
    for number, row in numbered:
        def fail(reason):
            raise ValueError(f'{name} line {number}: {reason}')
        kind = row.get('kind') if isinstance(row, dict) else None
        if kind not in ('add', 'end') or row.get('schema_version') != 1:
            fail('not a version 1 add or end row')
        if not isinstance(row.get('id'), str):
            fail('id must be a string')
        validate_id(row['id'])
        if kind == 'add':
            if row['id'] in added:
                fail(f'id {row["id"]!r} already used')
            if row.get('effect') not in EFFECTS:
                fail('effect is not allowed')
            match = row.get('match')
            if not isinstance(match, dict) or not match or set(match) - set(MATCH_KEYS) or not all(isinstance(v, str) for v in match.values()):
                fail('match must be a non-empty object of provider/model/account/op strings')
            try:
                parse_time(row.get('until'), 'until')
                parse_time(row.get('recorded_at'), 'recorded_at')
            except ValueError as error:
                fail(str(error))
            if not isinstance(row.get('reason'), str):
                fail('reason must be a string')
            if row.get('source') not in SOURCES:
                fail('source must be owner or lead')
            added[row['id']] = number
        else:
            if row['id'] not in added or row['id'] in ended:
                fail(f'end names {row["id"]!r}, which is not an open directive')
            ended.add(row['id'])
            if not isinstance(row.get('reason'), str):
                fail('reason must be a string')


def refuse_writable(stream_or_path):
    """A directive can authorize metered spend, so a file that group or world can write is not trusted."""
    mode = os.fstat(stream_or_path).st_mode if isinstance(stream_or_path, int) else os.stat(stream_or_path).st_mode
    if mode & 0o022:
        raise ValueError('directives file must not be group- or world-writable (chmod 600)')


def read_directives(path):
    """Directive records with a state: `active`, `expired` or `ended`. A missing file means none."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ValueError(f'directives file is unreadable or a symlink: {error.strerror}') from None
    try:
        refuse_writable(fd)
    except ValueError:
        os.close(fd)
        raise
    with os.fdopen(fd, encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        numbered = numbered_rows(stream, os.path.basename(path))
    check_rows(numbered, os.path.basename(path))
    ends = {row['id']: row for _, row in numbered if row['kind'] == 'end'}
    records = []
    for _, row in numbered:
        if row['kind'] == 'add':
            records.append(row | {'ended': ends.get(row['id'])})
    return records


def rejection(record, at, max_days):
    """Why a reader ignores a live row, or None. The writer cannot produce these rows; an older writer or a hand edit can.

    A row is rejected when it was recorded after the evaluation time, or when it lasts longer than
    the pack's `directive_max_days`. An ended or expired row is never rejected: it grants nothing.
    """
    recorded, until = parse_time(record['recorded_at'], 'recorded_at'), parse_time(record['until'], 'until')
    if recorded > at:
        return 'recorded_after_evaluation_time'
    if max_days is not None and until - recorded > timedelta(days=max_days):
        return f'lasts_longer_than_directive_max_days_{max_days}'
    return None


def classify(record, at, max_days=None):
    if record['ended']:
        return 'ended'
    if parse_time(record['until'], 'until') <= at:
        return 'expired'
    return 'rejected' if rejection(record, at, max_days) else 'active'


def active(records, at, max_days=None):
    return [r for r in records if classify(r, at, max_days) == 'active']


def rejected(records, at, max_days=None):
    return [{'id': r['id'], 'reason': rejection(r, at, max_days)} for r in records if classify(r, at, max_days) == 'rejected']


def view(record, at, max_days=None):
    keys = ('id', 'effect', 'match', 'until', 'source', 'reason', 'recorded_at')
    result = {k: record[k] for k in keys} | {'state': classify(record, at, max_days)}
    if result['state'] == 'rejected':
        result['rejected_because'] = rejection(record, at, max_days)
    if record['ended']:
        result['ended'] = {k: record['ended'][k] for k in ('reason', 'recorded_at')}
    return result


def matching(directives, effect, **facts):
    """Active directives of one effect whose every match key equals the pair's fact."""
    return [d for d in directives if d['effect'] == effect and matches(d, **facts)]


def matches(directive, **facts):
    """Every key in the directive must equal the pair's fact. A fact that is absent never matches."""
    return all(facts.get(key) == value for key, value in directive['match'].items())


def check_known(match, known):
    """Reject a provider, model or op that no source declares; warn for an account that none declares.

    `known` maps each match key to the set of values that the pack, the live catalog and the model
    catalog declare. A key with no set, or an empty set, is not checked. Returns the warnings.
    """
    for key in ('provider', 'model', 'op'):
        values = (known or {}).get(key)
        if values and match.get(key) not in (None, *values):
            raise ValueError(f'--match {key}={match[key]} is not in the pack, the live catalog or the model catalog')
    accounts = (known or {}).get('account')
    if accounts and match.get('account') not in (None, *accounts):
        return [f'account {match["account"]!r} is not in the live catalog or the account map; the directive may match nothing']
    return []


def add(path, directive_id, effect, match_items, until, reason, source, at, known=None, max_days=None):
    validate_id(directive_id)
    if effect not in EFFECTS:
        raise ValueError(f'--effect must be one of {", ".join(EFFECTS)}')
    if source not in SOURCES:
        raise ValueError(f'--source is required and must be one of {", ".join(SOURCES)}')
    row = {'schema_version': 1, 'kind': 'add', 'id': directive_id, 'effect': effect, 'source': source,
           'match': parse_match(match_items), 'until': None, 'reason': reason_text(reason),
           'recorded_at': at.isoformat()}
    if not until:
        raise ValueError('--until is required; no directive is permanent')
    expiry = parse_time(until, '--until')
    if expiry <= at:
        raise ValueError('--until must be in the future')
    if max_days is not None and expiry > at + timedelta(days=max_days):
        raise ValueError(f'--until is beyond the pack limit of {max_days} days (routing.directive_max_days); a longer rule belongs in the pack')
    row['until'] = expiry.isoformat()
    warnings = check_known(row['match'], known)
    if Path(path).exists():
        refuse_writable(path)
    with locked_private_rows(path, check_rows) as (stream, rows):
        if any(r['id'] == directive_id for r in rows):
            raise ValueError(f'directive ID {directive_id!r} already used')
        append_row(stream, row)
    return view(row | {'ended': None}, at, max_days) | ({'warnings': warnings} if warnings else {})


def end(path, directive_id, reason, at, max_days=None):
    validate_id(directive_id)
    row = {'schema_version': 1, 'kind': 'end', 'id': directive_id, 'reason': reason_text(reason),
           'recorded_at': at.isoformat()}
    if Path(path).exists():
        refuse_writable(path)
    with locked_private_rows(path, check_rows) as (stream, rows):
        opened = [r for r in rows if r['id'] == directive_id]
        if not opened or any(r['kind'] == 'end' for r in opened):
            raise ValueError(f'no open directive {directive_id!r}')
        append_row(stream, row)
        added = next(r for r in opened if r['kind'] == 'add')
    return view(added | {'ended': row}, at, max_days)


def execute(args, at, known=None, max_days=None):
    """`directive add|end|list`. Reads and writes only the directives file; `known` and `max_days` come from the pack.

    `at` is the real clock for `add` and the evaluation time for `list`.
    """
    action = args.op
    if action == 'add':
        if args.now:
            raise ValueError('directive add records the real clock; --now is not allowed (a directive cannot be back- or forward-dated)')
        if args.target:
            raise ValueError('directive add takes --id, not a positional ID')
        return add(args.directives, args.id, args.effect, args.match, args.until, args.reason, args.source, at, known, max_days)
    if action == 'end':
        if not args.target:
            raise ValueError('directive end requires the directive ID')
        return end(args.directives, args.target, args.reason, at, max_days)
    if action == 'list':
        return {'schema_version': 1, 'now': at.isoformat(),
                'directives': [view(r, at, max_days) for r in read_directives(args.directives)]}
    raise ValueError('directive requires add, end or list')
