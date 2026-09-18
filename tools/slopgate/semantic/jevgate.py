#!/usr/bin/env python3
"""jevgate — semantic slop findings for an agent to act on, via TypeSafe Jev.

slopgate's five deterministic layers are free, fast and blind to meaning: they
catch syntactic frames ("not just X but Y"), cadence and tool artifacts, but not
"this change improves robustness and ensures correctness", which is slop by
content rather than by shape. This adds that layer.

Design, from the measured findings in
ai/research/llm-integration/a-large-parallel-bank-of-atomic-typed-questions-*.md:

  - ONE request per document. Jev ingests the state once and evaluates every
    question against it in parallel, so a large bank costs roughly what a small
    one does (vendor: 13 questions batched = 12.2x cheaper, 10x faster).
  - Every question states an exact observable condition. Jev answers what you
    wrote, not what you meant (jaggedness failure mode 1).
  - Questions are phrased so that YES means "this defect is present", giving a
    finding an agent can act on rather than a score it must interpret.
  - Thresholds are per-question, set from how sharply each one separated on the
    labelled corpus, not one global cutoff.

Sourcing notes that shaped the bank, all from ai/research/writing-craft/:
  - No vocabulary questions: the 112-word AI-tell table measures 0.9x lift,
    worse than a coin flip.
  - No em-dash question: lift is inverted at 0.2x (fires more on human text),
    and slopgate independently tried and dropped it.
  - Passive voice is NOT asked as a tell: measured at half the human rate for
    GPT-4o, so the folk direction is backwards.
  - Sentence-rhythm questions are omitted here because slopgate's cadence.mjs
    already measures rhythm deterministically and for free, and because rhythm
    measured REVERSED on short texts in local testing.

Usage:
  jevgate.py --file <path>
  jevgate.py --stdin [--json] [--kind commit|pr|doc] [--quiet]

Exit code 1 if any finding survives, 0 otherwise. I/O errors and a missing API
key exit 0 with no findings, so a broken caller never blocks a workflow — the
same contract slopgate uses.
"""
import argparse
import json
import os
import pathlib
import sys

# Each entry: (question text, threshold, severity, suggestion).
# YES on the question == defect present. Thresholds come from observed
# separation on the labelled corpus; a question that separated sharply gets a
# lower bar, a noisy one a higher bar.
# Dropped after measuring against 31 commit messages by antirez, Tim Pope and the
# git maintainers (corpus/authorities/, authorship is a git fact and all pre-2023
# so none can be machine-written):
#   inventory_shape   scored up to 0.97 on that corpus -- great writers do list
#                     changes when the change really is a list.
#   repeated_openers  scored up to 0.92 -- repeated sentence openers are normal
#                     in good technical prose.
# Neither leaves headroom above excellent writing, so both were pure noise.
# Rules live in rules.json so they can be edited, extended or replaced without
# touching code. A user can point at their own bank with SLOPGATE_RULES, and can
# scope a rule to particular artifact kinds -- a rule that is right for a commit
# ("does this restate the edit without the reason?") is wrong for a website,
# where teaching a stranger is the job rather than the defect.
def load_rules(kind="doc"):
    path = os.environ.get("SLOPGATE_RULES") or str(
        pathlib.Path(__file__).resolve().parent / "rules.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for name, r in (data.get("rules") or {}).items():
        kinds = r.get("kinds")
        if kinds and kind not in kinds:
            continue
        q = r.get("question")
        if not q:
            continue
        # A rule may carry per-kind thresholds. The same defect needs a
        # different bar in different registers: a maintainer asserting a fact
        # about their own code without a citation is correct in a pull request
        # and a defect in a document, so `unsourced_assertion` sits at 0.93 for
        # pr and 0.60 for doc. Measured on 30 pre-2023 human-written PR bodies,
        # where the doc bar fired on 22 of them.
        thresholds = r.get("thresholds") or {}
        out[name] = {"question": q,
                     "threshold": float(thresholds.get(kind, r.get("threshold", 0.5))),
                     "severity": r.get("severity", "medium"),
                     "suggestion": r.get("suggestion", ""),
                     # A Choice rule names mutually exclusive diagnoses and
                     # reports which one applies, rather than firing a separate
                     # yes/no per candidate. `flag` lists the options that are
                     # defects; any other option is the healthy case.
                     "choices": r.get("choices"),
                     "flag": r.get("flag") or [],
                     # A Score rule rates a document on ordered levels and fires
                     # when the rating falls at or below `max_level` (0-indexed
                     # from the worst level). Use it where the defect is a
                     # matter of degree rather than presence, and where the
                     # levels can be described as concrete situations -- the
                     # Score docs warn that degree words like "moderately" score
                     # far worse than a described situation.
                     "levels": r.get("levels"),
                     "max_level": r.get("max_level")}
    return out

KIND_STATE = {
    "commit": "commit_message",
    "pr": "pr_description",
    "doc": "document",
    "web": "web_page",
}


def run(text, kind="doc"):
    try:
        from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
    except ImportError:
        return None, "typesafe_sdk not installed"
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None, "TYPESAFE_API_KEY not set"
    checks = load_rules(kind)
    if not checks:
        return None, "no rules loaded (check rules.json)"
    qs = {}
    for k, v in checks.items():
        if v["choices"]:
            qs[k] = Choice(instructions=v["question"],
                           criteria={c: None for c in v["choices"]})
        elif v["levels"]:
            qs[k] = Score(instructions=v["question"], criteria=v["levels"])
        else:
            qs[k] = Noul(instructions=v["question"])
    try:
        with TypeSafeClient() as client:
            r = client.system_one(
                state={KIND_STATE.get(kind, "document"): text}, questions=qs)
    except Exception as e:  # network, auth, rate limit
        return None, f"{type(e).__name__}: {str(e)[:120]}"

    findings = []
    for key, v in checks.items():
        if v["choices"]:
            picked = r.choices[key].choice
            if picked not in v["flag"]:
                continue
            findings.append({
                "type": "semantic",
                "rule": key.replace("_", "-"),
                "diagnosis": picked,
                "severity": v["severity"],
                "suggestion": v["suggestion"],
            })
            continue
        if v["levels"]:
            lvl = r.scores[key].score
            if v["max_level"] is not None and lvl <= v["max_level"]:
                findings.append({
                    "type": "semantic",
                    "rule": key.replace("_", "-"),
                    "level": round(lvl, 2),
                    "severity": v["severity"],
                    "suggestion": v["suggestion"],
                })
            continue
        p = r.nouls[key].noul
        if p >= v["threshold"]:
            findings.append({
                "type": "semantic",
                "rule": key.replace("_", "-"),
                "probability": round(p, 3),
                "severity": v["severity"],
                "suggestion": v["suggestion"],
            })
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], -f.get("probability", 1.0)))
    return findings, None


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--file")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--kind", default="doc", choices=sorted(KIND_STATE))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the unavailable-backend notice")
    a = ap.parse_args()

    if a.file:
        try:
            text = open(a.file, encoding="utf-8", errors="replace").read()
        except OSError:
            sys.exit(0)
    elif a.stdin:
        text = sys.stdin.read()
    else:
        ap.print_help()
        sys.exit(0)

    # Skip artifacts that are not authored prose. A revert or a merge carries
    # boilerplate plus a SHA and has no explanation to judge; grading it
    # produces findings nobody can act on. Measured: the one false positive on
    # the real-commit corpus was a two-line revert.
    if len(text.strip()) < 80:
        sys.exit(0)
    # The subject-line guard applies to commits ONLY. A commit's first line is a
    # subject, so a commit with no body carries nothing to judge. A document or
    # a web page has no subject line, and treating line 1 as one discarded the
    # whole text when it was a single paragraph.
    if a.kind == "commit":
        body = "\n".join(text.strip().splitlines()[1:]).strip()
        if len(body.split()) < 20:
            sys.exit(0)
    low = text.lower()
    if low.startswith(("revert ", "merge ")) or "this reverts commit" in low:
        sys.exit(0)

    findings, err = run(text, a.kind)
    if err is not None:
        if not a.quiet:
            print(f"jevgate: unavailable ({err}); skipping semantic layer",
                  file=sys.stderr)
        sys.exit(0)

    if a.json:
        print(json.dumps({"findings": findings}, indent=2))
    elif findings:
        for f in findings:
            print(f"  [{f['severity']}] {f['rule']} (p={f['probability']}) "
                  f"-> {f['suggestion']}")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
