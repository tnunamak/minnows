# model-choice-policy schema (v1)

Policy documents only. Every `expands_to` is a launch recipe, not a quality claim.

## `operating-points.json`

| Field | Role |
|-------|------|
| `id` | Stable op id (`task.constraint` style) |
| `task_family` | implement, review, recover, fanout, advisor, ui, docs |
| `constraint_family` | balanced, quota-tight, dollar-tight, latency-sensitive, accuracy-first |
| `expands_to` | `provider`, `model`, `effort`, `mode` / `service_tier` |
| `frontier_assumption` | Qualitative cost/quota/strength/evidence — **not** computed scores |
| `evidence_refs` | Strings pointing at catalog files or source ids |
| `use_when` / `avoid_when` | Human + agent guidance |
| `escalate_to` / `deescalate_to` | Other op ids |
| `known_gaps` | Explicit missing evidence |
| `override_policy` | Always `explicit_flags_win` in v1 |

Root also carries:

- `catalog_ref` — pinned model-catalog tag
- `policy_version` — pack semver
- `doctrine` — short non-goals

## `op-requirements.json` (DRAFT — owner review pending)

The recommender reads this policy and the catalog; it never edits `operating-points.json`.
JSON Schema: [`schemas/op-requirements-v1.schema.json`](schemas/op-requirements-v1.schema.json).

`task_families` is one op-to-family table. Each tagged metric in `metrics.json` is
selected by family; ops do not name metric ids. Current families are `coding`,
`agentic`, `research/browsing`, `knowledge/factuality`, `reasoning`, and
`computer-use`. A metric without an unambiguous family tag is not selected.
Rows are compared only within their `comparability_group`.

| Op | Task families | Failure treatment |
|----|---------------|-------------------|
| `implement.*` | coding | Verified: failed tasks are caught and retried |
| `review.audit` | coding | Judged: `silent_failure_cost_usd = 100` |
| `fanout.explore`, `grok.explore-only` | agentic, research/browsing | Verified |
| `docs.lookup`, `recover.report` | knowledge/factuality | Verified |
| `advisor.deep` | knowledge/factuality, reasoning | Judged: `silent_failure_cost_usd = 50` |
| `ui.computer-use` | computer-use | Verified |

`silent_failure_cost_usd` means **roughly what a missed problem costs you in dollars;
raise it if misses hurt more**. It is the only owner-set quality tradeoff number.
For a row with per-task cost `c` and success probability `p`, verified ops rank by
`E = c / p`; judged ops rank by `E = c + (1 - p) × silent_failure_cost_usd`.
The `accuracy`, `pass_rate`, and `error_rate` units are fractions in 0..1;
`error_rate` converts to `p = 1 - rate`. A zero success probability gives
infinite expected cost. Elo, indices, and other non-success units do not
produce E. They can only screen an unmeasured effort under the ceiling rule.

The recommender preserves GA, tier, newest-in-tier, access, effort, and CLI
surface filters. All six waspflow lanes are allowed by default: `claude`,
`codex`, `grok`, `antigravity`, `qwen`, and `deepseek`. A catalog model may
produce more than one lane arm. The lane's CLI effort surface takes precedence;
missing CLI surfaces fall back to `api` and are flagged. Antigravity dispatch
model ids with an effort suffix use that fixed effort. `xhigh`, `max`, and
`ultra` require an explicit override reason.

Per-group E choices retain the existing independent-versus-vendor weights,
source flags, majority consistency check, and exact missing-evidence report.
The ceiling screen excludes a model only when its best measured score at any
effort is below the best allowed-effort score in an independent group; vendor
groups only flag this. `review.audit` excludes the maker's model family and
shows maker and checker success rates side by side in shared groups. If the
maker is unresolved, the constraint uses its current `expands_to` and says so.
