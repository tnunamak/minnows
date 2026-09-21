#!/usr/bin/env python3
"""Treat Jev's probabilities as a feature vector, not as 36 separate verdicts.

The linter framing throws away almost everything Jev returns. Thirty six
calibrated probabilities get thresholded into thirty six booleans, which is
roughly thirty six bits out of thirty six real numbers. The thresholds are also
set one rule at a time against a reference corpus, so nothing ever learns that
two rules firing together means something different from either alone.

TypeSafe's own autoresearch cookbook does the opposite and reports it working:
Jev answers a bank of questions over every row, the probabilities become numeric
columns, and a supervised model trains on them against a graded target. On 2,000
wine reviews a CatBoost regressor reached RMSE 1.77 against critic scores, and
the loop improved it by proposing new questions each round and keeping one only
when held-out error fell.

The label here is provenance rather than a critic score: writing by antirez, Tim
Pope, the git maintainers and pre-2023 human PR authors on one side, machine
drafts on the other. That is weaker than a graded score, but the blind
comparison already showed the gap it stands for is real -- our own "clean by the
rules" output lost to real human writing five times out of six.

This builds the matrix. fit.py trains on it.
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jevgate import KIND_STATE, load_rules  # noqa: E402

CACHE = HERE / "feature_matrix.json"

# label 1 = written by a human whose writing we want to approximate
SOURCES = [
    ("corpus/authorities", 1, "commit"),
    ("corpus/mined",       1, "pr"),
    ("corpus/machine",     0, "commit"),
    ("loop_revisions",     0, "pr"),
]


def score_doc(text, kind, rules):
    from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
    qs = {}
    for k, v in rules.items():
        if v["choices"]:
            qs[k] = Choice(instructions=v["question"],
                           criteria={c: None for c in v["choices"]})
        elif v["levels"]:
            qs[k] = Score(instructions=v["question"], criteria=v["levels"])
        else:
            qs[k] = Noul(instructions=v["question"])
    with TypeSafeClient() as c:
        r = c.system_one(state={KIND_STATE.get(kind, "document"): text},
                         questions=qs)
    out = {}
    for k, v in rules.items():
        if v["choices"]:
            # One column per option: a distribution carries more than the argmax.
            picked = r.choices[k].choice
            for opt in v["choices"]:
                out[f"{k}::{opt[:28]}"] = 1.0 if opt == picked else 0.0
        elif v["levels"]:
            out[k] = float(r.scores[k].score)
        else:
            out[k] = float(r.nouls[k].noul)
    return out


def main():
    rules = load_rules("doc")
    data = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for d, label, kind in SOURCES:
        for f in sorted((HERE / d).glob("*.txt")):
            key = f"{d}/{f.stem}"
            if key in data:
                continue
            t = f.read_text().strip()
            if len(t.split()) < 40:
                continue
            try:
                feats = score_doc(t, kind, rules)
            except Exception as e:
                print(f"  skip {key}: {type(e).__name__}", flush=True)
                continue
            data[key] = {"label": label, "kind": kind,
                         "words": len(t.split()), "features": feats}
            CACHE.write_text(json.dumps(data, indent=2))
            print(f"  {key}  label={label}", flush=True)
    n1 = sum(1 for v in data.values() if v["label"] == 1)
    n0 = len(data) - n1
    ncol = len(next(iter(data.values()))["features"]) if data else 0
    print(f"\n{len(data)} documents, {ncol} features, {n1} human / {n0} machine")


if __name__ == "__main__":
    main()
