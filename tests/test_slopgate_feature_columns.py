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
    def test_binary_fit_drops_columns_not_measured_everywhere(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            doc = matrix([0, 1, 2])
            doc["1"]["features"]["new"] = 1.0
            (p / "feature_matrix.json").write_text(json.dumps(doc))
            with mock.patch.object(fit, "HERE", p), contextlib.redirect_stderr(io.StringIO()) as err:
                X, _, cols, _ = fit.load()
        self.assertEqual(cols, ["a"])
        self.assertEqual(X, [[0.0], [1.0], [2.0]])
        self.assertIn("new", err.getvalue())

    def test_graded_fit_drops_columns_not_measured_everywhere(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            doc = matrix([0, 1, 2, 3])
            doc["1"]["features"]["new"] = 1.0
            (p / "feature_matrix.json").write_text(json.dumps(doc))
            (p / "graded_labels.json").write_text(json.dumps({"0": 0, "1": .5, "2": 1, "3": .25}))
            with cwd(p), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = runpy.run_path(str(SEM / "graded_fit.py"))
        self.assertEqual(result["cols"], ["a"])

    def test_committed_matrix_fits_on_its_complete_columns(self):
        with contextlib.redirect_stderr(io.StringIO()):
            X, _, cols, _ = fit.load()
        self.assertEqual((len(X), len(cols)), (90, 35))
        with cwd(SEM), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = runpy.run_path(str(SEM / "graded_fit.py"))
        self.assertEqual((len(result["X"]), len(result["cols"])), (69, 35))


if __name__ == "__main__":
    unittest.main()
