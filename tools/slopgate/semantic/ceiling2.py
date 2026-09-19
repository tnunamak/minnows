"""The real question: is the output that clears our floor actually GOOD?

The floor test showed the loop stops when it runs out of known defects, not when
the writing stops improving. So: take the outputs that cleared our floor and
blind-compare them against genuinely excellent human writing on the same axis.

If a judge prefers human writing overwhelmingly, there is a real quality gap our
rules do not see, and that gap is the distance from ~70 to ~95.
"""
import pathlib, re, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import loop_test as LT

# Our best outputs vs real authority writing. Same genre, both "clean" by our rules.
pairs = []
for rev in sorted((HERE/"loop_revisions").glob("*.txt"))[:6]:
    pairs.append(("ours", rev))
humans = sorted((HERE/"corpus/mined").glob("*.txt"))[:6]

CMP = """Two pieces of technical writing, A and B. Which was written by a skilled human
engineer explaining their own work, and which is weaker? Judge on insight, judgment about
what to include, and whether it reads like someone who understood the problem.

--- A ---
{a}

--- B ---
{b}

Answer ONLY: BETTER: A   or   BETTER: B"""

wins = 0; n = 0
for (_, ours), hum in zip(pairs, humans):
    a, b = ours.read_text().strip(), hum.read_text().strip()
    # ours = A on even, B on odd, to cancel position bias
    flip = n % 2 == 1
    x, y = (b, a) if flip else (a, b)
    v = LT.llm(CMP.format(a=LT.clip(x), b=LT.clip(y)), max_tokens=20)
    m = re.search(r"BETTER:\s*([AB])", v, re.I)
    if not m: continue
    picked_ours = ((m.group(1).upper() == "A") != flip)
    wins += picked_ours; n += 1
    print(f"  {ours.stem[:24]:26} vs {hum.stem[:20]:22} -> {'OURS' if picked_ours else 'human'}", flush=True)
print(f"\nour loop output preferred over real human writing: {wins}/{n}")
print("50% would mean indistinguishable. Far below means a real quality gap.")
