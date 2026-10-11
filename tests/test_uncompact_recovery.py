import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).parents[1]
SCRIPT = REPO / "tools" / "uncompact" / "uncompact"
VENDORED_SCRIPT = REPO / "skills" / "uncompact" / "scripts" / "uncompact"


def load_cli(script, name):
    loader = importlib.machinery.SourceFileLoader(name, str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", [SCRIPT, VENDORED_SCRIPT], ids=["tool", "vendored-skill"])
def test_keyboard_interrupt_does_not_publish_partial_session(tmp_path, monkeypatch, script):
    uncompact = load_cli(script, f"uncompact_{script.parent.name}")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    source = project_dir / ("a" * 36 + ".jsonl")
    lines = [
        json.dumps({"sessionId": source.stem, "type": "user", "message": {"content": "one"}}),
        json.dumps({"sessionId": source.stem, "type": "assistant", "message": {"content": "two"}}),
    ]
    real_dumps = uncompact.json.dumps
    calls = 0

    def interrupt_after_first_row(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(uncompact.json, "dumps", interrupt_after_first_row)

    with pytest.raises(KeyboardInterrupt):
        uncompact.create_recovered_session(source, project_dir, 2, lines)

    assert list(project_dir.glob("*.jsonl")) == []
    assert list(project_dir.glob(".*.tmp")) == []


def test_completed_recovery_publishes_rewritten_session(tmp_path):
    uncompact = load_cli(SCRIPT, "uncompact_complete_test")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    source = project_dir / ("b" * 36 + ".jsonl")
    lines = [json.dumps({"sessionId": source.stem, "type": "user", "message": {"content": "resume"}})]

    recovered_id = uncompact.create_recovered_session(source, project_dir, 1, lines)

    recovered = project_dir / f"{recovered_id}.jsonl"
    assert recovered.is_file()
    assert json.loads(recovered.read_text()) == {
        "sessionId": recovered_id, "type": "user", "message": {"content": "resume"},
    }
    assert list(project_dir.glob(".*.tmp")) == []
