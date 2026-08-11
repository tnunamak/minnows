"""Regression tests for the stdlib-only convo CLI."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


TOOL = Path(__file__).parents[1] / "tools" / "convo" / "convo"
REPO = Path(__file__).parents[1]
LOADER = importlib.machinery.SourceFileLoader("convo_under_test", str(TOOL))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
convo = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(convo)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class QwenLoaderTests(unittest.TestCase):
    def test_clean_transcript_excludes_thoughts_and_internal_user_records(self):
        rows = [
            {
                "sessionId": "qwen-session", "timestamp": "2026-07-30T00:00:00Z",
                "type": "user", "provenance": "real_user", "cwd": "/project",
                "message": {"role": "user", "parts": [{"text": "First question"}]},
            },
            {
                "sessionId": "qwen-session", "timestamp": "2026-07-30T00:00:01Z",
                "type": "assistant", "provenance": "assistant_output", "cwd": "/project",
                "message": {"role": "model", "parts": [
                    {"text": "private reasoning", "thought": True},
                    {"functionCall": {"id": "call-1", "name": "lookup", "args": {"id": 1}}},
                ]},
            },
            {
                "sessionId": "qwen-session", "timestamp": "2026-07-30T00:00:02Z",
                "type": "tool_result", "provenance": "tool_result", "cwd": "/project",
                "message": {"role": "user", "parts": [{"functionResponse": {
                    "id": "call-1", "name": "lookup", "response": {"value": "found"},
                }}]},
            },
            {
                "sessionId": "qwen-session", "timestamp": "2026-07-30T00:00:03Z",
                "type": "assistant", "provenance": "assistant_output", "cwd": "/project",
                "message": {"role": "model", "parts": [{"text": "Visible answer"}]},
            },
            {
                "sessionId": "qwen-session", "timestamp": "2026-07-30T00:00:04Z",
                "type": "user", "provenance": "system", "cwd": "/project",
                "message": {"role": "user", "parts": [{"text": "tool notification"}]},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qwen-session.jsonl"
            write_jsonl(path, rows)
            session = convo.load_qwen(path)

        self.assertEqual([(turn.role, turn.text) for turn in session.turns], [
            ("user", "First question"),
            ("assistant", "Visible answer"),
        ])
        self.assertEqual(convo.build_pairs(session)[0]["assistant_texts"], ["Visible answer"])
        self.assertEqual(
            [(event["kind"], event["name"]) for event in session.turns[1].events],
            [("thinking", ""), ("tool_use", "lookup"), ("tool_result", "lookup")],
        )

    def test_qwen_project_filter_uses_its_project_directory_not_chats(self):
        path = Path("/tmp/qwen/projects/-project/chats/session.jsonl")
        self.assertEqual(convo._slug_for_project(path, "qwen"), "-project")
        self.assertTrue(convo._candidate_may_match_project("qwen", path, "/project"))


class CrossHarnessGrepTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.original_roots = {harness: config["root"] for harness, config in convo.HARNESSES.items()}
        for harness, config in convo.HARNESSES.items():
            config["root"] = self.root / harness

        now = time.time()
        write_jsonl(self.root / "claude" / "project" / "target.jsonl", [{
            "type": "user", "timestamp": "2026-07-30T00:00:00Z", "cwd": "/project",
            "isMeta": False, "message": {"content": "needle in Claude"},
        }])
        os.utime(self.root / "claude" / "project" / "target.jsonl", (now - 60, now - 60))
        write_jsonl(self.root / "qwen" / "project" / "chats" / "recent.jsonl", [{
            "sessionId": "recent", "timestamp": "2026-07-30T00:00:01Z", "type": "user",
            "provenance": "real_user", "cwd": "/other",
            "message": {"role": "user", "parts": [{"text": "different topic"}]},
        }])

    def tearDown(self):
        for harness, root in self.original_roots.items():
            convo.HARNESSES[harness]["root"] = root
        self.directory.cleanup()

    def grep_output(self, *args: str) -> str:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            convo.main(["grep", "needle", "--all-projects", "--limit", "1", *args])
        return stdout.getvalue()

    def test_default_all_and_explicit_all_search_each_harness_budget(self):
        default = self.grep_output()
        explicit_all = self.grep_output("--harness", "all")
        self.assertIn("needle in Claude", default)
        self.assertEqual(default, explicit_all)


class LedgerTests(unittest.TestCase):
    """The ledger is exercised through the CLI to keep its public boundary honest."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.data_dir = self.root / "ledger-data"
        self.original_roots = {harness: config["root"] for harness, config in convo.HARNESSES.items()}
        self.old_data_dir = os.environ.get("CONVO_DATA_DIR")
        self.old_source_cap = os.environ.get("CONVO_MAX_SOURCE_BYTES")
        self._ledger = None
        os.environ["CONVO_DATA_DIR"] = str(self.data_dir)
        os.environ["CONVO_MAX_SOURCE_BYTES"] = str(64 * 1024 * 1024)
        for harness, config in convo.HARNESSES.items():
            config["root"] = self.root / harness

    def tearDown(self):
        if self._ledger is not None:
            self._ledger.close()
        for harness, root in self.original_roots.items():
            convo.HARNESSES[harness]["root"] = root
        if self.old_data_dir is None:
            os.environ.pop("CONVO_DATA_DIR", None)
        else:
            os.environ["CONVO_DATA_DIR"] = self.old_data_dir
        if self.old_source_cap is None:
            os.environ.pop("CONVO_MAX_SOURCE_BYTES", None)
        else:
            os.environ["CONVO_MAX_SOURCE_BYTES"] = self.old_source_cap
        self.directory.cleanup()

    def claude_path(self, name: str = "session.jsonl") -> Path:
        return self.root / "claude" / "project" / name

    def write_claude(self, path: Path, user: str, assistant: str, timestamp: str = "2026-08-01T00:00:00Z") -> None:
        write_jsonl(path, [
            {"type": "user", "timestamp": timestamp, "cwd": "/project", "isMeta": False,
             "message": {"content": user}},
            {"type": "assistant", "timestamp": timestamp, "message": {"content": [
                {"type": "text", "text": assistant},
            ]}},
        ])

    def run_convo(self, *args: str) -> tuple[str, str, int]:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                convo.main(list(args))
            except SystemExit as exc:
                code = int(exc.code) if isinstance(exc.code, int) else 1
        return stdout.getvalue(), stderr.getvalue(), code

    def ledger(self):
        if self._ledger is None:
            self._ledger = convo.ledger_lib.Ledger()
        return self._ledger

    def test_sync_is_idempotent_and_uses_the_test_data_directory(self):
        self.write_claude(self.claude_path(), "Find idempotence", "Stored once")
        first, _, first_code = self.run_convo("sync", "--json")
        second, _, second_code = self.run_convo("sync", "--json")
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(json.loads(first)["imported"], 1)
        self.assertEqual(json.loads(second)["unchanged"], 1)
        self.assertGreater(json.loads(first)["bytes_processed"], 0)
        self.assertEqual(json.loads(second)["bytes_processed"], 0)
        self.assertGreater(json.loads(second)["bytes_observed"], 0)
        self.assertEqual(self.ledger().status()["messages"], 2)
        self.assertTrue((self.data_dir / "ledger.sqlite3").exists())

    def test_unchanged_full_sync_uses_one_database_connection(self):
        for index in range(25):
            self.write_claude(self.claude_path(f"source-{index}.jsonl"), f"source {index}", "answer")
        self.run_convo("sync")
        real_connect = convo.ledger_lib.sqlite3.connect
        calls = 0

        def counted_connect(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_connect(*args, **kwargs)

        with mock.patch.object(convo.ledger_lib.sqlite3, "connect", side_effect=counted_connect):
            _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertEqual(calls, 1)

    def test_thousand_oversized_sources_use_bounded_batch_commits(self):
        os.environ["CONVO_MAX_SOURCE_BYTES"] = "1"
        for index in range(1000):
            path = self.root / "gemini" / f"project-{index}" / "chats" / f"session-{index}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("too large for this test cap\n", encoding="utf-8")
        real_commit_batch = convo.ledger_lib.Ledger.commit_batch
        commits = 0

        def counted_commit_batch(ledger):
            nonlocal commits
            commits += 1
            return real_commit_batch(ledger)

        with mock.patch.object(convo.ledger_lib.Ledger, "commit_batch", new=counted_commit_batch):
            _, _, code = self.run_convo("sync")
        self.assertEqual(code, 2)
        # 1,000 source transitions at 250/batch plus the final missing-source pass.
        self.assertEqual(commits, 5)

    def test_changed_source_replaces_prior_rows(self):
        path = self.claude_path()
        self.write_claude(path, "old unique topic", "old answer")
        self.run_convo("sync")
        self.write_claude(path, "new unique topic", "new answer", "2026-08-02T00:00:00Z")
        self.run_convo("sync")
        self.assertEqual(self.ledger().search("old unique topic"), [])
        hits = self.ledger().search("new unique topic")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["text"], "new unique topic")

    def test_missing_source_keeps_a_searchable_snapshot_with_honest_labels(self):
        path = self.claude_path()
        self.write_claude(path, "retained deletion topic", "retained answer")
        self.run_convo("sync")
        path.unlink()
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        hit = self.ledger().search("retained deletion topic")[0]
        self.assertEqual(hit["source_status"], "missing")
        self.assertEqual(hit["content_basis"], "snapshot")
        self.assertEqual(hit["completeness"], "normalized_snapshot_source_missing")

    def test_reappeared_missing_source_is_reimported_even_with_same_identity(self):
        path = self.claude_path()
        self.write_claude(path, "reappeared source topic", "answer")
        self.run_convo("sync")
        hidden = self.root / "hidden-project"
        path.parent.rename(hidden)
        self.run_convo("sync")
        self.assertEqual(self.ledger().search("reappeared source topic")[0]["source_status"], "missing")
        hidden.rename(path.parent)
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger().search("reappeared source topic")[0]["source_status"], "present")

    def test_access_failure_is_retried_after_the_source_becomes_readable(self):
        path = self.claude_path()
        self.write_claude(path, "access recovery topic", "answer")
        self.run_convo("sync")
        self.ledger().record_access_failure("claude", path, OSError("transient permission failure"))
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        hit = self.ledger().search("access recovery topic")[0]
        self.assertEqual(hit["source_status"], "present")

    def test_parse_failure_preserves_last_good_rows_and_is_partial(self):
        path = self.claude_path()
        self.write_claude(path, "preserve good topic", "good answer")
        self.run_convo("sync")
        path.write_text('{"type":"user"}\nnot-json\n', encoding="utf-8")
        first, stderr, code = self.run_convo("sync", "--json")
        self.assertEqual(code, 0)
        self.assertNotIn("source preserved after parse failure", stderr)
        self.assertEqual(json.loads(first)["partial"], 1)
        self.assertEqual(json.loads(first)["failed"], 0)
        hit = self.ledger().search("preserve good topic")[0]
        self.assertEqual(hit["source_status"], "partial")
        self.assertEqual(hit["content_basis"], "snapshot")
        second, _, second_code = self.run_convo("sync", "--json")
        self.assertEqual(second_code, 0)
        self.assertEqual(json.loads(second)["unchanged"], 1)
        self.assertEqual(json.loads(second)["partial"], 1)
        self.assertEqual(json.loads(second)["failed"], 0)
        _, verbose, verbose_code = self.run_convo("sync", "--verbose")
        self.assertEqual(verbose_code, 0)
        self.assertIn(str(path), verbose)
        self.assertIn("cached source partial", verbose)

    def test_jsonl_sources_stream_past_the_legacy_whole_file_cap(self):
        path = self.claude_path()
        self.write_claude(path, "cap preserves topic", "good answer")
        self.run_convo("sync")
        os.environ["CONVO_MAX_SOURCE_BYTES"] = "1"
        _, stderr, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertNotIn("oversized", stderr)
        hit = self.ledger().search("cap preserves topic")[0]
        self.assertEqual(hit["source_status"], "present")
        self.assertEqual(self.ledger().status()["oversized_sources"], 0)

    def test_streaming_normalization_matches_existing_jsonl_loaders(self):
        cases = {
            "claude": (self.claude_path(), [
                {"type": "user", "isMeta": False, "message": {"content": "# AGENTS.md instructions, but explicit user"}},
                {"type": "assistant", "timestamp": "2026-08-01T00:00:00Z", "message": {"content": [{"type": "text", "text": "first"}]}},
                {"type": "assistant", "timestamp": "2026-08-01T00:00:01Z", "message": {"content": [{"source": {"type": "base64"}}]}},
            ]),
            "codex": (self.root / "codex" / "2026" / "rollout-codex.jsonl", [
                {"type": "session_meta", "payload": {"cwd": "/project"}},
                {"type": "response_item", "timestamp": "2026-08-01T00:00:00Z", "payload": {"type": "message", "role": "user", "content": "question"}},
                {"type": "response_item", "timestamp": "2026-08-01T00:00:01Z", "payload": {"type": "message", "role": "assistant", "content": "first"}},
                {"type": "response_item", "timestamp": "2026-08-01T00:00:02Z", "payload": {"type": "message", "role": "assistant", "content": "second"}},
            ]),
            "qwen": (self.root / "qwen" / "project" / "chats" / "qwen.jsonl", [
                {"type": "user", "provenance": "real_user", "message": {"parts": [{"text": "question"}]}},
                {"type": "assistant", "timestamp": "2026-08-01T00:00:01Z", "message": {"parts": [{"text": "first"}]}},
                {"type": "assistant", "timestamp": "2026-08-01T00:00:02Z", "message": {"parts": [{"text": "second"}]}},
            ]),
        }
        for harness, (path, rows) in cases.items():
            with self.subTest(harness=harness):
                write_jsonl(path, rows)
                raw = convo.HARNESSES[harness]["loader"](path)
                expected = [(m.role, m.text, m.timestamp) for m in convo._normalized_source(harness, path, raw, path.stat()).messages]
                streamed = convo._stream_jsonl_source(harness, path, path.stat(), self.data_dir / "spool")
                try:
                    actual = [(m.role, m.text, m.timestamp) for m in streamed.source.messages]
                finally:
                    streamed.spool_path.unlink(missing_ok=True)
                self.assertEqual(actual, expected)

    def test_stream_spool_is_removed_when_reading_raises(self):
        path = self.claude_path()
        self.write_claude(path, "spool failure", "answer")
        with mock.patch.object(convo, "_sync_jsonl_rows", side_effect=OSError("injected read failure")):
            with self.assertRaises(OSError):
                convo._stream_jsonl_source("claude", path, path.stat(), self.data_dir / "spool")
        self.assertEqual(list((self.data_dir / "spool").glob("sync-*.jsonl")), [])

    def test_stream_spool_is_removed_on_interrupt_and_next_sync_purges_stale_spools(self):
        path = self.claude_path()
        self.write_claude(path, "interrupt spool", "answer")
        with mock.patch.object(convo, "_sync_jsonl_rows", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                convo._stream_jsonl_source("claude", path, path.stat(), self.data_dir / "spool")
        spool_dir = self.data_dir / "spool"
        self.assertEqual(list(spool_dir.glob("sync-*.jsonl")), [])
        stale = spool_dir / "sync-stale.jsonl"
        stale.write_text("sensitive temporary text", encoding="utf-8")
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertFalse(stale.exists())

    def test_ledger_write_failure_is_not_mislabeled_as_a_source_parse_failure(self):
        path = self.claude_path()
        self.write_claude(path, "write failure", "answer")
        with mock.patch.object(convo.ledger_lib.Ledger, "replace_source", side_effect=sqlite3.OperationalError("disk full")):
            _, stderr, code = self.run_convo("sync")
        self.assertEqual(code, 2)
        self.assertIn("ledger write transaction failed", stderr)
        self.assertNotIn("source preserved after parse failure", stderr)
        self.assertEqual(self.ledger().status()["sources"], {})
        self.assertEqual(list((self.data_dir / "spool").glob("sync-*.jsonl")), [])

    def test_stream_keeps_bounded_malformed_diagnostic_samples(self):
        path = self.claude_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-json\n" * 1000, encoding="utf-8")
        streamed = convo._stream_jsonl_source("claude", path, path.stat(), self.data_dir / "spool")
        try:
            self.assertEqual(streamed.source.source_status, "partial")
            self.assertIn("1000 malformed JSONL row(s)", streamed.source.parser_error)
            self.assertEqual(streamed.source.parser_error.count("corrupt JSONL"), 3)
        finally:
            streamed.spool_path.unlink(missing_ok=True)

    def test_stream_chunks_oversized_normalized_assistant_messages(self):
        path = self.claude_path()
        self.write_claude(path, "question", "abcdefghij")
        with mock.patch.object(convo, "MAX_NORMALIZED_MESSAGE_BYTES", 4):
            streamed = convo._stream_jsonl_source("claude", path, path.stat(), self.data_dir / "spool")
        try:
            messages = list(streamed.source.messages)
            self.assertEqual(streamed.source.source_status, "partial")
            self.assertEqual("".join(message.text for message in messages if message.role == "assistant"), "abcdefghij")
            self.assertTrue(all(len(message.text.encode("utf-8")) <= 4 for message in messages if message.role == "assistant"))
        finally:
            streamed.spool_path.unlink(missing_ok=True)

    def test_legacy_oversized_jsonl_is_invalidated_by_streaming_parser_version(self):
        path = self.claude_path()
        self.write_claude(path, "legacy cap topic", "answer")
        self.ledger().record_oversized(
            "claude", path, path.stat(), 1, "whole-session-v0", convo.LEDGER_POLICY_VERSION,
        )
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        hit = self.ledger().search("legacy cap topic")[0]
        self.assertEqual(hit["source_status"], "present")
        self.assertEqual(self.ledger().status()["oversized_sources"], 0)

    def test_torn_final_jsonl_row_is_ignored_then_imported_after_resume(self):
        path = self.claude_path()
        self.write_claude(path, "stable before torn", "first reply")
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"user"')
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.ledger().search("stable before torn")), 1)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(',"timestamp":"2026-08-02T00:00:00Z","cwd":"/project","isMeta":false,"message":{"content":"resumed torn row"}}\n')
        self.run_convo("sync")
        self.assertEqual(len(self.ledger().search("resumed torn row")), 1)

    def test_pending_zero_row_rewrite_preserves_prior_snapshot(self):
        path = self.claude_path()
        self.write_claude(path, "prior pending snapshot", "answer")
        self.run_convo("sync")
        path.write_text('{"type":"user"', encoding="utf-8")
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        hit = self.ledger().search("prior pending snapshot")[0]
        self.assertEqual(hit["source_status"], "pending")
        self.assertEqual(hit["content_basis"], "snapshot")

    def test_new_pending_zero_row_source_has_no_messages(self):
        path = self.claude_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"type":"user"', encoding="utf-8")
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger().status()["pending_sources"], 1)
        self.assertEqual(self.ledger().status()["messages"], 0)

    def test_no_normalized_messages_are_skipped_and_cached(self):
        path = self.claude_path()
        write_jsonl(path, [{"type": "user", "isMeta": True, "message": {"content": "system only"}}])
        first, stderr, code = self.run_convo("sync", "--json")
        second, _, second_code = self.run_convo("sync", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(json.loads(first)["skipped"], 1)
        self.assertEqual(json.loads(second)["unchanged"], 1)
        self.assertEqual(self.ledger().status()["skipped_sources"], 1)
        self.assertNotIn("sync progress", stderr)

    def test_malformed_complete_row_retains_valid_rows_on_both_sides(self):
        path = self.claude_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"type": "user", "isMeta": False, "message": {"content": "before malformed"}}) + "\n"
            "{definitely bad}\n"
            + json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "after malformed"}]}}) + "\n",
            encoding="utf-8",
        )
        first, _, code = self.run_convo("sync", "--json")
        second, _, second_code = self.run_convo("sync", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(json.loads(first)["partial"], 1)
        self.assertEqual(json.loads(first)["failed"], 0)
        self.assertEqual(json.loads(second)["unchanged"], 1)
        self.assertEqual(json.loads(second)["partial"], 1)
        self.assertEqual(json.loads(second)["failed"], 0)
        self.assertEqual(self.ledger().search("before malformed")[0]["source_status"], "partial")
        self.assertEqual(self.ledger().search("after malformed")[0]["source_status"], "partial")

    def test_torn_final_row_is_pending_and_cached_until_changed(self):
        path = self.claude_path()
        self.write_claude(path, "pending prefix", "answer")
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"assistant"')
        _, _, code = self.run_convo("sync")
        _, verbose, second_code = self.run_convo("sync", "--verbose")
        self.assertEqual(code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(self.ledger().status()["pending_sources"], 1)
        self.assertIn(str(path), verbose)
        self.assertIn("cached source pending", verbose)

    def test_search_ranks_equal_text_matches_by_message_timestamp(self):
        self.write_claude(self.claude_path("old.jsonl"), "ranking shared phrase", "old", "2026-08-01T00:00:00Z")
        self.write_claude(self.claude_path("new.jsonl"), "ranking shared phrase", "new", "2026-08-03T00:00:00Z")
        self.run_convo("sync")
        hits = self.ledger().search("ranking shared phrase")
        self.assertEqual([hit["session_id"] for hit in hits], ["new", "old"])

    def test_fts_query_safety_treats_operators_and_punctuation_as_terms(self):
        self.write_claude(self.claude_path(), "safe FTS tokens", "answer")
        self.run_convo("sync")
        for query in ('" OR *', "safe OR tokens", "(((())))"):
            _, _, code = self.run_convo("search", query, "--json")
            self.assertEqual(code, 0, query)

    def test_search_human_output_sanitizes_ids_and_quotes_next_command(self):
        source = convo.ledger_lib.ParsedSource(
            "claude", "/untrusted/source", 1, 1,
            0, 0, 1,
            "unsafe; touch /tmp/nope\x1b[31m\nnext", "/project",
            (convo.ledger_lib.Message("user", "terminal safety query", "2026-08-01T00:00:00Z"),),
        )
        self.ledger().replace_source(source, "hash")
        stdout, _, code = self.run_convo("search", "terminal safety query")
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("convo show unsafe;", stdout)
        self.assertIn("next: convo show 'unsafe; touch /tmp/nope[31m", stdout)

    def test_same_size_rewrite_with_restored_mtime_reindexes_from_stat_identity(self):
        path = self.claude_path()
        self.write_claude(path, "same-size alpha", "answer")
        self.run_convo("sync")
        original_stat = path.stat()
        self.write_claude(path, "same-size bravo", "answer")
        self.assertEqual(path.stat().st_size, original_stat.st_size)
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        rewritten_stat = path.stat()
        self.assertNotEqual(rewritten_stat.st_ctime_ns, original_stat.st_ctime_ns)
        _, _, code = self.run_convo("sync")
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger().search("same-size alpha"), [])
        self.assertEqual(self.ledger().search("same-size bravo")[0]["text"], "same-size bravo")

    def test_mutation_during_hash_is_partial_and_preserves_prior_snapshot(self):
        path = self.claude_path()
        self.write_claude(path, "prior consistent topic", "answer")
        self.run_convo("sync")
        self.write_claude(path, "candidate topic", "answer")
        real_hash = convo.ledger_lib.Ledger.hash_file

        def mutate_after_hash(source_path):
            digest = real_hash(source_path)
            self.write_claude(source_path, "mutated during hash", "answer")
            return digest

        with mock.patch.object(convo.ledger_lib.Ledger, "hash_file", side_effect=mutate_after_hash):
            _, stderr, code = self.run_convo("sync")
        self.assertEqual(code, 2)
        self.assertNotIn("source changed while parsing or hashing", stderr)
        hit = self.ledger().search("prior consistent topic")[0]
        self.assertEqual(hit["source_status"], "corrupt")
        self.assertEqual(self.ledger().search("candidate topic"), [])

    def test_future_schema_version_is_not_downgraded_and_status_fails_cleanly(self):
        self.ledger().status()
        self._ledger.close()
        self._ledger = None
        with sqlite3.connect(self.data_dir / "ledger.sqlite3") as conn:
            conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(SystemExit) as raised:
            convo.main(["status"])
        self.assertIn("newer than supported", str(raised.exception))
        with sqlite3.connect(self.data_dir / "ledger.sqlite3") as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_sync_lock_excludes_other_syncs_while_wal_readers_continue(self):
        writer = convo.ledger_lib.Ledger()
        reader = convo.ledger_lib.Ledger()
        contender = convo.ledger_lib.Ledger()
        try:
            writer.status()
            with writer.sync_lock():
                writer.begin_batch()
                self.assertEqual(reader.status()["messages"], 0)
                self.assertEqual(reader.search("anything"), [])
                with self.assertRaises(convo.ledger_lib.LedgerBusyError):
                    with contender.sync_lock():
                        pass
                writer.commit_batch()
        finally:
            writer.close()
            reader.close()
            contender.close()

    def test_rollback_batch_keeps_ledger_connection_usable(self):
        source = convo.ledger_lib.ParsedSource(
            "claude", "/rollback-source", 1, 1, 0, 0, 1, "rollback", None,
            (convo.ledger_lib.Message("user", "discarded", None),),
        )
        ledger = self.ledger()
        ledger.begin_batch()
        ledger.replace_source(source, "hash")
        ledger.rollback_batch()
        self.assertEqual(ledger.status()["messages"], 0)
        self.assertFalse(ledger._batch_active)

    def test_parsing_never_runs_inside_a_buffered_write_batch(self):
        for index in range(convo.ledger_lib.SYNC_BATCH_SIZE + 1):
            self.write_claude(self.claude_path(f"buffered-{index}.jsonl"), f"buffer {index}", "answer")
        original_stream = convo._stream_jsonl_source
        original_begin = convo.ledger_lib.Ledger.begin_batch
        original_commit = convo.ledger_lib.Ledger.commit_batch
        write_active = False

        def checked_stream(*args, **kwargs):
            self.assertFalse(write_active)
            return original_stream(*args, **kwargs)

        def tracked_begin(ledger):
            nonlocal write_active
            original_begin(ledger)
            write_active = True

        def tracked_commit(ledger):
            nonlocal write_active
            original_commit(ledger)
            write_active = False

        convo._stream_jsonl_source = checked_stream
        try:
            with mock.patch.object(convo.ledger_lib.Ledger, "begin_batch", new=tracked_begin), \
                 mock.patch.object(convo.ledger_lib.Ledger, "commit_batch", new=tracked_commit):
                _, _, code = self.run_convo("sync")
        finally:
            convo._stream_jsonl_source = original_stream
        self.assertEqual(code, 0)

    def test_current_commands_remain_direct_raw_readers_after_ledger_commands(self):
        path = self.claude_path()
        self.write_claude(path, "compatibility needle", "compatibility answer")
        before = [
            self.run_convo("list", "--all-projects", "--no-color"),
            self.run_convo("show", str(path), "--all-projects", "--no-color"),
            self.run_convo("grep", "compatibility needle", "--all-projects", "--no-color"),
        ]
        self.run_convo("status")
        after = [
            self.run_convo("list", "--all-projects", "--no-color"),
            self.run_convo("show", str(path), "--all-projects", "--no-color"),
            self.run_convo("grep", "compatibility needle", "--all-projects", "--no-color"),
        ]
        self.assertEqual(before, after)


class PackagingBoundaryTests(unittest.TestCase):
    def test_convo_private_ledger_is_not_vendored_with_uncompact(self):
        self.assertTrue((REPO / "tools" / "convo" / "lib" / "convo_ledger.py").is_file())
        self.assertTrue((REPO / "skills" / "convo" / "scripts" / "lib" / "convo_ledger.py").is_file())
        self.assertFalse((REPO / "skills" / "uncompact" / "scripts" / "lib" / "convo_ledger.py").exists())


if __name__ == "__main__":
    unittest.main()
