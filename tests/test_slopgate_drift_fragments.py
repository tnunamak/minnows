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

def test_short_fragments_are_counted():
    module=load("drift_check")
    result=module.stats("It fails. Fix it. Done.")
    assert result is not None
    assert result["n"]==3 and result["frags"]==3

def test_destroyed_revision_remains_in_report(tmp_path,capsys):
    module=load("drift_check");(tmp_path/"loop_corpus").mkdir();(tmp_path/"loop_revisions").mkdir()
    (tmp_path/"loop_corpus"/"case.txt").write_text("This complete sentence contains the facts a reader needs to act on the failure.")
    (tmp_path/"loop_revisions"/"case.txt").write_text("")
    with patch.object(module,"HERE",tmp_path): module.main()
    assert "case" in capsys.readouterr().out
