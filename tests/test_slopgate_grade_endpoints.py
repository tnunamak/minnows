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

def test_removed_cached_endpoint_is_filtered_before_judging(tmp_path,capsys):
    module=load("grade_corpus");pairs=tmp_path/"pairs.json";pairs.write_text('[["removed","a",true],["a","b",true]]')
    out=tmp_path/"labels.json"
    with patch.object(module,"OUT",out),patch.object(module,"PAIRS",pairs),patch.object(module,"docs",return_value={"a":"one","b":"two"}),patch.object(module,"judge_pair",side_effect=AssertionError("no pending pairs should remain")),patch.object(sys,"argv",["grade_corpus","1"]): module.main()
    import json
    assert set(json.loads(out.read_text()))=={"a","b"}
    assert json.loads(pairs.read_text())==[["a","b",True]]
    assert "discarded 1" in capsys.readouterr().out
