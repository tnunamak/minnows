"""Fixture-only tests: no CLI launches or live state."""
import sqlite3
from pathlib import Path

import pytest
import test_route
from test_route import World, PREFIX, event

pytestmark = pytest.mark.offline_unit


def counted_world(tmp_path, monkeypatch):
    builds = []
    original = test_route.build_db
    def counted(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(test_route, 'build_db', counted)
    return World(tmp_path), builds


def test_five_unchanged_writes_build_once(tmp_path, monkeypatch):
    world, builds = counted_world(tmp_path, monkeypatch)
    writes = []
    original = Path.write_text
    def counted(path, *args, **kwargs):
        writes.append(path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', counted)
    for _ in range(5):
        world.write()
    assert len(builds) == 1
    assert sorted(writes) == ['accounts.json', 'catalog.json', 'policy.json', 'quota.json']


def test_nested_json_mutation_and_disk_damage_are_restored(tmp_path, monkeypatch):
    world, builds = counted_world(tmp_path, monkeypatch)
    world.write()
    world.accounts['accounts']['fixture-test'] = {'billing': 'subscription'}
    world.write()
    assert 'fixture-test' in (tmp_path / 'accounts.json').read_text()
    (tmp_path / 'quota.json').write_bytes(b'\xff')
    (tmp_path / 'catalog.json').unlink()
    world.write()
    import json
    assert json.loads((tmp_path / 'quota.json').read_text()) == world.quota
    assert json.loads((tmp_path / 'catalog.json').read_text()) == world.catalog
    assert len(builds) == 1


def test_events_extra_runs_items_and_order_invalidate(tmp_path, monkeypatch):
    world, builds = counted_world(tmp_path, monkeypatch)
    world.events = [event('a', 'first'), event('b', 'second')]
    world.write()
    world.events[0]['message'] = 'changed'
    world.write()
    world.db_extra['runs'].append(('extra-run', 'extra-instance', '{}'))
    world.write()
    world.db_extra['items'].append(('extra-item', 't', 'extra-run', 'message',
                                    'completed', '2026-10-10T00:20:00Z', '{}'))
    world.write()
    world.events.reverse()
    world.write()
    assert len(builds) == 5
    with sqlite3.connect(tmp_path / 't3.sqlite') as db:
        assert db.execute(f'SELECT provider_instance_id FROM {PREFIX}runs WHERE run_id=?',
                          ('run-0',)).fetchone() == ('b',)
        assert db.execute(f'SELECT count(*) FROM {PREFIX}turn_items').fetchone() == (3,)


def test_disk_db_mutation_and_deletion_invalidate(tmp_path, monkeypatch):
    world, builds = counted_world(tmp_path, monkeypatch)
    world.write()
    with sqlite3.connect(tmp_path / 't3.sqlite') as db:
        db.execute(f'DELETE FROM {PREFIX}runs')
    world.write()
    (tmp_path / 't3.sqlite').unlink()
    world.write()
    assert len(builds) == 3


def test_failed_build_does_not_cache_success(tmp_path, monkeypatch):
    world = World(tmp_path)
    original = test_route.build_db
    def fail(path, *args, **kwargs):
        path.write_bytes(b'partial')
        raise RuntimeError('injected build failure')
    monkeypatch.setattr(test_route, 'build_db', fail)
    with pytest.raises(RuntimeError, match='injected'):
        world.write()
    monkeypatch.setattr(test_route, 'build_db', original)
    world.write()
    with sqlite3.connect(tmp_path / 't3.sqlite') as db:
        assert db.execute(f'SELECT count(*) FROM {PREFIX}runs').fetchone()[0] == len(world.events)


def test_worlds_do_not_share_mutable_state(tmp_path):
    left, right = tmp_path / 'left', tmp_path / 'right'
    left.mkdir()
    right.mkdir()
    a, b = World(left), World(right)
    a.events.clear()
    a.write()
    b.write()
    assert b.events
    assert a.receipts != b.receipts
