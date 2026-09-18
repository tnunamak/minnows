#!/usr/bin/env python3
"""Calibrate jevgate thresholds from labelled examples.

Hand-set thresholds produced false positives on real Django commits: three of
three fired `repeated-openers` at a 0.70 bar when human text reaches 0.39, and
two rules (`abstract_subject`, `no_tradeoff`) turned out to have no separation at
all and were firing as pure noise.

This sets each threshold from data instead, on one rule:

    threshold = max(observed human value) + margin

so a question only fires above anything genuine human writing produced, and any
question whose human maximum sits at or above its machine mean is REJECTED as
non-discriminating rather than given a high bar.

Precision over recall, deliberately, matching slopgate's stated contract: "A
false positive here trains the owner to ignore or disable it, which is worse
than missing a real slop instance."

Usage:
  calibrate.py --human <dir> --ai <dir> [--margin 0.05] [--emit]

--emit prints a THRESHOLDS dict ready to paste into jevgate.py.
"""
import argparse
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from jevgate import CHECKS  # noqa: E402


def score_dir(d, kind):
    from typesafe_sdk import Noul, TypeSafeClient
    qs = {k: Noul(instructions=v[0]) for k, v in CHECKS.items()}
    out = {}
    for f in sorted(pathlib.Path(d).glob("*.*")):
        t = f.read_text(encoding="utf-8", errors="replace").strip()
        if len(t) < 80:
            continue
        with TypeSafeClient() as client:
            r = client.system_one(state={kind: t}, questions=qs)
        out[f.stem] = {k: r.nouls[k].noul for k in CHECKS}
        print(f"  scored {f.stem}", file=sys.stderr, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--human", required=True)
    ap.add_argument("--ai", required=True)
    ap.add_argument("--kind", default="commit_message")
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--cache", default="calibration.json")
    a = ap.parse_args()

    cache = pathlib.Path(__file__).parent / a.cache
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        data = {"human": score_dir(a.human, a.kind),
                "ai": score_dir(a.ai, a.kind)}
        cache.write_text(json.dumps(data, indent=2))

    nh, na = len(data["human"]), len(data["ai"])
    print(f"\ncalibrating on {nh} human / {na} machine documents\n")
    print(f"{'rule':24} {'h_max':>6} {'h_mean':>7} {'ai_mean':>8} "
          f"{'sep':>7}  {'threshold':>9}  verdict")

    keep = {}
    for k in CHECKS:
        hv = [v[k] for v in data["human"].values()]
        av = [v[k] for v in data["ai"].values()]
        h_max, h_mean, a_mean = max(hv), statistics.mean(hv), statistics.mean(av)
        sep = a_mean - h_mean
        thresh = round(min(h_max + a.margin, 0.97), 2)
        # Reject if the question does not separate, or if firing above the human
        # ceiling would also miss most machine text (no usable operating point).
        fires_on_ai = sum(1 for x in av if x >= thresh)
        if sep < 0.15:
            verdict = "DROP (no separation)"
        elif fires_on_ai == 0:
            verdict = "DROP (no headroom)"
        else:
            verdict = f"keep, catches {fires_on_ai}/{na}"
            keep[k] = thresh
        print(f"{k:24} {h_max:6.2f} {h_mean:7.2f} {a_mean:8.2f} "
              f"{sep:+7.3f}  {thresh:9.2f}  {verdict}")

    fp = sum(1 for v in data["human"].values()
             if any(v[k] >= t for k, t in keep.items()))
    tp = sum(1 for v in data["ai"].values()
             if any(v[k] >= t for k, t in keep.items()))
    print(f"\nkept {len(keep)}/{len(CHECKS)} rules")
    print(f"documents with >=1 finding: human {fp}/{nh} (false positives), "
          f"machine {tp}/{na} (caught)")

    if a.emit:
        print("\nTHRESHOLDS = {")
        for k, t in sorted(keep.items()):
            print(f'    "{k}": {t},')
        print("}")
        dropped = [k for k in CHECKS if k not in keep]
        if dropped:
            print(f"# dropped as non-discriminating: {', '.join(dropped)}")


if __name__ == "__main__":
    main()
