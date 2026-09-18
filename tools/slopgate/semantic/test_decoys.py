#!/usr/bin/env python3
"""Adversarial decoy test for jevgate.

Design follows the gap named in
ai/research/writing-craft/slopkit-benchmark-honesty-is-real-but-localized-*.md:
a decoy corpus that is MECHANICALLY CLEAN (slopgate's deterministic layers find
nothing) but still wrong, asserting the semantic layer rejects it "for the
SPECIFIC named reason ... not merely with a lower overall impression."

Why this exists rather than calibrating on generated text: calibrating against a
single local model dropped 10 of 14 rules for "no separation", including
`unearned_benefit` and `guarantee_language`. Those rules then scored 0.88 and
0.68 on obvious slop. The local model simply does not write that way, so its
output is the wrong yardstick — the slop worth catching is the kind a frontier
model produces under light steering. Decoys pin the rules to the defect itself
rather than to one generator's habits.

Each decoy declares `must_flag`: the rule that must fire. Entries with
`must_flag: null` are clean controls that must produce NO finding.

Reports per-rule recall on decoys and the false-positive rate on controls.
Exit 1 if any control is flagged (precision failure) — that is the failure mode
slopgate's own header calls worse than a miss.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from jevgate import CHECKS, KIND_STATE  # noqa: E402

HERE = pathlib.Path(__file__).parent


def score(text, kind):
    from typesafe_sdk import Noul, TypeSafeClient
    qs = {k: Noul(instructions=v[0]) for k, v in CHECKS.items()}
    with TypeSafeClient() as client:
        r = client.system_one(
            state={KIND_STATE.get(kind, "document"): text}, questions=qs)
    return {k: r.nouls[k].noul for k in CHECKS}


def main():
    cache = HERE / "decoy_scores.json"
    rows = [json.loads(l) for l in (HERE / "decoys.jsonl").read_text().splitlines() if l.strip()]
    if cache.exists():
        scores = json.loads(cache.read_text())
    else:
        scores = {}
        for r in rows:
            scores[r["id"]] = score(r["text"], r["kind"])
            print(f"  scored {r['id']}", file=sys.stderr, flush=True)
        cache.write_text(json.dumps(scores, indent=2))

    decoys = [r for r in rows if r["must_flag"]]
    controls = [r for r in rows if not r["must_flag"]]

    # Threshold per rule: must clear every control, with margin.
    print(f"{'rule':26} {'ctrl_max':>9} {'thresh':>7}  decoy hits")
    thresholds, recall = {}, {}
    for k in CHECKS:
        ctrl_max = max((scores[c["id"]][k] for c in controls), default=0.0)
        t = round(min(ctrl_max + 0.05, 0.95), 2)
        thresholds[k] = t
        mine = [d for d in decoys if d["must_flag"] == k]
        hit = [d for d in mine if scores[d["id"]][k] >= t]
        recall[k] = (len(hit), len(mine))
        if mine:
            marks = " ".join(
                f"{d['id'].rsplit('-',1)[-1]}={scores[d['id']][k]:.2f}" for d in mine)
            ok = "OK" if len(hit) == len(mine) else f"MISS {len(mine)-len(hit)}"
            print(f"{k:26} {ctrl_max:9.2f} {t:7.2f}  {marks}  [{ok}]")
        else:
            print(f"{k:26} {ctrl_max:9.2f} {t:7.2f}  (no decoy)")

    # Precision: any control that trips any rule is a false positive.
    fps = []
    for c in controls:
        fired = [k for k in CHECKS if scores[c["id"]][k] >= thresholds[k]]
        if fired:
            fps.append((c["id"], fired))
    caught = sum(1 for d in decoys if scores[d["id"]][d["must_flag"]] >= thresholds[d["must_flag"]])

    print(f"\ndecoys caught for the named reason: {caught}/{len(decoys)}")
    print(f"clean controls flagged (false positives): {len(fps)}/{len(controls)}")
    for i, f in fps:
        print(f"  FP {i}: {f}")

    print("\nTHRESHOLDS = {")
    for k in sorted(thresholds):
        print(f'    "{k}": {thresholds[k]},')
    print("}")

    sys.exit(1 if fps else 0)


if __name__ == "__main__":
    main()
