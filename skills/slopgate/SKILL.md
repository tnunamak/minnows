---
name: slopgate
description: Measure and reduce AI slop in your own writing before you ship it. Use after drafting a commit message, PR description, issue, technical document, brief, or report — and whenever asked to write "without slop", in a "neutral tone", "concisely", "without repeating yourself", or to a high writing standard. Runs a free deterministic layer plus an optional semantic layer, returns named findings with a fix for each, and exits non-zero until the draft is clean. Also calibrates to a specific person's taste from examples.
---

# slopgate

Check your own draft, read the findings, rewrite, check again.

```bash
slopgate check --file DRAFT.md --kind doc
git log -1 --format=%B | slopgate check --stdin --kind commit
slopgate check --file body.md --kind pr --json
```

`--kind` is `commit`, `pr`, or `doc`. Exit 1 while findings remain, 0 when clean.
Findings come out severity-ordered, so fixing `high` first is the fast path.

```
  [high] editorial-tone (p=0.91) -> neutral tone: state the fact and let the reader judge
  [high] unearned-benefit (p=0.89) -> name the mechanism, or cut the claim
  [high] repeats-itself (p=0.81) -> say it once; delete the restatement
  [medium] gate-status-evidence (p=0.88) -> say what the test proves about a concrete scenario, or cut it
```

Each finding names a specific defect and what to do about it. Rewrite against
them rather than regenerating the draft: targeted fixes converge, full rewrites
reintroduce old problems at the rate they fix new ones.

## Two layers

**Deterministic** (free, no network, always runs): syntactic frames like
"not just X but Y", sentence-cadence statistics, leftover tool artifacts, and
corpus-relative unearned names. Use `--fast` to stop here.

**Semantic** (needs `TYPESAFE_API_KEY`): the defects with no syntactic
signature — claiming a benefit without naming the mechanism, describing the edit
without the reason that forced it, teaching the reader what they already know,
repeating yourself, editorial tone, absolute guarantees.

A missing key, a missing SDK, or a network failure is never an error. The tool
notes it on stderr and continues with whatever layer is available.

## Calibrating to a person's taste

Shipped thresholds are set so the tool stays silent on writing by antirez, Tim
Pope and the git maintainers. To fit someone else's bar, give it examples:

```bash
slopgate mine                                    # harvest exemplars automatically
slopgate calibrate --human good/ --ai bad/ --emit
```

`mine` harvests commit messages by named authorities on commit craft, using git
authorship and a pre-2023 cutoff so the positive class is clean by construction
rather than by anyone's judgement.

Two calibration traps, both measured:

- **Do not calibrate against one generator.** Scoring one weak model's drafts
  against human commits "proved" 10 of 14 rules had no separation, including the
  two that matter most. Those rules then scored 0.88 and 0.68 on obvious slop.
- **Do not apply a uniform threshold floor.** Clamping every rule to 0.30 took
  false positives on real commits from 0 to 9 of 14. The probabilities are not
  on a shared scale; each rule needs its own bar.

## What it deliberately does not check

Each omission is measured, not assumed (see `semantic/corpus/README.md`):

- **Vocabulary word-lists.** The standard 112-word AI-tell table has 0.9x lift —
  it fires slightly *less* on machine text than on human text.
- **Em dashes.** Lift is inverted at 0.2x; they fire more on human writing.
- **Passive voice.** Measured at half the human rate in machine text, so the
  folk direction is backwards.

Two rules were dropped for the same reason after testing against authority
writing: listing changes scored 0.97 and repeated sentence openers 0.92, because
good writers do both. Neither left room above excellent prose.

## Files

- `slopgate` — the CLI.
- `detector/` — the deterministic layer (Node, zero dependency).
- `semantic/jevgate.py` — the semantic layer.
- `semantic/decoys.jsonl` + `test_decoys.py` — adversarial decoys that are
  mechanically clean but semantically wrong, each asserting the specific rule
  that must fire.
- `semantic/mine_authorities.py` — automatic exemplar harvesting.
- `semantic/corpus/README.md` — label provenance and how thresholds were set.
