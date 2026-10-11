import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SEM = Path(__file__).resolve().parents[1] / "tools/slopgate/semantic"
sys.path.insert(0, str(SEM))
import fit


class TypedBaseline(unittest.TestCase):
    def test_baseline_respects_kind_choices_and_scores(self):
        def rule(**kw):
            return {"choices": None, "levels": None, "threshold": .6,
                    "severity": "high", "flag": [], "max_level": None, **kw}
        rules = {"prob": rule(), "choice": rule(choices=["bad", "good"], flag=["bad"]),
                 "score": rule(levels=["poor", "good"], max_level=0)}
        doc = {
            "a": {"kind": "commit", "features": {"prob": .7, "choice::bad": 1, "choice::good": 0, "score": 1}},
            "b": {"kind": "pr", "features": {"prob": .7, "choice::bad": 0, "choice::good": 1, "score": 0}},
            "c": {"kind": "doc", "features": {"prob": .7, "choice::bad": 1, "choice::good": 0, "score": 1}},
            "d": {"kind": "doc", "features": {"prob": .1, "choice::bad": 0, "choice::good": 1, "score": 1}},
        }
        def load_rules(kind):
            if kind == "commit": return {**rules, "prob": rule(threshold=.9)}
            return rules
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); (p / "feature_matrix.json").write_text(json.dumps(doc))
            with mock.patch.object(fit, "HERE", p), mock.patch.object(fit, "load_rules", side_effect=load_rules):
                self.assertEqual(fit.threshold_baseline(["a", "b", "c", "d"], [1, 0, 0, 1]), 1.0)

    def test_baseline_zero_fills_sparse_probability_features(self):
        def rule():
            return {"choices": None, "levels": None, "threshold": .6,
                    "severity": "high", "flag": [], "max_level": None}
        rules = {"present": rule(), "missing": rule()}
        doc = {"a": {"kind": "doc", "features": {"present": .8}}}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); (p / "feature_matrix.json").write_text(json.dumps(doc))
            with mock.patch.object(fit, "HERE", p), mock.patch.object(fit, "load_rules", return_value=rules):
                self.assertEqual(fit.threshold_baseline(["a"], [1]), 1.0)


if __name__ == "__main__":
    unittest.main()
