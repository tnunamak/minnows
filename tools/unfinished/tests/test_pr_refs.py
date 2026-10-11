import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[1])
sys.argv = [sys.argv[0]]

def load(path, cfg):
    loader = SourceFileLoader("fixture_module", str(ROOT / path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"uf_config": cfg}):
        loader.exec_module(module)
    return module

class ReferenceTest(unittest.TestCase):
    def test_fully_qualified_reference_does_not_add_other_owner(self):
        cfg = types.ModuleType("uf_config")
        cfg.CFG = {}; cfg.CONVO_LEDGER = Path("unused"); cfg.WORK_DB = Path("unused")
        evidence = load("tools/unfinished/lib/resolve_evidence.py", cfg)
        mapping = {"minnows":"local/minnows", "waspflow":"local/waspflow"}
        self.assertEqual(evidence.pr_refs("external/minnows#42", "waspflow", mapping), [("external/minnows",42)])
        self.assertEqual(evidence.pr_refs("PR external/minnows#42 3 files changed", "waspflow", mapping),
                         [("external/minnows",42)])
        self.assertEqual(evidence.pr_refs("external/minnows#42 and minnows#43 and PR #44", "waspflow", mapping),
            [("external/minnows",42),("local/minnows",43),("local/waspflow",44)])

if __name__ == '__main__': unittest.main()
