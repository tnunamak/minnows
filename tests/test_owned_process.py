"""Ownership regressions for the test-only subprocess helper."""
from contextlib import ExitStack
import os
import subprocess
import sys

import pytest

from owned_process import owned_popen, run_owned


def test_timeout_reaps_direct_child():
    with owned_popen([sys.executable, '-c', 'import time; time.sleep(60)']) as child:
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=0.05)
    assert child.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


def test_assertion_failure_closes_capture_pipes():
    with pytest.raises(AssertionError):
        with owned_popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         stdout=subprocess.PIPE) as child:
            raise AssertionError('injected')
    assert child.returncode is not None and child.stdout.closed


def test_later_spawn_failure_cleans_earlier_child(tmp_path):
    with pytest.raises(FileNotFoundError):
        with ExitStack() as stack:
            child = stack.enter_context(owned_popen(
                [sys.executable, '-c', 'import time; time.sleep(60)']))
            stack.enter_context(owned_popen([str(tmp_path / 'missing-executable')]))
    assert child.returncode is not None


def test_run_owned_preserves_exit_and_streams():
    result = run_owned([sys.executable, '-c',
                        'import sys; print("out"); print("err", file=sys.stderr); sys.exit(7)'])
    assert (result.returncode, result.stdout, result.stderr) == (7, 'out\n', 'err\n')


def test_reaped_leader_is_never_signalled(monkeypatch):
    signalled = []
    monkeypatch.setattr(os, 'killpg', lambda *args: signalled.append(args))
    result = run_owned([sys.executable, '-c', 'pass'])
    assert result.returncode == 0
    assert signalled == []


def test_unreaped_leader_group_is_signalled(monkeypatch):
    signalled = []
    real = os.killpg
    monkeypatch.setattr(os, 'killpg', lambda pid, sig: (signalled.append(pid), real(pid, sig)))
    with owned_popen([sys.executable, '-c', 'import time; time.sleep(60)']) as child:
        pass
    assert signalled == [child.pid]


def test_run_owned_deadline():
    with pytest.raises(subprocess.TimeoutExpired):
        run_owned([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.05)


def test_group_cleanup_does_not_claim_setsid_child(tmp_path):
    # Capture a pidfd while the child is alive; never signal a reusable raw PID.
    import select
    import signal
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        pytest.fail('these tests require Linux pidfd support')
    code = ('import subprocess, sys, time; '
            'child = subprocess.Popen([sys.executable, "-c", '
            '"import time; time.sleep(60)"], start_new_session=True); '
            'print(child.pid, flush=True); time.sleep(60)')
    fd = None
    try:
        with owned_popen([sys.executable, '-c', code],
                         stdout=subprocess.PIPE, text=True) as parent:
            ready, _, _ = select.select([parent.stdout], [], [], 5)
            assert ready, 'child PID handshake timed out'
            pid = int(parent.stdout.readline())
            fd = os.pidfd_open(pid)
        # The helper cleaned only its group; escaped child's pidfd is not ready.
        assert not select.select([fd], [], [], 0)[0]
    finally:
        if fd is not None:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                assert select.select([fd], [], [], 5)[0], 'escaped child failed to exit'
            finally:
                os.close(fd)
    # The adopted grandchild is reaped by init once killed above; a pidfd exit
    # event alone is not proof of reaping.
