import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def route_tests():
    path = Path(__file__).with_name("test_route.py")
    spec = importlib.util.spec_from_file_location("route_cleanup_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_concurrent_fixture_reaps_children_when_later_spawn_fails(tmp_path, monkeypatch):
    module = route_tests()
    children = []

    class Child:
        returncode = None
        collected = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def communicate(self, timeout=None):
            self.collected = True
            return "", ""

    def spawn(*args, **kwargs):
        if len(children) == 2:
            raise OSError("injected spawn failure")
        child = Child()
        children.append(child)
        return child

    world = SimpleNamespace(tmp=tmp_path, receipts=tmp_path / "decisions.jsonl",
                            add_directive=lambda *args: None, write=lambda: None,
                            args=lambda: [])
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    with pytest.raises(OSError, match="injected spawn failure"):
        module.test_concurrent_directive_add_end_and_route_reads_never_tear_or_lose_a_row(world)
    assert len(children) == 2
    assert all(child.returncode == -9 and child.collected for child in children)


def test_lock_fixture_reaps_child_when_communication_times_out(tmp_path, monkeypatch):
    module = route_tests()
    directives = tmp_path / "directives.jsonl"
    directives.write_text('{"id":"seed"}\n')
    world = SimpleNamespace(tmp=tmp_path, receipts=tmp_path / "decisions.jsonl",
                            add_directive=lambda *args: None, write=lambda: None)
    calls = []

    class Child:
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def communicate(self, timeout=None):
            calls.append(timeout)
            if self.returncode is None:
                raise module.subprocess.TimeoutExpired("fixture", 30)
            return "", ""

    child = Child()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(module.subprocess.TimeoutExpired):
        module.test_directive_add_waits_for_a_held_exclusive_lock(world)
    assert child.returncode == -9
    assert calls == [30, 30]
