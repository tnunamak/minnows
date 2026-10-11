from pathlib import Path
import subprocess

def test_missing_corpus_input_is_an_incomplete_scan(tmp_path):
    script=Path(__file__).resolve().parents[1]/"tools/slopgate/tests/wild-scan.mjs"
    missing=tmp_path/"missing.md"
    result=subprocess.run(["node",str(script),str(missing)],text=True,capture_output=True)
    assert result.returncode!=0
    assert str(missing) in result.stderr
    assert "Failed inputs: 1" in result.stdout
