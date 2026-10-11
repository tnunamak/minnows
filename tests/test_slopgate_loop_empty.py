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

def test_empty_current_run_replaces_stale_report(tmp_path):
    module=load("loop_test");(tmp_path/"corpus").mkdir();out=tmp_path/"result.json";out.write_text('[{"case":"prior"}]')
    with patch.object(module,"HERE",tmp_path),patch.dict(module.os.environ,{"LOOP_CORPUS":"corpus","LOOP_OUT":"result.json"}),patch.object(sys,"argv",["loop_test"]): module.main()
    import json
    assert json.loads(out.read_text())==[]

def test_failed_judgment_does_not_publish_candidate_revision(tmp_path):
    module=load("loop_test");(tmp_path/"corpus").mkdir();(tmp_path/"corpus"/"case.txt").write_text("Original concrete prose.")
    revisions=tmp_path/"loop_revisions";revisions.mkdir();(revisions/"case.txt").write_text("Previous validated revision.")
    high=[{"rule":"one","severity":"high"},{"rule":"two","severity":"high"}]
    with patch.object(module,"HERE",tmp_path),patch.dict(module.os.environ,{"LOOP_CORPUS":"corpus","LOOP_OUT":"result.json"}),patch.object(module,"check",side_effect=[high,[]]),patch.object(module,"llm",side_effect=["Unjudged candidate revision.",RuntimeError("fixture judge outage")]),patch.object(sys,"argv",["loop_test"]): module.main()
    assert (revisions/"case.txt").read_text()=="Previous validated revision."
    import json
    assert json.loads((tmp_path/"result.json").read_text())==[]
