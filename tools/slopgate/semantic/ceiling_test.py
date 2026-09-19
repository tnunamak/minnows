"""Is there a ceiling, or did I just stop iterating?

Claim to test: the loop asymptotes at 'clean' and cannot reach excellent.
Test: run the SAME draft through repeated revise rounds and see whether the
blind judge keeps preferring each new version over the previous one. If wins
decay to chance by round 3, there is a real ceiling. If they keep landing,
I stopped too early.
"""
import json, os, pathlib, re, subprocess, sys, tempfile
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import loop_test as LT

cases = ["pdpp-318", "synthetic-structure", "generic-saas"]
dirs = {"pdpp-318":"loop_corpus_pr","synthetic-structure":"loop_corpus","generic-saas":"loop_corpus_web"}
kinds = {"pdpp-318":"pr","synthetic-structure":"doc","generic-saas":"web"}

for case in cases:
    p = HERE / dirs[case] / f"{case}.txt"
    if not p.exists(): continue
    kind = kinds[case]
    cur = p.read_text().strip()
    print(f"\n=== {case} ({kind}) ===", flush=True)
    for rnd in range(1, 5):
        f = LT.check(cur, kind)
        high = [x for x in f if x.get("severity") in ("high","critical")]
        if len(high) < 2:
            print(f"  round {rnd}: {len(high)} high -> below floor, stop", flush=True)
            break
        problems = "\n".join(f"- {x['rule']}: {x.get('suggestion','')}" for x in f)
        nxt = LT.llm(LT.REVISE.format(text=cur, problems=problems),
                     max_tokens=max(1200, int(len(cur.split())*2.2)))
        v = LT.llm(LT.JUDGE.format(a=LT.clip(cur), b=LT.clip(nxt)), max_tokens=20)
        m = re.search(r"BETTER:\s*([AB])", v, re.I)
        better = "new" if (m and m.group(1).upper()=="B") else "old"
        print(f"  round {rnd}: {len(f)} findings ({len(high)} high) -> judge prefers {better}", flush=True)
        if better == "old":
            print("           improvement stopped", flush=True)
            break
        cur = nxt
