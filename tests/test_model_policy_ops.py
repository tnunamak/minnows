import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "model-policy-ops" / "model-policy-ops"


@pytest.fixture(autouse=True)
def isolate_selected_policy(monkeypatch):
    monkeypatch.delenv("DATA_PACKS_HOME", raising=False)


def test_model_policy_ops_check():
    result = subprocess.run([str(TOOL), "check"], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0
    assert "PASS model-choice-policy" in result.stdout


def test_model_policy_ops_list_names_standard_op():
    result = subprocess.run([str(TOOL), "list"], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0
    assert "implement.standard" in result.stdout
    assert "provider" in result.stdout


def test_model_policy_ops_show_returns_json():
    result = subprocess.run(
        [str(TOOL), "show", "implement.standard"],
        cwd=REPO,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert '"id": "implement.standard"' in result.stdout
