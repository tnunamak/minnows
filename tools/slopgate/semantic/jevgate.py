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
CHECKS = {
    # --- added 2026-09-18 after labelling a real PR (PDP-Connect/pdpp#315,
    # 3,591 words) as slop. The existing bank barely fired on it: its defect is
    # not an unearned benefit claim but relentless tutorial exposition and
    # length far beyond what the change warrants. These target that directly.
    # --- doc-kind rules, derived from Tim's own repeated corrections in agent
    # sessions rather than from my taste. The recurring words are "neutral
    # tone", "don't repeat yourself", "concise", "editorial", "executive",
    # "facts", "citations", "positive statements". Verbatim instances:
    #   "speak plainly without repeating yourself in a neutral tone only on the
    #    key information"
    #   "Can we be more direct and avoid using negative statements and only use
    #    positive statements ... and also use a neutral tone and also never
    #    repeat ourselves?"
    #   "A TEE or ZK proof would add little value. this is a negative statement,
    #    can we avoid negative statements? what would it steer us toward?"
    #   "write up the facts about the proposed architecture (with nothing more:
    #    neutral tone, don't repeat yourself, avoid ...)"
    "repeats_itself": (
        "Does this text state the same point more than once in different words, "
        "for example making a claim and then restating it later as a summary or "
        "a recap?",
        0.60, "high",
        "say it once; delete the restatement",
    ),
    "editorial_tone": (
        "Does this text tell the reader how to feel about the subject, using "
        "words that praise, sell, or dramatize, such as powerful, exciting, "
        "critical, revolutionary, seamless, or significant?",
        0.55, "high",
        "neutral tone: state the fact and let the reader judge",
    ),
    "negative_framing": (
        "Does this text define something by what it is NOT, what it lacks, or "
        "what would add little value, rather than stating positively what it is "
        "or what it does?",
        0.55, "medium",
        "state it positively: say what it is and what it steers toward",
    ),
    "unsourced_assertion": (
        "Does this text make factual claims about external systems, standards, "
        "or other parties without citing where those facts come from?",
        0.60, "medium",
        "cite the source, or mark the claim as unverified",
    ),
    "overexplains": (
        "Does this text explain more than a reader needs to follow the point, "
        "restating context the reader already has or walking through reasoning "
        "that could be stated as a conclusion?",
        0.60, "medium",
        "give the conclusion; cut the walk-through",
    ),
    "teaches_the_project": (
        "Does this text explain what this project, protocol, or repository IS, "
        "or what its main artifacts are for, as background rather than as part "
        "of describing the specific change?",
        0.87, "high",
        "the reader works here; delete the orientation and start at the defect",
    ),
    "defines_external_standard": (
        "Does this text explain what a published external standard, RFC, or "
        "specification says or means, beyond simply citing it?",
        0.18, "high",
        "cite it and move on; the reader can open the RFC",
    ),
    "glosses_own_terms": (
        "Does this text stop to define its own domain terms mid-sentence, for "
        "example with a dash or parenthesis explaining what a named component is?",
        0.9, "high",
        "use the repo's terms unglossed; a reader who works here knows them",
    ),
    "length_exceeds_change": (
        "Is this text far longer than the change it describes needs, spending "
        "many paragraphs where a few would carry the same decision?",
        0.64, "high",
        "cut to the defect, the fix, and what is unverified",
    ),
    "unearned_benefit": (
        "Does this text claim a benefit such as improved robustness, better "
        "maintainability, a clearer interface, easier customization, or improved "
        "performance, WITHOUT stating the concrete change that produces it?",
        0.64, "high",
        "name the mechanism, or cut the claim",
    ),
    "guarantee_language": (
        "Does this text guarantee an outcome in absolute terms, for example that "
        "something is now correct, safe, robust, or can no longer fail?",
        0.26, "high",
        "state what the change does; drop the guarantee",
    ),
    "restates_without_reason": (
        "Does this text describe the edit that was made without saying what "
        "situation made the edit necessary?",
        0.81, "high",
        "open with the problem: what went wrong, under what condition",
    ),
    "summary_closer": (
        "Does the final sentence restate what the text already said, rather than "
        "adding new information?",
        0.48, "medium",
        "delete the closing restatement",
    ),
    "explains_the_obvious": (
        "Does this text explain what a widely known construct or component does, "
        "in a way a maintainer of this codebase would not need?",
        0.39, "medium",
        "assume the reader works here; cut the tutorial sentence",
    ),
    "process_narration": (
        "Does this text describe the authoring or verification process, such as "
        "which tests were run, which review rounds happened, or which tools were "
        "used, rather than describing the change itself?",
        0.67, "high",
        "describe the change, not how you arrived at it",
    ),
    "gate_status_evidence": (
        "Does this text report a bare pass or fail status, such as tests passing, "
        "a suite being green, or a count of passing checks, without saying what "
        "situation the test actually covers?",
        0.19, "medium",
        "say what the test proves about a concrete scenario, or cut it",
    ),
    "hedged_behavior": (
        "Does this text describe what the code does using may, might, could, or "
        "is intended to, where it could state plainly what the code does?",
        0.91, "medium",
        "state the behavior plainly, or say explicitly that it is unverified",
    ),
    "abstract_subject": (
        "Do most sentences in this text take an abstract subject such as this "
        "change, the implementation, the system, or the solution, rather than a "
        "concrete code entity or an actor?",
        0.81, "low",
        "make the code entity the subject of the sentence",
    ),
    "tricolon": (
        "Does this text contain a list of exactly three parallel items used for "
        "rhetorical effect rather than because there are exactly three things?",
        0.43, "medium",
        "keep the items that carry information",
    ),
    "copula_avoidance": (
        "Does this text use verbs such as serves as, functions as, represents, "
        "provides, enables, or ensures where a plain is or are would say the same "
        "thing?",
        0.81, "low",
        "use is or are",
    ),
    "no_tradeoff": (
        "Is this text free of any acknowledgement that the approach has a cost, a "
        "limitation, or something left unverified?",
        0.88, "low",
        "if something is untested or traded away, say so",
    ),
}

KIND_STATE = {
    "commit": "commit_message",
    "pr": "pr_description",
    "doc": "document",
}


def run(text, kind="doc"):
    try:
        from typesafe_sdk import Noul, TypeSafeClient
    except ImportError:
        return None, "typesafe_sdk not installed"
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None, "TYPESAFE_API_KEY not set"
    qs = {k: Noul(instructions=v[0]) for k, v in CHECKS.items()}
    try:
        with TypeSafeClient() as client:
            r = client.system_one(
                state={KIND_STATE.get(kind, "document"): text}, questions=qs)
    except Exception as e:  # network, auth, rate limit
        return None, f"{type(e).__name__}: {str(e)[:120]}"

    findings = []
    for key, (_q, thresh, sev, fix) in CHECKS.items():
        p = r.nouls[key].noul
        if p >= thresh:
            findings.append({
                "type": "semantic",
                "rule": key.replace("_", "-"),
                "probability": round(p, 3),
                "severity": sev,
                "suggestion": fix,
            })
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], -f["probability"]))
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
    body = "\n".join(text.strip().splitlines()[1:]).strip()
    if len(text.strip()) < 80 or len(body.split()) < 20:
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
