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

def test_recently_edited_old_pr_is_not_historical_prose():
    module=load("mine_exemplars")
    body="\n".join(["This paragraph explains the concrete failure and how the change corrects the affected behavior. "*3]*3)
    def fixture(args):
        if 'search/issues' in args[1]: return "123\tmaintainer"
        if args[-1]==".author_association": return "MEMBER"
        if args[-1]==".body": return body
        import json
        return json.dumps({"author_association":"MEMBER","body":body,"created_at":"2020-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z","merged_at":"2020-02-01T00:00:00Z"})
    with patch.object(module,"gh",side_effect=fixture):
        assert module.mine("org/repo",1,"2023-01-01")==[]

@pytest.mark.parametrize("updated,merged,expected",[("2020-02-01","2020-02-01",1),("2020-02-01","2026-01-01",0)])
def test_cutoff_checks_merge_and_preserves_historical_body(updated,merged,expected):
    module=load("mine_exemplars")
    body="\n".join(["This paragraph explains the concrete failure and how the change corrects the affected behavior. "*3]*3)
    import json
    def fixture(args):
        if 'search/issues' in args[1]: return "123\tmaintainer"
        if args[-1]==".author_association": return "MEMBER"
        if args[-1]==".body": return body
        return json.dumps({"author_association":"MEMBER","body":body,"created_at":"2020-01-01T00:00:00Z","updated_at":updated+"T00:00:00Z","merged_at":merged+"T00:00:00Z"})
    with patch.object(module,"gh",side_effect=fixture):
        assert len(module.mine("org/repo",1,"2023-01-01"))==expected
