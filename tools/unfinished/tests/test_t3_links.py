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

ROOT = Path(__file__).resolve().parents[3]

class LinkTest(unittest.TestCase):
    def test_unchanged_metadata_refresh_preserves_native_session_link(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            work = directory / 'work.db'; convo = directory / 'convo.db'
            db = sqlite3.connect(work)
            db.execute('''create table segments (seg_id text primary key, kind text, harness text, session_key text,
                        source_path text, project text, ts text, ordinal int, text text, context text,
                        unanswered int, human_rule int, next_ts text)''')
            source = sqlite3.connect(convo)
            source.executescript('''
                create table source_files (id int, harness text, session_id text, path text);
                create table messages (source_id int, text text, message_ts text, role text);
                insert into source_files values (1, 'codex', 'native', 'fixture');
                insert into messages values (1, 'matching final response', '2026-10-09', 'assistant');
            ''')
            source.close()
            cfg = types.ModuleType('uf_config')
            cfg.CFG = {'t3': True}; cfg.CONVO_LEDGER = convo; cfg.WORK_DB = work
            cfg.since_default = lambda: '2026-10-01'
            spec = importlib.util.spec_from_file_location('ingest_t3', ROOT / 'tools/unfinished/lib/ingest_t3.py')
            ingestion = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {'uf_config': cfg}): spec.loader.exec_module(ingestion)
            thread = {'id': 'fixture-thread', 'projectId': 'project', 'title': 'fixture', 'status': 'active',
                      'createdAt': '2026-10-01', 'latestUserMessageAt': '2026-10-09',
                      'latestRunCompletedAt': '2026-10-09'}
            reads = []
            def execute(argv, **kwargs):
                if 'read' in argv:
                    reads.append('read')
                    payload = {'data': {'thread': {'messages': [{'role': 'assistant', 'text': 'matching final response',
                                                               'createdAt': '2026-10-09'}]}}}
                else: payload = {'data': {'runtime': {'environmentId': 'fixture'}, 'projects': [], 'threads': [thread]}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')
            with patch.object(ingestion.subprocess, 'run', execute):
                ingestion.main()
                self.assertEqual(db.execute('select session_key from t3threads').fetchone()[0], 'codex:native')
                ingestion.main()
            self.assertEqual(reads, ['read'])
            self.assertEqual(db.execute('select session_key from t3threads').fetchone()[0], 'codex:native')
            db.close()

if __name__ == '__main__': unittest.main()
