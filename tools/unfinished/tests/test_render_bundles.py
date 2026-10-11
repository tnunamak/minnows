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

    def test_bundle_owner_decisions_hide_terminal_and_active_snoozes(self):
        decisions=[]
        for action, until in [('done',''), ('drop',''), ('snooze','2999-01-01'), ('snooze','2020-01-01')]:
            ident='pr:'+action+until
            self.item(ident, adj=False, bundle='old-prs')
            decisions.append({'id':ident, 'decision':action, 'until':until})
        (self.data / 'decisions.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in decisions))
        output=self.publish()
        for decision in decisions[:3]:
            self.assertNotIn('- [ ] '+decision['id'], output)
        self.assertIn('- [ ] '+decisions[3]['id'], output)
        records={x['id']:x for x in map(json.loads,(self.data/'ledger.jsonl').read_text().splitlines())}
        for decision in decisions:
            self.assertEqual(records[decision['id']]['owner_decision'], decision)

if __name__ == '__main__': unittest.main()
