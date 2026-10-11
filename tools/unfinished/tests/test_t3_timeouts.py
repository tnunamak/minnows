import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
sys.argv = [sys.argv[0]]
config = types.ModuleType('uf_config')
config.CFG = {'t3': True}; config.CONVO_LEDGER = Path('unused'); config.WORK_DB = Path('unused')
config.since_default = lambda: '2026-10-01'
spec = importlib.util.spec_from_file_location('ingest_t3', ROOT / 'tools/unfinished/lib/ingest_t3.py')
ingestion = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'uf_config': config}): spec.loader.exec_module(ingestion)

class TimeoutTest(unittest.TestCase):
    def test_failed_worker_does_not_abandon_successful_thread(self):
        for failure in [subprocess.TimeoutExpired('fixture', 120), OSError('fixture unavailable')]:
            with self.subTest(failure=type(failure).__name__):
                def execute(argv, **kwargs):
                    tid = argv[argv.index('--thread') + 1]
                    if tid == 'failed': raise failure
                    return subprocess.CompletedProcess(argv, 0, json.dumps({'data': {'thread': {'id': tid}}}), '')
                with patch.object(ingestion.subprocess, 'run', execute), ThreadPoolExecutor(2) as executor:
                    results = list(executor.map(ingestion.read, ['failed', 'good']))
                self.assertEqual(results, [('failed', None), ('good', {'id': 'good'})])

if __name__ == '__main__': unittest.main()
