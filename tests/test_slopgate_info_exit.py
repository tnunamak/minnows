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

def test_informational_score_does_not_fail_clean_cli(capsys):
    module=load("jevgate")
    findings=[{"type":"model","rule":"quality-score","probability":0.8,"severity":"info","suggestion":"quality score"}]
    with patch.object(module, "run", return_value=(findings,None)), patch.object(sys,"argv",["jevgate","--stdin","--json"]), patch.object(sys,"stdin",__import__('io').StringIO("A concrete account of the failure and its verified repair. "*4)):
        with pytest.raises(SystemExit) as exited: module.main()
    assert exited.value.code == 0
    assert "quality-score" in capsys.readouterr().out
