import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parents[1] / 'tools' / 'unfinished' / 'lib'
sys.path.insert(0, str(LIB))
SPEC = importlib.util.spec_from_file_location('cluster_top', LIB / 'cluster_top.py')
CLUSTER_TOP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLUSTER_TOP)


@pytest.mark.parametrize(
    ('returncode', 'stdout'),
    [
        (0, '{"type":"result","is_error":true,"structured_output":null}'),
        (1, '{"type":"result","structured_output":{"groups":[]}}'),
        (0, '{"type":"result","structured_output":{}}'),
    ],
)
def test_model_error_does_not_replace_existing_clusters(tmp_path, monkeypatch, returncode, stdout):
    state = tmp_path / 'state'
    state.mkdir()
    database = state / 'work.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('create table items (item_id text primary key, project text)')
        db.execute('create table resolved (item_id text primary key, out text)')
        db.execute("insert into items values ('pr:1', 'repo')")
        db.execute("insert into resolved values ('pr:1', ?)", (json.dumps({'status_now': 'open'}),))
        db.execute('create table top_clusters (item_id text primary key, survivor text, merged_action text)')
        db.execute("insert into top_clusters values ('pr:1', 'pr:1', 'Keep the existing action')")

    monkeypatch.setattr(CLUSTER_TOP, 'WORK_DB', database)
    monkeypatch.setattr(CLUSTER_TOP, 'STATE', state)
    monkeypatch.setattr(
        CLUSTER_TOP.subprocess,
        'run',
        lambda *args, **kwargs: __import__('subprocess').CompletedProcess(
            args[0], returncode, stdout=stdout, stderr='model failure'
        ),
    )

    try:
        CLUSTER_TOP.main()
    except (RuntimeError, SystemExit):
        pass

    with sqlite3.connect(database) as db:
        row = db.execute('select item_id, survivor, merged_action from top_clusters').fetchone()
    assert row == ('pr:1', 'pr:1', 'Keep the existing action')
