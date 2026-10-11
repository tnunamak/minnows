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

@pytest.mark.parametrize("status,output",[(2,""),(0,"not json"),(0,"{}"),(2,'{"findings":[]}'),(0,'{"findings":{}}'),(0,'{"findings":[],"notes":["semantic backend unavailable"]}')])
def test_unavailable_check_is_not_clean(status,output):
    module=load("loop_test")
    with patch.object(module.subprocess,"run",return_value=SimpleNamespace(returncode=status,stdout=output,stderr="fixture error")):
        with pytest.raises(RuntimeError): module.check("text","doc")
@pytest.mark.parametrize("status",[0,1])
def test_valid_cli_results_are_measurements(status):
    module=load("loop_test")
    findings=[] if status==0 else [{"rule":"specific","severity":"high"}]
    import json
    with patch.object(module.subprocess,"run",return_value=SimpleNamespace(returncode=status,stdout=json.dumps({"findings":findings}),stderr="")):
        assert module.check("text","doc")==findings
