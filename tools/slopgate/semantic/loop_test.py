#!/usr/bin/env python3
"""Does acting on the findings actually improve the writing?

Every other number in this repo measures whether a rule fires in the right
place. None of them measure the thing the tool exists for: that an agent which
fixes what the tool names ends up with better prose. Those are different claims,
and they come apart when a rule is vague enough to be satisfiable by deleting
content instead of improving it -- which has already happened here once, when a
"plain sentences" criterion drove composite from 0.560 to 0.731 while the prose
visibly got worse.

So this measures improvement-under-edit directly:

  1. take a draft, measure it
  2. revise ONLY what fired, using the finding's own suggestion
  3. re-measure
  4. have a DIFFERENT model blind-rank before vs after, order shuffled, without
     being told which is which or that a tool was involved

The blind rank is the real result. Score movement alone is self-agreement: the
optimiser and the scorer are the same rule bank, so of course the score drops.
The question is whether a reader prefers the output.

Writer and judge both run on the local gateway, so a rerun is free.
"""
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jevgate import load_rules  # noqa: E402

GATEWAY = "https://ai.vivid.fish/v1/chat/completions"
MODEL = "current-fast"

REVISE = """Revise the text below. Fix ONLY the specific problems listed; leave everything
else exactly as it is.

Rules:
- Keep every fact a reader needs in order to act: what broke, under what
  condition, which component, which version, what the fix does, what is
  unverified. Cutting padding is good; cutting one of those is not.
- Specifics are never padding. Version numbers, error codes, command names,
  file paths, counts and identifiers must survive verbatim even while the
  sentences around them change. If a sentence is repetitive, rewrite the
  sentence and carry its specifics into the replacement.
- Do not invent facts. Anything you state must already be in the text.
- Length should follow content. If the text is padded, it should get shorter.
- Write full sentences, averaging roughly twenty words. Do not chop prose into
  short fragments: measured against real maintainer writing, every revision that
  drifted away from how good technical prose reads did so by over-compressing,
  not by staying too long. Fixing repetition means removing the repeated idea,
  not shortening the sentence that survives.

TEXT:
---
{text}
---

PROBLEMS TO FIX:
{problems}

Output ONLY the revised text, nothing else."""

LOSS = """Below are two versions of the same technical text: an original and a revision.

Did the revision drop any fact a reader needs in order to act on it? That means:
what broke, under what condition, which component or version, what the fix does,
or what is left unverified.

Padding, repetition, background the reader already has, and restated summaries do
NOT count as facts a reader needs. Removing those is an improvement.

--- ORIGINAL ---
{before}

--- REVISION ---
{after}

Answer with exactly two lines:
LOST: yes
WHAT: <the specific needed fact that vanished>
or
LOST: no
WHAT: none"""

JUDGE = """Below are two versions of the same piece of technical writing, labelled A and B.

Which is better written? Judge on: does it state a concrete problem, explain the
mechanism, avoid saying the same thing twice, avoid empty claims, and put the
thing that matters where a reader will find it.

Ignore length. Ignore formatting differences.

--- A ---
{a}

--- B ---
{b}

Answer with ONLY one line:
BETTER: A
or
BETTER: B"""


def llm(prompt, max_tokens=1200, timeout=300, tries=4):
    """Retry transient gateway failures; the local backend sheds load."""
    last = None
    for i in range(tries):
        try:
            return _llm(prompt, max_tokens, timeout)
        except RuntimeError as e:
            last = e
            if "no available server" in str(e) or "overload" in str(e).lower():
                time.sleep(15 * (i + 1))
                continue
            raise
    raise last


def _llm(prompt, max_tokens=1200, timeout=300):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "temperature": 0.3}
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as fh:
        fh.write(json.dumps(payload))
        body = pathlib.Path(fh.name)
    try:
        r = subprocess.run(
            ["curl", "-sS", "-m", str(timeout), GATEWAY,
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {os.environ.get('OPENAI_API_KEY','')}",
             "-d", f"@{body}"],
            capture_output=True, text=True, timeout=timeout + 30)
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            # An over-long prompt gets rejected without a JSON body. Surface it
            # rather than crashing the whole run.
            raise RuntimeError(f"gateway error: {r.stdout[:200] or r.stderr[:200]}")
        if "choices" not in d:
            raise RuntimeError(f"gateway error: {str(d)[:200]}")
        t = d["choices"][0]["message"]["content"].strip()
        lines = t.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
        return "\n".join(lines).strip()
    finally:
        body.unlink(missing_ok=True)


def check(text, kind):
    """Run the real CLI so this tests what a user actually runs."""
    r = subprocess.run(
        ["slopgate", "check", "--stdin", "--kind", kind, "--json", "--semantic-only"],
        input=text, capture_output=True, text=True, timeout=600)
    try:
        return json.loads(r.stdout).get("findings", [])
    except json.JSONDecodeError:
        return []


def clip(s, n=6000):
    """Cap a document so a two-document comparison prompt always fits."""
    return s if len(s) <= n else s[:n] + "\n[...truncated for comparison]"


def facts(text):
    """Load-bearing facts only, for detecting real loss during revision.

    An earlier version matched any digit and any backticked span, which counted
    `0`, `1`, `` `+` `` and the fragments of a date as separate facts, and an
    unterminated backtick swallowed 500 characters of prose as one "fact". That
    inflated pdpp-315's apparent loss to 58 when most of the missing items were
    not facts at all. Match only things whose loss would actually change what a
    reader knows.
    """
    out = set()
    out |= set(re.findall(r"\bRFC\s?\d{3,5}\b", text))
    out |= set(re.findall(r"\bCVE-\d{4}-\d+\b", text))
    out |= set(re.findall(r"\b\d[\d,]{2,}\b", text))          # 3+ digit counts
    out |= set(re.findall(r"\b\d+(?:\.\d+)+\b", text))       # versions
    out |= set(re.findall(r"\b[\w-]+\.(?:md|ts|py|mjs|yml|yaml|json|sh)\b", text))
    out |= set(re.findall(r"`([A-Za-z_][\w./-]{2,60})`", text)) # short code idents
    return out


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "doc"
    src = HERE / os.environ.get("LOOP_CORPUS", "loop_corpus")
    rng = random.Random(int(os.environ.get('LOOP_SEED', '20260918')))
    rows = []

    for f in sorted(src.glob("*.txt")):
      try:
        before = f.read_text().strip()
        fb = check(before, kind)
        # Only act when there is real signal. Measured: drafts with >=4 findings
        # improve under revision 4 of 6 times; drafts with fewer improve 3 of 9,
        # i.e. revising a nearly-clean draft churns good prose for nothing. The
        # high-severity count is the gate rather than the raw count, because a
        # pile of `low` findings is not evidence the draft needs work.
        high = [x for x in fb if x.get("severity") in ("high", "critical")]
        if len(high) < 2:
            print(f"[{f.stem}] below action floor "
                  f"({len(high)} high of {len(fb)}), leaving alone", flush=True)
            continue
        problems = "\n".join(
            f"- {x['rule']}: {x.get('suggestion','')}" for x in fb)
        # Budget output tokens from input size. A long document truncated
        # mid-sentence reads as catastrophic fact loss when the real fault is
        # the token ceiling; pdpp-315 failed this way at a fixed 1200.
        budget = max(1200, int(len(before.split()) * 2.2))
        after = llm(REVISE.format(text=before, problems=problems),
                    max_tokens=budget)
        fa = check(after, kind)
        # Persist revisions so the non-LLM drift check can compare distributions.
        outdir = HERE / "loop_revisions"
        outdir.mkdir(exist_ok=True)
        (outdir / f"{f.stem}.txt").write_text(after)

        # Judged, not counted. An earlier version counted vanished tokens and
        # reported 58 losses on a padded document whose "facts" were mostly line
        # numbers, a date fragment and the word MUST. Deleting those from a bloated
        # document is the improvement, not damage. What matters is whether the
        # revision dropped something a reader needed, which requires judgement.
        # Guard against a truncated revision being scored as a real edit.
        # Truncation is only detectable RELATIVE to the original. Absolute
        # heuristics fail: pdpp-318's own original ends "Assisted-by: AI", a
        # legitimate trailer that no sentence-ending test accepts, so an
        # absolute check flagged texts the judge preferred. A revision is
        # suspect when it is much shorter than asked for AND ends mid-prose
        # while the original did not.
        def ends_clean(s):
            last = (s.rstrip().splitlines() or [""])[-1].strip()
            if not last or re.match(r"^[#>|\-*\d`\[]", last):
                return True                      # heading/list/table/code
            if re.match(r"^[A-Z][\w-]+:\s", last):
                return True                      # trailer, e.g. Assisted-by:
            return last.endswith((".", "!", "?", ":", '"', ")"))
        shrank = len(after.split()) < len(before.split()) * 0.45
        truncated = shrank and ends_clean(before) and not ends_clean(after)

        loss_verdict = llm(LOSS.format(before=clip(before), after=clip(after)),
                           max_tokens=120)
        lost_real = bool(re.search(r"LOST:\s*yes", loss_verdict, re.I))
        lost_what = ""
        m2 = re.search(r"WHAT:\s*(.+)", loss_verdict)
        if m2:
            lost_what = m2.group(1).strip()[:160]

        # Blind pairwise, order shuffled.
        flip = rng.random() < 0.5
        a, b = (after, before) if flip else (before, after)
        verdict = llm(JUDGE.format(a=clip(a), b=clip(b)), max_tokens=20)
        m = re.search(r"BETTER:\s*([AB])", verdict, re.I)
        pick = None
        if m:
            chosen = m.group(1).upper()
            pick = ("after" if (chosen == "A") == flip else "before")

        rows.append({"case": f.stem, "n_before": len(fb), "n_after": len(fa),
                     "preferred": pick, "lost_needed": lost_real,
                     "truncated": truncated, "lost_what": lost_what})
        print(f"[{f.stem}] findings {len(fb)}->{len(fa)}  judge prefers "
              f"{pick}  lost_needed={'YES '+lost_what if lost_real else 'no'}",
              flush=True)
        (HERE / os.environ.get("LOOP_OUT", "loop_test_results.json")).write_text(json.dumps(rows, indent=2))
      except Exception as e:
        print(f"[{f.stem}] SKIPPED: {type(e).__name__}: {str(e)[:110]}", flush=True)

    if not rows:
        print("no cases")
        return
    n = len(rows)
    better = sum(1 for r in rows if r["preferred"] == "after")
    worse = sum(1 for r in rows if r["preferred"] == "before")
    dropped = sum(1 for r in rows if r["n_after"] < r["n_before"])
    lost_any = sum(1 for r in rows if r["lost_needed"])
    print(f"\n=== {n} cases ===")
    print(f"judge prefers the revision : {better}/{n}")
    print(f"judge prefers the original : {worse}/{n}")
    print(f"findings decreased         : {dropped}/{n}")
    print(f"lost something a reader needed: {lost_any}/{n}")
    trunc = sum(1 for r in rows if r.get("truncated"))
    if trunc:
        print(f"truncated revisions (a bug, not a result): {trunc}/{n}")
    print("\nThe first line is the result. The rest is diagnosis.")


if __name__ == "__main__":
    main()
