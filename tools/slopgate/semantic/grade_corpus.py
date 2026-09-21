#!/usr/bin/env python3
"""Build a GRADED quality target, because the binary one has hit its ceiling.

The model currently predicts authorship: written by a human expert, or generated.
The learning curve flattens around 55 documents, so more of the same labels will
not help. That is not surprising, because authorship is not the thing anyone
wants to know. A mediocre human PR body is label 1 and a polished machine draft
is label 0, so the model is learning a proxy that merely correlates with quality.

The wine study did not do this. It predicted critic scores on an 80-100 scale,
and a graded target carries far more information per example than a binary one,
which is why it kept improving across rounds.

There is no critic here, so this derives a grade the way chess derives ratings:
sample pairs, ask which is better, and fit a Bradley-Terry strength per document.
A few hundred comparisons over ~90 documents gives each one a continuous score,
and the comparisons are cheap because the local gateway is free.

Two safeguards:
  - each pair is judged in BOTH orders and counted only when the two agree, which
    discards position bias rather than averaging it in
  - pairs are sampled across the whole corpus, so a document is compared against
    both stronger and weaker neighbours
"""
import itertools
import json
import math
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GATEWAY = "https://ai.vivid.fish/v1/chat/completions"
MODEL = "current-fast"
OUT = HERE / "graded_labels.json"
PAIRS = HERE / "pairwise_judgments.json"

PROMPT = """Two pieces of technical writing, A and B.

Which is better? Judge on: does it state a concrete problem, explain the mechanism
causally, give the reader what they need to act, and avoid saying anything it
cannot support. Ignore length, formatting and subject matter.

--- A ---
{a}

--- B ---
{b}

Answer ONLY: BETTER: A   or   BETTER: B"""


def llm(prompt, timeout=180):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 16, "temperature": 0.0}
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as fh:
        fh.write(json.dumps(payload))
        body = pathlib.Path(fh.name)
    try:
        r = subprocess.run(
            ["curl", "-sS", "-m", str(timeout), GATEWAY,
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {os.environ.get('OPENAI_API_KEY','')}",
             "-d", f"@{body}"],
            capture_output=True, text=True, timeout=timeout + 20)
        return json.loads(r.stdout)["choices"][0]["message"]["content"].strip()
    finally:
        body.unlink(missing_ok=True)


def clip(s, n=4000):
    return s if len(s) <= n else s[:n] + "\n[...]"


def docs():
    m = json.loads((HERE / "feature_matrix.json").read_text())
    out = {}
    for key in m:
        d, stem = key.rsplit("/", 1)
        f = HERE / d / f"{stem}.txt"
        if f.exists():
            out[key] = f.read_text().strip()
    return out


def judge_pair(a, b):
    """Both orders; None unless they agree."""
    v1 = llm(PROMPT.format(a=clip(a), b=clip(b)))
    v2 = llm(PROMPT.format(a=clip(b), b=clip(a)))
    m1 = re.search(r"BETTER:\s*([AB])", v1, re.I)
    m2 = re.search(r"BETTER:\s*([AB])", v2, re.I)
    if not m1 or not m2:
        return None
    first_wins = m1.group(1).upper() == "A"
    second_run_first_wins = m2.group(1).upper() == "B"
    if first_wins != second_run_first_wins:
        return None                      # position bias; discard
    return first_wins


def bradley_terry(keys, results, iters=300, lr=0.1):
    """Fit a strength per document from pairwise wins."""
    s = {k: 0.0 for k in keys}
    for _ in range(iters):
        g = {k: 0.0 for k in keys}
        for a, b, a_won in results:
            p = 1 / (1 + math.exp(-(s[a] - s[b])))
            e = (1.0 if a_won else 0.0) - p
            g[a] += e
            g[b] -= e
        for k in keys:
            s[k] += lr * g[k] / max(1, len(results))
    return s


def main():
    n_pairs = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    texts = docs()
    keys = sorted(texts)
    rng = random.Random(1234)
    done = json.loads(PAIRS.read_text()) if PAIRS.exists() else []
    seen = {(r[0], r[1]) for r in done}

    all_pairs = [p for p in itertools.combinations(keys, 2)
                 if (p[0], p[1]) not in seen]
    rng.shuffle(all_pairs)
    for a, b in all_pairs[:n_pairs]:
        try:
            res = judge_pair(texts[a], texts[b])
        except Exception:
            continue
        if res is None:
            continue
        done.append([a, b, res])
        if len(done) % 20 == 0:
            PAIRS.write_text(json.dumps(done))
            print(f"  {len(done)} agreed comparisons", flush=True)
    PAIRS.write_text(json.dumps(done))

    s = bradley_terry(keys, done)
    lo, hi = min(s.values()), max(s.values())
    rng_ = (hi - lo) or 1.0
    graded = {k: (v - lo) / rng_ for k, v in s.items()}
    OUT.write_text(json.dumps(graded, indent=2))
    print(f"\n{len(done)} usable comparisons -> graded score for {len(graded)} docs")
    top = sorted(graded.items(), key=lambda t: -t[1])[:5]
    bot = sorted(graded.items(), key=lambda t: t[1])[:5]
    print("\nhighest graded:")
    for k, v in top:
        print(f"  {v:.2f}  {k}")
    print("lowest graded:")
    for k, v in bot:
        print(f"  {v:.2f}  {k}")


if __name__ == "__main__":
    main()
