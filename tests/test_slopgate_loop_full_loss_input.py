import importlib.util
import pathlib
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools/slopgate/semantic/loop_test.py"
SPEC = importlib.util.spec_from_file_location("slopgate_loop_test", SCRIPT)
loop_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop_test)


def test_fact_loss_judge_receives_complete_long_inputs(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sentinel = "FACT_AFTER_CHARACTER_6000"
    before = "x" * 6100 + sentinel
    (corpus / "long.txt").write_text(before)
    after = "y" * 6100 + sentinel
    prompts = []

    def judge(prompt, **_kwargs):
        prompts.append(prompt)
        return {
            1: after,
            2: "LOST: no\nWHAT: none",
            3: "BETTER: A",
        }[len(prompts)]

    with mock.patch.object(loop_test, "HERE", tmp_path), \
         mock.patch.object(loop_test, "check", return_value=[
             {"severity": "high", "rule": "one", "suggestion": "fix"},
             {"severity": "high", "rule": "two", "suggestion": "fix"},
         ]), \
         mock.patch.object(loop_test, "llm", side_effect=judge), \
         mock.patch.object(loop_test.sys, "argv", ["loop_test.py", "doc"]), \
         mock.patch.dict("os.environ", {
             "LOOP_CORPUS": "corpus",
             "LOOP_OUT": "results.json",
         }, clear=False):
        loop_test.main()

    assert sentinel in prompts[1]
    assert "[...truncated for comparison]" not in prompts[1]
