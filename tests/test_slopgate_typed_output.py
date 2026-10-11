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

@pytest.mark.parametrize("finding, expected", [
    ({"rule":"choice", "severity":"high", "diagnosis":"empty claim", "suggestion":"explain"}, "empty claim"),
    ({"rule":"score", "severity":"medium", "level":1, "suggestion":"explain"}, "level=1"),
])
def test_supported_typed_findings_render(finding, expected, capsys):
    module=load("jevgate")
    with patch.object(module, "run", return_value=([finding], None)), patch.object(sys, "argv", ["jevgate", "--stdin"]), patch.object(sys, "stdin", __import__('io').StringIO("A concrete account of the failure and its verified repair. " * 4)):
        with pytest.raises(SystemExit) as exited:
            module.main()
    assert exited.value.code == 1
    assert expected in capsys.readouterr().out
