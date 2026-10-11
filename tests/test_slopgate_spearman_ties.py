import contextlib
import io
import json
import math
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

class SpearmanTies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "feature_matrix.json").write_text(json.dumps(matrix([0, 1, 2])))
            (p / "graded_labels.json").write_text(json.dumps({"0": 0, "1": .5, "2": 1}))
            with cwd(p), contextlib.redirect_stdout(io.StringIO()):
                cls.correlation = staticmethod(runpy.run_path(str(SEM / "graded_fit.py"))["spearman"])

    def test_ties_get_average_ranks(self):
        expected = math.sqrt(3) / 2
        self.assertAlmostEqual(self.correlation([1, 1, 2], [1, 2, 3]), expected)
        self.assertAlmostEqual(self.correlation([2, 1, 1], [3, 2, 1]), expected)

    def test_constant_inputs_are_undefined(self):
        self.assertTrue(math.isnan(self.correlation([1, 1, 1], [1, 2, 3])))
        self.assertTrue(math.isnan(self.correlation([1, 1, 1], [2, 2, 2])))


if __name__ == "__main__":
    unittest.main()
