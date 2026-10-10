"""Test-only launcher: run the model-policy-ops CLI with its evaluation clock pinned.

The CLI has no flag that sets the clock for a dispatchable route (`route --now` is a read-only simulation), so
tests that need a fixed clock set PINNED_NOW and run the tool through this file. Only `directives.now` is patched;
receipt `recorded_at` stays real. START_AFTER=FILE holds the run until FILE exists, so a test can start many processes together.
"""
import os
import runpy
import time
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / 'tools/model-policy-ops/model-policy-ops'
sys.path.insert(0, str(TOOL.parent / 'lib'))

import directives  # noqa: E402

barrier = os.environ.get('START_AFTER')
if barrier:
    deadline = time.monotonic() + 30
    while not os.path.exists(barrier) and time.monotonic() < deadline:
        time.sleep(0.001)

pinned = os.environ.get('PINNED_NOW')
if pinned:
    fixed = directives.parse_time(pinned, 'PINNED_NOW')
    directives.now = lambda: fixed
sys.argv = [str(TOOL), *sys.argv[1:]]
runpy.run_path(str(TOOL), run_name='__main__')
