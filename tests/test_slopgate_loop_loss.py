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

@pytest.mark.parametrize("loss",["", "refused", "LOST: perhaps\nWHAT: unsure", "LOST: no", "LOST: no\nWHAT: none\nLOST: yes"])
def test_malformed_loss_is_excluded(tmp_path,loss):
    module=load("loop_test");corpus=tmp_path/"corpus";corpus.mkdir();(corpus/"case.txt").write_text("Original prose with all required facts.")
    output=tmp_path/"result.json"
    high=[{"rule":"a","severity":"high"},{"rule":"b","severity":"high"}]
    with patch.object(module,"HERE",tmp_path),patch.dict(module.os.environ,{"LOOP_CORPUS":"corpus","LOOP_OUT":"result.json"}),patch.object(module,"check",side_effect=[high,[]]),patch.object(module,"llm",side_effect=["Revised prose with all required facts.",loss,"BETTER: B"]),patch.object(sys,"argv",["loop_test"]):
        module.main()
    import json
    assert not output.exists() or json.loads(output.read_text()) == []
