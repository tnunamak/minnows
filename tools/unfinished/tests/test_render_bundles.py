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

    def test_parked_projects_exclude_owner_hidden_and_resolved_closed_prs(self):
        decisions = []
        cases = [('done', '', None), ('drop', '', None),
                 ('snooze', '2999-01-01', None), ('snooze', '2020-01-01', None),
                 ('', '', 'done'), ('', '', 'superseded'), ('', '', 'open')]
        for group, (action, until, resolution) in enumerate(cases):
            repo = f'org/repo{group}'
            for number in (1, 2):
                ident = f'pr:{repo}#{number}'
                # An adjudication moves this PR from its original bundle into items.
                self.item(ident, adj=True, bundle='abandoned-prs', resolution=resolution)
                self.db.execute('update items set source_type=?,kind=?,title=?,links=? where item_id=?',
                    ('pr','open-pr',f'PR {repo}#{number}',json.dumps([f'https://github.com/{repo}/pull/{number}']),ident))
                if action:
                    decisions.append({'id':ident, 'decision':action, 'until':until})
        self.db.commit()
        (self.data / 'decisions.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in decisions))
        self.assertEqual(self.db.execute('select count(*) from adj').fetchone()[0], 14)
        output = self.publish()
        parked = output.split('## Parked projects:', 1)[1].split('## ', 1)[0]
        self.assertIn('**org/repo3**: 2 open PRs', parked)
        self.assertIn('**org/repo6**: 2 open PRs', parked)
        for group in (0, 1, 2, 4, 5):
            self.assertNotIn(f'**org/repo{group}**', parked)
        records = {x['id']:x for x in map(json.loads,(self.data/'ledger.jsonl').read_text().splitlines())}
        self.assertEqual(len(records), 14)

    def test_in_flight_pr_keeps_its_repo_out_of_parked_projects(self):
        today = self.render.NOW.strftime('%Y-%m-%d')
        for number, last_ts, needs_owner in [(1, '2026-01-01', True), (2, '2026-01-01', True), (3, today, False)]:
            ident = f'pr:org/busy#{number}'
            self.item(ident, adj=True)
            self.db.execute('update adj set out=? where item_id=?', (json.dumps({'verdict':'open', 'next_action':ident,
                'why':'fixture', 'priority':3, 'effort':'minutes', 'needs_owner':needs_owner}), ident))
            self.db.execute('update items set source_type=?,kind=?,title=?,links=?,last_ts=? where item_id=?',
                ('pr','open-pr',f'PR org/busy#{number}',json.dumps([f'https://github.com/org/busy/pull/{number}']),last_ts,ident))
        self.db.commit()
        output = self.publish()
        self.assertIn('pr:org/busy#3', output.split('## In flight', 1)[1])
        self.assertNotIn('**org/busy**', output)

if __name__ == '__main__': unittest.main()
