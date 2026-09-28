"""cmd_check exit code: `info` findings are reported but never gate.

Run: python3 tools/slopgate/tests/test_exit_code.py
"""
import argparse
import importlib.machinery
import importlib.util
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "slopgate"
loader = importlib.machinery.SourceFileLoader("slopgate_cli", str(SCRIPT))
spec = importlib.util.spec_from_loader("slopgate_cli", loader)
slopgate = importlib.util.module_from_spec(spec)
loader.exec_module(slopgate)

TEXT = "Fix the retry loop so a timeout no longer drops the queued job on restart."


def check(semantic_findings):
    a = argparse.Namespace(file=None, kind="pr", json=True, fast=False,
                           semantic_only=True, quiet=True)
    with mock.patch.object(slopgate, "check_rhythm", return_value=[]), \
         mock.patch.object(slopgate, "run_semantic",
                           return_value=(semantic_findings, None)), \
         mock.patch("sys.stdin.read", return_value=TEXT), \
         mock.patch("builtins.print"):
        return slopgate.cmd_check(a)


class ExitCode(unittest.TestCase):
    def test_info_only_exits_zero(self):
        self.assertEqual(check([{"rule": "quality-score", "severity": "info"}]), 0)

    def test_medium_exits_one(self):
        self.assertEqual(check([{"rule": "quality-score", "severity": "info"},
                                {"rule": "restates-edit", "severity": "medium"}]), 1)

    def test_no_findings_exits_zero(self):
        self.assertEqual(check([]), 0)


if __name__ == "__main__":
    unittest.main()
