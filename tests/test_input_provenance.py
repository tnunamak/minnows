"""Input-provenance boundaries for the owner-authorized free-route trial.

The manifest is a caller declaration. A local input is not a free-floating path: it must name the
authorized repository it comes from, the root it is read under, a path contained in that root and an
explicit allowed classification. The router refuses the excluded company namespace anywhere in a
path, credential and secret paths, traversal and symlink escape, unknown or omitted provenance, and
sensitive classification. Nothing here proves content scanning or OS isolation, and an allowed
manifest says so.
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools' / 'model-policy-ops' / 'lib'))
import input_provenance as provenance  # noqa: E402


POLICY = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
INPUT_POLICY = POLICY['experimental_free_routes'][0]['input_policy']
OWNED = 'tnunamak/minnows'
OWNED_ROOT = str(REPO)
SOURCE = 'tools/model-policy-ops/lib/input_provenance.py'


def manifest(*inputs):
    return {'v': 1, 'inputs': list(inputs)}


def github(repo, visibility=None, **extra):
    return {'kind': 'github', 'repo': repo, **({'visibility': visibility} if visibility else {}), **extra}


def local(path, *, root=OWNED_ROOT, repo=OWNED, classification='code', **extra):
    entry = {'kind': 'local', 'repo': repo, 'root': root, 'path': path}
    if classification is not None:
        entry['classification'] = classification
    return entry | extra


def assess(*inputs):
    return provenance.assess(manifest(*inputs), INPUT_POLICY)


def reason(state):
    assert state['state'] == 'refused', state
    return state['refusal']


def test_an_absent_or_empty_manifest_is_required_not_allowed():
    for value in (None, manifest()):
        state = provenance.assess(value, INPUT_POLICY)
        assert state['state'] == 'absent' and state['refusal'] == 'input_provenance_required'
        assert state['limits']['declarations_only'] is True


def test_the_owners_own_repositories_are_allowed_public_or_private():
    state = assess(github(OWNED, 'private'), github('tnunamak/public-thing', 'public'))
    assert state['state'] == 'allowed' and state['refusal'] is None
    assert state['inputs'][0]['repo'] == ('tnunamak', 'minnows')


@pytest.mark.parametrize('entry', [
    {'kind': 'local', 'path': 'src/a.py'},
    {'kind': 'local', 'path': '/synthetic/vana-com/private-repo/src/a.py', 'classification': 'code'},
    {'kind': 'local', 'path': 'tools/slopgate/semantic/loop_corpus_web/oauth.txt', 'classification': 'code'},
    {'kind': 'local', 'path': '/synthetic/unrelated-repo/src/a.py', 'classification': 'code'},
])
def test_the_four_reported_unsafe_allowances_are_refused(entry):
    """Repair round 1: these four manifests were allowed before the repair."""
    state = provenance.assess({'v': 1, 'inputs': [entry]}, INPUT_POLICY)
    assert state['state'] == 'refused'
    assert state['refusal'] in ('repo_binding_required', 'invalid_root', 'invalid_path')


def test_a_local_input_must_bind_its_authorized_repository():
    assert reason(assess(local(SOURCE, repo=None))) == 'repo_binding_required'
    assert reason(assess(local(SOURCE, repo='not-a-repo'))) == 'repo_binding_required'
    assert assess(local(SOURCE))['state'] == 'allowed', 'an owned repository binding is enough'


def test_a_local_input_must_state_its_root_and_a_contained_relative_path():
    assert reason(assess(local(SOURCE, root=None))) == 'invalid_root'
    assert reason(assess(local(SOURCE, root=''))) == 'invalid_root'
    assert assess(local(SOURCE, root='/nonexistent-root'))['state'] == 'allowed', \
        'a missing root is a claim, not an escape; a path under it still binds to the named repository'
    assert reason(assess(local(None))) == 'invalid_path'
    assert reason(assess(local(''))) == 'invalid_path'
    assert reason(assess(local('/absolute/src/a.py'))) == 'invalid_path'
    assert reason(assess(local('../outside.py'))) == 'path_traversal'
    assert reason(assess(local('src/../../outside.py'))) == 'path_traversal'


def test_the_excluded_company_namespace_is_refused_anywhere_in_the_bound_root_or_path():
    for entry in (local('src/a.py', repo='vana-com/thing'),
                  local('src/a.py', root='/synthetic/vana-com/private-repo'),
                  local('vana-com/src/a.py'),
                  github('VANA-COM/thing'),
                  github('vana-com/thing', 'public', public_provenance='https://github.com/vana-com/thing')):
        assert reason(assess(entry)) == 'company_private_namespace'


def test_another_owner_needs_public_visibility_and_explicit_provenance():
    assert reason(assess(github('other/thing', 'private'))) == 'other_namespace_requires_public'
    assert reason(assess(github('other/thing', 'public'))) == 'public_provenance_required'
    assert reason(assess(github('other/thing', 'public', public_provenance='   '))) == 'public_provenance_required'
    allowed = assess(github('other/thing', 'public', public_provenance='https://github.com/other/thing'))
    assert allowed['state'] == 'allowed'
    assert assess(local('src/a.py', repo='other/thing', visibility='public',
                        public_provenance='https://github.com/other/thing'))['state'] == 'allowed'


@pytest.mark.parametrize('repo', ['tnunamak', 'tnunamak/', 'tnunamak/minnows/extra', 'tnunamak/../minnows',
                                  'tnunamak/minnows files', '-bad/minnows', '', 'https://github.com/tnunamak/minnows'])
def test_a_non_canonical_repository_identity_is_refused_not_guessed(repo):
    assert reason(assess(github(repo, 'public'))) == 'github_repo_invalid'


@pytest.mark.parametrize('repo,canonical', [('tnunamak/minnows', ('tnunamak', 'minnows')),
                                            ('TnUnAmAk/Minnows.git', ('tnunamak', 'minnows')),
                                            ('tnunamak/minnows/', ('tnunamak', 'minnows'))])
def test_a_case_or_dot_git_variant_resolves_to_the_same_allowed_owner(repo, canonical):
    assert provenance.canonical_repo(repo) == canonical
    assert assess(github(repo, 'private'))['state'] == 'allowed'


def test_one_refused_input_refuses_the_whole_manifest_in_any_position():
    allowed = github(OWNED, 'private')
    refused = github('vana-com/thing', 'public')
    for order in ((allowed, refused), (refused, allowed)):
        state = assess(*order)
        assert state['state'] == 'refused' and state['refusal'] == 'company_private_namespace'


def test_an_excluded_owner_wins_even_if_it_is_also_declared_allowed():
    policy = {'allowed_github_owners': ['tnunamak'], 'excluded_github_owners': ['tnunamak', 'vana-com']}
    state = provenance.assess(manifest(github(OWNED, 'private')), policy)
    assert state['state'] == 'refused' and state['refusal'] == 'company_private_namespace'


@pytest.mark.parametrize('path', ['deploy/tls.pem', 'src/server.key', 'certs/ca.p12', 'config/token.json',
                                  'notes/.netrc', '.env', 'app/.env.production', '.aws/credentials',
                                  'app.token', 'ops/keystore', 'x/secrets/notes', 'data/passwords.yml',
                                  'tools/slopgate/semantic/loop_corpus_web/oauth.txt', 'src/oauth.json',
                                  'config/credentials.json', 'deploy/id_rsa', 'keys/api_key.txt',
                                  'docs/oauth.txt', 'config/secret.yaml'])
def test_a_credential_path_is_refused_by_name_or_extension(path):
    assert reason(assess(local(path))) == 'credential_path'


@pytest.mark.parametrize('path', ['src/tokens.py', 'lib/tokens.ts', 'docs/tokenizer.md', 'src/keyboard.py',
                                  'src/secretary.py', 'src/oauth_client.py', 'docs/oauth.md',
                                  SOURCE, 'data/bench.csv', 'src/credentials.py'])
def test_an_ordinary_source_lookalike_is_not_a_credential_path(path):
    assert assess(local(path))['state'] == 'allowed'


def test_a_symlink_that_escapes_the_declared_root_is_refused(tmp_path):
    root = tmp_path / 'clone'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'real.py').write_text('print(1)\n')
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (outside / 'secret.txt').write_text('not read by the router')
    os.symlink(outside / 'secret.txt', root / 'src' / 'linked.py')
    escaped = provenance.assess(manifest(local('src/linked.py', root=str(root))), INPUT_POLICY)
    assert escaped['state'] == 'refused' and escaped['refusal'] == 'path_escapes_root'
    os.symlink(root / 'src' / 'real.py', root / 'src' / 'inside.py')
    inside = provenance.assess(manifest(local('src/inside.py', root=str(root))), INPUT_POLICY)
    assert inside['state'] == 'allowed'


def test_a_symlink_to_a_credential_target_is_refused_without_reading_it(tmp_path):
    root = tmp_path / 'clone'
    (root / 'vendored').mkdir(parents=True)
    (root / 'vendored' / 'token.json').write_text('{"token": "not read by the router"}')
    os.symlink(root / 'vendored' / 'token.json', root / 'innocent.py')
    assert provenance.assess(manifest(local('innocent.py', root=str(root))), INPUT_POLICY)['refusal'] == 'credential_path'


def test_the_declared_root_itself_is_checked_for_the_excluded_namespace_and_credentials(tmp_path):
    assert reason(assess(local('src/a.py', root='/synthetic/vana-com/private-repo'))) == 'company_private_namespace'
    assert reason(assess(local('src/a.py', root='/home/someone/.ssh'))) == 'credential_path'


def test_classification_is_required_explicit_and_allowed():
    assert reason(assess(local('src/a.py', classification=None))) == 'classification_required'
    assert reason(assess(local('src/a.py', classification='sensitive'))) == 'sensitive_content'
    for value in ('code', 'docs', 'data'):
        assert assess(local('src/a.py', classification=value))['state'] == 'allowed'


def test_typed_prohibited_content_is_refused():
    state = assess(local('config.json', contains=['PDPP_data', 'secrets']))
    assert state['refusal'] == 'prohibited_content:PDPP_data'
    assert reason(assess(github(OWNED, 'private', contains=['Jev_text']))) == 'prohibited_content:Jev_text'


def test_a_github_entry_cannot_carry_a_path_or_a_classification():
    with pytest.raises(ValueError):
        provenance.assess(manifest({'kind': 'github', 'repo': OWNED, 'path': 'src/a.py'}), INPUT_POLICY)
    with pytest.raises(ValueError):
        provenance.assess(manifest({'kind': 'github', 'repo': OWNED, 'classification': 'code'}), INPUT_POLICY)


@pytest.mark.parametrize('value,message', [('{', 'JSON object'), ('[]', 'JSON object'), ('"text"', 'JSON object')])
def test_parse_refuses_malformed_caller_json(value, message):
    with pytest.raises(ValueError, match=message):
        provenance.parse(value)
    assert provenance.parse(None) is None


@pytest.mark.parametrize('value', [
    {'v': 2, 'inputs': []}, {'v': 1}, {'v': 1, 'inputs': {}}, {'v': 1, 'inputs': [], 'extra': 1},
    {'v': 1, 'inputs': [{'kind': 'url', 'repo': 'a/b'}]},
    {'v': 1, 'inputs': [{'kind': 'github', 'repo': 'a/b', 'visibility': 'maybe'}]},
    {'v': 1, 'inputs': [{'kind': 'local', 'repo': 'a/b', 'root': '.', 'path': 'x', 'classification': 'unknown'}]},
    {'v': 1, 'inputs': [{'kind': 'local', 'repo': 'a/b', 'root': '.', 'path': 'x', 'contains': ['unknown']}]},
    {'v': 1, 'inputs': [{'kind': 'local', 'repo': 'a/b', 'root': '.', 'path': 'x', 'classification': 'code',
                         'extra': 1}]},
    {'v': 1, 'inputs': [{'kind': 'local', 'repo': 'a/b', 'root': '.', 'path': 'x', 'classification': 'code'}
                        for _ in range(65)]},
])
def test_a_malformed_input_entry_is_a_caller_error(value):
    with pytest.raises(ValueError):
        provenance.assess(value, INPUT_POLICY)


def test_every_state_carries_the_caller_assertion_limit():
    for state in (assess(github(OWNED, 'private')), assess(github('other/thing', 'private'))):
        assert state['limits']['declarations_only'] is True
        assert 'do not prove content scanning' in state['limits']['caller_assertion_limit']
        assert 'no runtime or OS isolation guarantee' in state['limits']['caller_assertion_limit']
    assert provenance.summary(assess(github(OWNED, 'private')))['limits']['declarations_only'] is True


def test_the_pack_policy_is_the_effective_default_when_absent():
    assert provenance.assess(manifest(github('vana-com/thing', 'public')), None)['refusal'] == 'company_private_namespace'
    assert provenance.assess(manifest(github(OWNED, 'private')), None)['state'] == 'allowed'
    assert provenance.check_policy(None) == []
    assert provenance.check_policy(INPUT_POLICY) == []
