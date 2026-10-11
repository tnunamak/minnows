import contextlib
import io
import json
from pathlib import Path
import os
import runpy
import sys
import tempfile
import types
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

class FoldScaling(unittest.TestCase):
    def test_binary_fit_normalizes_only_training_rows(self):
        raw = [[0.0], [1.0], [2.0], [10000.0]]
        calls = []
        predicted = []
        original = fit.standardize
        def scaling(rows):
            calls.append([r[:] for r in rows])
            return original(rows)
        with mock.patch.object(fit, "load", return_value=(raw, [0, 1, 0, 1], ["a"], list("abcd"))), \
             mock.patch.object(fit, "threshold_baseline", return_value=.5), \
             mock.patch.object(fit, "standardize", side_effect=scaling), \
             mock.patch.object(fit, "train", return_value=([0.0], 0.0)), \
             mock.patch.object(fit, "predict", side_effect=lambda w, b, x: predicted.append(x[:]) or .5), \
             contextlib.redirect_stdout(io.StringIO()):
            fit.main()
        self.assertEqual(calls[:4], [raw[:i] + raw[i + 1:] for i in range(4)])
        self.assertEqual(calls[-1], raw)  # Full-data fit is only for final coefficients.
        for i, held in enumerate(predicted):
            _, mu, sd = original(raw[:i] + raw[i + 1:])
            self.assertAlmostEqual(held[0], (raw[i][0] - mu[0]) / sd[0])

    def test_graded_fit_normalizes_each_fold(self):
        raw = [0, 1, 2, 10000]
        calls = []
        original = fit.standardize
        fake = types.ModuleType("fit")
        def scaling(rows):
            calls.append([r[:] for r in rows])
            return original(rows)
        fake.standardize = scaling
        fake.train = lambda *args, **kwargs: ([0.0], 0.0)
        fake.predict = lambda *args: .5
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "feature_matrix.json").write_text(json.dumps(matrix(raw)))
            (p / "graded_labels.json").write_text(json.dumps({str(i): i / 3 for i in range(4)}))
            with cwd(p), mock.patch.dict(sys.modules, {"fit": fake}), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(SEM / "graded_fit.py"))
        rows = [[float(v)] for v in raw]
        self.assertEqual(calls, [rows[:i] + rows[i + 1:] for i in range(4)])

    def test_curve_never_normalizes_outside_training_fold(self):
        raw = [[float(i * i)] for i in range(10)]
        calls = []
        original = fit.standardize
        fake = types.ModuleType("fit")
        fake.load = lambda: (raw, [i % 2 for i in range(10)], ["a"], list(range(10)))
        def scaling(rows):
            calls.append([r[:] for r in rows])
            return original(rows)
        fake.standardize = scaling
        fake.train = lambda *args, **kwargs: ([0.0], 0.0)
        fake.predict = lambda *args: .5
        with mock.patch.dict(sys.modules, {"fit": fake}), contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(SEM / "curve.py"))
        self.assertEqual(max(map(len, calls)), 9)
        self.assertIn(3, list(map(len, calls)))  # 40% subsample has three training rows.


if __name__ == "__main__":
    unittest.main()
