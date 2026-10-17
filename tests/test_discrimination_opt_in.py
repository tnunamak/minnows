"""Guard regressions run only the refusal and fake-provider self-test paths."""
import os
from pathlib import Path

import pytest

from owned_process import run_owned

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/hone/providers/test-discrimination.mjs'


@pytest.mark.parametrize('args', [[], ['--save'], ['--provider', 'claude'],
                                 ['--live'], ['--live', '--provider', 'codex']])
def test_offline_lane_refuses_live_provider_imports(args):
    result = run_owned(['node', str(SCRIPT), *args],
                       env=dict(os.environ, MINNOWS_OFFLINE='1'))
    assert result.returncode == 2
    assert 'Live evaluation disabled' in result.stderr
    assert 'discrimination test:' not in result.stdout


@pytest.mark.parametrize('args', [['--live', '--provider', ''], ['--live', '--case', ''],
                                 ['--live', '--provider'], ['--live', '--case'],
                                 ['--live', '--provider', 'nope'], ['--live', '--case', 'nope']])
def test_live_selectors_must_be_valid_and_non_empty(args):
    # Validation must refuse before any provider module is imported.
    env = dict(os.environ)
    env.pop('MINNOWS_OFFLINE', None)
    result = run_owned(['node', str(SCRIPT), *args], env=env)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert 'Unknown --' in result.stderr
    assert 'discrimination test:' not in result.stdout


def test_default_requires_opt_in_even_outside_offline_lane():
    env = dict(os.environ)
    env.pop('MINNOWS_OFFLINE', None)
    result = run_owned(['node', str(SCRIPT)], env=env)
    assert result.returncode == 2
    assert 'Live evaluation disabled' in result.stderr


def test_offline_provider_self_test_still_runs():
    result = run_owned(['node', str(SCRIPT), '--self-test'],
                       env=dict(os.environ, MINNOWS_OFFLINE='1'))
    assert result.returncode == 0, result.stderr
    assert 'FAIL' not in result.stdout
    assert result.stdout.count('ok   ') >= 11, result.stdout
