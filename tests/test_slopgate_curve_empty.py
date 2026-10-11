import contextlib
import io
from pathlib import Path
import os
import runpy
import sys
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

class CurveEmpty(unittest.TestCase):
    def test_small_or_one_class_samples_report_insufficient_data(self):
        for labels in ([0, 1, 0], [1] * 10):
            with self.subTest(labels=labels):
                fake = types.ModuleType("fit")
                fake.load = lambda: ([[float(i)] for i in range(len(labels))], labels, ["a"], list(range(len(labels))))
                fake.standardize = fit.standardize
                fake.train = lambda *args, **kwargs: ([0.0], 0.0)
                fake.predict = lambda *args: .5
                output = io.StringIO()
                with mock.patch.dict(sys.modules, {"fit": fake}), contextlib.redirect_stdout(output):
                    runpy.run_path(str(SEM / "curve.py"))
                self.assertIn("insufficient data", output.getvalue())
                self.assertEqual(len(output.getvalue().splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
