"""Deterministic, offline fixtures for read-only decision reconciliation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'tools/model-policy-ops/model-policy-ops'
sys.path.insert(0, str(CLI.parent / 'lib'))
import cost_report as report
import delegation_audit as audit
import observations

NOW = '2026-10-15T12:00:00+00:00'
START = '2026-10-01T12:00:00+00:00'
MODEL = 'gpt-6.1-sol-2026-10-14'
TARGET = {'providerInstanceId': 'codex', 'model': MODEL, 'options': {'reasoningEffort': 'xhigh'}}


def receipt(d='d1', at='2026-10-14T10:00:00+00:00', **extra):
    return {'schema_version': 1, 'decision_id': d, 'recorded_at': at,
            'request': {'op': 'implement.standard', 'account': 'caller-hint-not-target'},
            'parent': {'thread_id': 'parent-thread'}, 'target': copy.deepcopy(TARGET),
            'selection': {'arm': {'provider': 'wrong-fallback', 'model': 'fallback-only'}},
            'reason': 'PRIVATE RECEIPT TEXT', **extra}


def close(d='d1', cid='c1', outcome='accepted', supersedes=None, repairs=None, at=NOW):
    return {'schema_version': 1, 'kind': 'close', 'decision_id': d, 'close_id': cid,
            'outcome': outcome, 'judged_by': 'parent', 'check': 'none', 'repairs': repairs,
            'owner_input': 'unknown', 'evidence': [], 'note': 'PRIVATE CLOSE TEXT',
            'reason': 'PRIVATE REASON', 'supersedes': supersedes, 'closer_thread': None,
            'recorded_at': at, 'evidence_summary': {'accepted_with_hash_matched_check_log': False}}


def observation(kind, fact, d='d1', source_ids=None, timestamps=None):
    row = observations.make_observation('t3', 'run:child', d, kind, fact,
              source_ids=source_ids or {'run_id': 'run:child'}, source_timestamps=timestamps)
    return row | {'observed_at': NOW, 'run_id': 'observation-run'}


def turn(tid='turn:1', n=150, **extra):
    return {'provider_turn_id': tid, 'ordinal': 1, 'turn_status': 'completed',
            'usage_status': 'complete', 'usage_scope': 'main_agent', 'has_subagents': False,
            'input_tokens': n, 'cached_input_tokens': 30, 'cache_creation_tokens': 20,
            'output_tokens': 40, 'reasoning_tokens': 10, **extra}


def call(d='d1', cid='call:1', run='run:child', turns=None, **extra):
    return {'call_id': cid, 'decision_id': d, 'thread_id': 'parent-thread',
            'timestamp': '2026-10-14T11:00:00+00:00', 'target': copy.deepcopy(TARGET),
            'child_run_id': run, 'status': 'completed', 'child_status': 'completed',
            'child_provider': 'codex', 'child_requested_model': MODEL,
            'child_requested_options': {'reasoningEffort': 'xhigh'},
            'observed_child_usage': {'state': 'complete', 'attempts': [
                {'attempt_id': 'attempt:1', 'attempt_ordinal': 1, 'turns': turns or [turn()]}]}, **extra}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir(mode=0o700)
        self.outputs = self.root / 'outputs'
        self.outputs.mkdir(mode=0o700)
        self.args = SimpleNamespace(receipts=self.inputs / 'receipts.jsonl',
                   outcomes=self.inputs / 'outcomes.jsonl', observations=self.inputs / 'observations.jsonl',
                   db=None, export=self.inputs / 'export.json', report_dir=self.outputs / 'report',
                   record=False, now=None, op=None, thread=None, since=START)
        self.write_sources()

    def write_jsonl(self, path, rows):
        path.write_bytes((' \n' + ''.join(json.dumps(r) + '\n' for r in rows)).encode())

    def write_sources(self, receipts=None, closes=(), observed=None, delegations=()):
        self.write_jsonl(self.args.receipts, [receipt()] if receipts is None else receipts)
        self.write_jsonl(self.args.outcomes, closes)
        if observed is not None:
            self.write_jsonl(self.args.observations, observed)
        self.args.export.write_text(json.dumps({'schema_version': 1, 'delegations': list(delegations)}))

    def make(self, receipts=None, closes=(), observed=(), calls=None, since=None):
        return report.make_report([receipt()] if receipts is None else receipts, list(closes), list(observed),
                 report.project_calls([call()] if calls is None else calls), None,
                 {'db': {'path': '/fixture/db.sqlite', 'state': 'transaction_projection'}},
                 as_of=NOW, since=since)

    def create_db(self, two_turns=False):
        path = self.inputs / 'fixture.sqlite'
        db = sqlite3.connect(path)
        prefix = audit.PREFIX
        db.executescript(f'''
        CREATE TABLE {prefix}runs(run_id TEXT PRIMARY KEY, provider_instance_id TEXT, status TEXT, payload_json TEXT);
        CREATE TABLE {prefix}turn_items(turn_item_id TEXT,thread_id TEXT,run_id TEXT,type TEXT,status TEXT,payload_json TEXT);
        CREATE TABLE {prefix}run_attempts(attempt_id TEXT,run_id TEXT,attempt_ordinal INTEGER,provider_turn_id TEXT);
        CREATE TABLE {prefix}provider_turns(provider_turn_id TEXT,run_attempt_id TEXT,ordinal INTEGER,status TEXT,payload_json TEXT);
        CREATE INDEX {prefix}run_attempts_run_id_idx ON {prefix}run_attempts(run_id,attempt_ordinal);
        CREATE INDEX {prefix}provider_turns_attempt_id_idx ON {prefix}provider_turns(run_attempt_id,ordinal);
        ''')
        for run in ('run:parent', 'run:child'):
            db.execute(f'INSERT INTO {prefix}runs VALUES(?,?,?,?)',
                (run, 'codex', 'completed', json.dumps({'modelSelection': {'model': MODEL, 'options': {'reasoningEffort': 'xhigh'}},
                                                     'title': 'PRIVATE RUN TITLE'})))
        payload = {'toolName': 'delegate_task', 'startedAt': '2026-10-14T11:00:00Z',
                   'input': {'clientRequestId': 'd1', 'target': TARGET, 'task': 'PRIVATE PROMPT'},
                   'output': {'childRunId': 'run:child'}}
        db.execute(f'INSERT INTO {prefix}turn_items VALUES(?,?,?,?,?,?)',
                   ('call:1', 'parent-thread', 'run:parent', 'dynamic_tool', 'completed', json.dumps(payload)))
        db.execute(f'INSERT INTO {prefix}run_attempts VALUES(?,?,?,?)', ('attempt:1', 'run:child', 1, 'turn:1'))
        for i in range(1, 3 if two_turns else 2):
            values = {'usageStatus': 'complete', 'usageScope': 'main_agent', 'hasSubagents': False,
                      'inputTokens': 150, 'cachedInputTokens': 30, 'cacheCreationTokens': 20,
                      'outputTokens': 40, 'reasoningTokens': 10}
            db.execute(f'INSERT INTO {prefix}provider_turns VALUES(?,?,?,?,?)',
                       (f'turn:{i}', 'attempt:1', i, 'completed', json.dumps({'turnTokenUsage': values})))
        db.commit()
        db.close()
        return path

    def test_authoritative_150_replaces_historical_100_not_250(self):
        old = observation('t3_turn_usage', turn(n=100))
        new = observation('t3_turn_usage', turn(n=150))
        r = self.make(observed=[new, old])  # reverse collection order cannot change totals
        self.assertEqual(r['daily'][0]['tokens']['input_tokens']['observed_sum'], 150)
        self.assertEqual(r['coverage']['historical_usage_observations_ignored'], 2)
        self.assertEqual(r, self.make(observed=[old, new]))

    def test_two_turns_same_numbers_count_twice_and_joins_once(self):
        c = call(turns=[turn(), turn('turn:2')])
        r = self.make(calls=[c, copy.deepcopy(c), call(cid='call:2', turns=[turn(), turn('turn:2')])])
        self.assertEqual(r['daily'][0]['tokens']['input_tokens']['observed_sum'], 300)
        self.assertEqual(r['daily'][0]['calls'], 2)
        self.assertEqual(len(r['usage']), 2)

    def test_run_decision_conflicts_excluded(self):
        r = self.make(receipts=[receipt(), receipt('d2')], calls=[call(), call('d2', cid='call:2')])
        self.assertEqual(r['coverage']['all_source_conflicting_turns'], 1)
        self.assertEqual(r['daily'][0]['unique_usage_units'], 0)
        self.assertIsNone(r['daily'][0]['tokens']['input_tokens']['observed_sum'])
        self.assertIn('run_ownership_conflict', r['usage'][0]['conflicts'])

    def test_attempt_and_turn_ownership_conflicts(self):
        second = call(cid='call:2', run='run:other')
        r = self.make(calls=[call(), second])
        self.assertIn('attempt_ownership_conflict', r['usage'][0]['conflicts'])
        self.assertIn('turn_ownership_conflict', r['usage'][0]['conflicts'])

    def test_inconsistent_authoritative_snapshots_never_choose_largest(self):
        r = self.make(calls=[call(), call(cid='call:2', turns=[turn(n=100)])])
        self.assertEqual(r['usage'][0]['state'], 'excluded')
        self.assertEqual(len(r['usage'][0]['variants']), 2)

    def test_completed_and_destination_accepted_are_not_closes(self):
        obs = observations.make_observation('destination:dot', 'd1', 'd1', 'destination_event', {'event_type': 'accepted'})
        r = self.make(observed=[obs])
        self.assertEqual(r['rows'][0]['decision_outcome'], 'unknown')
        self.assertEqual(r['daily'][0]['accepted_denominator'], 0)
        self.assertIsNone(r['task_accepted_result_count'])
        self.assertIsNone(r['daily'][0]['known_components_usd_per_accepted'])

    def test_superseding_rejection_removes_acceptance(self):
        r = self.make(closes=[close(), close(cid='c2', outcome='rejected', supersedes='c1')])
        self.assertEqual(r['rows'][0]['current_close']['close_id'], 'c2')
        self.assertEqual(r['daily'][0]['accepted'], 0)
        self.assertEqual(r['daily'][0]['rejected'], 1)
        self.assertEqual(r['daily'][0]['tokens']['input_tokens']['observed_sum'], 150)

    def test_repairs_and_rejected_attempts_all_stay_in_numerator(self):
        c2 = call('d2', cid='call:2', run='run:2', turns=[turn('turn:2', 200)])
        c2['observed_child_usage']['attempts'][0]['attempt_id'] = 'attempt:2'
        r = self.make(receipts=[receipt(), receipt('d2')], calls=[call(), c2],
             closes=[close(outcome='accepted_after_repair', repairs=2), close('d2', cid='c2', outcome='rejected')])
        self.assertEqual(r['daily'][0]['tokens']['input_tokens']['observed_sum'], 350)
        self.assertEqual(r['daily'][0]['accepted_denominator'], 1)
        self.assertEqual(r['daily'][0]['repairs_known'], 1)
        self.assertEqual(r['daily'][0]['repairs_missing'], 1)
        self.assertIsNone(r['daily'][0]['task_cost'])

    def test_inclusive_partition_reasoning_and_nested_semantics(self):
        r = self.make(calls=[call(turns=[turn(has_subagents=True)])])
        t = r['usage'][0]['turn']
        self.assertEqual(t['fresh_input_tokens'], 100)
        self.assertEqual(t['total_tokens'], 190)
        self.assertEqual(t['output_tokens'], 40)
        self.assertEqual(t['reasoning_tokens'], 10)
        self.assertEqual(t['usage_status'], 'complete')
        self.assertEqual(t['nested_usage'], 'unknown')
        self.assertEqual(r['daily'][0]['nested_unknown_units'], 1)
        self.assertEqual(r['parent_overhead_usage'], 'unknown_unallocated')


    def test_unverified_usage_scope_preserved_but_excluded_from_main_totals(self):
        for scope in (None, 'all_agents'):
            with self.subTest(scope=scope):
                r = self.make(calls=[call(turns=[turn(usage_scope=scope)])])
                t = r['usage'][0]['turn']
                self.assertEqual(t['input_tokens'], 150)
                self.assertIsNone(t['fresh_input_tokens'])
                self.assertIsNone(t['total_tokens'])
                self.assertIsNone(r['daily'][0]['tokens']['input_tokens']['observed_sum'])
                self.assertEqual(r['daily'][0]['tokens']['input_tokens']['missing_units'], 1)
                self.assertEqual(r['daily'][0]['scope_unverified_units'], 1)
                self.assertEqual(r['rows'][0]['scope_unverified_units'], 1)

    def test_runtime_interpretation_is_not_historical_attestation(self):
        r = self.make()
        self.assertEqual(r['usage'][0]['turn']['semantics_basis'], 'current_adapter_interpretation_assumption')
        self.assertEqual(r['usage'][0]['turn']['producing_runtime'], 'unattested')
        self.assertEqual(r['usage'][0]['turn']['historical_applicability'], 'unknown')
        self.assertIn('provider_instance_driver_mapping_unavailable', r['source_gaps'])

    def test_invalid_missing_unknown_semantics_and_no_money(self):
        for t in (turn(cached_input_tokens=151), turn(cache_creation_tokens=None),
                  turn(input_tokens=-1), turn(input_tokens=True), turn(reasoning_tokens=41)):
            r = self.make(calls=[call(turns=[t])])
            u = r['usage'][0]['turn']
            self.assertIsNone(u['fresh_input_tokens'] if t.get('reasoning_tokens') != 41 else u['total_tokens'])
            self.assertIsNone(u['total_usd'])
            self.assertIsNone(u['credits'])
        r = self.make(calls=[call(child_provider='unknown-provider', child_requested_model='unknown-model',
                                 child_requested_options={'serviceTier': 'flex'})])
        self.assertEqual(r['usage'][0]['turn']['semantics'], 'unknown')
        self.assertIsNone(r['usage'][0]['turn']['total_tokens'])
        self.assertIsNone(r['total_usd'])
        self.assertIsNone(r['credits'])

    def test_null_usage_never_becomes_zero(self):
        t = turn(usage_status='unknown', has_subagents=None)
        r = self.make(calls=[call(turns=[t])])
        self.assertIsNone(r['usage'][0]['turn']['input_tokens'])
        self.assertEqual(r['daily'][0]['tokens']['input_tokens']['missing_units'], 1)
        self.assertIsNone(r['daily'][0]['tokens']['input_tokens']['observed_sum'])

    def test_receipt_target_not_fallback_or_account_hint(self):
        r = self.make(receipts=[receipt(target=None, destination='dot')])
        self.assertIsNone(r['rows'][0]['receipt_target'])
        self.assertEqual(r['rows'][0]['destination'], 'dot')
        text = report.canonical(r)
        self.assertNotIn('fallback-only', text)
        self.assertNotIn('caller-hint-not-target', text)
        self.assertEqual(r['rows'][0]['calls'][0]['child_requested_configuration']['model'], MODEL)
        self.assertEqual(r['rows'][0]['calls'][0]['child_requested_configuration']['effort'], 'xhigh')

    def test_rolling_cohort_half_open_boundary_and_outside_lineage(self):
        records = [receipt('older', at='2026-10-01T11:59:59.999999Z'), receipt('edge', at=START),
                   receipt('end', at=NOW), receipt('future', at='2026-10-15T12:00:00.000001Z'),
                   receipt(relaunch={'from': 'older'}, escalation={'from': 'edge'})]
        records[-1]['request']['maker'] = 'older'
        r = self.make(receipts=records)
        self.assertEqual({x['decision_id'] for x in r['rows']}, {'edge', 'd1'})
        row = next(x for x in r['rows'] if x['decision_id'] == 'd1')
        self.assertEqual({x['kind'] for x in row['edges']}, {'relaunch', 'escalate', 'maker'})
        self.assertFalse(row['edges'][0]['in_launch_cohort'])
        self.assertTrue(r['linked_decision_views'][0]['nonadditive'])
        self.assertIsNone(r['linked_decision_views'][0]['task_cost'])

    def test_late_close_and_parallel_time_are_not_wall_time(self):
        timing = observation('t3_run_status', {'status': 'completed'}, timestamps={
               'started_at': '2026-10-14T11:00:00Z', 'completed_at': '2026-10-14T11:01:00Z'})
        c2 = call(cid='call:2', run='run:2', turns=[turn('turn:2')])
        c2['observed_child_usage']['attempts'][0]['attempt_id'] = 'attempt:2'
        second = observation('t3_run_status', {'status': 'completed'}, source_ids={'run_id': 'run:2'},
                             timestamps={'started_at': '2026-10-14T11:00:30Z', 'completed_at': '2026-10-14T11:01:30Z'})
        r = self.make(closes=[close()], observed=[timing, second], calls=[call(), c2])
        self.assertEqual([c['run_timing']['elapsed_seconds'] for c in r['rows'][0]['calls']], [60, 60])
        self.assertEqual(r['rows'][0]['decision_latency_seconds'], 26 * 3600)
        self.assertIsNone(r['rows'][0]['run_wall_time_seconds'])
        self.assertIsNone(r['daily'][0]['wall_time_seconds'])
        self.assertIsNone(self.make()['rows'][0]['calls'][0]['run_timing']['elapsed_seconds'])

    def test_duplicate_receipts_fail_even_outside_cohort(self):
        with self.assertRaises(ValueError):
            self.make(receipts=[receipt('old', at='2020-01-01T00:00:00Z'), receipt('old')])

    def test_db_projection_usage_and_exact_source_provenance(self):
        self.args.db = self.create_db(two_turns=True)
        self.args.export = None
        before = {p: p.read_bytes() for p in self.inputs.iterdir()}
        loaded = report.load_sources(self.args)
        receipts, closes, observed, calls, outside, sources = loaded
        r = report.make_report(receipts, closes, observed, calls, outside, sources, as_of=NOW)
        self.assertEqual(r['daily'][0]['tokens']['input_tokens']['observed_sum'], 300)
        self.assertEqual(sources['receipts']['sha256'], hashlib.sha256(before[self.args.receipts]).hexdigest())
        self.assertEqual(sources['outcomes']['sha256'], hashlib.sha256(before[self.args.outcomes]).hexdigest())
        self.assertEqual(sources['db']['hash_kind'], 'canonical_projected_transaction_not_database_file')
        self.assertIsNone(sources['db']['database_file_sha256'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.inputs.iterdir()})
        self.assertNotIn('PRIVATE', report.canonical(r))

    def test_pinned_helpers_never_reopen_live_source(self):
        original = self.args.receipts.read_bytes()
        reader = report.read_receipts
        def read_pin(path):
            self.assertNotEqual(path, self.args.receipts)
            self.args.receipts.write_text('MUTATED BY FIXTURE')
            return reader(path)
        with patch.object(report, 'read_receipts', side_effect=read_pin):
            loaded = report.load_sources(self.args)
        self.assertEqual(loaded[0][0]['decision_id'], 'd1')
        self.assertEqual(loaded[-1]['receipts']['sha256'], hashlib.sha256(original).hexdigest())

    def test_export_preserves_audit_contract_and_missing_usage(self):
        delegation = {'call_id': 'call:1', 'thread_id': 'parent-thread', 'timestamp': NOW,
                      'input': {'clientRequestId': 'd1', 'target': TARGET, 'task': 'PRIVATE PROMPT'},
                      'output': {'childRunId': 'run:child'}, 'child_status': 'completed',
                      'child_provider': 'codex', 'child_requested_model': MODEL,
                      'child_requested_options': TARGET['options'],
                      'observed_child_usage': call()['observed_child_usage']}
        self.write_sources(delegations=[delegation])
        values = report.load_sources(self.args)
        r = report.make_report(*values[:-1], values[-1], as_of=NOW)
        self.assertEqual(r['rows'][0]['calls'][0]['usage_state'], 'unknown_not_in_export')
        self.assertEqual(r['daily'][0]['unique_usage_units'], 0)
        self.assertIn('export_usage_not_projected', r['source_gaps'])
        self.assertIsNone(r['daily'][0]['tokens']['input_tokens']['observed_sum'])

    def test_malformed_missing_and_empty_sources_distinct(self):
        for path in (self.args.receipts, self.args.outcomes, self.args.export):
            data = path.read_bytes()
            path.write_text('not json')
            with self.assertRaises((ValueError, OSError)):
                report.execute(self.args)
            self.assertFalse(self.args.report_dir.exists())
            path.unlink()
            with self.assertRaises((ValueError, OSError)):
                report.execute(self.args)
            path.write_bytes(data)
        self.write_sources(receipts=[])
        values = report.load_sources(self.args)
        self.assertEqual(values[0], [])
        self.assertEqual(values[-1]['observations']['state'], 'unavailable')
        self.args.observations.write_text('broken')
        self.assertEqual(report.load_sources(self.args)[-1]['observations']['reason'], 'malformed_optional_source')

    def test_alias_symlink_overlapping_and_existing_output_refused(self):
        paths = [self.args.receipts, self.args.outcomes, self.args.observations, self.args.export]
        for target in (self.inputs, self.inputs / 'nested', self.root, self.args.receipts):
            with self.assertRaises(ValueError):
                report.check_paths(target, paths)
        alias = self.outputs / 'alias'
        alias.symlink_to(self.inputs, target_is_directory=True)
        with self.assertRaises(ValueError):
            report.check_paths(alias / 'report', paths)
        self.args.report_dir.mkdir()
        with self.assertRaises(ValueError):
            report.check_paths(self.args.report_dir, paths)
        self.args.outcomes.unlink()
        os.link(self.args.receipts, self.args.outcomes)
        with self.assertRaises(ValueError):
            report.check_paths(self.outputs / 'new', paths)

    def test_atomic_private_artifacts_and_unchanged_sources(self):
        before = {p: p.read_bytes() for p in self.inputs.iterdir()}
        with patch.object(report.os, 'rename', side_effect=OSError('fixture publish failure')):
            with self.assertRaises(OSError):
                report.execute(self.args)
        self.assertFalse(self.args.report_dir.exists())
        self.assertEqual(list(self.outputs.iterdir()), [])
        result = report.execute(self.args)
        self.assertEqual(stat.S_IMODE(self.args.report_dir.stat().st_mode), 0o700)
        for name, path in result['paths'].items():
            self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
            self.assertNotIn('PRIVATE', Path(path).read_text())
        self.assertEqual(before, {p: p.read_bytes() for p in self.inputs.iterdir()})
        self.assertLess(len(json.dumps(result)), 2500)

    def test_pinned_report_repeatable_and_no_collection_order_dependence(self):
        r = self.make()
        self.assertEqual(report.canonical(r), report.canonical(self.make()))
        self.assertEqual(report.markdown(r), report.markdown(self.make()))

    def test_outside_cohort_units_only_contribute_coverage(self):
        c = call('older', cid='call:2', run='run:old', turns=[turn('turn:old')])
        c['observed_child_usage']['attempts'][0]['attempt_id'] = 'attempt:old'
        r = self.make(receipts=[receipt(), receipt('older', at='2020-01-01T00:00:00Z')], calls=[call(), c])
        self.assertEqual(r['coverage']['all_source_unique_turns'], 2)
        self.assertEqual(r['coverage']['calls_outside_launch_cohort'], 1)
        self.assertEqual(len(r['usage']), 1)

    def test_unlinked_call_conflict_with_cohort_is_not_hidden(self):
        r = self.make(calls=[call(), call('not-in-receipts', cid='call:2')])
        self.assertEqual(r['coverage']['unlinked_calls'], 1)
        self.assertEqual(r['usage'][0]['state'], 'excluded')
        self.assertEqual(r['daily'][0]['unique_usage_units'], 0)

    def test_followup_on_superseded_close_is_not_current_acceptance(self):
        followup = {'kind': 'followup', 'decision_id': 'd1', 'followup_id': 'f1', 'close_id': 'c1',
                    'finding': 'no_rework_found', 'observed_at': NOW}
        r = self.make(closes=[close(), followup, close(cid='c2', outcome='rejected', supersedes='c1')])
        self.assertFalse(r['rows'][0]['followups']['items'][0]['on_current_close'])
        self.assertEqual(r['rows'][0]['decision_outcome'], 'rejected')

    def test_usage_read_failure_is_not_empty_usage(self):
        self.args.db = self.create_db()
        self.args.export = None
        db = sqlite3.connect(self.args.db)
        db.execute(f'UPDATE {audit.PREFIX}provider_turns SET payload_json=?', ('bad-json',))
        db.commit()
        db.close()
        loaded = report.load_sources(self.args)
        self.assertEqual(loaded[3][0]['usage_state'], 'unavailable')
        self.assertEqual(loaded[3][0]['usage_reason'], 'read_error')
        self.assertEqual(loaded[3][0]['attempts'], [])

    def test_missing_and_corrupt_db_fail_without_output(self):
        self.args.export = None
        self.args.db = self.inputs / 'absent.sqlite'
        with self.assertRaises(ValueError):
            report.execute(self.args)
        self.assertFalse(self.args.db.exists())
        self.args.db.write_text('not sqlite')
        with self.assertRaises(ValueError):
            report.execute(self.args)
        self.assertFalse(self.args.report_dir.exists())

    def test_private_publication_uses_directory_descriptors(self):
        rename = report.os.rename
        def anchored(source, destination, **kwargs):
            self.assertEqual(destination, 'report')
            self.assertEqual(kwargs['src_dir_fd'], kwargs['dst_dir_fd'])
            self.assertIsInstance(kwargs['src_dir_fd'], int)
            return rename(source, destination, **kwargs)
        with patch.object(report.os, 'rename', side_effect=anchored):
            report.execute(self.args)

    def test_table_rows_precede_detail_sections(self):
        r = self.make(receipts=[receipt(), receipt('d2', at='2026-10-13T10:00:00Z')])
        md = report.markdown(r)
        self.assertLess(md.index('| 2026-10-14'), md.index('## 2026-10-13'))

    def test_partial_unknown_options_redacted_and_no_observe_execution(self):
        r = self.make(calls=[call(turns=[turn(usage_status='partial', cache_creation_tokens=None)],
                  child_requested_options={'reasoningEffort': 'xhigh', 'prompt': 'PRIVATE SECRET'})])
        self.assertEqual(r['usage'][0]['turn']['usage_status'], 'partial')
        self.assertEqual(r['rows'][0]['calls'][0]['child_requested_configuration']['options_dropped'], 1)
        self.assertNotIn('PRIVATE SECRET', report.canonical(r))
        import observe
        with patch.object(observe, 'execute', side_effect=AssertionError('must not observe')):
            report.execute(self.args)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              capture_output=True, text=True, env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})

    def test_cli_report_rejections_and_legacy_audit_unchanged(self):
        base = ['--receipts', self.args.receipts, '--outcomes', self.args.outcomes,
                '--export', self.args.export]
        before = {p: p.read_bytes() for p in self.inputs.iterdir()}
        audit_before = self.cli('audit', *base)
        self.assertEqual(audit_before.returncode, 0, audit_before.stderr)
        for flag in ('--record', '--now'):
            extra = [flag] + ([NOW] if flag == '--now' else [])
            result = self.cli('report', *base, '--report-dir', self.args.report_dir, *extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.args.report_dir.exists())
        result = self.cli('report', *base, '--report-dir', self.args.report_dir, '--since', START)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('paths', json.loads(result.stdout))
        self.assertLess(len(result.stdout), 2500)
        self.assertEqual(audit_before.stdout, self.cli('audit', *base).stdout)
        self.assertEqual(before, {p: p.read_bytes() for p in self.inputs.iterdir()})

    def test_cli_requires_exclusive_db_or_export_and_output(self):
        for db, export in ((None, None), (Path('unused.sqlite'), self.args.export)):
            args = copy.copy(self.args)
            args.db, args.export = db, export
            with self.assertRaises(ValueError):
                report.execute(args)
        args = copy.copy(self.args)
        args.report_dir = None
        with self.assertRaises(ValueError):
            report.execute(args)

    def test_legacy_check_list_show_contracts(self):
        path = self.inputs / 'policy.json'
        policy = {'policy_version': 'fixture', 'catalog_ref': 'fixture', 'operating_points': [
            {'id': 'op1', 'expands_to': {'provider': 'codex', 'model': MODEL, 'effort': 'high'}}]}
        path.write_text(json.dumps(policy))
        args = ['--policy', path]
        check = self.cli('check', *args, '--no-user-policy')
        self.assertEqual(check.stdout, 'PASS model-choice-policy fixture (1 operating points)\n')
        shown = self.cli('show', 'op1', *args)
        self.assertEqual(json.loads(shown.stdout), policy['operating_points'][0])
        listed = self.cli('list', *args)
        self.assertEqual(listed.returncode, 0)
        self.assertIn('op1', listed.stdout)
        self.assertNotIn('report_version', listed.stdout)


if __name__ == '__main__':
    unittest.main()


def test_arm_summary_keeps_judged_and_derived_apart_and_divides_tokens_by_accepted():
    import cost_report

    def row(decision, outcome=None, derived=None, model='m', account='acct-a'):
        return {'decision_id': decision, 'op': 'implement.oracle-bounded', 'destination': 'model',
                'receipt_target': {'provider_instance_id': account, 'model': model, 'effort': 'high'},
                'outcome_state': 'closed' if outcome else 'unclosed', 'decision_outcome': outcome or 'unknown',
                'derived_outcome': None if outcome else {'outcome': derived or 'unknown', 'reason': None if derived else 'completed_no_disposition'},
                'calls': [{'child_run_id': 'run-' + decision, 'run_timing': {'elapsed_seconds': 10.0 if decision != 'b' else 30.0}},
                          {'child_run_id': 'run-' + decision, 'run_timing': {'elapsed_seconds': 10.0 if decision != 'b' else 30.0}}]}

    def unit(decision, output):
        turn = {name: None for name in cost_report.CATEGORIES} | {'output_tokens': output, 'input_tokens': 10 * output,
                                                                 'usage_scope': 'main_agent'}
        return {'state': 'attributed', 'decision_id': decision, 'turn': turn}

    rows = [row('a', 'accepted'), row('b', 'accepted_after_repair', account='acct-b'), row('c', 'rejected'),
            row('d', derived='accepted'), row('e'), row('f', 'accepted', model='other')]
    units = [unit(d, 100) for d in 'abcde'] + [unit('f', 7)]
    first, other = cost_report.arm_summary(rows, units)
    assert (first['model'], first['effort'], first['decisions']) == ('m', 'high', 5)
    assert first['accounts'] == {'acct-a': 4, 'acct-b': 1}, 'accounts are counted, not split into arms'
    assert first['judged'] == {'accepted': 1, 'accepted_after_repair': 1, 'rejected': 1, 'other': 0}
    assert first['derived'] == {'accepted': 1, 'rejected': 0} and first['unknown'] == 1
    assert first['unknown_reasons'] == {'completed_no_disposition': 1}
    assert first['accepted'] == 3 and first['first_time_share_of_judged_accepted'] == 0.5
    assert first['tokens_per_accepted']['output_tokens'] == 500 / 3, 'rejected and unknown attempts stay in the numerator'
    assert first['runs_timed'] == 5, 'one duration per child run, not per call'
    assert first['run_elapsed_seconds_p50'] == 10.0 and first['usd_per_accepted'] is None
    _, _ = cost_report.arm_summary([row('x'), row('y')], [])[0], None
    pair = cost_report.arm_summary([dict(row('x'), calls=[{'child_run_id': 'r1', 'run_timing': {'elapsed_seconds': 10.0}}]),
                                    dict(row('y'), calls=[{'child_run_id': 'r2', 'run_timing': {'elapsed_seconds': 30.0}}])], [])[0]
    assert pair['run_elapsed_seconds_p50'] == 20.0, 'the median of an even sample is the mean of the middle two'
    assert other['model'] == 'other' and other['tokens_per_accepted']['output_tokens'] == 7
