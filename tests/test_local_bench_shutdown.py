"""Exercise benchmark lifecycle failures without models or a live server."""
import io
import pathlib
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class BenchmarkReached(Exception):
    pass


class Child:
    def __init__(self, exited=False):
        self.exited = exited
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self):
        return 1 if self.exited else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits += 1
        return 1


class ServerShutdownTests(unittest.TestCase):
    def run_script(self, name, child, health):
        modules = {name: types.ModuleType(name) for name in (
            "numpy", "psutil", "scenarios", "decider", "decider.infer",
            "decider.systemone", "decider.temperature")}
        for function in ("mixed_request", "choice20_request", "single_choice12_request"):
            setattr(modules["scenarios"], function, lambda: {})
        def reached(*args, **kwargs):
            raise BenchmarkReached("benchmark started")
        modules["decider.infer"].Decider = reached
        modules["decider.systemone"].assemble = lambda *args: None
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules), \
             patch.object(sys, "argv", [name, "synthetic.gguf", "12345", str(pathlib.Path(tmp) / "result.json")]), \
             patch("subprocess.Popen", return_value=child), \
             patch("urllib.request.urlopen", side_effect=health), patch("time.sleep"):
            runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / "data/decision-model-catalog/evidence/local-decider-cpu-bench" / name), run_name="__main__")

    def test_shutdown_timeout_kills_and_reaps_owned_child(self):
        for name in ("bench_server.py", "bench_server_prime.py"):
            child = Child()
            def wait(timeout=None):
                child.waits += 1
                if child.waits == 1:
                    raise subprocess.TimeoutExpired("synthetic-server", timeout)
                return -9
            child.wait = wait
            with self.subTest(name=name), self.assertRaises(BenchmarkReached):
                try:
                    self.run_script(name, child, lambda *a, **k: io.BytesIO(b'{"status":"ok"}'))
                finally:
                    self.assertTrue(child.killed, "owned server was not killed")
                    self.assertEqual(child.waits, 2, "killed server was not reaped")


if __name__ == "__main__":
    unittest.main()
