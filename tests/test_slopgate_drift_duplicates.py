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

def test_duplicate_case_is_counted_once(tmp_path,capsys):
    module=load("drift_check")
    for name in ["loop_corpus","loop_corpus_pr","loop_revisions"]: (tmp_path/name).mkdir()
    text="This complete sentence contains the facts a reader needs to act on the failure."
    for name in ["loop_corpus","loop_corpus_pr","loop_revisions"]: (tmp_path/name/"case.txt").write_text(text)
    with patch.object(module,"HERE",tmp_path): module.main()
    output=capsys.readouterr().out
    assert "0/1" in output and "0/2" not in output

def test_different_originals_cannot_share_one_revision(tmp_path):
    module=load("drift_check")
    for name in ["loop_corpus","loop_corpus_pr","loop_revisions"]: (tmp_path/name).mkdir()
    (tmp_path/"loop_corpus"/"case.txt").write_text("First original with concrete details.")
    (tmp_path/"loop_corpus_pr"/"case.txt").write_text("Different original with other facts.")
    (tmp_path/"loop_revisions"/"case.txt").write_text("One shared revision.")
    with patch.object(module,"HERE",tmp_path):
        with pytest.raises(RuntimeError,match="ambiguous"): module.main()
