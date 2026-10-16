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

from decision_receipts import (append_row, find_receipt, locked_private_rows, numbered_rows, validate_id,
                               read_receipts, metadata_object, metadata_id, validate_task_link)

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


def op_proof_assessment(op_id, proof_class, purpose=None):
    """Describe only the declared op/proof relationship, without I/O or judging adequacy.

    `implement.oracle-bounded` normally declares a change whose whole standard a deterministic oracle
    accepts or rejects. Its one bounded exception is the `test-design` purpose: a test suite's
    acceptance standard is a checker's judgment, so an explicit oracle or judged declaration is
    consistent there. The purpose is used to decide the state but is not part of the description,
    which stays a function of the recorded op and proof class.
    """
    if not isinstance(op_id, str) or not op_id.startswith('implement.'):
        state = 'not_applicable'
    elif proof_class is None:
        state = 'missing_proof'
    elif op_id == 'implement.standard' and proof_class == 'oracle':
        state = 'standard_oracle_review_required'
    elif op_id == 'implement.oracle-bounded' and purpose == 'test-design':
        state = 'declared_consistent'
    elif op_id == 'implement.oracle-bounded' and proof_class != 'oracle':
        state = 'oracle_bounded_requires_oracle'
    else:
        state = 'declared_consistent'
    return {'state': state, 'op': op_id, 'proof_class': proof_class,
            'meaning': 'declared op/proof relationship only; not proof adequacy, wrongdoing or acceptance'}


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
        for block in iter(lambda: stream.read(min(1 << 20, MAX_HASH_BYTES - total + 1)), b''):
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
        rows = {'--outcomes': 'outcome rows', '--observations': 'observation rows', '--quota-state': 'quota substitution state'}.get(flag, 'directive rows')
        raise ValueError(f'{first} and {flag} name the same file; {rows} must never enter {"decisions.jsonl" if first == "--receipts" else "another ledger"}')


def split_rows(rows):
    return [r for r in rows if r.get('kind') == 'close'], [r for r in rows if r.get('kind') == 'followup']



def validate_task_result(value, refs):
    keys = {'v', 'consumer_task_id', 'supersedes_task_close_id', 'standard_evidence'}
    if not isinstance(value, dict) or set(value) != keys or type(value.get('v')) is not int or value['v'] != 1:
        raise ValueError('task_result requires exactly v:1, consumer_task_id, supersedes_task_close_id and standard_evidence')
    metadata_id(value['consumer_task_id'], 'consumer_task_id')
    if value['supersedes_task_close_id'] is not None:
        metadata_id(value['supersedes_task_close_id'], 'supersedes_task_close_id')
    indices = value['standard_evidence']
    if (not isinstance(indices, list) or not indices or len(indices) > MAX_EVIDENCE
            or any(type(index) is not int or not 0 <= index < len(refs) for index in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError('standard_evidence must contain unique indices into the existing evidence array')
    return value


def result_matches_receipt(value, receipt):
    link = validate_task_link(receipt.get('task_link'))
    if link['consumer_task_id'] != value['consumer_task_id']:
        raise ValueError('task_result requires the receipt explicitly linked to the same consumer task')


def task_projection(receipts, rows):
    """Conservative whole-result claims, by links, never timestamps; legacy decision semantics are untouched.

    An untagged normal successor invalidates the declaration and becomes its recoverable head.
    A fork, malformed metadata, missing receipt, or broken task link has no accepted task.
    No lifecycle event, ordinary checker close, or token-price estimate is task acceptance/cost.
    """
    tasks, receipt_map, bad, global_bad = {}, {}, set(), False

    def ensure(task):
        if isinstance(task, str):
            try:
                metadata_id(task, 'consumer_task_id')
            except ValueError:
                return None
            tasks.setdefault(task, [])
            return task
        return None

    for receipt in receipts:
        decision = receipt.get('decision_id')
        if decision in receipt_map:
            global_bad = True
        receipt_map[decision] = receipt
        if 'task_link' in receipt:
            raw = receipt['task_link']
            task = ensure(raw.get('consumer_task_id')) if isinstance(raw, dict) else None
            try:
                validate_task_link(raw)
            except ValueError:
                if task:
                    bad.add(task)
                else:
                    global_bad = True

    closes = [row for row in rows if row.get('kind') == 'close']
    by_id, association, valid = {}, {}, {}
    for row in closes:
        close_id = row.get('close_id')
        if not isinstance(close_id, str) or close_id in by_id:
            global_bad = True
            continue
        by_id[close_id] = row
        if 'task_result' not in row:
            continue
        raw = row['task_result']
        task = ensure(raw.get('consumer_task_id')) if isinstance(raw, dict) else None
        receipt = receipt_map.get(row.get('decision_id'), {})
        linked = receipt.get('task_link')
        linked_task = ensure(linked.get('consumer_task_id')) if isinstance(linked, dict) else None
        if task:
            association[close_id] = task
        elif linked_task:
            association[close_id] = linked_task
        try:
            validate_task_result(raw, row.get('evidence', []))
            result_matches_receipt(raw, receipt)
        except (ValueError, TypeError):
            bad.update(t for t in (task, linked_task) if t)
            if not task and not linked_task:
                global_bad = True
        else:
            valid[close_id] = raw
    # Link traversal is independent of ledger/list order and of wall-clock timestamps.
    changed = True
    while changed:
        changed = False
        for close_id, row in by_id.items():
            if close_id not in association and 'task_result' not in row and row.get('supersedes') in association:
                association[close_id] = association[row['supersedes']]
                changed = True
    for close_id, task in association.items():
        tasks[task].append(close_id)

    try:
        validate_outcome_rows(list(enumerate(rows, 1)))
    except (ValueError, TypeError, KeyError):
        # Do not repair or silently omit broken legacy history.
        global_bad = True
    result = {}
    for task, nodes in tasks.items():
        parents, children = {}, {node: [] for node in nodes}
        broken = global_bad or task in bad
        for node in nodes:
            row = by_id[node]
            parent = (valid[node]['supersedes_task_close_id'] if node in valid else row.get('supersedes'))
            parents[node] = parent
            if parent is not None:
                if parent not in children or parent == node:
                    broken = True
                else:
                    children[parent].append(node)
        roots = [node for node in nodes if parents[node] is None]
        heads = [node for node in nodes if not children[node]]
        if nodes and (len(roots) != 1 or len(heads) != 1 or any(len(kids) > 1 for kids in children.values())):
            broken = True
        reached, pending = set(), list(roots)
        while pending:
            node = pending.pop()
            if node in reached:
                broken = True
                continue
            reached.add(node)
            pending.extend(children[node])
        if reached != set(nodes):
            broken = True
        head = heads[0] if nodes and not broken else None
        declared = head in valid if head else False
        terminal = by_id[head]['outcome'] if declared else None
        result[task] = {'state': 'declared' if declared else 'unknown',
                        'current_close_id': head, 'terminal_judgment': terminal,
                        'accepted_count': int(declared and terminal in ('accepted', 'accepted_after_repair')),
                        'total_cost': None,
                        'reason': 'conflicting_or_malformed_metadata' if broken else
                                  'explicit_whole_result_claim' if declared else
                                  'untagged_successor' if head else 'no_task_declaration'}
    return {'tasks': result, 'accepted_count': sum(item['accepted_count'] for item in result.values()),
            'total_cost': None, 'scope': 'explicit whole-consumer-result caller claims; incomplete cost coverage'}


def validate_task_append(value, receipts, rows):
    projection = task_projection(receipts, rows)
    state = projection['tasks'].get(value['consumer_task_id'])
    if state and state['reason'] == 'conflicting_or_malformed_metadata':
        raise ValueError('task history has conflicting or malformed metadata')
    head = state['current_close_id'] if state else None
    if value['supersedes_task_close_id'] != head:
        raise ValueError('supersedes_task_close_id must name the unique current close of this task (null for its first declaration)')


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
    raw_task_result = getattr(args, 'task_result', None)
    task_result = (validate_task_result(metadata_object(raw_task_result, '--task-result', 2048), refs)
                   if raw_task_result is not None else None)
    if task_result is not None:
        result_matches_receipt(task_result, receipt)
    intent = {'decision_id': decision_id, 'outcome': outcome, 'judged_by': judged_by, 'check': check,
              'repairs': args.repairs, 'owner_input': owner_input, 'evidence': refs, 'note': note,
              'supersedes': args.supersedes, 'reason': reason, 'closer_thread': closer_thread}
    with locked_private_rows(args.outcomes, validate_outcome_rows) as (stream, rows):
        closes, _ = split_rows(rows)
        for old in closes:
            if old['close_id'] == close_id:
                stored = {k: old[k] for k in intent if k != 'evidence'} | {'evidence': [{k: e[k] for k in e if k not in ('verified_state', 'verified_at', 'verification_scope')} for e in old['evidence']]}
                if stored != intent or (task_result is not None and old.get('task_result') != task_result):
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
        # Free-route close obligations gate a *new* close only, so replaying a historical close row
        # stays byte-identical. The trial requires an explicit repairs count on every free-route close
        # and, for a judged proof class, a named independent judge rather than a maker self-check.
        free_route = is_free_route_receipt(receipt)
        proof_class = (receipt.get('launch_facts') or {}).get('proof_class')
        if free_route and args.repairs is None:
            raise ValueError('a free-route close requires an explicit --repairs count '
                             '(0 is a value; an absent count is unknown and cannot settle a trial task)')
        if free_route and proof_class == 'judged' and not (check == 'independent_judged' and judged_by == 'independent'):
            raise ValueError('a judged free-route close requires --check independent_judged --judged-by independent: '
                             'the trial requires an independent checker as the judge, not the maker')
        receipt_rows = read_receipts(args.receipts) if (task_result is not None or free_route) else []
        if task_result is not None:
            matches = [row for row in receipt_rows if row.get('decision_id') == decision_id]
            if len(matches) != 1:
                raise ValueError('task result requires exactly one matching receipt')
            result_matches_receipt(task_result, matches[0])
            validate_task_append(task_result, receipt_rows, rows)
        record = {'schema_version': 1, 'kind': 'close', 'close_id': close_id, **intent,
                  **verify_all(refs), 'recorded_at': now(), 'cli_version': CLI_VERSION,
                  'policy_sha256': receipt.get('policy_sha256'), 'scope': SCOPE, 'assertion': 'caller_claim'}
        if task_result is not None:
            record['task_result'] = task_result
        record['evidence_summary'] = evidence_summary(record['evidence'], check, outcome)
        # Optional top-level metadata only: old readers compare evidence entries on replay.
        # Use the receipt, never current flags/pack, and do not retrofit a replayed close.
        assessment = op_proof_assessment((receipt.get('request') or {}).get('op'),
                                         (receipt.get('launch_facts') or {}).get('proof_class'),
                                         (receipt.get('launch_facts') or {}).get('purpose'))
        if assessment['state'] != 'not_applicable':
            record['op_proof_assessment'] = assessment
        # The scoped completion state is written on every free-route close, and a whole-task
        # completion claim (--task-result) is refused while the task still owes an obligation.
        # Only this consumer task is inspected, so unrelated receipts and in-flight work are untouched.
        link = receipt.get('task_link')
        task = link.get('consumer_task_id') if isinstance(link, dict) else None
        if free_route and isinstance(task, str):
            completion = free_route_completion(receipt_rows, list(rows) + [record], task)
            if task_result is not None and completion['state'] != 'complete':
                raise ValueError('free-route completion claim refused: consumer task ' + task + ' still owes '
                                 + json.dumps(completion['open'], sort_keys=True)
                                 + '; close each obligation, or record a stage close without --task-result')
            record['free_route'] = {'consumer_task_id': task, 'completion': completion}
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


# The close outcomes that count an owner-authorized trial task as settled. `abandoned_by_choice`,
# `superseded` and `unknown` are not task outcomes, and a rejected close is a settled decision, so it
# counts here as a measured negative result.
FREE_ROUTE_OUTCOMES = ('accepted', 'accepted_after_repair', 'rejected')
# The state `apply_experimental_route_policy` stores on a receipt that the free route was allowed to
# launch. It is the receipt's own evidence of a free-route decision, so identity survives a later
# revocation, expiry or policy edit.
FREE_ROUTE_POLICY_STATE = 'allowed_public_experimental'


def is_free_route_receipt(receipt):
    """True when this receipt records a free-route launch, by its own stored data policy."""
    return isinstance(receipt, dict) and (receipt.get('data_policy') or {}).get('state') == FREE_ROUTE_POLICY_STATE


def free_route_decisions(receipts):
    """Free-route decision summaries, identified by the state stored inside each receipt."""
    found = []
    for receipt in receipts:
        if not is_free_route_receipt(receipt):
            continue
        state = receipt.get('data_policy') or {}
        link = receipt.get('task_link')
        task = link.get('consumer_task_id') if isinstance(link, dict) else None
        found.append({'decision_id': receipt.get('decision_id'), 'model': state.get('route'),
                      'account': state.get('account'), 'op': (receipt.get('request') or {}).get('op'),
                      'effort': ((receipt.get('selection') or {}).get('arm') or {}).get('effort'),
                      'purpose': (receipt.get('launch_facts') or {}).get('purpose'),
                      'proof_class': (receipt.get('launch_facts') or {}).get('proof_class'),
                      'task_link': link if isinstance(link, dict) else None,
                      'consumer_task_id': task if isinstance(task, str) else None,
                      'recorded_at': receipt.get('recorded_at')})
    return found


def close_obligations(decision, rows):
    """What one free-route decision's close chain still owes: [] when its close is complete.

    A decision owes its close, an explicit `repairs` count on that close, and -- when it launched
    with a judged proof class -- a close that names an independent judge. The chain is followed by
    supersede links, never by timestamp, so a revision cannot hide an unmet obligation.
    """
    closes, _ = split_rows(rows)
    heads = chain_heads([row for row in closes if row.get('decision_id') == decision.get('decision_id')])
    current = heads.get(decision.get('decision_id'), [])
    if not current:
        return ['close']
    if len(current) > 1:
        return ['unambiguous_close']
    head = current[0]
    missing = []
    if head.get('repairs') is None:
        missing.append('repairs')
    if (decision.get('proof_class') == 'judged'
            and not (head.get('check') == 'independent_judged' and head.get('judged_by') == 'independent')):
        missing.append('independent_judge_check')
    return missing


def free_route_completion(receipts, rows, consumer_task_id):
    """The completion state of one consumer task's free-route obligations, scoped to that task.

    This is the enforcement boundary a completion claim is checked against: `complete` only when
    every free-route decision of this consumer task is closed with an explicit repairs count and,
    for a judged proof class, an independent judge. Unrelated historical receipts and other tasks'
    in-flight work are outside the scope and are never touched.
    """
    decisions = [d for d in free_route_decisions(receipts) if d['consumer_task_id'] == consumer_task_id]
    open_items = [{'decision_id': d['decision_id'], 'missing': close_obligations(d, rows)} for d in decisions]
    open_items = [item for item in open_items if item['missing']]
    return {'scope': 'consumer_task', 'consumer_task_id': consumer_task_id,
            'state': 'complete' if not open_items else 'incomplete',
            'checked_decisions': len(decisions), 'open': open_items,
            'note': 'one consumer task only; unrelated receipts and other in-flight tasks are untouched'}


def free_route_pending(receipts, rows, consumer_task_id=None):
    """Free-route decisions whose required close, repairs, judge, or rework observation is outstanding.

    `close_obligations` is the hard boundary. A missing rework observation is listed here too, but it
    is soft: absence is reported unknown, never no rework, and it never blocks a close.
    """
    closes, followups = split_rows(rows)
    heads = chain_heads(closes)
    observed = {f['close_id'] for f in followups}
    entries = []
    for decision in free_route_decisions(receipts):
        if consumer_task_id is not None and decision['consumer_task_id'] != consumer_task_id:
            continue
        current = heads.get(decision['decision_id'], [])
        missing = close_obligations(decision, rows)
        if len(current) == 1:
            if current[0]['close_id'] not in observed:
                missing = missing + ['rework_observation']
        elif not current:
            missing = missing + ['rework_observation']
        if not missing:
            continue
        entries.append({'decision_id': decision['decision_id'], 'op': decision['op'], 'effort': decision['effort'],
                        'purpose': decision['purpose'], 'consumer_task_id': decision['consumer_task_id'],
                        'missing': missing})
    return {'count': len(entries), 'decisions': entries}


def free_route_trial_retro(receipts, rows, policy, threshold=None):
    """The deterministic, idempotent trial retro and its threshold signal.

    Counting is by `task_projection`, so a superseded revision, a checker close or a repair receipt
    never adds a task. Free-route receipts without a task link are a separate legacy decision-level
    cohort and never enter the task count. Missing later-rework observation is reported `unknown`,
    never zero, and a task with conflicting or malformed metadata is reported `broken` rather than
    merged with an unclosed task. The result is a pure function of the two ledgers and the policy,
    so repeated calls are identical.
    """
    authority = (next(iter((policy or {}).get('experimental_free_routes') or []), {}) or {}).get('selection') or {}
    limit = threshold or authority.get('threshold') or 50
    decisions = free_route_decisions(receipts)
    projection = task_projection(receipts, rows)
    closes, followups = split_rows(rows)
    closes_by_id = {close['close_id']: close for close in closes if isinstance(close.get('close_id'), str)}
    observed = {f['close_id'] for f in followups}
    tasks, legacy = {}, []
    for decision in decisions:
        if decision['consumer_task_id'] is None:
            legacy.append(decision['decision_id'])
        else:
            tasks.setdefault(decision['consumer_task_id'], []).append(decision)
    paid_by_task = {}
    for receipt in receipts:
        if (receipt.get('data_policy') or {}).get('state') == 'allowed_public_experimental':
            continue
        link = receipt.get('task_link')
        if isinstance(link, dict) and isinstance(link.get('consumer_task_id'), str):
            paid_by_task.setdefault(link['consumer_task_id'], []).append(receipt.get('decision_id'))
    counts = {name: 0 for name in FREE_ROUTE_OUTCOMES}
    by_op_effort, counted, matched, unmatched = {}, [], [], []
    other_closed = unclosed = broken = repairs_missing = 0
    rework = {'observed': 0, 'unknown': 0}
    for task, members in sorted(tasks.items()):
        key = ' + '.join(sorted({f'{m["op"]}@{m["effort"]}' for m in members}))
        bucket = by_op_effort.setdefault(key, {'tasks': 0, **{name: 0 for name in FREE_ROUTE_OUTCOMES},
                                               'other_closed_tasks': 0, 'unclosed_tasks': 0, 'broken_tasks': 0})
        state = projection['tasks'].get(task)
        if not state or state['state'] != 'declared':
            # A fork, malformed metadata or missing close is never a counted task, and never merged
            # with the other: a caller needs to know which obligation is unmet.
            if state and state.get('reason') == 'conflicting_or_malformed_metadata':
                broken += 1
                bucket['broken_tasks'] += 1
            else:
                unclosed += 1
                bucket['unclosed_tasks'] += 1
            continue
        bucket['tasks'] += 1
        terminal = state['terminal_judgment']
        if terminal not in counts:
            other_closed += 1
            bucket['other_closed_tasks'] += 1
            continue
        counts[terminal] += 1
        bucket[terminal] += 1
        head = closes_by_id.get(state['current_close_id']) or {}
        rework['observed' if head.get('close_id') in observed else 'unknown'] += 1
        repairs_missing += 1 if head.get('repairs') is None else 0
        entry = {'consumer_task_id': task, 'terminal_judgment': terminal, 'current_close_id': state['current_close_id'],
                 'decisions': sorted(m['decision_id'] for m in members), 'op_effort': key}
        counted.append(entry)
        (matched if paid_by_task.get(task) else unmatched).append(entry)
    unique_closed = sum(counts.values())
    return {'scope': 'unique consumer tasks with a declared whole-result close of a free-route receipt',
            'authorization': authority.get('authorization'), 'authority_note': authority.get('authority_note'),
            'benchmark': authority.get('benchmark'), 'threshold': limit,
            'unique_closed_tasks': unique_closed, 'due': unique_closed >= limit,
            **counts,
            'other_closed_tasks': other_closed, 'unclosed_tasks': unclosed, 'broken_tasks': broken,
            'rework_observed': rework['observed'], 'rework_unknown': rework['unknown'],
            'repairs_missing': repairs_missing,
            'legacy_decision_level': {'decisions': len(legacy),
                                      'cohort': 'free-route receipts without a task link; excluded from the task count'},
            'by_op_effort': by_op_effort, 'counted_tasks': counted,
            'comparator': {'matched_task_ids': [e['consumer_task_id'] for e in matched],
                           'unmatched_task_ids': [e['consumer_task_id'] for e in unmatched],
                           'note': 'matched means a paid receipt shares the consumer task; the frozen 2026-10-15 '
                                   'benchmark compared public docs lookup against Haiku, not against the paid cheap arm'},
            'enforcement_boundary': {
                'close': 'a free-route close is refused without an explicit --repairs count and, for a judged '
                         'proof class, --check independent_judged --judged-by independent',
                'completion': 'a free-route completion claim (--task-result) is refused while any free-route '
                              'decision of that consumer task owes a close, a repairs count or an independent judge',
                'scope': 'one consumer task; unrelated historical receipts and other in-flight tasks are untouched',
                'authority': 'the router authorizes the launch and the outcome ledger proves the close; neither '
                             'one alone settles a task'},
            'scheduler_trigger': {
                'signal': 'free_route_trial_due',
                'due_when': f'unique_closed_tasks >= threshold ({limit})',
                'contract': 'a pure function of the receipt ledger, the outcome ledger and the policy: identical '
                            'inputs give identical output, so a scheduler may run `model-policy-ops calibrate` on any '
                            'cadence and trigger on due=true. This tool schedules nothing itself and holds no secret.'},
            'signal': 'deterministic and idempotent: a pure function of the receipt ledger, the outcome ledger and '
                      'the policy, keyed by unique consumer tasks'}


def free_route_trial_due(receipts, rows, policy, threshold=None):
    """The single deterministic signal a scheduler may trigger on: `due` once enough tasks are closed.

    It is a pure function of the two ledgers and the pack, so any run at any cadence agrees. The tool
    starts no recurring job and holds no secret; the caller decides whether to act on `due`.
    """
    retro = free_route_trial_retro(receipts, rows, policy, threshold=threshold)
    return {'signal': retro['scheduler_trigger']['signal'], 'due': retro['due'],
            'unique_closed_tasks': retro['unique_closed_tasks'], 'threshold': retro['threshold'],
            'idempotent': True, 'contract': retro['scheduler_trigger']['contract']}
