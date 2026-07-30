"""Regression tests for the stdlib-only convo CLI."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path


TOOL = Path(__file__).parents[1] / "tools" / "convo" / "convo"
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


if __name__ == "__main__":
    unittest.main()
