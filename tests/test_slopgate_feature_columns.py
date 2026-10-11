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

class FeatureColumns(unittest.TestCase):
    def test_binary_fit_rejects_unmeasured_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            doc = matrix([0, 1, 2])
            doc["1"]["features"]["new"] = 1.0
            (p / "feature_matrix.json").write_text(json.dumps(doc))
            with mock.patch.object(fit, "HERE", p), self.assertRaisesRegex(ValueError, "feature columns"):
                fit.load()

    def test_graded_fit_rejects_unmeasured_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            doc = matrix([0, 1, 2])
            doc["1"]["features"]["new"] = 1.0
            (p / "feature_matrix.json").write_text(json.dumps(doc))
            (p / "graded_labels.json").write_text(json.dumps({"0": 0, "1": .5, "2": 1}))
            with cwd(p), self.assertRaisesRegex(ValueError, "feature columns"), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(SEM / "graded_fit.py"))


if __name__ == "__main__":
    unittest.main()
