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

class SelectionTest(unittest.TestCase):
    def test_normal_run_scores_mid_session_reply_before_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = types.ModuleType("uf_config")
            cfg.DATA = cfg.STATE = Path(tmp); cfg.WORK_DB = cfg.STATE/"work.db"
            cfg.CFG = {"relayed_prefixes":[]}
            cfg.ensure_dirs=lambda:None;cfg.since_default=lambda *a:"2026-01-01"
            cli=load("tools/unfinished/unfinished",cfg)
            with sqlite3.connect(cfg.WORK_DB) as db:
                db.executescript("create table sessions(session_key text,n_human int);create table segments(seg_id text,kind text,session_key text,ts text,next_ts text,ordinal int,unanswered int,text text);create table jev1(seg_id text,answers text);")
                db.execute("insert into sessions values('session',3)")
                db.executemany("insert into segments values(?,?,?,?,?,?,?,?)", [
                    ("reply","reply","session","2026-01-02T10:00:00","2026-01-02T10:10:00",1,0,"Choose A? Choose B? Choose C?"),
                    ("user","user","session","2026-01-02T10:10:00",None,2,0,"Only A.")])
            def step(label,script,*args):
                if script=="jev_pass1.py":
                    with sqlite3.connect(cfg.WORK_DB) as db:
                        ids=[x[0] for x in db.execute("select seg_id from segments where "+args[0])]
                        for sid in ids:
                            db.execute("insert into jev1 values(?,?)",(sid,json.dumps({"asks_human":{"noul":0.9 if sid=="reply" else 0},"decision":{"noul":0.9},"human":{"noul":1}})))
                if script=="select_cands.py":load("tools/unfinished/lib/select_cands.py",cfg).main()
            with patch.object(cli,"_cfg",return_value=cfg), patch.object(cli,"_step",side_effect=step), patch.object(cli.subprocess,"run"):
                cli.cmd_run([])
            with sqlite3.connect(cfg.WORK_DB) as db:
                self.assertEqual(db.execute("select why from cands where seg_id='reply'").fetchone(),("asks-tim-mid",))

if __name__ == '__main__': unittest.main()
