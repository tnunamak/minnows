import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_local_eval.py"
SPEC = importlib.util.spec_from_file_location("run_local_eval", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class PolicySnapshotTests(unittest.TestCase):
    def test_execution_and_result_use_one_policy_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local = root / "local"
            fixture = local / "fixture"
            fixture.mkdir(parents=True)
            (fixture / "input.txt").write_text("fixture", encoding="utf-8")
            results = root / "results"
            policy = root / "policy.json"

            def write_policy(provider, model):
                policy.write_text(json.dumps({
                    "policy_version": model,
                    "catalog_ref": f"catalog-{model}",
                    "operating_points": [{
                        "id": "op",
                        "expands_to": {"provider": provider, "model": model, "effort": "low"},
                    }],
                }), encoding="utf-8")

            write_policy("codex", "old-model")
            task_path = root / "task.json"
            task_path.write_text(json.dumps({
                "id": "snapshot-test",
                "op": "op",
                "fixture": "fixture",
                "prompt": "do the task",
                "metric": "local-snapshot",
                "task_family": "test",
                "oracle": {"type": "shell", "command": ["true"]},
            }), encoding="utf-8")

            real_copy2 = runner.shutil.copy2

            def update_after_fixture_copy(src, dest):
                result = real_copy2(src, dest)
                write_policy("claude", "new-model")
                return result

            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                mock.patch.object(runner, "LOCAL", local),
                mock.patch.object(runner, "RESULTS", results),
                mock.patch.object(runner, "POLICY", policy),
                mock.patch.object(runner.shutil, "copy2", side_effect=update_after_fixture_copy),
                mock.patch.object(runner.subprocess, "run", side_effect=fake_run),
                mock.patch.object(runner, "REPO", root),
            ):
                self.assertEqual(runner.run_one(task_path, dry_run=False), 0)

            result = json.loads(next(results.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(commands[0][0], "codex")
            self.assertEqual(commands[0][commands[0].index("--model") + 1], "old-model")
            self.assertEqual(result["run"]["expands_to"]["model"], "old-model")
            self.assertEqual(result["run"]["policy_version"], "old-model")


if __name__ == "__main__":
    unittest.main()
