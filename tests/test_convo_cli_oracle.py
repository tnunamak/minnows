"""Black-box compatibility oracle for the current ``convo`` CLI.

The subprocess boundary is intentional.  ``convo`` resolves its discovery roots from
``Path.home()`` at import time and its default project from ``Path.cwd()``; setting
``HOME`` and ``cwd`` gives these tests isolated roots without adding a production seam.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional


TOOL = Path(__file__).parents[1] / "tools" / "convo" / "convo"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def claude_slug(cwd: Path) -> str:
    return "-" + str(cwd).replace("/", "-").lstrip("-")


class ConvoCliOracleTests(unittest.TestCase):
    """Characterize default/list/show/grep across all four supported harnesses."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.project = self.root / "workspace" / "target"
        self.other_project = self.root / "workspace" / "other"
        self.project.mkdir(parents=True)
        self.other_project.mkdir(parents=True)
        self.paths = self._write_representative_sessions()

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PYTHONNOUSERSITE"] = "1"
        return subprocess.run(
            [str(TOOL), *args],
            cwd=str(cwd or self.project),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_representative_sessions(self) -> dict[str, Path]:
        target = str(self.project)
        paths: dict[str, Path] = {}

        paths["claude"] = self.home / ".claude" / "projects" / claude_slug(self.project) / "claude-target.jsonl"
        write_jsonl(paths["claude"], [
            {
                "type": "user", "timestamp": "2026-08-01T00:00:00Z", "cwd": target,
                "isMeta": True, "message": {"content": "# AGENTS.md instructions"},
            },
            {
                "type": "user", "timestamp": "2026-08-01T00:00:01Z", "cwd": target,
                "isMeta": False, "message": {"content": "needle-claude user"},
            },
            {
                "type": "assistant", "timestamp": "2026-08-01T00:00:02Z", "cwd": target,
                "message": {"content": [
                    {"type": "thinking", "thinking": "hidden reasoning"},
                    {"type": "text", "text": "claude draft"},
                    {"type": "tool_use", "name": "inspect", "input": {"path": "fixture"}},
                ]},
            },
            {
                "type": "user", "timestamp": "2026-08-01T00:00:03Z", "cwd": target,
                "isMeta": False, "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "inspect", "content": "ok"},
                ]},
            },
            {
                "type": "assistant", "timestamp": "2026-08-01T00:00:04Z", "cwd": target,
                "message": {"content": [{"type": "text", "text": "claude final"}]},
            },
            {
                "type": "user", "timestamp": "2026-08-01T00:00:06Z", "cwd": target,
                "isMeta": False, "message": {"content": "second claude prompt"},
            },
            {
                "type": "assistant", "timestamp": "2026-08-01T00:00:07Z", "cwd": target,
                "message": {"content": [{"type": "text", "text": "second claude reply"}]},
            },
            {
                "type": "assistant", "timestamp": "2026-08-01T00:00:08Z", "cwd": target,
                "isSidechain": True, "message": {"content": [{"type": "text", "text": "sidechain noise"}]},
            },
        ])

        paths["codex"] = self.home / ".codex" / "sessions" / "2026" / "08" / "01" / "rollout-codex-target.jsonl"
        write_jsonl(paths["codex"], [
            {"type": "session_meta", "timestamp": "2026-08-01T01:00:00Z", "payload": {"cwd": target}},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:01Z", "payload": {
                "type": "message", "role": "user", "content": "# AGENTS.md instructions preamble",
            }},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:02Z", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "needle-codex user"}],
            }},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:03Z", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "codex draft"}],
            }},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:04Z", "payload": {
                "type": "reasoning", "summary": [{"text": "hidden reasoning"}],
            }},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:05Z", "payload": {
                "type": "function_call", "name": "inspect", "arguments": {"path": "fixture"}, "call_id": "call-1",
            }},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:06Z", "payload": {
                "type": "function_call_output", "call_id": "call-1", "output": "ok",
            }},
            {"type": "response_item", "timestamp": "2026-08-01T01:00:07Z", "payload": {
                "type": "message", "role": "assistant", "content": "codex final",
            }},
        ])

        qwen_slug = str(self.project).replace("/", "-")
        paths["qwen"] = self.home / ".qwen" / "projects" / qwen_slug / "chats" / "qwen-target.jsonl"
        write_jsonl(paths["qwen"], [
            {
                "sessionId": "qwen-target", "timestamp": "2026-08-01T02:00:00Z", "type": "user",
                "provenance": "real_user", "cwd": target,
                "message": {"role": "user", "parts": [{"text": "needle-qwen user"}]},
            },
            {
                "sessionId": "qwen-target", "timestamp": "2026-08-01T02:00:01Z", "type": "assistant",
                "provenance": "assistant_output", "cwd": target,
                "message": {"role": "model", "parts": [
                    {"text": "hidden reasoning", "thought": True},
                    {"functionCall": {"id": "call-1", "name": "inspect", "args": {"path": "fixture"}}},
                    {"text": "qwen draft"},
                ]},
            },
            {
                "sessionId": "qwen-target", "timestamp": "2026-08-01T02:00:02Z", "type": "tool_result",
                "provenance": "tool_result", "cwd": target,
                "message": {"role": "user", "parts": [{"functionResponse": {
                    "id": "call-1", "name": "inspect", "response": "ok",
                }}]},
            },
            {
                "sessionId": "qwen-target", "timestamp": "2026-08-01T02:00:03Z", "type": "assistant",
                "provenance": "assistant_output", "cwd": target,
                "message": {"role": "model", "parts": [{"text": "qwen final"}]},
            },
            {
                "sessionId": "qwen-target", "timestamp": "2026-08-01T02:00:04Z", "type": "user",
                "provenance": "system", "cwd": target,
                "message": {"role": "user", "parts": [{"text": "internal notification"}]},
            },
        ])

        paths["gemini"] = self.home / ".gemini" / "tmp" / "project-hash" / "chats" / "session-gemini.json"
        paths["gemini"].parent.mkdir(parents=True, exist_ok=True)
        paths["gemini"].write_text(json.dumps({
            "sessionId": "gemini-target", "projectHash": "project-hash",
            "summary": "summary is metadata, not a user turn",
            "messages": [
                {"type": "user", "timestamp": "2026-08-01T03:00:00Z", "content": "needle-gemini user"},
                {"type": "gemini", "timestamp": "2026-08-01T03:00:01Z", "content": "gemini final"},
            ],
        }), encoding="utf-8")

        remote = self.home / ".claude" / "projects" / claude_slug(self.other_project) / "claude-remote.jsonl"
        write_jsonl(remote, [
            {"type": "user", "timestamp": "2026-08-01T04:00:00Z", "cwd": str(self.other_project),
             "isMeta": False, "message": {"content": "needle-remote user"}},
            {"type": "assistant", "timestamp": "2026-08-01T04:00:01Z", "cwd": str(self.other_project),
             "message": {"content": [{"type": "text", "text": "remote reply"}]}},
        ])
        paths["remote"] = remote

        for offset, path in enumerate(paths.values()):
            os.utime(path, (1_754_000_000 + offset, 1_754_000_000 + offset))
        return paths

    def test_default_is_list_and_filters_to_current_project(self):
        default = self.run_cli()
        explicit = self.run_cli("list")

        self.assertEqual(default.returncode, 0)
        self.assertEqual(default.stdout, explicit.stdout)
        self.assertIn("[CC]", default.stdout)
        self.assertIn("[CX]", default.stdout)
        self.assertIn("[QW]", default.stdout)
        self.assertNotIn("claude-remote", default.stdout)
        self.assertNotIn("[GM]", default.stdout)

    def test_list_json_shape_across_all_harnesses(self):
        result = self.run_cli("list", "--all-projects", "--harness", "all", "--limit", "20", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)
        self.assertEqual({entry["harness"] for entry in entries}, {"claude", "codex", "gemini", "qwen"})
        self.assertIn("claude-remote", {entry["session_id"] for entry in entries})
        self.assertTrue(all(set(entry) == {
            "harness", "session_id", "path", "project", "mtime", "user_msgs",
            "user_msgs_truncated", "first_user",
        } for entry in entries))

    def test_show_json_shape_and_clean_exchange_for_each_harness(self):
        expected = {
            "claude": ("needle-claude user", "claude final"),
            "codex": ("needle-codex user", "codex final"),
            "gemini": ("needle-gemini user", "gemini final"),
            "qwen": ("needle-qwen user", "qwen final"),
        }
        for harness, path in self.paths.items():
            if harness == "remote":
                continue
            with self.subTest(harness=harness):
                result = self.run_cli("show", str(path), "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(set(data), {"harness", "session_id", "path", "project", "exchanges"})
                self.assertEqual(data["harness"], harness)
                exchange = data["exchanges"][0]
                self.assertEqual(set(exchange), {"ts", "user", "assistant_final", "assistant_all"})
                self.assertEqual((exchange["user"], exchange["assistant_final"]), expected[harness])
                self.assertEqual(exchange["assistant_all"][-1], expected[harness][1])

    def test_grep_regex_json_and_all_projects(self):
        pattern = r"needle-(?:claude|codex|gemini|qwen|remote)"
        scoped = self.run_cli("grep", pattern, "--json")
        all_projects = self.run_cli("grep", pattern, "--all-projects", "--json")

        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        self.assertEqual(all_projects.returncode, 0, all_projects.stderr)
        scoped_hits = json.loads(scoped.stdout)
        all_hits = json.loads(all_projects.stdout)
        self.assertEqual({hit["harness"] for hit in scoped_hits}, {"claude", "codex", "qwen"})
        self.assertEqual({hit["session_id"] for hit in scoped_hits}, {
            "claude-target", "codex-target", "qwen-target",
        })
        self.assertEqual({hit["session_id"] for hit in all_hits}, {
            "claude-target", "codex-target", "gemini-target", "qwen-target", "claude-remote",
        })
        self.assertTrue(all(set(hit) == {"harness", "session_id", "path", "role", "ts", "text"} for hit in all_hits))

    def test_torn_unterminated_final_jsonl_row_is_ignored(self):
        path = self.home / ".claude" / "projects" / claude_slug(self.project) / "claude-torn.jsonl"
        write_jsonl(path, [
            {"type": "user", "timestamp": "2026-08-02T00:00:00Z", "cwd": str(self.project),
             "isMeta": False, "message": {"content": "torn prompt"}},
            {"type": "assistant", "timestamp": "2026-08-02T00:00:01Z", "cwd": str(self.project),
             "message": {"content": [{"type": "text", "text": "safe prefix"}]}},
        ])
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"type":"assistant","message":{"content":[{"type":"text","text":"incomplete')

        result = self.run_cli("show", str(path), "--no-color")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("torn prompt", result.stdout)
        self.assertIn("safe prefix", result.stdout)
        self.assertNotIn("incomplete", result.stdout)

    def test_corrupt_terminated_jsonl_row_is_reported_with_exit_two(self):
        path = self.home / ".claude" / "projects" / claude_slug(self.project) / "claude-corrupt.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"type": "user", "timestamp": "2026-08-03T00:00:00Z", "cwd": str(self.project),
                        "isMeta": False, "message": {"content": "valid prefix"}}) + "\n"
            + "{not valid json}\n"
            + json.dumps({"type": "assistant", "timestamp": "2026-08-03T00:00:01Z", "cwd": str(self.project),
                          "message": {"content": [{"type": "text", "text": "unreachable"}]}}) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli("list", "--all-projects", "--harness", "claude", "--limit", "20", "--json")

        self.assertEqual(result.returncode, 2)
        self.assertIn("claude-target", {entry["session_id"] for entry in json.loads(result.stdout)})
        self.assertIn("corrupt/unreadable session skipped", result.stderr)
        self.assertIn("claude-corrupt.jsonl", result.stderr)

    def test_human_output_removes_terminal_control_bytes(self):
        path = self.home / ".claude" / "projects" / claude_slug(self.project) / "claude-terminal.jsonl"
        write_jsonl(path, [
            {"type": "user", "timestamp": "2026-08-04T00:00:00Z", "cwd": str(self.project),
             "isMeta": False, "message": {"content": "terminal \x1b[31muser\x1b[0m\x7f"}},
            {"type": "assistant", "timestamp": "2026-08-04T00:00:01Z", "cwd": str(self.project),
             "message": {"content": [{"type": "text", "text": "terminal \x1b[32massistant\x1b[0m\x9b"}]}},
        ])

        result = self.run_cli("show", str(path), "--no-color")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotRegex(result.stdout, r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
        self.assertIn("terminal [31muser[0m", result.stdout)
        self.assertIn("terminal [32massistant[0m", result.stdout)


if __name__ == "__main__":
    unittest.main()
