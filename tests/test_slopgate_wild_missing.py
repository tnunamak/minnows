from pathlib import Path
import os
import subprocess

import pytest

def test_missing_corpus_input_is_an_incomplete_scan(tmp_path):
    script=Path(__file__).resolve().parents[1]/"tools/slopgate/tests/wild-scan.mjs"
    missing=tmp_path/"missing.md"
    result=subprocess.run(["node",str(script),str(missing)],text=True,capture_output=True)
    assert result.returncode!=0
    assert str(missing) in result.stderr
    assert "Failed inputs: 1" in result.stdout

SLOP="Let's delve into a rich tapestry. It's not just a tool, it's a game-changer.\n"

def test_unreadable_nested_directory_does_not_stop_sibling_scans(tmp_path):
    if os.geteuid()==0: pytest.skip("root can read mode 000 directories")
    script=Path(__file__).resolve().parents[1]/"tools/slopgate/tests/wild-scan.mjs"
    corp=tmp_path/"corp"
    (corp/"a").mkdir(parents=True)
    (corp/"locked").mkdir()
    (corp/"a"/"x.md").write_text(SLOP)
    (corp/"z.md").write_text(SLOP)
    (corp/"locked"/"hidden.md").write_text(SLOP)
    (corp/"locked").chmod(0o000)
    try:
        result=subprocess.run(["node",str(script),str(corp)],text=True,capture_output=True)
    finally:
        (corp/"locked").chmod(0o755)
    assert result.returncode!=0
    assert str(corp/"locked") in result.stderr
    assert "Failed inputs: 1" in result.stdout
    assert str(corp/"a"/"x.md") in result.stdout
    assert str(corp/"z.md") in result.stdout
