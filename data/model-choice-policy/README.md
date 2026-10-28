# Data pack: `model-choice-policy`

**Policy**, not facts. Task-shaped **operating points** that expand to explicit
`provider` / `model` / `effort` / `mode` flags for agent orchestration tools.

Separates from [`model-catalog`](../model-catalog/) so recommendations never
contaminate pricing or benchmark evidence.

## Get this pack

| | |
|---|---|
| **This version** | [data-model-choice-policy-v0.2.8](https://github.com/tnunamak/minnows/releases/tag/data-model-choice-policy-v0.2.8) — unreleased; published by CI after merge to main |
| **Latest** | [releases](https://github.com/tnunamak/minnows/releases?q=data-model-choice-policy&expanded=true) |
| **Facts catalog** | [model-catalog](../model-catalog/) — pin is `catalog_ref` in the policy file |

```bash
./scripts/fetch-data-pack.sh model-choice-policy
# or
TAG=data-model-choice-policy-v0.2.8
curl -fsSL -L \
  "https://github.com/tnunamak/minnows/releases/download/${TAG}/${TAG}.tar.gz" \
  | tar -xz
```

## Doctrine

1. Operating points are **task-shaped** (`implement.standard`), not `cheap|default|max`.
2. Expansion must be **explicit and logged** — no silent auto-routing.
3. **Evidence confidence** is as important as sticker cost.
4. **Quota ≠ dollars** — never merge without an explicit exchange rate.
5. Update ops only from source-backed catalog facts or local evals.
6. Raw flags always win: `--provider` / `--model` / `--effort` override `--op`.

## Normal free routes

`free_routes` declares an exact zero-price arm as an ordinary `route` candidate.
It needs no experimental flag. Primary operating points stay unchanged.

| Operation | Basis | Scope | Proof and effort |
|-----------|-------|-------|------------------|
| `implement.oracle-bounded` | `qualified_free` | Small PUBLIC source changes whose full acceptance standard is a deterministic oracle | `oracle`, `high` |
| `docs.lookup` | `qualified_free` | Known PUBLIC sources with a structured fact oracle; excludes open-ended research | `oracle`, `high` |
| `review.rule-check` | `unvalidated` | Bounded PUBLIC typed rule judgment; the two known-defect probes do not establish review adequacy | `judged`, `high` |

Use `route <op> --data-classification public --proof-class oracle` for the two
qualified operations. No override is required and no experimental flag exists.
The router applies evidence eligibility, then explicit owner model/account
preferences, then zero source price, quota draining and pace. A qualified free
pair is not displaced only to spend subscription tokens; an explicit owner
preference is not a quota-draining preference. An owner `avoid` directive and checker independence still
remove it, and an observed cooldown still applies.

The `review.rule-check` row is an alternative, not an automatic target. It is
ranked as judgment-required and requires exact
provider/model/account/effort overrides plus `--reason`. It cannot replace
`review.audit`, and broad, security, protocol or open-ended review is unchanged.
No paid fallback is authorized by this pack: an unmapped or unfunded account
stays excluded exactly as before.

The normal declaration has no artificial expiry. `status` is the last observed
free date, and the evidence dates are the last source reads. Revocation is
explicit: remove the operation or the route. Replay keeps the kind that was
recorded. A normal receipt is rechecked as normal and refused with
`normal_route_receipt_not_currently_authorized`; a legacy experimental receipt
keeps its own path and refusal code. Neither is rewritten.

Public classification and proof class are caller assertions, not content scans
or sandbox guarantees. `--sensitive` always refuses, and a private repository,
PDPP data, secrets, Jev text or personal data never becomes eligible because the
price is zero. The pack does not widen consent for private or personal inputs.

The [matched public benchmark](evidence/deepseek-public-eval-2026-10-15.json)
holds four implementation and two known-source lookup gate pairs per arm, all
passing. That is bounded gate evidence: the sample is small and purposive, uses
public tests and reference fixes, and estimates neither whole-task quality nor
audit recall. The two review probes are classification probes only; R2 also
exceeded its inspection scope. Provider usage and complete workflow cost were
not reported, so the $0 source price is not an observed total cost. Rate limits
remain unknown. No availability monitor is implied: the source dates record the
last check, not continuous verification.

A free provider can enforce usage limits. Zero source price implies neither
unlimited availability nor a reset or retry time. A usage-limit response does
not permanently invalidate the free pair. The router cools the account only
from recorded failure events; the pack does not carry those events.

OpenCode attributes zero retention and no training to the upstream provider;
its unpaid-service terms separately permit content use to improve OpenCode
services. Both processing layers are named in the declaration. The shared
OpenCode server exposes host tools, and injected `t3_thread_read` can access
private thread content. The declaration records both risks in `known_risks`,
and the validator requires them. Per-instance provider sandbox and MCP isolation
remain later hardening.

## Retired legacy experimental route

The OpenCode DeepSeek V4.1 Flash Free legacy experimental declaration (`experimental_free_routes`) was retired on
2026-10-22. Its seven-day local recheck horizon ended with it. The pack must not carry the key; `check` reports it.
Historical receipts recorded under it keep their recorded kind and are refused on replay with
`experimental_route_receipt_not_currently_authorized`; no receipt is rewritten. The normal `free_routes` declaration is
the only zero-price route for that model: no flag, no expiry, public classification required, and `--sensitive`
refused. The legacy experimental `fanout.explore` and `resolve --no-op` authority
was retired with that contract. The current normal `fanout.explore` free-route
authorization is separate: its `owner_authorized` declaration in `free_routes`
allows `judged` or `none` proof at high effort. The experimental retirement does
not grant or remove this current authorization; normal declaration and public-input
checks apply.

## Read operating points

```bash
model-policy-ops list
model-policy-ops show implement.standard
model-policy-ops check
```

## Derive ops from data (DRAFT)

`op-requirements.json` holds the human-set slots for each op: task families,
allowed lanes and efforts, missed-failure cost, and constraints.
`scripts/recommend_ops.py` derives a recommended arm from these slots and the
catalog, or reports missing model/board coverage. It uses a 90-day price horizon
and requires independent cross-model evidence for model changes. It never edits
`operating-points.json`. See
[SCHEMA.md](SCHEMA.md#op-requirementsjson-draft--owner-review-pending).

```bash
./scripts/recommend_ops.py            # markdown table + per-op detail
./scripts/recommend_ops.py --json
uv run --with pytest --with jsonschema pytest tests/test_recommend_ops.py
```

`model-policy-ops` reads this checkout by default. Pass `--policy <path>` to
read a separately installed pack. Keep expansion explicit in the caller; do
not silently route by model name.

## Operating points (12)

| Op | Provider / model / effort |
|----|---------------------------|
| `recover.report` | claude / haiku-5-5 / medium |
| `fanout.explore` | codex / gpt-6.1-sol / medium |
| `fanout.dollar-tight` | codex / gpt-6-luna / medium |
| `docs.lookup` | claude / haiku-5-5 / medium |
| `implement.standard` | claude / opus-5-5 / medium |
| `implement.quota-tight` | claude / sonnet-5-5 / medium |
| `implement.oracle-bounded` | codex / gpt-6-luna / high |
| `implement.accuracy-first` | claude / opus-5-5 / high |
| `review.audit` | codex / gpt-6.1-sol / high |
| `review.rule-check` | validated typed destination when configured; fallback claude / haiku-5-5 / medium |
| `advisor.deep` | claude / opus-5-5 / high |
| `ui.computer-use` | codex / gpt-6.1-sol / medium |
| `grok.explore-only` | grok / grok-4.7 / medium |

## Changelog

### v0.2.7 — unreleased

- Add `review.rule-check` for a whole answer consisting of a typed yes/no check against an explicit supplied rule. Configured typed destinations need fixed questions, model versions, labelled agreement evidence and measured cost. Haiku medium remains the fallback; broad audits stay on `review.audit`.
- The local sample supports three narrow lead checks only. Classification, severity, triage and semantic duplicates need their own accepted evidence; exact facts remain code.

### v0.2.6 — unreleased

- Admit optional `implement.oracle-bounded` and `docs.lookup` overrides only for the reviewed public scopes, oracle proof and high effort. Primary recommendations stay unchanged.
- Recheck qualification on fresh targets and both historical replay paths. Keep generic governed destinations refused and preserve the public-only exploration paths.
- Bundle sanitized paired benchmark evidence, including the review scope failure, small-sample limits and unknown measured costs and rate limits.

### v0.2.5 — 2026-10-15

- Refuse undeclared OpenCode `variant` models and require fresh source evidence for the exact temporary $0 route. Record the owner's public-only experimental T3 authorization and keep qualified operations empty. The seven-day expiry is a local recheck horizon, not a vendor end date.
- Validate experimental route dates, evidence, authentication, rate-limit shape, expiry, and privacy floor through one shared contract. Document that `public` is a caller assertion.

### v0.2.4 — 2026-10-15

- Add an exact OpenCode DeepSeek V4.1 Flash Free experimental route. It has no earned operations, costs $0 temporarily as of this date, and refuses inputs without an explicit public classification.

### v0.2.3 — 2026-10-13

- **No routing change.** Every operating point expands exactly as in v0.2.2.
- Repins to catalog v0.5.15, which adds Google Gemini 4 Argon. Google's API price is an introductory $2 / $10 per M tokens until 2026-11-30, every board reads Argon at high effort only, and Hone has no Google provider path, so no operating point can use it yet.
- Catalog pin: **v0.5.15**.

### v0.2.2 — 2026-10-13

- **No routing change.** Every operating point expands exactly as in v0.2.1.
- Repins to catalog v0.5.14, which adds Meta Muse Wonder. Meta lists Muse Wonder as a preview, publishes only max-effort scores, and Hone has no Meta provider path, so no operating point can use it yet.
- Catalog pin: **v0.5.14**.

### v0.2.1 — 2026-10-11

- **No routing change.** Every operating point expands exactly as in v0.2.0.
- Repins to catalog v0.5.13, which corrects price-lookup order, vendor evidence grades and deduplicated board rows.
- Ships the `observation_max_age_hours` (3) and `observation_window_days` (14) parameters that `route` already reads for its evidence-freshness notes. They equal the code defaults, so routes do not change.
- Catalog pin: **v0.5.13**.

### v0.1.19 — 2026-10-07

Evidence for every change below is the same-snapshot Artificial Analysis read of 2026-10-07 (`performance/artificial-analysis-claude-haiku-5-5-2026-10-07`, grade B), unless another file is named. `scripts/recommend_ops.py` returns INSUFFICIENT_EVIDENCE for 11 of 12 ops, as it does for 9 of 10 on v0.1.18, so these changes rest on rule 1 (the cheapest tier whose evidence clears the bar, then the newest GA model in that tier) and on the boards.

- Move `recover.report` and `docs.lookup` from claude-sonnet-5-5/low to **claude-haiku-5-5/medium**. Haiku 5.5 medium beats Sonnet 5.5 low on GDPval-AA (1277 vs 1179) and AA Briefcase (1372 vs 1272), is level on GDP.pdf (15.2% vs 16.0%), and costs $0.047 per task against $0.345. It loses on closed-book recall (HLE without tools 33.8% vs 36.2%; AA-Omniscience 4.5 vs 19.4). Both ops work from supplied context or docs, so recall does not decide them; each op names its escalation trigger. Low effort does not clear the bar (GDPval-AA 1125, Briefcase 1112).
- Add **`fanout.dollar-tight`**: codex/gpt-6-luna/medium. GPT-6 Luna is the newest GA model in Codex's cheapest tier ($0.10/$0.50 per MTok, a twentieth of GPT-6.1 Sol). Against GPT-6.1 Sol medium: AutomationBench 40.5% vs 62.6% and GDPval-AA 1262 vs 1433 at $0.0175 vs $0.214 per task; Vals Finance Agent v2 49.9% vs 52.0% at $0.124 vs $1.62 (`vals-ai-index-finance-agent-2026-10-06`). Scout results must be verified: OpenAI reports that Luna hides a broken search tool in 28.7% of cases (`openai-gpt-6-sol-luna-launch-curves-2026-09`).
- Add **`implement.oracle-bounded`**: codex/gpt-6-luna/high, for small patches that a deterministic oracle accepts or rejects. When the oracle catches failures, Luna-first costs less than the stronger arm if Luna passes more than about one task in three. OpenAI's DeepSWE v1.1 curve (grade C) has Luna high at 59.3% for $0.084 per task, against GPT-6 Sol medium at 56.6% for $0.38. Independent Terminal-Bench 4.0 has Luna at 13.6% (Vals) and 16.4% (tbench.ai), so long terminal tasks are out of scope (`vals-ai-terminal-bench-4-2026-10-06`, `third-party-board-roster-refresh-2026-10-07`).
- Keep `review.audit` on **codex/gpt-6.1-sol/high**, now at evidence confidence medium. At high effort it ties GPT-6 Astra on the boards that report high (AA Intelligence Index 50.2 vs 50.9; ARC-AGI-2 91.7% vs 92.1%, `arcprize-leaderboard-2026-10-07`) at a fifth of the cost. Astra leads by 1 to 4 points only at max.
- Keep `fanout.explore` on codex/gpt-6.1-sol/medium, now at evidence confidence medium: AutomationBench 62.6% at $0.196 per task, against Opus 5.5 medium 61.2% at $0.638.
- Keep `implement.quota-tight` on claude-sonnet-5-5/medium. Haiku 5.5 does not clear it: AA Terminal-Bench 4.0 at medium 15.2% vs 29.8%.
- Keep `implement.standard`, `implement.accuracy-first` and `advisor.deep` on Opus 5.5. Opus leads GPT-6.1 Sol on Vals Terminal-Bench 4.0 (65.2% vs 55.1%) and at high on the AA Intelligence Index (53.6 vs 50.2).
- Keep `ui.computer-use` on codex/gpt-6.1-sol/medium (AutomationBench as above) and `grok.explore-only` on grok-4.7/medium (still the newest GA Grok model).
- Catalog pin: **v0.5.12**.

### v0.1.18 — 2026-10-07

- **No routing change.** Every operating point expands exactly as in v0.1.17.
- Repins to catalog v0.5.12, which adds Claude Haiku 5.5 (a candidate for the cheap-tier ops, not yet routed), new board snapshots, and a pricing re-verification.
- Catalog pin: **v0.5.12**.

### v0.1.17 — 2026-09-30

- **No routing change.** Every operating point expands exactly as in v0.1.16.
- Repins to catalog v0.5.11, which corrects the GPT-6.1 Sol Codex CLI note: the model is listed and dispatchable on codex-cli 0.159.2.
- Catalog pin: **v0.5.11**.

### v0.1.16 — 2026-09-30

- Move `review.audit` from codex/gpt-6-astra/high to **codex/gpt-6.1-sol/high**, `ui.computer-use` from codex/gpt-6-astra/medium to **codex/gpt-6.1-sol/medium**, and `fanout.explore` from claude-opus-5-5/medium to **codex/gpt-6.1-sol/medium**. No op runs GPT-6 Astra now; it stays selectable by explicit flag.
- This is a best guess with low or medium confidence, made on the owner's direction that Astra ($10/$50 per MTok) does not justify its price for routine work. Two blind reviews (Claude Opus 5.5 and a Codex reviewer, 2026-09-29) found the public evidence too thin to prove any of these moves: GPT-6.1 Sol has one launch-day Artificial Analysis snapshot and vendor claims, and no independent audit-recall, computer-use-at-medium or navigation evidence.
- Why these three: Astra costs five times Sol per token, and on the vendor charts Astra medium costs more than $2 per task over GPT-6 Sol medium, more than the draft silent-failure cost. Opus 5.5 medium on `fanout.explore` costs $2.94 per task against $0.83 for Sonnet 5.5 medium on Anthropic's own curve. The reviewers' evals (seeded-defect review, bounded patches, cheap-worker tasks, computer use) are pending; they decide whether to keep these moves.
- Unchanged: `implement.standard`, `implement.accuracy-first` and `advisor.deep` (Opus 5.5), `recover.report`, `docs.lookup` and `implement.quota-tight` (Sonnet 5.5), `grok.explore-only` (grok-4.7).
- Catalog pin: **v0.5.10**.

### v0.1.15 — 2026-09-29

- **No routing change.** Every operating point expands exactly as in v0.1.14.
- Repins to catalog v0.5.10, which adds GPT-6.1 Sol, Xiaomi MiMo-V2.6 and launch-day board rows. GPT-6.1 Sol ($2/$10 per MTok, near-Astra by OpenAI's description) is a candidate for `review.audit` and `ui.computer-use`, which run GPT-6 Astra at $10/$50. It is not routed: independent low-effort evidence is still provisional, and the change needs owner approval.
- Catalog pin: **v0.5.10**.

### v0.1.14 — 2026-09-29

- Move `recover.report` and `docs.lookup` from claude-sonnet-5/low to **claude-sonnet-5-5/low**, and `implement.quota-tight` from claude-sonnet-5/low to **claude-sonnet-5-5/medium**. Rule 1: take the newest model in the cheapest tier that clears the bar.
- Evidence (catalog v0.5.9): AA Intelligence Index v4.3.2 has Sonnet 5.5 at low at 36 for $0.41/task, against Sonnet 5 at max at 38.2 for $5.09/task. AA says its runs used a pre-release deployment.
- `implement.quota-tight` uses medium because vendor Terminal-Bench 4.0 shows Sonnet 5.5 at low at 20.0%, against 28.8% at medium. Opus 5.5 leads Sonnet 5.5 at low (38.5%) and medium (57.6%) on that board; Sonnet 5.5 passes it only at max, at a higher cost per task.
- `scripts/recommend_ops.py` returns INSUFFICIENT_EVIDENCE for 9 of 10 ops, as it does on plain `origin/main`. This change rests on the tier rule and the boards above, not on a recommender result.
- Every Opus, Astra and Grok op is unchanged.
- Catalog pin: **v0.5.9**.

### v0.1.13 — 2026-09-23

- **No routing change.** Every operating point expands exactly as in v0.1.12.
- `op-requirements.json` pins `price_as_of`, so recommender output no longer depends on the run date. `review.audit`'s different-vendor constraint is marked as an owner decision pending (`constraint_decision`).
- The recommender keeps a tied incumbent and breaks other ties by expected cost. It uses the published pass@4 to calibrate retries and re-prices promotional costs at the horizon rate. It treats independent boards without an effort label as blocking disagreements, and can take lane quota availability from clawmeter as input.
- Three independent Claude Opus 5.5 review rounds (2026-09-23) signed off on releasing this with no routing change. The only benchmark-driven question left open is `review.audit` (Gemini 3.8 Flash vs GPT-6 Astra). It needs op-level evidence, such as a seeded-defect review oracle.
- Catalog pin: **v0.5.9**.

### v0.1.12 — 2026-09-23

- Move `grok.explore-only` from grok-4.6/high to **grok-4.7/medium**. On 2026-09-23 the authenticated grok CLI offers only grok-4.7, so the grok-4.6 arm could no longer be dispatched. Rule 1 picks grok-4.7 as the only newest GA model in the lane, at the default effort (medium). An independent Claude Opus 5.5 review signed this off in both rounds.
- No other routing change. `scripts/recommend_ops.py` now ranks by expected cost per task, with evidence gates, confidence intervals, a price horizon and robustness sweeps. The review found defects that block any move it would drive: the tie-break rule, an unwired pass@k field, wall-clock price dates, and board rows with no effort recorded. They were fixed in v0.1.13.
- Catalog pin: **v0.5.7**.

### v0.1.11 — 2026-09-22

- Move `implement.standard` and `fanout.explore` from claude-sonnet-5/medium to **claude-opus-5-5/medium**. Sonnet 5 at max effort (its best) scores below Opus 5.5 at medium on every independent board, at a higher cost per task: AA Intelligence Index v4.3.2 38.2 at $5.09/task vs 51.2 at $1.34; vals.ai Terminal-Bench 4 8.1% vs 61.6% (both at max).
- Add `op-requirements.json` (DRAFT): the owner-set inputs for each op (which benchmarks count as evidence, allowed efforts, quality bar, constraints). `scripts/recommend_ops.py` derives each op's model from it and the catalog. It also names the missing evidence wherever the data cannot decide.
- Unsettled, so unchanged, with the reason in `known_gaps`:
  - `recover.report`, `docs.lookup`, `implement.quota-tight`: no independent low-effort evidence exists.
  - `review.audit`: "checker at least as strong as maker" and "different family" conflict while Opus 5.5 is the strongest maker.
  - `ui.computer-use`: no shared OSWorld harness covers Claude and GPT-6.
- Catalog pin: **v0.5.7**.

### v0.1.10 — 2026-09-22

- Move `grok.explore-only` from grok-4.5 to **grok-4.6** / high. grok-4.6 is the newest GA Grok model, the grok CLI default, and the catalog's `grok` family default. There is no same-snapshot quality comparison with grok-4.5 (AA v4.2: grok-4.6 51 at high).
- Correct the `pack.json` description, which still described v0.1.8 (catalog v0.5.4).

### v0.1.9 — 2026-09-22

- Move `advisor.deep` (sonnet-5/high) and `implement.accuracy-first` (codex gpt-6-astra/high) to **claude-opus-5-5 / high**. Evidence, all in catalog v0.5.5:
  - AA Intelligence Index v4.3.2 (grade B, one snapshot): Opus 5.5 57.6 at max, 54 at high ($1.82/task), 51.2 at medium. Fable 5.1 53.4, GPT-6 Astra 52.7, Opus 5 50.8 (all at max). Sonnet 5 is not in the top 32.
  - vals.ai Terminal-Bench 4.0 (grade C): Opus 5.5 61.6%, GPT-6 Astra 57.1%, Fable 5.1 49.5%, Opus 5 45.5%, Sonnet 5 8.1%.
  - Vendor effort curves (grade C): Terminal-Bench 4.0 at high, Opus 5.5 64.2% for $3.88/task vs GPT-6 Astra 57.9% for $7.21/task.
- `implement.accuracy-first → review.audit` now crosses model families (Claude implements, Codex audits). Before this change it was a same-arm edge that waspflow skipped.
- `evidence_confidence` stays **medium**: the independent evidence is one AA snapshot and one secondary board, with no local eval.
- The Sonnet 5 ops are unchanged. No benchmark compares Sonnet 5 with Opus 5.5 on bounded edits or reading tasks.
- Catalog pin: **v0.5.5**.

### v0.1.8 — 2026-09-09

- Move Codex ops to GA **gpt-6-astra** per owner model policy (retire gpt-5.6-sol): `review.audit` and `implement.accuracy-first` to effort **high** (never xhigh/max by default), `ui.computer-use` to effort **medium** (mechanical/implementation-shaped work). Trigger: `waspflow doctor`'s stale-edge warning on the `preferred_over` entry below, surfaced after gpt-6-astra reached GA.
- Retire the `gpt-5.6-luna over gpt-5.4-mini` `preferred_over` edge — it was authored rot-aware and the owner policy no longer prefers any 5.x model. No replacement edge added: the catalog's gpt-6 family has only `gpt-6-astra` as GA, no cheap-tier gpt-6 model yet, so there is nothing to prefer over gpt-5.6-luna without inventing evidence.
- `evidence_refs` re-pointed at existing catalog rows for gpt-6-astra (`performance/openai-gpt-6-astra-launch-2026-09`, `performance/terminal-bench-4-astra-audit-2026-09`, `pricing/openai-api-2026-07`, `pricing/codex-credits-2026-07`). `evidence_confidence` held at **medium**, not raised — the model/effort swap is owner-policy + GA-status driven, not new local evidence (the cited terminal-bench-4 audit row is itself grade C / `comparable: false`).
- Catalog pin carries forward unchanged from v0.1.7: **v0.5.4**.

### v0.1.7 — 2026-09-05

- Pin catalog **v0.5.4**. Operating-point recommendations are unchanged.

### v0.1.6 — 2026-08-02

- Pin catalog **v0.5.3** after the GPT-5.6 Codex effort-tier value-claim check.

### v0.1.5 — 2026-07-11

- Refresh `review.audit` to Codex **gpt-5.6-sol** / xhigh.

### v0.1.4 — 2026-07-09

- Pin catalog **v0.5.2**. Top-3 ops: `evidence_confidence: medium` (not high); local rows are harness smoke only.

### v0.1.4 — 2026-07-09

- Pin catalog to **`data-model-catalog-v0.5.2`** (was v0.3.0).
- Evidence refs for Sonnet ops cite digitized effort curves (`anthropic-sonnet5-digitized-2026-07`).
- Validator now enforces: unique op ids, escalate graph, `expands_to` vs capabilities, catalog:// and source:// resolvability, pack.json pin agreement.

### v0.1.0 — 2026-07-09

- Initial 10 operating points from expert recommendation (README previously said 8).
- Pins `model-catalog@data-model-catalog-v0.3.0`.
