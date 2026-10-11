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

class ResolverTest(unittest.TestCase):
    def test_open_and_unclear_are_rechecked_terminal_results_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = types.ModuleType("uf_config")
            cfg.DATA = cfg.STATE = Path(tmp)
            cfg.WORK_DB = cfg.STATE / "work.db"
            cfg.CFG = {}; cfg.claude_env = lambda: {}; cfg.owner = lambda: "fixture"
            resolver = load("tools/unfinished/lib/resolve_agentic.py", cfg)
            with sqlite3.connect(cfg.WORK_DB) as db:
                db.execute("create table resolved(item_id text primary key, out text, src text)")
                for status in ("open", "unclear", "done"):
                    db.execute("insert into resolved values(?,?,?)", (status, json.dumps({"status_now":status}), "fixture"))
            (cfg.DATA / "ledger.jsonl").write_text("".join(json.dumps({"id":status, "verdict":"open", "project":"fixture", "source_type":"session", "first_seen":"2026-01-01", "slug":status})+"\n" for status in ("open", "unclear", "done")))
            seen = []
            def answer(batch):
                seen.extend(x["id"] for x in batch)
                return {"results":[{"id":x["id"], "status_now":"done", "evidence":"fixture now complete"} for x in batch]}, 0
            with patch.object(resolver, "call", side_effect=answer), patch.object(sys, "argv", ["resolve", "24"]):
                resolver.main()
            self.assertEqual(set(seen), {"open", "unclear"})
            with sqlite3.connect(cfg.WORK_DB) as db:
                self.assertEqual({json.loads(out)["status_now"] for out, in db.execute("select out from resolved")}, {"done"})
            seen.clear()
            with patch.object(resolver, "call", side_effect=answer), patch.object(sys, "argv", ["resolve", "24"]):
                resolver.main()
            self.assertEqual(seen, [])

if __name__ == '__main__': unittest.main()
