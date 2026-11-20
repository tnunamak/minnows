"""readerfit check: deterministic findings a draft's reader would object to."""
import json
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "tools" / "readerfit" / "readerfit"


def run(text, *args, env=None):
    p = subprocess.run([sys.executable, str(CLI), "check", "--stdin", "--json", *args],
                       input=text, capture_output=True, text=True, env=env)
    return p.returncode, json.loads(p.stdout)


def codes(res):
    return {f["code"] for f in res["findings"]}


def test_clean_short_reply_passes():
    rc, res = run("The vendor needs your choice by Friday.\n1. Usage costs the same either way.\n2. Their seat count is wrong.\n",
                  "--budget", "5")
    assert rc == 0 and res["findings"] == []


def test_over_budget_is_high():
    rc, res = run("\n".join(f"Point {i} about something different entirely {i}" for i in range(12)), "--budget", "7")
    assert rc == 1 and "over-budget" in codes(res)


def test_checkbox_is_high_in_any_medium():
    rc, res = run("Questions for the CEO:\n- [ ] Who pays the fee?\n", "--medium", "doc")
    assert rc == 1 and "checkbox" in codes(res)


def test_chat_markup_headings_blockquote_and_bold_leadins():
    text = "## Status\n> quoted\n- **Owner:** Ana\n- next step is a review\n"
    rc, res = run(text, "--medium", "chat")
    assert {"heading-first", "blockquote", "bold-leadin", "headings-in-short-chat"} <= codes(res)


def test_doc_allows_headings_and_tables():
    text = "# Fees\nThe decision is due Friday.\n\n| Option | Cost |\n|---|---|\n| A | 1 |\n"
    rc, res = run(text, "--medium", "doc")
    assert rc == 0
    assert "heading-first" not in codes(res)


def test_slack_rejects_tables():
    rc, res = run("Plan:\n| a | b |\n|---|---|\n", "--medium", "slack")
    assert "table-in-slack" in codes(res)


def test_back_reference_and_repeat():
    text = "The gateway checks scopes on each read.\nThis means builders need new names.\nThe gateway checks scopes on each read again.\n"
    rc, res = run(text)
    assert {"back-reference", "repeated-point"} <= codes(res)


def test_undefined_acronym_unless_defined_or_known(tmp_path):
    rc, res = run("The TEE holds the key.\n")
    assert "undefined-acronym" in codes(res)
    rc, res = run("A trusted execution environment (TEE) holds the key. The TEE signs.\n")
    assert "undefined-acronym" not in codes(res)
    prof = tmp_path / "ana.md"
    prof.write_text("---\nname: ana\n---\n## Knows\n- TEE and attestation.\n")
    import os
    env = dict(os.environ, READERFIT_PROFILES=str(tmp_path))
    rc, res = run("The TEE holds the key.\n", "--reader", "ana", env=env)
    assert "undefined-acronym" not in codes(res)


def test_profile_lookup(tmp_path):
    import os
    (tmp_path / "ana.md").write_text("profile body\n")
    env = dict(os.environ, READERFIT_PROFILES=str(tmp_path))
    p = subprocess.run([sys.executable, str(CLI), "profile", "Ana"], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and p.stdout == "profile body\n"
    p = subprocess.run([sys.executable, str(CLI), "profile", "nobody"], capture_output=True, text=True, env=env)
    assert p.returncode == 2


def test_roman_numeral_clause_is_not_an_acronym():
    rc, res = run("The agreement (clause VI.3) keeps metadata in Australia.\n", "--medium", "doc")
    assert "undefined-acronym" not in codes(res)
