import importlib.util
from importlib.machinery import SourceFileLoader
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

def load(path, cfg):
    loader = SourceFileLoader("fixture_module", str(ROOT / path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"uf_config": cfg}):
        loader.exec_module(module)
    return module

import fcntl

class LockTest(unittest.TestCase):
    def test_render_obeys_run_lock_then_releases_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = types.ModuleType("uf_config")
            cfg.STATE = Path(tmp)
            cfg.ensure_dirs = lambda: None
            cli = load("tools/unfinished/unfinished", cfg)
            with patch.object(cli, "_cfg", return_value=cfg), patch.object(cli, "_step") as step:
                with open(cfg.STATE / ".run.lock", "w") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(cli.main(["render"]), 0)
                    step.assert_not_called()
                self.assertEqual(cli.main(["render"]), 0)
                step.assert_called_once_with("render", "render.py")
                with open(cfg.STATE / ".run.lock", "w") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

if __name__ == '__main__': unittest.main()
