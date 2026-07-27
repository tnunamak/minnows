"""Synthetic regression coverage for the stdlib-only convo CLI."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONVO_PATH = REPO / "tools" / "convo" / "convo"
GENERATED_CONVO_PATH = REPO / "skills" / "convo" / "scripts" / "convo"
SPEC = importlib.util.spec_from_loader("convo_under_test", SourceFileLoader("convo_under_test", str(CONVO_PATH)))
convo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = convo
SPEC.loader.exec_module(convo)


def write_jsonl(path: Path, rows: list[dict], trailing: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + trailing,
                    encoding="utf-8")


def claude_user(text: object, **extra: object) -> dict:
    return {"type": "user", "message": {"content": text}, **extra}


def claude_assistant(text: str, **extra: object) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]},
            **extra}


def codex_message(role: str, text: str, **extra: object) -> dict:
    return {"type": "response_item", "payload": {"type": "message", "role": role,
            "content": [{"type": "input_text", "text": text}], **extra}}


class ConvoLoaderTests(unittest.TestCase):
    def temp_path(self, suffix: str = ".jsonl") -> tuple[tempfile.TemporaryDirectory, Path]:
        tmp = tempfile.TemporaryDirectory()
        return tmp, Path(tmp.name) / f"session{suffix}"

    def test_claude_content_variants_filters_and_events(self):
        tmp, path = self.temp_path()
        self.addCleanup(tmp.cleanup)
        write_jsonl(path, [
            claude_user("plain prompt", uuid="u1"),
            claude_assistant("first", uuid="a1"),
            {"type": "user", "uuid": "tools", "message": {"content": [
                {"type": "text", "text": "list prompt"},
                {"type": "image", "source": {"type": "base64"}},
                {"type": "tool_result", "tool_use_id": "call-1", "content": "tool output"},
            ]}},
            claude_user("hidden", isMeta=True),
            claude_user("side", isSidechain=True),
            claude_assistant("second", uuid="a2"),
            claude_assistant("final", uuid="a3"),
        ])
        session = convo.load_claude(path)
        self.assertEqual([turn.role for turn in session.turns], ["user", "assistant", "user", "assistant"])
        self.assertEqual(session.turns[2].text, "list prompt\n\n[image]")
        self.assertEqual(session.turns[2].events[0]["kind"], "tool_result")
        self.assertEqual(session.turns[-1].texts, ["second", "final"])
        self.assertEqual(session.provenance.harness_metadata["skipped_meta_record_count"], 1)
        self.assertEqual(session.provenance.harness_metadata["skipped_sidechain_record_count"], 1)

    def test_claude_compaction_replay_conflict_and_leaf_metadata(self):
        tmp, path = self.temp_path()
        self.addCleanup(tmp.cleanup)
        repeated = claude_assistant("kept once", uuid="a1", parentUuid="u1")
        write_jsonl(path, [
            claude_user("before", uuid="u1"), repeated,
            repeated,
            {"type": "system", "subtype": "compact_boundary", "uuid": "c1", "parentUuid": "a1"},
            claude_user("continued from a previous conversation: synthetic", uuid="summary"),
            claude_user("after", uuid="u2", parentUuid="c1"),
            claude_assistant("conflict one", uuid="a2", parentUuid="u2"),
            claude_assistant("conflict two", uuid="a2", parentUuid="u2"),
            {"type": "last-prompt", "sessionId": "s", "leafUuid": "missing-leaf"},
        ])
        session = convo.load_claude(path)
        self.assertEqual([turn.text for turn in session.turns if turn.role == "user"], ["before", "after"])
        self.assertEqual(session.provenance.segment_count, 2)
        self.assertEqual(session.provenance.deduplicated_record_count, 1)
        self.assertEqual(session.provenance.harness_metadata["conflicting_record_id_count"], 1)
        self.assertIn("not reconstructed", " ".join(session.provenance.notes))
        self.assertEqual([pair["assistant_texts"][-1] for pair in convo.build_pairs(session)],
                         ["kept once", "conflict two"])

    def test_claude_replay_identity_ignores_usage_but_keeps_content_and_graph_distinct(self):
        base = {"type": "assistant", "uuid": "same", "parentUuid": "parent-a",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}],
                            "usage": {"input_tokens": 1}}}
        usage_replay = {"type": "assistant", "uuid": "same", "parentUuid": "parent-a",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}],
                                    "usage": {"input_tokens": 2}}}
        changed_content = {"type": "assistant", "uuid": "same", "parentUuid": "parent-a",
                           "message": {"role": "assistant", "content": [{"type": "text", "text": "changed"}]}}
        changed_parent = {"type": "assistant", "uuid": "same", "parentUuid": "parent-b",
                          "message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}}
        self.assertEqual(convo._claude_record_fingerprint(base), convo._claude_record_fingerprint(usage_replay))
        self.assertNotEqual(convo._claude_record_fingerprint(base), convo._claude_record_fingerprint(changed_content))
        self.assertNotEqual(convo._claude_record_fingerprint(base), convo._claude_record_fingerprint(changed_parent))

        tmp, path = self.temp_path()
        self.addCleanup(tmp.cleanup)
        write_jsonl(path, [claude_user("prompt", uuid="prompt"), base, usage_replay])
        session = convo.load_claude(path)
        self.assertEqual(session.provenance.deduplicated_record_count, 1)
        self.assertEqual(convo.build_pairs(session)[0]["assistant_texts"], ["reply"])

    def test_codex_compactions_keep_primary_rows_and_native_ancestry(self):
        tmp, path = self.temp_path()
        self.addCleanup(tmp.cleanup)
        write_jsonl(path, [
            {"type": "session_meta", "payload": {"cwd": "/work/demo", "id": "thread-a",
             "parent_thread_id": "parent-thread", "forked_from_id": "fork-thread"}},
            codex_message("system", "ignore"), codex_message("developer", "ignore"),
            codex_message("user", "before"), codex_message("assistant", "reply one"),
            {"type": "compacted", "payload": {"replacement_history": [
                {"role": "user", "content": "before"}, {"role": "assistant", "content": "reply one"}
            ]}},
            codex_message("user", "after"), codex_message("assistant", "reply two"),
            {"type": "compacted", "payload": {"replacement_history": []}},
        ])
        session = convo.load_codex(path)
        self.assertEqual([pair["user"] for pair in convo.build_pairs(session)], ["before", "after"])
        self.assertEqual(session.provenance.segment_count, 3)
        self.assertEqual(session.provenance.logical_thread_status, "fork")
        self.assertIsNone(session.provenance.parent_session_id)
        self.assertEqual(session.provenance.harness_metadata["parent_thread_id"], "parent-thread")
        self.assertEqual(session.provenance.harness_metadata["replacement_history_item_count"], 2)

    def test_codex_events_reasoning_and_final_mode(self):
        tmp, path = self.temp_path()
        self.addCleanup(tmp.cleanup)
        write_jsonl(path, [
            codex_message("user", "prompt"),
            {"type": "response_item", "payload": {"type": "reasoning", "summary": "hidden"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "lookup",
             "arguments": {"id": 1}}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call",
             "output": "result"}},
            codex_message("assistant", "first"), codex_message("assistant", "last"),
        ])
        session = convo.load_codex(path)
        pair = convo.build_pairs(session)[0]
        self.assertEqual(pair["assistant_texts"][-1], "last")
        self.assertEqual({event["kind"] for event in pair["assistant_events"]},
                         {"thinking", "tool_use", "tool_result"})

    def test_gemini_json_summary_and_jsonl_mutations(self):
        tmp, path = self.temp_path(".json")
        self.addCleanup(tmp.cleanup)
        path.write_text(json.dumps({"sessionId": "gm", "projectHash": "hash", "summary": "not a prompt",
            "messages": [{"type": "user", "content": "hello"}, {"type": "gemini", "content": "world"}]}))
        snapshot = convo.load_gemini(path)
        self.assertEqual(convo.build_pairs(snapshot)[0]["user"], "hello")
        self.assertEqual(snapshot.provenance.harness_metadata["summary"], "present")

        stream_path = path.with_suffix(".jsonl")
        write_jsonl(stream_path, [
            {"kind": "session", "sessionId": "gm-stream", "projectHash": "hash"},
            {"type": "user", "content": [{"text": "stream prompt"}]},
            {"$set": {"lastUpdated": "later"}},
            {"type": "gemini", "content": "stream reply"},
        ])
        stream = convo.load_gemini(stream_path)
        self.assertEqual(convo.build_pairs(stream)[0]["assistant_texts"], ["stream reply"])
        self.assertEqual(stream.provenance.harness_metadata["source_format"], "jsonl_mutation_stream")

        set_path = path.with_name("session-set.jsonl")
        write_jsonl(set_path, [
            {"kind": "session", "sessionId": "gm-set", "projectHash": "hash"},
            {"$set": {"messages": [
                {"type": "user", "content": "set prompt"},
                {"type": "gemini", "content": "set reply"},
            ]}},
        ])
        set_stream = convo.load_gemini(set_path)
        self.assertEqual(convo.build_pairs(set_stream)[0]["assistant_texts"], ["set reply"])

    def test_torn_prefix_and_middle_corruption(self):
        tmp, path = self.temp_path()
        self.addCleanup(tmp.cleanup)
        write_jsonl(path, [claude_user("safe"), claude_assistant("reply")], trailing='{"type":')
        session = convo.load_claude(path)
        self.assertEqual(session.provenance.physical_session_status, "complete_prefix")
        path.write_text(json.dumps(claude_user("safe")) + "\nnot-json\n" + json.dumps(claude_assistant("reply")) + "\n")
        with self.assertRaisesRegex(ValueError, "corrupt JSONL"):
            convo.load_claude(path)


class ConvoCliTests(unittest.TestCase):
    def run_cli(self, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOME": str(home), "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(CONVO_PATH), *args], text=True,
                              capture_output=True, env=env, check=False)

    def test_show_json_is_additive_and_grep_omits_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session = home / ".claude/projects/-work-demo/session.jsonl"
            write_jsonl(session, [
                claude_user("continued from a previous conversation: hidden"),
                claude_user("real prompt"), claude_assistant("real response"),
            ])
            shown = self.run_cli(home, "show", str(session), "--json")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            data = json.loads(shown.stdout)
            self.assertEqual(data["harness"], "claude")
            self.assertIn("transcript", data)
            self.assertEqual(data["exchanges"][0]["user"], "real prompt")
            searched = self.run_cli(home, "grep", "previous conversation", "--all-projects", "--json")
            self.assertEqual(searched.returncode, 0)
            self.assertEqual(json.loads(searched.stdout), [])

    def test_discovery_includes_verified_gemini_jsonl_and_list_stays_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            stream = home / ".gemini/tmp/hash/chats/session-stream.jsonl"
            write_jsonl(stream, [{"kind": "session", "sessionId": "stream", "projectHash": "hash"},
                                 {"type": "user", "content": "prompt"},
                                 {"type": "gemini", "content": "reply"}])
            listed = self.run_cli(home, "list", "--harness", "gemini", "--all-projects", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout)[0]["session_id"], "stream")

            large = home / ".codex/sessions/2026/01/01/rollout-large.jsonl"
            write_jsonl(large, [{"type": "session_meta", "payload": {"cwd": "/work/demo"}}] +
                       [codex_message("user", f"prompt {i}") for i in range(convo._PEEK_MAX_LINES + 5)])
            listed = self.run_cli(home, "list", "--harness", "codex", "--all-projects", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertTrue(json.loads(listed.stdout)[0]["user_msgs_truncated"])

            huge_stream = home / ".gemini/tmp/hash/chats/session-huge.jsonl"
            huge_stream.parent.mkdir(parents=True, exist_ok=True)
            huge_stream.write_text(
                json.dumps({"kind": "session", "sessionId": "huge", "projectHash": "hash"}) + "\n" +
                json.dumps({"type": "user", "content": "prompt"}) + "\n" +
                "".join(json.dumps({"type": "gemini", "content": "noise"}) + "\n"
                        for _ in range(convo._PEEK_MAX_LINES + 5)) +
                "not-json\n",
                encoding="utf-8",
            )
            listed = self.run_cli(home, "list", "--harness", "gemini", "--all-projects", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertTrue(json.loads(listed.stdout)[0]["user_msgs_truncated"])

    def test_aliases_flags_and_exchange_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session = home / ".codex/sessions/2026/01/01/rollout-session.jsonl"
            write_jsonl(session, [codex_message("user", "one"), codex_message("assistant", "one reply"),
                                  {"type": "compacted", "payload": {}},
                                  codex_message("user", "two"), codex_message("assistant", "two reply")])
            shown = self.run_cli(home, "show", str(session), "--harness", "cx", "-n", "1", "--json")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual([item["user"] for item in json.loads(shown.stdout)["exchanges"]], ["two"])
            both = self.run_cli(home, "list", "--project", "demo", "--all-projects")
            self.assertNotEqual(both.returncode, 0)
            self.assertIn("mutually exclusive", both.stderr)

    def test_generated_skill_uses_vendored_loader_in_temporary_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session = home / ".gemini/tmp/hash/chats/session-generated.jsonl"
            write_jsonl(session, [{"kind": "session", "sessionId": "generated", "projectHash": "hash"},
                                 {"type": "user", "content": "prompt"},
                                 {"type": "gemini", "content": "reply"}])
            env = {**os.environ, "HOME": str(home), "NO_COLOR": "1"}
            result = subprocess.run([sys.executable, str(GENERATED_CONVO_PATH), "show", str(session), "--json"],
                                    text=True, capture_output=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["session_id"], "generated")


if __name__ == "__main__":
    unittest.main()
