#!/usr/bin/env python3
"""Which rule GROUPS actually cause the writing to improve?

Firing frequency measures volume, not value. A rule that fires on every draft
may be contributing nothing to whether the revision is better, and a rule that
fires rarely may be carrying the whole result when it does.

So: run the same revise-and-blind-judge loop with only one group of rules
active at a time, and compare win rates against the full bank. The group whose
absence costs the most is where the leverage is.

Groups, chosen to test the open question directly -- is the leverage in
structural rules (what the detection literature says is the strongest signal)
or in sentence-level rules?

  structure  buried lede, empty section, context before point, document shape,
             scaffolding headings, uniform section shape, length exceeds change
  substance  grounding, unsourced assertion, benefit without mechanism,
             guarantee language, unearned benefit, scope overclaim, no tradeoff
  register   editorial tone, negative framing, copula avoidance, tricolon,
             abstract subject, hedged behavior, false ease
  audience   teaches project, glosses own terms, defines external standard,
             explains the obvious, necessary jargon carveout
  economy    repeats itself, overexplains, summary closer, restates without
             reason, process narration, gate status evidence

Usage: ablate.py [seed]
"""
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
RULES = HERE / "rules.json"

GROUPS = {
    "structure": ["buried_lede", "empty_section", "context_before_point",
                  "document_shape", "scaffolding_headings",
                  "uniform_section_shape", "length_exceeds_change"],
    "substance": ["grounding", "unsourced_assertion", "benefit_without_mechanism",
                  "guarantee_language", "unearned_benefit", "scope_overclaim",
                  "no_tradeoff"],
    "register":  ["editorial_tone", "negative_framing", "copula_avoidance",
                  "tricolon", "abstract_subject", "hedged_behavior", "false_ease"],
    "audience":  ["teaches_the_project", "glosses_own_terms",
                  "defines_external_standard", "explains_the_obvious",
                  "necessary_jargon_carveout"],
    "economy":   ["repeats_itself", "overexplains", "summary_closer",
                  "restates_without_reason", "process_narration",
                  "gate_status_evidence"],
}


def subset(keep):
    """Write a rules file containing only `keep`, return the path."""
    d = json.loads(RULES.read_text())
    d["rules"] = {k: v for k, v in d["rules"].items() if k in keep}
    p = HERE / f".ablate_rules.json"
    p.write_text(json.dumps(d, indent=2))
    return p


def run(seed, rules_path=None):
    env = dict(os.environ, LOOP_SEED=str(seed))
    if rules_path:
        env["SLOPGATE_RULES"] = str(rules_path)
    r = subprocess.run([sys.executable, "loop_test.py", "doc"],
                       capture_output=True, text=True, cwd=str(HERE),
                       env=env, timeout=5400)
    out = r.stdout
    win = tot = 0
    for line in out.splitlines():
        if "judge prefers the revision" in line:
            win = int(line.split(":")[1].split("/")[0])
            tot = int(line.split("/")[1])
    return win, tot


def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else "424242"
    results = {}
    print(f"seed {seed}\n")
    for name, keys in GROUPS.items():
        p = subset(keys)
        w, t = run(seed, p)
        results[name] = (w, t)
        pct = f"{w/t:.0%}" if t else "n/a"
        print(f"  {name:11} {w}/{t}  {pct}", flush=True)
        (HERE / "ablation_results.json").write_text(json.dumps(results, indent=2))
    (HERE / ".ablate_rules.json").unlink(missing_ok=True)
    print("\nThe group with the highest win rate ALONE is carrying the result.")


if __name__ == "__main__":
    main()
