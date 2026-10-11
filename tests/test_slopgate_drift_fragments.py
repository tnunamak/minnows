import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import pytest

SEMANTIC = Path(__file__).resolve().parents[1] / "tools/slopgate/semantic"
def load(name):
    spec = importlib.util.spec_from_file_location("p2_" + name, SEMANTIC / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_unmeasurable_text_keeps_reference_filter():
    module=load("drift_check")
    result=module.stats("It fails. Fix it. Done.")
    assert result is None
    assert module.stats("Four words still count. This sentence has enough words to measure.")["n"] == 2

def test_destroyed_revision_remains_in_report(tmp_path,capsys):
    module=load("drift_check");(tmp_path/"loop_corpus").mkdir();(tmp_path/"loop_revisions").mkdir()
    (tmp_path/"loop_corpus"/"destroyed-case.txt").write_text("This complete sentence contains the facts a reader needs to act on the failure.")
    (tmp_path/"loop_revisions"/"destroyed-case.txt").write_text("")
    with patch.object(module,"HERE",tmp_path): module.main()
    output = capsys.readouterr().out
    assert "destroyed-case" in output
    assert "destroyed revision" in output
    assert "moved AWAY from it              : 1/1" in output
    assert "outside 2sd after revision      : 1/1" in output
