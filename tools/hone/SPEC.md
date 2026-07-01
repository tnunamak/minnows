# hone — the repo quality engine (v1 spec, FROZEN 2026-07-01)

One artifact, whole design. Compresses 8 memos + 3 expert rounds + a falsification experiment + ~45 gated
refactors of empirical evidence. Provenance: `~/code/dotfiles/ai/research/code-quality/` (memos 6-8, the
portfolio-machine doctrine, the canon). This spec supersedes none of that research; it is the buildable form.

## Objective (owner's, verbatim intent)

> Maximize product value and minimize complexity under owner attention, subject to near-maximal internal
> purity for owned code and near-maximal boundary purity for rented complexity.

**Terminal standard:** the fully optimal codebase — every owned subsystem is internally pure by the canon,
product-surface pure, rented behind a pure boundary, deleted, or explicitly justified
(quarantine/freeze/generated/compat/essential). NOT "good enough where the mass is."
**Execution order:** attention-weighted expected value (churn × complexity × risk × product impact).
Low-mass = later, never never.
**Success metric:** the owner buys codebase quality with tokens — higher quality per token, less owner
attention, no re-reasoning, honest reports, no self-grading.

## The engine in one line

> Turn sparse owner intent into evidence-backed repo improvements by building durable inventory, generating
> compact candidate packets, choosing the right action per piece of complexity, running the cheapest
> sufficient workflow, and recording cost + lessons so every future run is cheaper and smarter.

This is a **repo-quality engine**, not a refactor loop. Refactoring is one workflow among:
delete, document/spec-repair, rent (dependency proposal), freeze/quarantine, evidence-generation,
product-contract proposal.

## Architecture

```
durable inventory → candidate packets → classification → ranking
→ workflow execution → evidence verification → claim report → cost/lesson ledgers
```

- **Engine code** lives here (minnows; repo-independent; Node CLI).
- **Per-repo state** lives in the target repo on a branch: `quality/{inventory,packets,claims,cost,lessons}/`.
  This is how the engine works on any codebase and how knowledge compounds per-repo. The packet stream and
  ledgers ARE the product; diffs are exhaust. `skipped(reason)` and `blocked(missing-oracle)` are first-class,
  persisted outcomes — negative results are the expensive knowledge, never re-derived.
- **Repo profile** `quality/hone.yaml`: test cmd, typecheck cmd, risk markers, project-specific collectors,
  owner policy. Common collectors ship with the engine; project collectors are config (PDPP's: routes,
  OpenAPI, MCP, OAuth/consent, dual storage, connector manifests).

## CLI surface (v1)

```
hone inventory   [--repo .]                 # collectors → quality/inventory/*.json
hone plan                                    # generate + classify + rank → quality/packets/*.yaml
hone work <id>   [--maker P] [--judge P]     # execute one packet: edit → evidence → independent judge → land/revert/skip
hone report                                  # compile from claim ledger (never hand-written prose)
hone run         [--budget N|--n K] [--lanes L]  # plan→work loop; conflict-aware parallel lanes (disjoint touchsets)
```

**Verdict providers:** judgment steps (propose seam/design, judge diff+evidence) go through a provider
interface. v1 providers are subprocesses: `claude -p`, `codex exec`. Packets make fresh contexts cheap by
design, so subprocess ≈ native in capability; a native-subagent adapter can be added behind the same
interface later. **Maker and judge MUST be different providers** — enforced structurally by `work`, not by
convention.

## Candidate packet

The unit of work and the main token-efficiency move: expensive reasoning produces durable packets; cheap
contexts execute them. Schema: `schemas/candidate-packet.yaml` (v1.1 — amended after reality-validation on
5 hand-authored packets, 1 executed; packet-alone sufficiency ~95%). Core fields: behavior_status, ownership,
action, **proof_class** (first-class), **execution_gate** (autonomous|owner_ratify — `work`/`run` refuse
owner_ratify and ungated packets, fail-closed), **plan** (the WHAT: transform_class + instruction),
evidence_required (literal runnable commands + expected results, not prose), authoring_evidence (e.g.
liveness-root sweeps carried by delete packets), not_allowed, touchset (lane scheduling), maker_tier +
judge_tier (judge scales with silent_wrongness_cost), batch_key, depends_on/unlocks, terminal status incl.
skipped(reason)/blocked(oracle).

### Behavior status (classify BEFORE working — prevents both over-preserving AI sediment and casual rewrites)
```
contract         preserve unless owner explicitly changes it
likely_intended  preserve by default
provisional      may improve toward inferred intent; behavior change allowed if labeled
accidental       delete or rewrite
unknown          investigate or quarantine
```

### Ownership (classify BEFORE decomposing — never decompose what should be deleted/rented/frozen)
```
OWN | RENT | DELETE | FREEZE | QUARANTINE | GENERATED | TEMPORARY
```

## Evidence policy (the oracle question, resolved)

A senior engineer refactors "on skill" — instant mental simulation. The machine's equivalent is judgment
(agents) + **actually running what the human only imagines** (differential execution). Tests are ONE evidence
rung: corroborating everywhere, required nowhere. The rule: **cheapest sufficient evidence for the change
class; independent agents review evidence, they never replace it; no external evidence = no preservation
claim.**

```
type-only change            emitted-JS equivalence
exact move                  body hash + callsite/import/export equivalence + init-order safety
certified local transform   side-condition check + AST diff
pure-logic change           differential probes (old vs new, generated + real inputs) OR a direct test
effectful/integration       focused integration evidence
auth/storage/security       named property-at-risk + proof the evidence covers THAT invariant
provisional/accidental      judgment-first: maker rationale + independent cross-model review; changes labeled
deletion (v2)               liveness-root packet (entrypoints/routes/specs/dynamic-dispatch/config/...)
product contract change     proposal packet, owner-ratified, never auto-landed
```

Scrutiny scales with **cost of silent wrongness** (blast radius × detectability × reversibility), not with
filename. "Wrong but correctable" is valid only where wrongness is detectable and reversible.

## Ranking

```
priority = expected_quality_gain × owner_attention_reduction × product_impact × confidence
           / (risk × evidence_cost × token_cost × reversibility_cost)
```
Early runs: these are agent-estimated priors, soft by nature; the cost ledger's actuals recalibrate them.
**Metrics nominate; they never decide.** Every metric finding gets semantic validation before work
(the dynamic-import-cycle lesson). Cognitive-complexity is a nominator, not the backlog.

## Workflows

**v1 (automated):** surface-repair (code/spec/docs/tests/errors agree) · owned-preserve-refactor
(decomplect, honest names, explicit state, deep modules — reject relocation even when green) ·
evidence-generation (build the oracle that unlocks higher-value work).
**Modeled, v2 (packets prepared, owner ratifies):** delete/prune · rent/dependency-adoption ·
freeze/quarantine · idealize-rewrite (provisional code) · product-contract proposal.

## Non-negotiables (earned lessons, encoded as machine parts — never re-derive)

1. **Maker ≠ judge.** The producer of a change cannot certify it. Structural, tested.
2. **Tests are evidence, not the oracle.** No direct test ≠ no action; no evidence = no preservation claim.
3. **Codemods are hygiene, not the center.** Measured: T0 = 41.8% of function count, 16.3% of complexity mass.
4. **Metric findings nominate work; they do not define it.**
5. **Agents overclaim; reports compile from the claim ledger.** No "done/complete/clean" without the exact
   supporting evidence. Claim types: verified_fact | judged_design_claim | behavior_preserved |
   behavior_changed | hypothesis | uncertainty | remaining_work.
6. **Deletion is not refactoring.** Liveness roots required; tests-pass is not a deletion proof.
7. **OWN/RENT/DELETE/FREEZE/QUARANTINE before decomposition.**
8. **Reject relocation.** Moving a blob behind a filename with green tests is not decomplecting
   (the computeParticipation revert is the canonical example).
9. **Never re-read what inventory already knows; never re-litigate a persisted skip.**

## Owner interface

Sparse intent: *"Improve this repo toward its quality target. Work on a branch. Standing policy. Escalate
only owner-level decisions."* Owner-level = new prod dependency, public behavior change, auth/security
policy, storage contract, data migration, rubric change, merge/release. Everything else is autonomous on a
branch. Policy knob in `hone.yaml` (`autonomous_branch` ... `autonomous_preprod` with
`current_behavior_default: provisional`). Zero-interruption mode is legitimate and labeled.

## Acceptance tests (v1 gate — no write-enabled fan-out until green)

1. `work` refuses maker==judge (structural maker≠judge).
2. Report compiler refuses an unbacked "done/complete/clean" claim (seeded fixture).
3. Metric-nomination negative control: a seeded non-defect finding must end `skipped(validated-non-defect)`,
   not worked.
4. Packet schema validation rejects malformed packets.

## v1 build order

1. Spec + schemas (this).
2. `inventory` + `plan` — reuse the proven PDPP instruments (ast-scope/smell-callbacks/tier-mass/discover)
   as the first collectors; hand-validate the packet schema on ~5 real candidates before building further.
3. `work` + verdict providers + claim/cost ledgers.
4. `report`, then `run` with parallel lanes (worktree per lane; disjoint touchsets only).
5. Dogfood on PDPP RI: 3-5 surface + 3-5 owned-code + 1 evidence-generation packets, one branch, small
   commits, compiled report. Success = real quality gain, tokens accounted, zero unbacked claims, correct
   classifications, and run 2 measurably cheaper than run 1.

## What NOT to do

No broad strategy memos. No codemod-centering. No test-centering. No threshold-worship. No deletion
automation in v1. No rereading repos. No self-certification. No hand-written report prose. No routine owner
escalation. No governance cathedral before v1 proves itself on a real batch.
