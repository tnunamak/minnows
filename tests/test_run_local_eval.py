import importlib.util
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    'run_local_eval', Path(__file__).resolve().parents[1] / 'scripts' / 'run_local_eval.py'
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_file_equals_rejects_correct_first_line_with_wrong_trailing_content(tmp_path):
    (tmp_path / 'result.txt').write_text('PASS\nincorrect additional result\n', encoding='utf-8')

    passed, detail = RUNNER.run_oracle(
        {'type': 'file_equals', 'path': 'result.txt', 'equals': 'PASS'},
        tmp_path,
        None,
    )

    assert not passed, detail


def test_file_equals_accepts_the_complete_expected_output(tmp_path):
    (tmp_path / 'result.txt').write_text('PASS\n', encoding='utf-8')

    passed, detail = RUNNER.run_oracle(
        {'type': 'file_equals', 'path': 'result.txt', 'equals': 'PASS'},
        tmp_path,
        None,
    )

    assert passed, detail
