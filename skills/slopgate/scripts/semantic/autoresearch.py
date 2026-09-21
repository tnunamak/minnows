#!/usr/bin/env python3
"""Discover new questions automatically, keeping only those that measurably help.

The rule bank was written by hand from the owner's corrections and from the
writing-craft corpus. That is a good start and a poor stopping point: nobody
knows in advance which questions separate expert writing from machine drafts,
and the one time this was measured, three hand-written rules turned out to point
the wrong way.

TypeSafe's autoresearch cookbook is the method. An LLM proposes questions, Jev
answers them across the corpus, a model fits on the resulting columns, and a
question survives only if held-out error falls. On 2,000 wine reviews that took
RMSE from 1.87 to 1.77 over five rounds.

The discipline that makes it work, and that is easy to skip:

  - A proposed question is KEPT only if leave-one-out accuracy improves. Not if
    it sounds insightful, not if it fires often. Firing frequency already proved
    misleading here once.
  - A question whose column is flat carries no information and is dropped before
    it can add noise.
  - The baseline is re-measured each round, so a question that helped in round
    one can be dropped in round three when something better subsumes it.

Usage:
  autoresearch.py --rounds 3 [--propose 8]
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fit  # noqa: E402
from jevgate import KIND_STATE  # noqa: E402

GATEWAY = "https://ai.vivid.fish/v1/chat/completions"
MODEL = "current-fast"
MATRIX = HERE / "feature_matrix.json"
DISCOVERED = HERE / "discovered_rules.json"

PROPOSE = """You are helping find questions that separate writing by expert software
engineers from writing produced by a language model.

Questions that ALREADY work, with how strongly each separates the two classes
(positive means the property is more common in expert human writing):

{known}

Questions that were tried and DID NOT help, do not propose these again:
{rejected}

Propose {n} NEW yes/no questions about a piece of technical writing that might
separate the two classes and that are NOT already covered above.

Rules for a good question:
- It must be an exact observable condition, answerable the same way by any
  careful reader. "Is the prose good?" is useless; "does the text state a
  condition under which the failure occurs?" is usable.
- It must be answerable from the text alone, with no outside knowledge.
- Prefer properties of structure, evidence, and what the writer chose to
  include or leave out. Surface style has already been tested and carries little.
- Do not ask about length, formatting, or markdown.

Output ONLY a JSON array of objects, each {{"name": "snake_case_name",
"question": "the question text"}}. No prose."""


def llm(prompt, max_tokens=900, timeout=300):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "temperature": 0.7}
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
        return json.loads(r.stdout)["choices"][0]["message"]["content"].strip()
    finally:
        body.unlink(missing_ok=True)


def score_corpus(questions):
    """Answer `questions` for every document already in the matrix."""
    from typesafe_sdk import Noul, TypeSafeClient
    data = json.loads(MATRIX.read_text())
    srcdirs = {"corpus/authorities": "commit", "corpus/mined": "pr",
               "corpus/machine": "commit", "loop_revisions": "pr"}
    qs = {q["name"]: Noul(instructions=q["question"]) for q in questions}
    for key, row in data.items():
        if all(q["name"] in row["features"] for q in questions):
            continue
        d, stem = key.rsplit("/", 1)
        f = HERE / d / f"{stem}.txt"
        if not f.exists():
            continue
        kind = srcdirs.get(d, "doc")
        try:
            with TypeSafeClient() as c:
                r = c.system_one(
                    state={KIND_STATE.get(kind, "document"): f.read_text().strip()},
                    questions=qs)
            for q in questions:
                row["features"][q["name"]] = float(r.nouls[q["name"]].noul)
        except Exception as e:
            print(f"    skip {stem}: {type(e).__name__}", flush=True)
            for q in questions:
                row["features"].setdefault(q["name"], 0.5)
    MATRIX.write_text(json.dumps(data, indent=2))


def loo_accuracy(drop=()):
    X, y, cols, _ = fit.load()
    keep = [i for i, c in enumerate(cols) if c not in drop]
    X = [[r[i] for i in keep] for r in X]
    Xs, _, _ = fit.standardize(X)
    correct = 0
    for i in range(len(Xs)):
        w, b = fit.train(Xs[:i] + Xs[i + 1:], y[:i] + y[i + 1:])
        correct += (fit.predict(w, b, Xs[i]) >= 0.5) == y[i]
    return correct / len(y)


def flat(name):
    data = json.loads(MATRIX.read_text())
    vals = [v["features"].get(name) for v in data.values()]
    vals = [v for v in vals if v is not None]
    return (max(vals) - min(vals)) < 0.15 if vals else True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--propose", type=int, default=8)
    a = ap.parse_args()

    state = json.loads(DISCOVERED.read_text()) if DISCOVERED.exists() else \
        {"kept": [], "rejected": []}
    base = loo_accuracy()
    print(f"baseline leave-one-out accuracy: {base:.1%}\n")

    for rnd in range(1, a.rounds + 1):
        X, y, cols, _ = fit.load()
        Xs, _, _ = fit.standardize(X)
        w, b = fit.train(Xs, y)
        known = "\n".join(
            f"  {wt:+.2f}  {c}" for c, wt in
            sorted(zip(cols, w), key=lambda t: -abs(t[1]))[:12])
        rejected = "\n".join(f"  {r}" for r in state["rejected"][-15:]) or "  (none yet)"

        print(f"=== round {rnd}: proposing {a.propose} questions ===", flush=True)
        raw = llm(PROPOSE.format(known=known, rejected=rejected, n=a.propose))
        try:
            proposed = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
        except Exception:
            print(f"  unparseable proposal, skipping round: {raw[:120]}")
            continue
        proposed = [q for q in proposed
                    if q.get("name") and q["name"] not in cols
                    and q["name"] not in state["rejected"]]
        if not proposed:
            print("  nothing new proposed")
            continue

        print(f"  scoring {len(proposed)} questions across the corpus...", flush=True)
        score_corpus(proposed)

        for q in proposed:
            name = q["name"]
            if flat(name):
                state["rejected"].append(name)
                print(f"  DROP {name}: column is flat", flush=True)
                continue
            acc = loo_accuracy()
            without = loo_accuracy(drop=(name,))
            if acc > without + 1e-9:
                state["kept"].append(q)
                base = acc
                print(f"  KEEP {name}: {without:.1%} -> {acc:.1%}", flush=True)
            else:
                state["rejected"].append(name)
                print(f"  drop {name}: no improvement ({acc:.1%})", flush=True)
        DISCOVERED.write_text(json.dumps(state, indent=2))

    print(f"\nfinal accuracy {loo_accuracy():.1%}; "
          f"{len(state['kept'])} discovered questions kept")


if __name__ == "__main__":
    main()
