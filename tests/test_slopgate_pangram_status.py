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

@pytest.mark.parametrize("status,body",[(22,'{"error":"rate limit"}'),(0,'{"error":"authentication failed"}'),(0,'null')])
def test_api_error_is_not_a_completed_paid_audit(tmp_path,status,body):
    module=load("pangram_audit");cache=tmp_path/"results.json"
    item={"label":"human","path":"fixture.txt","text":"Fixture prose only.","units":1}
    with patch.object(module,"HERE",tmp_path),patch.object(module,"CACHE",cache),patch.object(module,"select",return_value=[item]),patch.dict(module.os.environ,{"PANGRAM_API_KEY":"fixture-not-a-real-key"}),patch.object(module.subprocess,"run",return_value=SimpleNamespace(stdout=body,stderr="fixture error",returncode=status)),patch.object(sys,"argv",["pangram_audit","--confirm"]):
        assert module.main()==1
    assert not cache.exists()

def test_accepted_async_job_is_cached_but_not_reported_completed(tmp_path):
    module=load("pangram_audit");cache=tmp_path/"results.json"
    item={"label":"human","path":"fixture.txt","text":"Fixture prose only.","units":1}
    import json
    response={"bulk_id":"fixture-job","status":"queued","total_items":1,"accepted_items":[{"index":0,"task_id":"fixture-task"}],"failed_items":[]}
    with patch.object(module,"HERE",tmp_path),patch.object(module,"CACHE",cache),patch.object(module,"select",return_value=[item]),patch.dict(module.os.environ,{"PANGRAM_API_KEY":"fixture-not-a-real-key"}),patch.object(module.subprocess,"run",return_value=SimpleNamespace(stdout=json.dumps(response),stderr="",returncode=0)) as called,patch.object(sys,"argv",["pangram_audit","--confirm"]):
        assert module.main()==1
        assert module.main()==2
    assert called.call_count==1
    assert json.loads(cache.read_text())["response"]==response
