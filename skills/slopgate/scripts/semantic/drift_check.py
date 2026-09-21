#!/usr/bin/env python3
"""Do the revisions drift away from how good writing actually reads?

The loop's win rate is judged by an LLM. That establishes the revisions are
better by a model's reading, not that they are better by a measurable property
of the text. This is the independent, non-LLM check.

It measures sentence rhythm against a reference band computed from authority
prose (antirez, Tim Pope, the git maintainers): mean sentence length 20.8 words,
standard deviation 6.3, and 0.6 very short sentences per document.

This exact failure has happened here before. A "plain sentences, one idea each"
criterion once drove an issue body to 10.2 mean words per sentence with seven
fragments, against 27.4 and zero for the reference, while its composite score
IMPROVED and an LLM judge ranked the degraded text first. A model judge does not
reliably notice that kind of drift; a distribution does.

Passing means the loop is climbing a real hill. Failing means it is optimising
the rule bank at the expense of the prose, whatever the judge says.
"""
import json
import pathlib
import re
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Measured from corpus/authorities.
REF_MEAN, REF_SD, REF_FRAGS = 20.8, 6.3, 0.6


def stats(text):
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"^\s*[|>#].*$", " ", t, flags=re.M)
    t = re.sub(r"^\s*([-*+]|\d+[.)])\s.*$", " ", t, flags=re.M)
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", t) if len(x.split()) > 3]
    wl = [len(x.split()) for x in sents]
    if not wl:
        return None
    return {"n": len(wl), "mean": statistics.mean(wl),
            "frags": sum(1 for w in wl if w <= 6)}


def verdict(s):
    """How far outside the reference band, in standard deviations."""
    if not s:
        return None
    return abs(s["mean"] - REF_MEAN) / REF_SD


def main():
    rows = []
    for d in ("loop_corpus", "loop_corpus_pr", "loop_corpus_web"):
        for f in sorted((HERE / d).glob("*.txt")):
            rev = HERE / "loop_revisions" / f"{f.stem}.txt"
            if not rev.exists():
                continue
            sb, sa = stats(f.read_text()), stats(rev.read_text())
            if not sb or not sa:
                continue
            rows.append((f.stem, sb, sa, verdict(sb), verdict(sa)))

    print(f"reference band: mean {REF_MEAN} words/sentence (sd {REF_SD}), "
          f"{REF_FRAGS} fragments/doc\n")
    print(f"{'case':22} {'before':>12} {'after':>12}   drift")
    toward = away = 0
    for name, sb, sa, db, da in rows:
        arrow = "->" if da < db else ("==" if abs(da - db) < 0.05 else "<-")
        if da < db - 0.05:
            toward += 1
        elif da > db + 0.05:
            away += 1
        print(f"  {name:20} {sb['mean']:5.1f}w/{sb['frags']:<2}f "
              f"{sa['mean']:5.1f}w/{sa['frags']:<2}f  "
              f"{db:.1f}sd {arrow} {da:.1f}sd")
    n = len(rows)
    print(f"\nmoved TOWARD the reference band : {toward}/{n}")
    print(f"moved AWAY from it              : {away}/{n}")
    outside_after = sum(1 for *_, da in rows if da > 2)
    print(f"outside 2sd after revision      : {outside_after}/{n}")
    print("\nThis is the non-LLM check. If revisions drifted away from how good")
    print("writing actually reads, the loop is optimising the rules rather than")
    print("the prose, whatever the model judge said.")


if __name__ == "__main__":
    main()
