"""Run the offline CLI regression through the repository's pytest suite."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_offline_regression():
    result = subprocess.run([sys.executable, str(ROOT / "tools/unfinished/tests/test_pr_refs.py"), str(ROOT)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
