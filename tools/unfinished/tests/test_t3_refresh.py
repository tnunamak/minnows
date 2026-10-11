import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
sys.argv = [sys.argv[0]]

class RefreshTest(unittest.TestCase):
    def test_changed_tail_invalidates_both_caches_and_unchanged_tail_keeps_them(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / 'work.db'
            convo = directory / 'convo.db'
            db = sqlite3.connect(work)
            db.executescript('''
                create table segments (seg_id text primary key, kind text, harness text, session_key text,
                  source_path text, project text, ts text, ordinal int, text text, context text,
                  unanswered int, human_rule int, next_ts text);
                create table jev1 (seg_id text primary key, answers text);
                create table seg_items (seg_id text primary key, items text, model text);
            ''')
            source = sqlite3.connect(convo)
            source.executescript('''
                create table source_files (id int, harness text, session_id text, path text);
                create table messages (source_id int, text text, message_ts text, role text);
            ''')
            source.close()
            cfg = types.ModuleType('uf_config')
            cfg.CFG = {'t3': True}; cfg.CONVO_LEDGER = convo; cfg.WORK_DB = work
            cfg.since_default = lambda: '2026-10-01'
            spec = importlib.util.spec_from_file_location('ingest_t3', ROOT / 'tools/unfinished/lib/ingest_t3.py')
            ingestion = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {'uf_config': cfg}): spec.loader.exec_module(ingestion)
            tid = 'fixture-thread'
            thread = {'id': tid, 'projectId': 'project', 'title': 'fixture', 'status': 'active',
                      'createdAt': '2026-10-01', 'latestUserMessageAt': '2026-10-10',
                      'latestRunCompletedAt': '2026-10-10'}
            tail = {'messages': [{'role': 'user', 'text': 'next request'},
                                 {'role': 'assistant', 'text': 'new unresolved blocker', 'createdAt': '2026-10-09'}]}
            def execute(argv, **kwargs):
                payload = {'data': {'thread': tail}} if 'read' in argv else {'data': {
                    'runtime': {'environmentId': 'fixture'}, 'projects': [], 'threads': [thread]}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')
            segment = 't3-' + tid
            db.execute('insert into segments values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (segment, 't3tail', 't3', 't3:fixture', '', '', '2026-10-08', 0, 'all complete', '', 0, 0, None))
            for stage, changed in [('changed text', True), ('unchanged', False), ('changed context', True)]:
                db.execute('insert or replace into jev1 values (?,?)', (segment, '{"complete":1}'))
                db.execute('insert or replace into seg_items values (?,?,?)', (segment, '[]', 'fixture-model'))
                db.commit()
                if stage == 'changed context': tail['messages'][0]['text'] = 'different request'
                with patch.object(ingestion.subprocess, 'run', execute): ingestion.main()
                self.assertEqual(db.execute('select text from segments where seg_id=?', (segment,)).fetchone()[0],
                                 'new unresolved blocker')
                for table in ['jev1', 'seg_items']:
                    self.assertEqual(db.execute(f'select count(*) from {table} where seg_id=?', (segment,)).fetchone()[0],
                                     0 if changed else 1, f'{stage}: {table}')
            db.executescript('drop table jev1; drop table seg_items;')
            tail['messages'][-1]['text'] = 'tail before classification tables exist'
            with patch.object(ingestion.subprocess, 'run', execute): ingestion.main()
            self.assertEqual(db.execute('select text from segments where seg_id=?', (segment,)).fetchone()[0],
                             'tail before classification tables exist')
            db.close()

if __name__ == '__main__': unittest.main()
