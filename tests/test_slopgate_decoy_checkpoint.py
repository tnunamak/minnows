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

def test_late_outage_keeps_earlier_paid_score(tmp_path):
    module=load("test_decoys");rows=[{"id":"one","text":"first","kind":"doc","must_flag":None},{"id":"two","text":"second","kind":"doc","must_flag":None}]
    import json
    (tmp_path/"decoys.jsonl").write_text("\n".join(map(json.dumps,rows)))
    with patch.object(module,"HERE",tmp_path),patch.object(module,"score",side_effect=[{"rule":0.1},RuntimeError("fixture outage")]):
        with pytest.raises(RuntimeError): module.main()
    assert json.loads((tmp_path/"decoy_scores.json").read_text()) == {"one":{"rule":0.1}}

def test_resume_scores_only_missing_decoy(tmp_path):
    module=load("test_decoys")
    import json
    rows=[{"id":"one","text":"first","kind":"doc","must_flag":None},{"id":"two","text":"second","kind":"doc","must_flag":None}]
    (tmp_path/"decoys.jsonl").write_text("\n".join(map(json.dumps,rows)))
    (tmp_path/"decoy_scores.json").write_text('{"one":{"rule":0.1}}')
    with patch.object(module,"HERE",tmp_path),patch.object(module,"CHECKS",{"rule":{}}),patch.object(module,"score",return_value={"rule":0.2}) as score:
        with pytest.raises(SystemExit) as exited: module.main()
    assert exited.value.code==0
    score.assert_called_once_with("second","doc")
    assert set(json.loads((tmp_path/"decoy_scores.json").read_text()))=={"one","two"}

def test_failed_checkpoint_write_keeps_previous_checkpoint(tmp_path):
    module=load("test_decoys")
    import json
    rows=[{"id":"one","text":"first","kind":"doc","must_flag":None},{"id":"two","text":"second","kind":"doc","must_flag":None}]
    (tmp_path/"decoys.jsonl").write_text("\n".join(map(json.dumps,rows)))
    real=Path.write_text;writes=[]
    def flaky(self,data,*a,**k):
        if not self.name.startswith("decoy_scores"): return real(self,data,*a,**k)
        writes.append(self)
        if len(writes)==2:
            real(self,data[:len(data)//2],*a,**k);raise OSError("fixture disk full")
        return real(self,data,*a,**k)
    with patch.object(module,"HERE",tmp_path),patch.object(Path,"write_text",flaky),patch.object(module,"score",side_effect=[{"rule":0.1},{"rule":0.2}]):
        with pytest.raises(OSError): module.main()
    assert json.loads((tmp_path/"decoy_scores.json").read_text()) == {"one":{"rule":0.1}}
    with patch.object(module,"HERE",tmp_path),patch.object(module,"CHECKS",{"rule":{}}),patch.object(module,"score",return_value={"rule":0.2}) as score:
        with pytest.raises(SystemExit): module.main()
    score.assert_called_once_with("second","doc")
