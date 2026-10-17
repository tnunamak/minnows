"""Test-only subprocess ownership. setsid descendants escape it.

The context always kills the owned process group and waits for its direct child,
including assertion failures and failures partway through a multi-process spawn.
It does NOT claim ownership of setsid descendants. Never use outside tests.
"""
from contextlib import contextmanager
import os
import signal
import subprocess


@contextmanager
def owned_popen(argv, **kwargs):
    if os.name != 'posix':
        raise RuntimeError('owned_popen requires POSIX; these tests need Linux')
    if 'start_new_session' in kwargs:
        raise TypeError('owned_popen owns start_new_session')
    process = subprocess.Popen(argv, start_new_session=True, **kwargs)
    try:
        yield process
    finally:
        try:
            # Signal the group only while this process still holds the leader's
            # PID (returncode is None: not yet reaped). After a reap the PID can
            # be reused, and killpg could hit an unrelated process group.
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            try:
                # Never communicate here: an escaped child can hold a pipe open.
                process.wait(timeout=5)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


def run_owned(argv, *, timeout=30, env=None):
    with owned_popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, env=env) as process:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
