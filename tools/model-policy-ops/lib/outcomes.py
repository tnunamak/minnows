"""Append-only outcome ledger for delegated decisions.

Unit: one recorded decision. Not a whole procedure and not top-level task performance.
Every enum is a caller claim. Evidence checks verify existence or a file hash only.
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone

from decision_receipts import append_row, find_receipt, locked_private_rows, numbered_rows, validate_id

CLI_VERSION = 1
OUTCOMES = ('accepted', 'accepted_after_repair', 'rejected', 'abandoned_by_choice', 'superseded', 'unknown')
JUDGED_BY = ('parent', 'independent', 'owner')
CHECKS = ('oracle', 'independent_judged', 'maker_judged', 'none')
OWNER_INPUT = ('none_observed', 'desired', 'correction', 'rescue', 'unknown')
FINDINGS = ('no_rework_found', 'fix_commit', 'revert', 'reopened', 'defect_reported')
PURPOSES = ('execution', 'review', 'research', 'exploration', 'test-design')
INPUTS = ('github', 'local')
PROOF_CLASSES = ('oracle', 'judged', 'none')
MAX_NOTE = 280
MAX_EVIDENCE = 8
MAX_EVIDENCE_JSON = 2048
MAX_HASH_BYTES = 256 * 1024 * 1024
HEX = {'sha1': re.compile(r'[0-9a-f]{40}'), 'sha256': re.compile(r'[0-9a-f]{64}')}
EVIDENCE_KEYS = {
    'commit': {'type', 'repo', 'sha'},
    'file': {'type', 'path', 'sha256'},
    'check': {'type', 'label', 'exit_code', 'log', 'sha256'},
    'pr': {'type', 'repo', 'number'},
}
SCOPE = 'one delegated decision; not whole-procedure or top-level task performance'
VERIFICATION_SCOPE = 'existence or file hash only; never the truth of a check or of success'
# Optional top-level row key, parallel to `evidence`: null, or {state, recorded_at} for commit evidence. Older CLIs ignore it.
# It is not an evidence-entry key: an older CLI compares entries on replay and would call the row a changed input.
COMMIT_CHECKS = ('exists', 'missing', 'timeout', 'error')
VERIFICATION_KEY = 'evidence_verification'


def now():
    return datetime.now(timezone.utc).isoformat()


def text_field(value, name, required=False):
    if value is None:
        if required:
            raise ValueError(f'{name} is required')
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NOTE or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{name} must be one line of 1-{MAX_NOTE} characters; caller-supplied, not secret-filtered, so no prompts or secrets')
    return value


def choice(value, allowed, name):
    if value not in allowed:
        raise ValueError(f'{name} must be one of {", ".join(allowed)}')
    return value


def launch_facts(args):
    """Optional launch facts stored beside, never inside, `request`. None when none were given."""
    facts = {k: v for k, v in (('purpose', args.purpose), ('proof_class', args.proof_class),
                               ('urgent', True if args.urgent else None),
                               ('irreversible', True if args.irreversible else None),
                               ('inputs', args.inputs), ('sensitive', True if args.sensitive else None)) if v is not None}
    return {'schema_version': 1, **facts} if facts else None


def absolute(path, name):
    if not isinstance(path, str) or not os.path.isabs(path) or '\0' in path:
        raise ValueError(f'{name} must be an absolute path')
    return path


def hex_field(value, kind, name):
    if not isinstance(value, str) or not HEX[kind].fullmatch(value):
        raise ValueError(f'{name} must be lowercase hex ({kind})')
    return value


def parse_evidence(raw):
    """Validate one --evidence JSON object. Raises on malformed input; does not touch the filesystem."""
    if len(raw) > MAX_EVIDENCE_JSON:
        raise ValueError('evidence reference too large')
    try:
        ref = json.loads(raw)
    except ValueError as error:
        raise ValueError('evidence must be a JSON object') from error
    if not isinstance(ref, dict) or ref.get('type') not in EVIDENCE_KEYS:
        raise ValueError('evidence needs "type": commit, file, check or pr')
    kind = ref['type']
    if set(ref) != EVIDENCE_KEYS[kind]:
        raise ValueError(f'{kind} evidence takes exactly: {", ".join(sorted(EVIDENCE_KEYS[kind]))}')
    if kind == 'commit':
        absolute(ref['repo'], 'commit repo')
        hex_field(ref['sha'], 'sha1', 'commit sha')
    elif kind == 'file':
        absolute(ref['path'], 'file path')
        hex_field(ref['sha256'], 'sha256', 'file sha256')
    elif kind == 'check':
        if not isinstance(ref['label'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}', ref['label']):
            raise ValueError('check label must be 1-64 letters, digits, dots, underscores, colons or hyphens')
        if type(ref['exit_code']) is not int or not 0 <= ref['exit_code'] <= 255:
            raise ValueError('check exit_code must be an integer 0-255')
        absolute(ref['log'], 'check log')
        hex_field(ref['sha256'], 'sha256', 'check log sha256')
    else:
        if not isinstance(ref['repo'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', ref['repo']):
            raise ValueError('pr evidence requires repo as owner/name')
        if type(ref['number']) is not int or ref['number'] < 1:
            raise ValueError('pr number must be a positive integer')
    return ref


def file_hash_state(path, expected):
    """Hash a regular file with a hard byte cap. A FIFO or device is `unreadable`, never opened blocking."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'unreadable'
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return 'unreadable'
    with os.fdopen(fd, 'rb') as stream:
        digest = hashlib.sha256()
        total = 0
        # The cap counts bytes actually read, so a file that grows during the read stays bounded.
        for block in iter(lambda: stream.read(1 << 20), b''):
            total += len(block)
            if total > MAX_HASH_BYTES:
                return 'too_large'
            digest.update(block)
    return 'hash_matches' if digest.hexdigest() == expected else 'mismatch'


def git_env():
    """The environment for a read-only git call. GIT_DIR and GIT_WORK_TREE would override -C and check the wrong repository.

    GIT_NO_LAZY_FETCH stops a partial clone from fetching a missing object from its promisor remote: a read must never write.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith('GIT_')} | {'GIT_NO_LAZY_FETCH': '1'}


def git_argv(repo, *args):
    return ['git', '--no-optional-locks', '-C', repo, *args]


COMMIT_TIMEOUT = 10
OBJECT_ID = re.compile(r'[0-9a-f]{40}')


def commit_state(repo, sha):
    """'exists', 'missing', 'timeout' or 'error'. `sha` is validated hex, so it cannot be read as an option.

    `cat-file --batch-check` answers on stdout and exits 0 whether or not the object exists. Only its explicit `<name> missing` line means
    absence; a non-zero exit, a signal or any other output is an operational failure, never a missing commit.
    """
    query = sha + '^{commit}'
    try:
        done = subprocess.run(git_argv(repo, 'cat-file', '--batch-check'), input=(query + '\n').encode(), capture_output=True,
                              timeout=COMMIT_TIMEOUT, env=git_env())
    except subprocess.TimeoutExpired:
        return 'timeout'
    except OSError:
        return 'error'
    lines = done.stdout.decode('utf-8', 'replace').splitlines()
    if done.returncode != 0 or len(lines) != 1:
        return 'error'
    fields = lines[0].split(' ')
    if fields == [query, 'missing']:
        return 'missing'
    if len(fields) == 3 and OBJECT_ID.fullmatch(fields[0]) and fields[1] == 'commit' and fields[2].isdigit():
        return 'exists'
    return 'error'


def commit_exists(repo, sha):
    """True when `sha` names a commit in the local object store of `repo`. Never fetches."""
    return commit_state(repo, sha) == 'exists'


def verify_evidence(ref):
    """Return (entry, verification). The entry is ref plus the legacy verified_state; verification is the exact commit_state or None.

    No shell, no network, no claim about what the evidence proves. A commit that git could not check keeps the legacy
    `unverified_missing`, but its verification state says `timeout` or `error`, not `missing`.
    """
    kind = ref['type']
    stamp = now()
    verification = None
    if kind == 'commit':
        observed = commit_state(ref['repo'], ref['sha'])
        state = 'exists' if observed == 'exists' else 'unverified_missing'
        verification = {'state': observed, 'recorded_at': stamp}
    elif kind == 'file':
        state = file_hash_state(ref['path'], ref['sha256'])
    elif kind == 'check':
        state = file_hash_state(ref['log'], ref['sha256'])
        if state == 'hash_matches':
            state = 'log_hash_matches'
    else:
        state = 'claim_only'
    return ref | {'verified_state': state, 'verified_at': stamp, 'verification_scope': VERIFICATION_SCOPE}, verification


def verify_all(refs):
    """Row fields for a list of references: `evidence`, plus the optional parallel verification list when a commit is present."""
    pairs = [verify_evidence(r) for r in refs]
    fields = {'evidence': [entry for entry, _ in pairs]}
    if any(v for _, v in pairs):
        fields[VERIFICATION_KEY] = [v for _, v in pairs]
    return fields


COMMIT_VERIFICATION_STATES = ('exists', 'missing', 'operational_failure', 'not_recorded')


def recorded_commit_states(row):
    """One state per commit evidence entry of a row: exists, missing, operational_failure or not_recorded.

    A row written before this field has none, and that stays `not_recorded`: nothing was observed then, so it is neither missing nor failed.
    """
    recorded = row.get(VERIFICATION_KEY)
    states = []
    for index, entry in enumerate(row['evidence']):
        if entry['type'] != 'commit':
            continue
        item = recorded[index] if isinstance(recorded, list) and index < len(recorded) else None
        state = item.get('state') if isinstance(item, dict) else None
        states.append('not_recorded' if state not in COMMIT_CHECKS else
                      'operational_failure' if state in ('timeout', 'error') else state)
    return states


def commit_verification_summary(rows):
    """Counts of recorded commit-evidence verification over `rows`, with the denominator (commit evidence entries)."""
    counts = dict.fromkeys(COMMIT_VERIFICATION_STATES, 0)
    for row in rows:
        for state in recorded_commit_states(row):
            counts[state] += 1
    return {'denominator': sum(counts.values()), **counts,
            'meaning': 'the commit check at close time; not_recorded is a row written before it was kept, neither missing nor failed'}


def check_consistency(outcome, check, repairs, refs):
    checks = [r for r in refs if r['type'] == 'check']
    if check == 'oracle' and not checks:
        raise ValueError('--check oracle requires a check evidence reference')
    if check != 'oracle' and checks:
        raise ValueError('check evidence requires --check oracle')
    if check != 'none' and not refs:
        raise ValueError('--check other than none requires evidence')
    if outcome in ('accepted', 'accepted_after_repair') and any(r['exit_code'] != 0 for r in checks):
        raise ValueError('a nonzero check exit cannot back an accepted outcome')
    if repairs is not None and (type(repairs) is not int or not 0 <= repairs <= 100):
        raise ValueError('--repairs must be an integer 0-100')
    if outcome == 'accepted' and repairs:
        raise ValueError('accepted with repairs > 0 is accepted_after_repair')
    if outcome == 'accepted_after_repair' and repairs == 0:
        raise ValueError('accepted_after_repair needs --repairs >= 1 or unset')


def evidence_summary(refs, check, outcome):
    states = {}
    for ref in refs:
        states[ref['verified_state']] = states.get(ref['verified_state'], 0) + 1
    oracle = [r for r in refs if r['type'] == 'check']
    verified = (check == 'oracle' and outcome in ('accepted', 'accepted_after_repair')
                and all(r['exit_code'] == 0 and r['verified_state'] == 'log_hash_matches' for r in oracle))
    # Only the log file hash is observed. Exit codes are the caller's claim, shown beside it.
    return {'states': states, 'accepted_with_hash_matched_check_log': verified,
            'exit_codes_claimed': [r['exit_code'] for r in oracle]}


def chain_heads(closes):
    """Current close per decision: the close nothing supersedes. Several heads means a broken chain."""
    superseded = {c['supersedes'] for c in closes if c.get('supersedes')}
    heads = {}
    for close in closes:
        if close['close_id'] not in superseded:
            heads.setdefault(close['decision_id'], []).append(close)
    return heads


ROW_KEYS = {
    'close': {'close_id', 'decision_id', 'outcome', 'judged_by', 'check', 'repairs', 'owner_input', 'evidence', 'note',
              'supersedes', 'reason', 'closer_thread', 'recorded_at', 'evidence_summary'},
    'followup': {'followup_id', 'decision_id', 'finding', 'checked_scope', 'evidence', 'close_id', 'observed_at',
                 'lag_seconds_since_close'},
}
ROW_ENUMS = {'close': (('outcome', OUTCOMES), ('judged_by', JUDGED_BY), ('check', CHECKS), ('owner_input', OWNER_INPUT)),
             'followup': (('finding', FINDINGS),)}


def check_verification(row):
    """The optional commit verification list must match `evidence` one to one: an object for commit evidence, null otherwise."""
    if VERIFICATION_KEY not in row:
        return
    recorded = row[VERIFICATION_KEY]
    if not isinstance(recorded, list) or len(recorded) != len(row['evidence']):
        raise ValueError(f'{VERIFICATION_KEY} must be a list with one item per evidence entry')
    for entry, item in zip(row['evidence'], recorded):
        if entry['type'] != 'commit':
            if item is not None:
                raise ValueError(f'{VERIFICATION_KEY} must be null for {entry["type"]} evidence')
            continue
        if not isinstance(item, dict) or set(item) != {'state', 'recorded_at'} or item['state'] not in COMMIT_CHECKS:
            raise ValueError(f'{VERIFICATION_KEY} for commit evidence must be {{state, recorded_at}} with state {", ".join(COMMIT_CHECKS)}')
        try:
            datetime.fromisoformat(item['recorded_at'])
        except (TypeError, ValueError):
            raise ValueError(f'{VERIFICATION_KEY} recorded_at must be an ISO timestamp') from None


def check_row(row):
    """Raise ValueError with a reason when one outcome row is not what this CLI writes."""
    if not isinstance(row, dict):
        raise ValueError('row is not an object')
    kind = row.get('kind')
    if kind not in ROW_KEYS:
        raise ValueError('kind must be close or followup')
    if row.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    missing = sorted(ROW_KEYS[kind] - set(row))
    if missing:
        raise ValueError(f'{kind} row missing {", ".join(missing)}')
    for name, allowed in ROW_ENUMS[kind]:
        if row[name] not in allowed:
            raise ValueError(f'{name} {row[name]!r} is not allowed')
    ids = [f'{kind}_id', 'decision_id'] + (['close_id'] if kind == 'followup' else [])
    if kind == 'close' and row['supersedes'] is not None:
        ids.append('supersedes')
    for name in ids:
        if not isinstance(row[name], str):
            raise ValueError(f'{name} must be a string')
        validate_id(row[name])
    if not isinstance(row['evidence'], list) or not all(
            isinstance(e, dict) and e.get('type') in EVIDENCE_KEYS and isinstance(e.get('verified_state'), str)
            for e in row['evidence']):
        raise ValueError('evidence must be a list of verified evidence objects')
    check_verification(row)
    stamp = 'recorded_at' if kind == 'close' else 'observed_at'
    try:
        datetime.fromisoformat(row[stamp])
    except (TypeError, ValueError):
        raise ValueError(f'{stamp} must be an ISO timestamp') from None
    if kind == 'close':
        summary = row['evidence_summary']
        if not isinstance(summary, dict) or type(summary.get('accepted_with_hash_matched_check_log')) is not bool:
            raise ValueError('evidence_summary.accepted_with_hash_matched_check_log must be true or false')
        if row['closer_thread'] is not None and not isinstance(row['closer_thread'], str):
            raise ValueError('closer_thread must be a string or null')
    elif not isinstance(row['lag_seconds_since_close'], (int, float)) or isinstance(row['lag_seconds_since_close'], bool):
        raise ValueError('lag_seconds_since_close must be a number')


def validate_outcome_rows(numbered, name='outcomes.jsonl'):
    """Fail closed on the first bad row or broken lineage. Nothing is skipped, repaired or dropped."""
    seen = {}
    superseded = set()
    closed = set()
    for number, row in numbered:
        def fail(reason):
            raise ValueError(f'{name} line {number}: {reason}')
        try:
            check_row(row)
        except ValueError as error:
            fail(str(error))
        own = row.get('close_id') if row['kind'] == 'close' else row['followup_id']
        if own in seen:
            fail(f'id {own!r} already used on line {seen[own]["line"]}')
        decision = row['decision_id']
        if row['kind'] == 'close':
            target = row['supersedes']
            if target is None and decision in closed:
                fail('decision already has a close; this row must supersede the current one')
            if target is not None:
                old = seen.get(target)
                if not old or old['kind'] != 'close' or old['decision'] != decision:
                    fail(f'supersedes {target!r}, which is not an earlier close of this decision')
                if target in superseded:
                    fail(f'supersedes {target!r}, which is already superseded')
                superseded.add(target)
            closed.add(decision)
        else:
            old = seen.get(row['close_id'])
            if not old or old['kind'] != 'close' or old['decision'] != decision:
                fail(f'close_id {row["close_id"]!r} is not an earlier close of this decision')
        seen[own] = {'line': number, 'kind': row['kind'], 'decision': decision}


def read_outcomes(path):
    """All outcome rows, validated. A missing file means no rows."""
    try:
        with open(path) as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            numbered = numbered_rows(stream, os.path.basename(path))
    except FileNotFoundError:
        return []
    validate_outcome_rows(numbered, os.path.basename(path))
    return [row for _, row in numbered]


def refuse_shared_ledger(receipts, other, flag='--outcomes', first='--receipts'):
    """Two ledgers (`first` and `flag` name them in the error) must be different files, however they are named."""
    receipts, other = os.fspath(receipts), os.fspath(other)
    same = os.path.realpath(receipts) == os.path.realpath(other)
    if not same:
        try:
            a, b = os.stat(receipts), os.stat(other)
            same = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
        except OSError:
            pass  # one of them does not exist yet, so they cannot be one file
    if same:
        rows = {'--outcomes': 'outcome rows', '--observations': 'observation rows'}.get(flag, 'directive rows')
        raise ValueError(f'{first} and {flag} name the same file; {rows} must never enter {"decisions.jsonl" if first == "--receipts" else "another ledger"}')


def split_rows(rows):
    return [r for r in rows if r.get('kind') == 'close'], [r for r in rows if r.get('kind') == 'followup']


def write_close(args, receipt):
    close_id = args.close_id
    if not close_id:
        raise ValueError('--close-id is required (explicit IDs keep retries unambiguous)')
    validate_id(close_id)
    decision_id = args.op
    if args.supersedes:
        validate_id(args.supersedes)
        if not args.reason:
            raise ValueError('--supersedes requires --reason')
    elif args.reason:
        raise ValueError('--reason applies only to --supersedes')
    outcome = choice(args.outcome, OUTCOMES, '--outcome')
    judged_by = choice(args.judged_by, JUDGED_BY, '--judged-by')
    check = choice(args.check, CHECKS, '--check')
    owner_input = choice(args.owner_input or 'unknown', OWNER_INPUT, '--owner-input')
    reason = text_field(args.reason, '--reason')
    note = text_field(args.note, '--note')
    closer_thread = text_field(args.closer_thread, '--closer-thread')
    if len(args.evidence) > MAX_EVIDENCE:
        raise ValueError(f'at most {MAX_EVIDENCE} evidence references')
    refs = [parse_evidence(raw) for raw in args.evidence]
    check_consistency(outcome, check, args.repairs, refs)
    intent = {'decision_id': decision_id, 'outcome': outcome, 'judged_by': judged_by, 'check': check,
              'repairs': args.repairs, 'owner_input': owner_input, 'evidence': refs, 'note': note,
              'supersedes': args.supersedes, 'reason': reason, 'closer_thread': closer_thread}
    with locked_private_rows(args.outcomes, validate_outcome_rows) as (stream, rows):
        closes, _ = split_rows(rows)
        for old in closes:
            if old['close_id'] == close_id:
                stored = {k: old[k] for k in intent if k != 'evidence'} | {'evidence': [{k: e[k] for k in e if k not in ('verified_state', 'verified_at', 'verification_scope')} for e in old['evidence']]}
                if stored != intent:
                    raise ValueError('close ID already recorded with changed inputs; use --supersedes with a new ID')
                return old | {'replayed': True}
        if any(r.get('close_id') == close_id or r.get('followup_id') == close_id for r in rows):
            raise ValueError('ID already used by another outcome record')
        heads = chain_heads([c for c in closes if c['decision_id'] == decision_id]).get(decision_id, [])
        if len(heads) > 1:
            raise ValueError('close chain for this decision is broken (several current closes)')
        if args.supersedes:
            if not heads or heads[0]['close_id'] != args.supersedes:
                raise ValueError('--supersedes must name the current close of this decision')
        elif heads:
            raise ValueError('decision already closed; correct it with --supersedes CURRENT_CLOSE_ID and --reason')
        record = {'schema_version': 1, 'kind': 'close', 'close_id': close_id, **intent,
                  **verify_all(refs), 'recorded_at': now(), 'cli_version': CLI_VERSION,
                  'policy_sha256': receipt.get('policy_sha256'), 'scope': SCOPE, 'assertion': 'caller_claim'}
        record['evidence_summary'] = evidence_summary(record['evidence'], check, outcome)
        append_row(stream, record)
    return record


def write_followup(args, receipt):
    followup_id = args.followup_id
    if not followup_id:
        raise ValueError('--followup-id is required')
    validate_id(followup_id)
    decision_id = args.op
    finding = choice(args.finding, FINDINGS, '--finding')
    scope = text_field(args.checked_scope, '--checked-scope', required=True)
    if len(args.evidence) > MAX_EVIDENCE:
        raise ValueError(f'at most {MAX_EVIDENCE} evidence references')
    refs = [parse_evidence(raw) for raw in args.evidence]
    if finding != 'no_rework_found' and not refs:
        raise ValueError(f'--finding {finding} requires evidence')
    intent = {'decision_id': decision_id, 'finding': finding, 'checked_scope': scope, 'evidence': refs}
    with locked_private_rows(args.outcomes, validate_outcome_rows) as (stream, rows):
        closes, followups = split_rows(rows)
        for old in followups:
            if old['followup_id'] == followup_id:
                stored = {k: old[k] for k in intent if k != 'evidence'} | {'evidence': [{k: e[k] for k in e if k not in ('verified_state', 'verified_at', 'verification_scope')} for e in old['evidence']]}
                if stored != intent:
                    raise ValueError('followup ID already recorded with changed inputs; use a new ID')
                return old | {'replayed': True}
        if any(r.get('close_id') == followup_id or r.get('followup_id') == followup_id for r in rows):
            raise ValueError('ID already used by another outcome record')
        heads = chain_heads([c for c in closes if c['decision_id'] == decision_id]).get(decision_id, [])
        if len(heads) != 1:
            raise ValueError('followup requires exactly one current close; close the decision first')
        observed_at = now()
        lag = (datetime.fromisoformat(observed_at) - datetime.fromisoformat(heads[0]['recorded_at'])).total_seconds()
        record = {'schema_version': 1, 'kind': 'followup', 'followup_id': followup_id, **intent,
                  **verify_all(refs), 'close_id': heads[0]['close_id'],
                  'observed_at': observed_at, 'lag_seconds_since_close': lag, 'cli_version': CLI_VERSION,
                  'policy_sha256': receipt.get('policy_sha256'), 'scope': SCOPE,
                  'semantics': 'sampled observation; absence of a followup means not checked, never no rework'}
        append_row(stream, record)
    return record


def execute(args):
    """Run close or followup for a recorded decision."""
    validate_id(args.op or '')
    receipt = find_receipt(args.receipts, args.op)
    if receipt is None:
        raise ValueError('decision receipt not found; outcomes attach only to recorded decisions')
    return write_close(args, receipt) if args.command == 'close' else write_followup(args, receipt)
