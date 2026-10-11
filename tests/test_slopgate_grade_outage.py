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

def test_judge_outage_preserves_previous_labels(tmp_path):
    module=load("grade_corpus")
    out=tmp_path/"labels.json";out.write_text('{"prior":0.8}')
    with patch.object(module,"OUT",out),patch.object(module,"PAIRS",tmp_path/"pairs.json"),patch.object(module,"docs",return_value={"a":"one", "b":"two"}),patch.object(module,"judge_pair",side_effect=RuntimeError("fixture outage")),patch.object(sys,"argv",["grade_corpus","1"]):
        with pytest.raises((RuntimeError,SystemExit)): module.main()
    assert out.read_text() == '{"prior":0.8}'

def test_uncompared_document_does_not_receive_a_label(tmp_path):
    module=load("grade_corpus");out=tmp_path/"labels.json";out.write_text('{"prior":0.8}')
    pairs=tmp_path/"pairs.json";pairs.write_text('[["a","b",true]]')
    with patch.object(module,"OUT",out),patch.object(module,"PAIRS",pairs),patch.object(module,"docs",return_value={"a":"one","b":"two","c":"three"}),patch.object(sys,"argv",["grade_corpus","0"]):
        with pytest.raises(RuntimeError): module.main()
    assert out.read_text()=='{"prior":0.8}'
