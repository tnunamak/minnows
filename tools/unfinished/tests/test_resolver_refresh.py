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

    def test_fresh_items_are_not_starved_by_cached_open_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = types.ModuleType("uf_config")
            cfg.DATA = cfg.STATE = Path(tmp); cfg.WORK_DB = cfg.STATE / "work.db"
            cfg.CFG = {}; cfg.claude_env = lambda: {}; cfg.owner = lambda: "fixture"
            resolver = load("tools/unfinished/lib/resolve_agentic.py", cfg)
            with sqlite3.connect(cfg.WORK_DB) as db:
                db.execute("create table resolved(item_id text primary key, out text, src text)")
                db.execute("insert into resolved values('cached',?, 'fixture')",(json.dumps({"status_now":"open"}),))
            (cfg.DATA/"ledger.jsonl").write_text("".join(json.dumps({"id":iid,"verdict":"open","project":"fixture","source_type":"session","first_seen":"2026-01-01","slug":iid})+"\n" for iid in ("cached","fresh")))
            seen=[]
            def answer(batch):
                seen.extend(x["id"] for x in batch)
                return {"results":[]},0
            with patch.object(resolver,"call",side_effect=answer),patch.object(sys,"argv",["resolve","1"]):
                resolver.main()
            self.assertEqual(seen,["fresh"])

    def test_owner_hidden_items_do_not_spend_the_refresh_limit(self):
        for decision in ({'decision':'done'}, {'decision':'drop'},
                         {'decision':'snooze','until':'2999-01-01T00:00:00+00:00'},
                         {'decision':'snooze','until':'2020-01-01T00:00:00+00:00'}):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as tmp:
                cfg = types.ModuleType('uf_config')
                cfg.DATA = cfg.STATE = Path(tmp); cfg.WORK_DB = cfg.STATE / 'work.db'
                cfg.CFG = {}; cfg.claude_env = lambda: {}; cfg.owner = lambda: 'fixture'
                resolver = load('tools/unfinished/lib/resolve_agentic.py', cfg)
                with sqlite3.connect(cfg.WORK_DB) as db:
                    db.execute('create table resolved(item_id text primary key, out text, src text)')
                    for iid in ('owner-decided','live'):
                        db.execute('insert into resolved values(?,?,?)',
                            (iid,json.dumps({'status_now':'open'}),'fixture'))
                rows = [{'id':iid,'verdict':'open','project':'fixture','source_type':'session',
                         'first_seen':'2026-01-01','slug':iid} for iid in ('owner-decided','live')]
                rows[0]['owner_decision'] = decision
                (cfg.DATA/'ledger.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
                seen = []
                def answer(batch):
                    seen.extend(x['id'] for x in batch)
                    return {'results':[]}, 0
                with patch.object(resolver,'call',side_effect=answer),patch.object(sys,'argv',['resolve','1']):
                    resolver.main()
                expected = 'owner-decided' if decision.get('until','').startswith('2020') else 'live'
                self.assertEqual(seen,[expected])

if __name__ == '__main__': unittest.main()
