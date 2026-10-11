import contextlib
import io
import json
from pathlib import Path
import os
import runpy
import sys
import tempfile
import unittest
from unittest import mock

SEM = Path(__file__).resolve().parents[1] / "tools/slopgate/semantic"
sys.path.insert(0, str(SEM))
import fit


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def matrix(values):
    return {str(i): {"features": {"a": value}, "label": i % 2, "kind": "doc"}
            for i, value in enumerate(values)}

class FeatureSchema(unittest.TestCase):
    def test_binary_fit_rejects_unmeasured_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            doc = matrix([0, 1, 2])
            doc["1"]["features"]["new"] = 1.0
            (p / "feature_matrix.json").write_text(json.dumps(doc))
            with mock.patch.object(fit, "HERE", p), self.assertRaisesRegex(ValueError, "schema"):
                fit.load()

    def test_graded_fit_rejects_unmeasured_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            doc = matrix([0, 1, 2])
            doc["1"]["features"]["new"] = 1.0
            (p / "feature_matrix.json").write_text(json.dumps(doc))
            (p / "graded_labels.json").write_text(json.dumps({"0": 0, "1": .5, "2": 1}))
            with cwd(p), self.assertRaisesRegex(ValueError, "schema"), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(SEM / "graded_fit.py"))

    def test_extraction_refreshes_same_path_and_removes_failed_stale_rows(self):
        import features
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); (p / "corpus").mkdir()
            (p / "corpus/current.txt").write_text("changed " * 50)
            (p / "corpus/failed.txt").write_text("failed " * 50)
            cache = p / "feature_matrix.json"
            cache.write_text(json.dumps({"corpus/current": {"features": {"old": 1}, "label": 1, "kind": "doc", "words": 50}, "corpus/failed": {"features": {"old": 1}, "label": 1, "kind": "doc", "words": 50}}))
            def score(text, kind, rules):
                if text.startswith("failed"): raise RuntimeError("synthetic outage")
                return {"new": .25}
            with mock.patch.object(features, "HERE", p), mock.patch.object(features, "CACHE", cache), \
                 mock.patch.object(features, "SOURCES", [("corpus", 1, "doc")]), \
                 mock.patch.object(features, "score_doc", side_effect=score), \
                 mock.patch.object(features, "load_rules", return_value={}), contextlib.redirect_stdout(io.StringIO()):
                features.main()
            result = json.loads(cache.read_text())
            self.assertEqual(result["corpus/current"]["features"], {"new": .25})
            self.assertNotIn("corpus/failed", result)


if __name__ == "__main__":
    unittest.main()
