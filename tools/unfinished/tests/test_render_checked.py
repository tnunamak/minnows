import datetime as dt
import importlib.util
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

    def test_top_and_start_only_admit_checked_open_items(self):
        self.item('s:unchecked')
        self.item('s:unclear', resolution='unclear')
        self.item('s:checked', resolution='open')
        output=self.publish()
        top=output.split('## Top 25')[1].split('## Not yet checked')[0]
        start=output.split('## Start here')[1].split('## Top 25')[0]
        for section in (top,start):
            self.assertIn('s:checked', section)
            self.assertNotIn('s:unchecked', section)
            self.assertNotIn('s:unclear', section)
        rest=output.split('## Not yet checked')[1].split('## In flight')[0]
        self.assertIn('s:unchecked', rest)
        self.assertIn('s:unclear', rest)
        rows={line.rsplit('`',2)[-2]:line for line in rest.splitlines() if line.startswith('- [ ]')}
        self.assertIn('checked: unclear', rows['s:unclear'])
        self.assertNotIn('checked: unclear', rows['s:unchecked'])

if __name__ == '__main__': unittest.main()
