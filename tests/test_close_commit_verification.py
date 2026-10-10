"""Close records what git said about a commit, apart from the legacy verified_state, and older CLIs still read the rows."""
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

from test_policy_decisions import REPO, record, run, setup  # noqa: F401  (setup is a fixture)
from test_policy_outcomes import GOOD_CLOSE, audit_export, close, follow, git_repo, outcomes_path  # noqa: F401

sys.path.insert(0, str(REPO / 'tools/model-policy-ops/lib'))
import outcomes as outcomes_lib  # noqa: E402

BASELINE = '0979936'
KEY = 'evidence_verification'


def commit_ref(repo, sha_):
    return json.dumps({'type': 'commit', 'repo': str(repo), 'sha': sha_})


def stub_git(tmp_path, monkeypatch, body):
    """A `git` first on PATH that fails the way a broken installation or a killed process does."""
    bin_dir = tmp_path / 'stubbin'
    bin_dir.mkdir()
    stub = bin_dir / 'git'
    stub.write_text('#!/bin/sh\n' + body + '\n')
    stub.chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')


@pytest.fixture(scope='module')
def old_tool(tmp_path_factory):
    """The CLI as of the baseline commit, extracted from git history. Skipped only when history lacks the commit (shallow clone)."""
    probe = subprocess.run(['git', '-C', REPO, 'cat-file', '-e', f'{BASELINE}^{{commit}}'], capture_output=True)
    if probe.returncode != 0:
        pytest.skip(f'{BASELINE} is not in this clone; fetch full history to run the old-reader checks')
    tree = tmp_path_factory.mktemp('baseline')
    archive = subprocess.run(['git', '-C', REPO, 'archive', BASELINE, 'tools/model-policy-ops'], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tree, filter='data')
    return tree / 'tools/model-policy-ops/model-policy-ops'


def run_with(tool, *args):
    result = subprocess.run([str(tool), *map(str, args)], capture_output=True, text=True)
    return result, json.loads(result.stdout) if result.returncode == 0 and result.stdout.startswith('{') else None


@pytest.mark.parametrize('body,why', [('exit 3', 'non-zero exit'), ('kill -KILL $$', 'killed by a signal')])
def test_operational_git_failure_is_not_recorded_as_missing(setup, tmp_path, monkeypatch, git_repo, body, why):
    record(setup)
    repo, head = git_repo
    stub_git(tmp_path, monkeypatch, body)
    result, row = close(setup, 'one', 'c1', '--evidence', commit_ref(repo, head))
    assert result.returncode == 0, result.stderr
    # The legacy field keeps its two values and meaning; the new field says git could not answer.
    assert row['evidence'][0]['verified_state'] == 'unverified_missing', why
    assert row[KEY] == [{'state': 'error', 'recorded_at': row[KEY][0]['recorded_at']}]
    assert row[KEY][0]['recorded_at'] == row['evidence'][0]['verified_at']


def test_commit_timeout_records_timeout_and_keeps_the_legacy_value(monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(outcomes_lib.subprocess, 'run', hang)
    entry, verification = outcomes_lib.verify_evidence({'type': 'commit', 'repo': '/r', 'sha': 'a' * 40})
    assert entry['verified_state'] == 'unverified_missing' and verification['state'] == 'timeout'


def test_true_absence_and_presence_are_recorded_apart(setup, git_repo):
    record(setup)
    repo, head = git_repo
    result, row = close(setup, 'one', 'c1', '--evidence', commit_ref(repo, 'a' * 40), '--evidence', commit_ref(repo, head),
                        '--evidence', json.dumps({'type': 'pr', 'repo': 'owner/name', 'number': 1}))
    assert result.returncode == 0, result.stderr
    assert [e['verified_state'] for e in row['evidence']] == ['unverified_missing', 'exists', 'claim_only']
    assert [v and v['state'] for v in row[KEY]] == ['missing', 'exists', None]


def test_rows_without_commit_evidence_have_no_new_key(setup):
    record(setup)
    result, row = close(setup, 'one', 'c1')
    assert result.returncode == 0 and KEY not in row


def test_followup_records_commit_verification_too(setup, git_repo):
    record(setup)
    repo, head = git_repo
    assert close(setup)[0].returncode == 0
    result, row = follow(setup, 'one', 'f1', '--evidence', commit_ref(repo, head))
    assert result.returncode == 0, result.stderr
    assert row[KEY][0]['state'] == 'exists'


def test_replay_returns_the_original_verification(setup, tmp_path, monkeypatch, git_repo):
    record(setup)
    repo, head = git_repo
    _, first = close(setup, 'one', 'c1', '--evidence', commit_ref(repo, head))
    stub_git(tmp_path, monkeypatch, 'exit 3')
    result, again = close(setup, 'one', 'c1', '--evidence', commit_ref(repo, head))
    assert result.returncode == 0 and again == first | {'replayed': True}


@pytest.mark.parametrize('change,reason', [
    ({KEY: 'x'}, 'one item per evidence entry'),
    ({KEY: []}, 'one item per evidence entry'),
    ({KEY: [None]}, 'must be {state, recorded_at}'),
    ({KEY: [{'state': 'gone', 'recorded_at': '2026-10-08T00:00:00+00:00'}]}, 'must be {state, recorded_at}'),
    ({KEY: [{'state': 'exists', 'recorded_at': 'yesterday'}]}, 'ISO timestamp'),
    ({KEY: [{'state': 'exists', 'recorded_at': '2026-10-08T00:00:00+00:00'}], 'evidence': [{'type': 'pr', 'verified_state': 'claim_only'}]}, 'must be null'),
])
def test_malformed_verification_fails_closed(setup, tmp_path, change, reason):
    record(setup)
    evidence = [{'type': 'commit', 'repo': '/r', 'sha': 'a' * 40, 'verified_state': 'exists'}]
    path = outcomes_path(setup)
    path.write_text(json.dumps(GOOD_CLOSE | {'evidence': evidence} | change) + '\n')
    path.chmod(0o600)
    (tmp_path / 'e.json').write_text('{"schema_version":1,"delegations":[]}')
    result, _ = run('audit', '--export', tmp_path / 'e.json', '--receipts', setup[3])
    assert result.returncode != 0 and reason in result.stderr, result.stderr


def commit_entry(state):
    return {'type': 'commit', 'repo': '/nowhere', 'sha': 'a' * 40, 'verified_state': state, 'verified_at': '2026-10-08T00:00:00+00:00',
            'verification_scope': outcomes_lib.VERIFICATION_SCOPE}


def seed(setup, evidence, extra=None):
    record(setup)
    path = outcomes_path(setup)
    path.write_text(json.dumps(GOOD_CLOSE | {'evidence': evidence} | (extra or {})) + '\n')
    path.chmod(0o600)


def at(state):
    return {'state': state, 'recorded_at': '2026-10-08T00:00:00+00:00'}


def test_legacy_rows_are_not_recorded_in_audit_calibrate_and_observe(setup, tmp_path):
    seed(setup, [commit_entry('unverified_missing'), commit_entry('exists')])
    result, audit = audit_export(setup, tmp_path)
    assert result.returncode == 0, result.stderr
    summary = audit['outcomes']['commit_evidence_verification']
    assert (summary['denominator'], summary['not_recorded'], summary['missing'], summary['operational_failure'], summary['exists']) == (2, 2, 0, 0, 0)
    assert 'accepted_with_hash_matched_check_log' in audit['outcomes']
    _, available, quota, receipts = setup
    result, calibrate = run('calibrate', '--available', available, '--quota', quota, '--receipts', receipts)
    assert result.returncode == 0, result.stderr
    assert calibrate['commit_evidence_verification']['not_recorded'] == 2 and calibrate['commit_evidence_verification']['denominator'] == 2
    result, observed = run('observe', '--receipts', receipts)
    assert result.returncode == 0, result.stderr
    assert observed['recorded_commit_verification']['not_recorded'] == 2


def test_recorded_rows_are_counted(setup, tmp_path):
    seed(setup, [commit_entry('exists'), commit_entry('unverified_missing'), commit_entry('unverified_missing'), commit_entry('unverified_missing'),
                 {'type': 'pr', 'repo': 'o/n', 'number': 1, 'verified_state': 'claim_only'}],
         {KEY: [at('exists'), at('missing'), at('timeout'), at('error'), None]})
    result, audit = audit_export(setup, tmp_path)
    assert result.returncode == 0, result.stderr
    summary = audit['outcomes']['commit_evidence_verification']
    assert {k: summary[k] for k in ('denominator', 'exists', 'missing', 'operational_failure', 'not_recorded')} == {
        'denominator': 4, 'exists': 1, 'missing': 1, 'operational_failure': 2, 'not_recorded': 0}
    _, available, quota, receipts = setup
    _, calibrate = run('calibrate', '--available', available, '--quota', quota, '--receipts', receipts)
    assert calibrate['commit_evidence_verification']['operational_failure'] == 2


def test_followup_view_reports_not_recorded_for_a_legacy_row(setup, tmp_path):
    from test_policy_outcomes import GOOD_FOLLOWUP
    record(setup)
    path = outcomes_path(setup)
    path.write_text(json.dumps(GOOD_CLOSE) + '\n' + json.dumps(GOOD_FOLLOWUP | {'finding': 'fix_commit', 'evidence': [commit_entry('exists')]}) + '\n')
    path.chmod(0o600)
    _, audit = audit_export(setup, tmp_path)
    assert audit['delegations'][0]['followups']['items'][0]['commit_verification'] == ['not_recorded']


def test_older_cli_reads_ledgers_with_the_new_field(setup, tmp_path, monkeypatch, git_repo, old_tool):
    """The baseline CLI must append a followup, replay the close, and audit a ledger that holds new-format rows."""
    record(setup)
    repo, head = git_repo
    evidence = ['--evidence', commit_ref(repo, head), '--evidence', commit_ref(repo, 'a' * 40)]
    result, row = close(setup, 'one', 'c1', *evidence)
    assert result.returncode == 0 and KEY in row
    assert row[KEY][1]['state'] == 'missing'
    stub_git(tmp_path, monkeypatch, 'exit 3')
    result, failed = run('close', 'one', '--receipts', setup[3], '--close-id', 'c2', '--supersedes', 'c1', '--reason', 'again',
                         '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none', *evidence)
    assert result.returncode == 0 and failed[KEY][0]['state'] == 'error', result.stderr
    # Old CLI, new rows.
    result, replay = run_with(old_tool, 'close', 'one', '--receipts', setup[3], '--close-id', 'c2', '--supersedes', 'c1', '--reason', 'again',
                              '--outcome', 'accepted', '--judged-by', 'parent', '--check', 'none', *evidence)
    assert result.returncode == 0, result.stderr
    assert replay['replayed'] is True and replay[KEY] == failed[KEY]
    result, _ = run_with(old_tool, 'followup', 'one', '--receipts', setup[3], '--followup-id', 'f1', '--finding', 'fix_commit',
                         '--checked-scope', 'x', '--evidence', commit_ref(repo, head))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(setup[3].read_text().splitlines()[0])
    export = tmp_path / 'export.json'
    export.write_text(json.dumps({'schema_version': 1, 'delegations': [{
        'call_id': 'call-1', 'thread_id': 'parent-thread', 'timestamp': '2026-10-08T12:00:00Z',
        'input': {'clientRequestId': receipt['decision_id'], 'target': receipt['target']}, 'output': {'childRunId': 'child'}}]}))
    result, audit = run_with(old_tool, 'audit', '--export', export, '--receipts', setup[3])
    assert result.returncode == 0, result.stderr
    assert audit['outcomes']['close_coverage']['decisions_closed'] == 1
    # The current CLI still reads what the old one appended.
    result, _ = audit_export(setup, tmp_path)
    assert result.returncode == 0, result.stderr


def test_baseline_cli_cannot_tell_failure_from_absence(setup, tmp_path, monkeypatch, git_repo, old_tool):
    """Reproduction of the defect on the baseline: both cases record the same bare unverified_missing."""
    record(setup)
    repo, head = git_repo
    stub_git(tmp_path, monkeypatch, 'exit 3')
    result, failed = run_with(old_tool, 'close', 'one', '--receipts', setup[3], '--close-id', 'c1', '--outcome', 'accepted',
                              '--judged-by', 'parent', '--check', 'none', '--evidence', commit_ref(repo, head))
    assert result.returncode == 0, result.stderr
    assert failed['evidence'][0]['verified_state'] == 'unverified_missing' and KEY not in failed and 'verification' not in failed['evidence'][0]
