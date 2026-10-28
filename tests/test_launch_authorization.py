"""Read-only launch authorization bridge; retain all fixtures for inspection."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'tools/model-policy-ops'
CLI = Path(os.environ.get('LAUNCH_AUTH_CLI', TOOL / 'model-policy-ops'))
PACK = TOOL.parents[1] / 'data/model-choice-policy/operating-points.json'
FIXTURES = Path(tempfile.mkdtemp(prefix='launch-authorization-'))


class LaunchAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(PACK.read_text())
        self.target = {'providerInstanceId': 'opencode', 'model': 'opencode/deepseek-v4.1-flash-free',
                       'options': {'variant': 'high'}}
        identity = {'provider': 'opencode', 'account': 'opencode', 'model': self.target['model'], 'effort': 'high'}
        self.receipt = {'schema_version': 1, 'decision_id': 'launch-auth-test', 'target': self.target, 'launch_ready': True,
                        'request': {'op': 'fanout.explore', 'data_classification': 'public',
                                    'overrides': identity.copy(), 'reason': 'synthetic bounded public review'},
                        'launch_facts': {'proof_class': 'judged'},
                        'data_policy': {'state': 'allowed_public_normal', 'route': self.target['model'],
                                        'targets': [identity.copy()]}}
        self.directory = Path(tempfile.mkdtemp(prefix='case-', dir=FIXTURES))

    def run_bridge(self, target=None, extra=()):
        policy = self.directory / 'policy.json'
        ledger = self.directory / 'receipts.jsonl'
        policy.write_text(json.dumps(self.policy))
        ledger.write_text(json.dumps(self.receipt) + '\n')
        ledger.chmod(0o600)
        before = ledger.read_bytes()
        proc = subprocess.run([sys.executable, str(CLI), 'authorize-launch', '--json', '--policy', str(policy),
                               '--receipts', str(ledger), '--decision-id', 'launch-auth-test',
                               '--launch-target', json.dumps(target or self.target), *extra],
                              text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(ledger.read_bytes(), before, 'authorization must never mutate the receipt ledger')
        return proc

    def authorization(self, expected, target=None):
        proc = self.run_bridge(target)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['authorized'], expected, proc.stdout)

    def test_current_public_exact_target_is_authorized(self):
        self.authorization(True)

    def test_current_public_list_options_is_same_binding(self):
        self.authorization(True, {**self.target, 'options': [{'id': 'variant', 'value': 'high'}]})

    def test_supported_strict_integer_receipt_schema_is_required(self):
        self.receipt.pop('schema_version')
        with self.subTest(version='missing'):
            self.authorization(False)
        for version in (None, True, 1.0, '1', 0, 2, {}):
            with self.subTest(version=version):
                self.receipt['schema_version'] = version
                self.authorization(False)

    def test_explicit_boolean_launch_readiness_is_required(self):
        self.receipt.pop('launch_ready')
        with self.subTest(ready='missing'):
            self.authorization(False)
        for ready in (None, False, 0, 1, 'false', 'true', []):
            with self.subTest(ready=ready):
                self.receipt['launch_ready'] = ready
                self.authorization(False)

    def test_sensitive_unknown_and_recorded_refusal_never_authorize(self):
        for classification in ('sensitive', None):
            with self.subTest(classification=classification):
                self.receipt['request']['data_classification'] = classification
                self.authorization(False)
        self.receipt['request']['data_classification'] = 'public'
        self.receipt['data_policy']['state'] = 'refused'
        self.authorization(False)

    def test_revoked_and_legacy_receipts_never_authorize(self):
        self.policy['free_routes'] = []
        self.authorization(False)
        self.policy = json.loads(PACK.read_text())
        self.receipt['data_policy']['state'] = 'allowed_public_experimental'
        self.authorization(False)
        self.receipt.pop('data_policy')
        self.authorization(False)

    def test_target_drift_and_scope_drift_never_authorize(self):
        self.authorization(False, {**self.target, 'options': {'variant': 'medium'}})
        self.authorization(False, {**self.target, 'options': {'effort': 'high'}})
        self.authorization(False, {**self.target, 'providerInstanceId': 'other-opencode'})
        self.receipt['launch_facts']['sensitive'] = True
        self.authorization(False)

    def test_no_record_no_probes_no_simulated_clock(self):
        for extra in (('--record',), ('--available', 'auto'), ('--available', ''), ('--quota', 'auto'), ('--quota', ''), ('--now', ''), ('--now', '2026-10-28T00:00:00Z')):
            with self.subTest(extra=extra):
                proc = self.run_bridge(extra=extra)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('authorize-launch requires', proc.stderr)


if __name__ == '__main__':
    print(f'Retained fixtures: {FIXTURES}', flush=True)
    unittest.main(verbosity=2)
