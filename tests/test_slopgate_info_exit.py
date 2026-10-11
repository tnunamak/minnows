import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import json
import math
import pytest

SEMANTIC = Path(__file__).resolve().parents[1] / "tools/slopgate/semantic"
def load(name):
    spec = importlib.util.spec_from_file_location("p2_" + name, SEMANTIC / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_informational_score_does_not_fail_clean_cli(capsys):
    module=load("jevgate")
    findings=[{"type":"model","rule":"quality-score","score":0.8,"severity":"info","suggestion":"model score"}]
    with patch.object(module, "run", return_value=(findings,None)), patch.object(sys,"argv",["jevgate","--stdin","--json"]), patch.object(sys,"stdin",__import__('io').StringIO("A concrete account of the failure and its verified repair. "*4)):
        with pytest.raises(SystemExit) as exited: module.main()
    assert exited.value.code == 0
    assert "quality-score" in capsys.readouterr().out


def test_quality_score_is_named_score_for_graded_and_classifier_models(tmp_path):
    module = load("jevgate")

    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def system_one(self, **kwargs):
            return SimpleNamespace(
                nouls={"sample": SimpleNamespace(noul=0.8)}, choices={}, scores={})

    sdk = SimpleNamespace(
        Choice=lambda **kwargs: None,
        Noul=lambda **kwargs: None,
        Score=lambda **kwargs: None,
        TypeSafeClient=Client,
    )
    rules = {"sample": {
        "question": "Is the sample defect present?", "threshold": 0.5,
        "severity": "high", "suggestion": "fix it", "choices": None,
        "flag": [], "levels": None, "max_level": None,
    }}
    model_dir = tmp_path / "semantic"
    model_dir.mkdir()
    fake_file = model_dir / "jevgate.py"
    fake_file.write_text("")

    for target in ("graded", "authorship"):
        (model_dir / "quality_model.json").write_text(json.dumps({
            "target": target, "cols": ["sample"], "w": [0.0],
            "mu": [0.0], "sd": [1.0], "b": 0.8,
        }))
        with patch.object(module, "__file__", str(fake_file)), \
             patch.dict(sys.modules, {"typesafe_sdk": sdk}), \
             patch.dict(module.os.environ, {"TYPESAFE_API_KEY": "fixture"}), \
             patch.object(module, "load_rules", return_value=rules):
            findings, error = module.run("fixture text")

        model_finding = next(f for f in findings if f["type"] == "model")
        assert error is None
        expected = 0.8 if target == "graded" else round(1 / (1 + math.exp(-0.8)), 3)
        assert model_finding["score"] == expected
        assert "probability" not in model_finding
        assert model_finding["suggestion"].startswith("model score")


def test_slopgate_text_output_labels_model_value_as_score(capsys):
    cli_path = Path(__file__).resolve().parents[1] / "tools/slopgate/slopgate"
    loader = importlib.machinery.SourceFileLoader("slopgate_cli_score_test", str(cli_path))
    spec = importlib.util.spec_from_loader("slopgate_cli_score_test", loader)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    args = SimpleNamespace(file=None, kind="pr", json=False, fast=False,
                           semantic_only=True, quiet=True)
    findings = [{"type": "model", "rule": "quality-score", "score": 0.8,
                 "severity": "info", "suggestion": "model score"}]
    with patch.object(cli, "check_rhythm", return_value=[]), \
         patch.object(cli, "run_semantic", return_value=(findings, None)), \
         patch("sys.stdin.read", return_value="Concrete technical text. " * 8):
        assert cli.cmd_check(args) == 0

    output = capsys.readouterr().out
    assert "(score=0.8)" in output
    assert "(p=0.8)" not in output
