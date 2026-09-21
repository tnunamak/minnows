#!/usr/bin/env python3
"""Audit our corpus labels against Pangram. Budget: 20 billable units, once.

WHY THIS EXISTS. Every precision number slopgate reports rests on an assumption
nobody checked: that corpus/human and corpus/authorities are human-written and
corpus/machine is machine-written. Git authorship and a pre-2023 cutoff make the
human side very likely, but "very likely" is not measured. If some of the human
corpus actually reads as machine-written, every threshold calibrated against it
is built on sand.

Pangram is the strongest available independent check. Its published FPR on clean
benchmarks is 0.0041%, and independent academic evaluation found it "the only AI
detector to match the performance of the majority vote among human evaluators."
It is not ground truth -- the same independent work measured 2% FPR on harder
sets, and journalists have documented real-world false positives -- but it is a
far better oracle than our own assumption.

BUDGET DISCIPLINE. Billing is per started 100-word block per item, minimum one
unit, NOT per API call. So the budget is ~2,000 words total, and short documents
are the efficient unit. This script:

  - refuses to run if the selection exceeds the unit budget
  - requires --confirm to spend anything
  - caches the full response to disk on first success, and refuses to re-spend
    if the cache exists
  - keeps the per-window detail, not just the verdict, so this never needs
    re-running for a different question

Usage:
  pangram_audit.py --plan              # show what would be sent, spend nothing
  pangram_audit.py --confirm           # actually spend the units
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / "pangram_audit_results.json"
BULK = "https://text.external-api.pangram.com/bulk"
UNIT_WORDS = 100      # Pangram 4 block size
BUDGET_UNITS = 20


def units(text):
    w = len(text.split())
    return max(1, -(-w // UNIT_WORDS))


def select():
    """Pick the shortest texts per class so the budget covers the most documents.

    Three classes, because they answer different questions:
      human       real Django maintainer commits (label: git authorship)
      authorities antirez / Tim Pope / git maintainers (label: authorship + pre-2023)
      machine     local-model drafts (label: we generated them)
    """
    out = []
    for label, d in (("human", "corpus/human"),
                     ("authority", "corpus/authorities"),
                     ("machine", "corpus/machine")):
        files = sorted((HERE / d).glob("*.txt"),
                       key=lambda p: len(p.read_text().split()))
        for p in files:
            t = p.read_text().strip()
            if len(t.split()) < 40:      # too short to judge
                continue
            out.append({"label": label, "path": str(p.relative_to(HERE)),
                        "text": t, "units": units(t)})
    return out


def fit(cands, budget):
    """Greedy, class-balanced: round-robin cheapest-first across classes."""
    by = {}
    for c in cands:
        by.setdefault(c["label"], []).append(c)
    for v in by.values():
        v.sort(key=lambda c: c["units"])
    chosen, spent = [], 0
    while True:
        progressed = False
        for label in ("machine", "authority", "human"):
            q = by.get(label) or []
            if not q:
                continue
            c = q[0]
            if spent + c["units"] > budget:
                continue
            chosen.append(q.pop(0))
            spent += c["units"]
            progressed = True
        if not progressed:
            break
    return chosen, spent


def call(items, key):
    payload = {"items": [{"text": c["text"]} for c in items]}
    body = HERE / ".pangram_req.json"
    body.write_text(json.dumps(payload))
    try:
        r = subprocess.run(
            ["curl", "-sS", "-m", "600", BULK,
             "-H", "Content-Type: application/json",
             "-H", f"x-api-key: {key}",
             "-d", f"@{body}"],
            capture_output=True, text=True, timeout=660)
        return r.stdout, r.stderr
    finally:
        body.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--budget", type=int, default=BUDGET_UNITS)
    a = ap.parse_args()

    if CACHE.exists() and a.confirm:
        print(f"REFUSING: {CACHE.name} already exists. The budget is spent and "
              f"the results are cached. Delete it deliberately to re-spend.")
        return 2

    chosen, spent = fit(select(), a.budget)
    from collections import Counter
    print(f"selection: {len(chosen)} documents, {spent}/{a.budget} billable units")
    print(f"per class: {dict(Counter(c['label'] for c in chosen))}")
    for c in chosen:
        print(f"  {c['units']}u  {c['label']:10} {c['path']}")

    if a.plan or not a.confirm:
        print("\nplan only; nothing spent. Re-run with --confirm to spend.")
        return 0

    key = os.environ.get("PANGRAM_API_KEY")
    if not key:
        print("PANGRAM_API_KEY not set", file=sys.stderr)
        return 1

    print(f"\nspending {spent} units...", flush=True)
    out, err = call(chosen, key)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print(f"unparseable response (NOT cached, budget may be spent):\n{out[:600]}\n{err[:300]}")
        (HERE / "pangram_audit_raw_error.txt").write_text(out + "\n---\n" + err)
        return 1

    CACHE.write_text(json.dumps(
        {"requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "units_spent": spent,
         "items": [{k: v for k, v in c.items() if k != "text"} for c in chosen],
         "response": data}, indent=2))
    print(f"cached full response to {CACHE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
