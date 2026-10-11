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

def test_firing_critical_rule_sorts_and_returns():
    module=load("jevgate")
    class Client:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def system_one(self,**kwargs): return SimpleNamespace(nouls={"critical_rule":SimpleNamespace(noul=0.99)},choices={},scores={})
    sdk=SimpleNamespace(Choice=lambda **kw:None,Noul=lambda **kw:None,Score=lambda **kw:None,TypeSafeClient=Client)
    rules={"critical_rule":{"question":"Is a needed fact missing?","threshold":0.5,"severity":"critical","suggestion":"restore it","choices":None,"flag":[],"levels":None,"max_level":None}}
    with patch.dict(sys.modules,{"typesafe_sdk":sdk}),patch.dict(module.os.environ,{"TYPESAFE_API_KEY":"fixture-not-a-real-key"}),patch.object(module,"load_rules",return_value=rules),patch.object(module,"quality_score",return_value=None):
        findings,error=module.run("fixture text")
    assert error is None
    assert findings[0]["severity"] == "critical"
