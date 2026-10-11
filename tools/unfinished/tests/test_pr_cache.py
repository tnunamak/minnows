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

class CacheTest(unittest.TestCase):
    def test_open_closed_and_errors_refresh_but_merged_remains_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = types.ModuleType("uf_config")
            cfg.CFG = {}; cfg.WORK_DB = Path(tmp)/"work.db"; cfg.CONVO_LEDGER = Path(tmp)/"convo.db"
            with sqlite3.connect(cfg.CONVO_LEDGER): pass
            with sqlite3.connect(cfg.WORK_DB) as db:
                db.executescript("create table items(item_id text, source_type text, project text, title text, detail text, quote text, ts text, session_key text);create table prs(ref text primary key, data text);")
                for n,state in enumerate(("OPEN", "CLOSED", "error", "MERGED"),1):
                    db.execute("insert into items values(?,?,?,?,?,?,?,?)", (str(n),"pr","fixture",f"PR example/repo#{n}","{}","","", ""))
                    old={"error":"temporary"} if state=="error" else {"state":state}
                    db.execute("insert into prs values(?,?)", (f"example/repo#{n}",json.dumps(old)))
            evidence = load("tools/unfinished/lib/resolve_evidence.py", cfg)
            seen=[]
            def lookup(ref):
                seen.append(ref);return ref,{"state":"MERGED", "url":f"https://example.invalid/{ref[1]}"}
            with patch.object(evidence,"repo_map",return_value={}), patch.object(evidence,"gh_state",side_effect=lookup):
                evidence.main()
            self.assertEqual(set(seen), {("example/repo",1),("example/repo",2),("example/repo",3)})
            with sqlite3.connect(cfg.WORK_DB) as db:
                self.assertEqual({json.loads(data)["state"] for data, in db.execute("select data from prs")},{"MERGED"})
            seen.clear()
            with patch.object(evidence,"repo_map",return_value={}), patch.object(evidence,"gh_state",side_effect=lookup):
                evidence.main()
            self.assertEqual(seen,[])

    def test_failed_refresh_keeps_cached_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = types.ModuleType("uf_config")
            cfg.CFG = {}; cfg.WORK_DB = Path(tmp)/"work.db"; cfg.CONVO_LEDGER = Path(tmp)/"convo.db"
            with sqlite3.connect(cfg.CONVO_LEDGER): pass
            with sqlite3.connect(cfg.WORK_DB) as db:
                db.executescript("create table items(item_id text, source_type text, project text, title text, detail text, quote text, ts text, session_key text);create table prs(ref text primary key, data text);")
                for n in (1, 2):
                    db.execute("insert into items values(?,?,?,?,?,?,?,?)", (str(n),"pr","fixture",f"PR example/repo#{n}","{}","","", ""))
                db.execute("insert into prs values(?,?)", ("example/repo#1",json.dumps({"state":"OPEN","url":"https://example.invalid/1"})))
            evidence = load("tools/unfinished/lib/resolve_evidence.py", cfg)
            with patch.object(evidence,"repo_map",return_value={}), \
                 patch.object(evidence,"gh_state",side_effect=lambda ref:(ref,{"error":"gh auth expired"})):
                evidence.main()
            with sqlite3.connect(cfg.WORK_DB) as db:
                rows = {ref:json.loads(data) for ref,data in db.execute("select ref, data from prs")}
            self.assertEqual(rows["example/repo#1"]["state"], "OPEN")
            self.assertEqual(rows["example/repo#2"], {"error":"gh auth expired"})

if __name__ == '__main__': unittest.main()
