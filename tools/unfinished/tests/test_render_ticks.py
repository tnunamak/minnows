import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[1])
sys.argv = [sys.argv[0]]

class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        cfg = types.ModuleType('uf_config')
        cfg.DATA = self.data; cfg.EVIDENCE = self.data / 'evidence'; cfg.STATE = self.data
        cfg.WORK_DB = self.data / 'work.db'
        spec = importlib.util.spec_from_file_location('render_fixture', ROOT / 'tools/unfinished/lib/render.py')
        self.render = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'uf_config': cfg}): spec.loader.exec_module(self.render)
        self.db = sqlite3.connect(cfg.WORK_DB)
        self.addCleanup(self.db.close)
        self.db.executescript('\n            create table items(item_id text primary key, source_type text, kind text, project text, title text,\n                detail text, owner text, ts text, last_ts text, status_reason text, links text, quote text,\n                session_key text, seg_id text);\n            create table prerank(item_id text, score int, bundle text);\n            create table adj(item_id text, out text);\n            create table evidence(item_id text, prs text, snippets text);\n            create table t3threads(session_key text, url text);\n            create table dups(item_id text, survivor text);\n            create table resolved(item_id text, out text);\n        ')
        self.render.write_evidence = lambda db, item: None

    def item(self, ident, adj=True, bundle=None, resolution=None):
        self.db.execute('insert into items values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, 'session', 'task', 'fixture', ident, '{}', 'owner', '2026-01-01', '2026-01-01', '', '[]', '', '', ''))
        self.db.execute('insert into prerank values(?,?,?)', (ident, 1, bundle))
        if adj:
            self.db.execute('insert into adj values(?,?)', (ident, json.dumps({'verdict':'open', 'next_action':ident,
                'why':'fixture', 'priority':3, 'effort':'minutes', 'needs_owner':True})))
        if resolution:
            self.db.execute('insert into resolved values(?,?)', (ident, json.dumps({'status_now':resolution,
                'evidence':'verified fixture', 'next_action':ident, 'why':'fixture', 'priority':3, 'effort':'minutes'})))
        self.db.commit()

    def publish(self):
        self.render.main()
        return (self.data / 'LEDGER.md').read_text()

    def test_tick_supersedes_snooze_and_keeps_generation_protection(self):
        ident = 's:fixture'
        (self.data / 'decisions.jsonl').write_text(json.dumps({'id':ident, 'decision':'snooze', 'until':'2020-01-01'})+'\n')
        (self.data / '.generation').write_text('current')
        (self.data / 'LEDGER.md').write_text('<!-- generation: stale -->\n- [x] task `s:fixture`\n')
        self.assertEqual(self.render.import_ticks(), 0)
        (self.data / 'LEDGER.md').write_text('<!-- generation: current -->\n- [x] task `s:fixture`\n')
        self.assertEqual(self.render.import_ticks(), 1)
        self.assertEqual(self.render.load_decisions()[ident]['decision'], 'done')
        self.assertEqual(self.render.import_ticks(), 0)

    def test_newer_active_snooze_wins_over_an_older_unimported_tick(self):
        ident = 's:fixture'
        ledger = self.data / 'LEDGER.md'
        (self.data / '.generation').write_text('current')
        ledger.write_text('<!-- generation: current -->\n- [x] task `s:fixture`\n')
        tick_at = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        os.utime(ledger, (tick_at.timestamp(), tick_at.timestamp()))
        decision = {'id':ident, 'decision':'snooze',
                    'at':(tick_at + dt.timedelta(seconds=1)).isoformat(), 'until':'2999-01-01'}
        (self.data / 'decisions.jsonl').write_text(json.dumps(decision)+'\n')
        self.assertEqual(self.render.import_ticks(), 0)
        self.assertEqual(self.render.load_decisions()[ident], decision)
        # A subsequent checkbox edit is newer than the decision and can close it.
        later_tick = tick_at + dt.timedelta(seconds=2)
        os.utime(ledger, (later_tick.timestamp(), later_tick.timestamp()))
        self.assertEqual(self.render.import_ticks(), 1)
        self.assertEqual(self.render.load_decisions()[ident]['decision'], 'done')
        self.assertEqual(self.render.import_ticks(), 0)

if __name__ == '__main__': unittest.main()
