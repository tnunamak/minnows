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

### Expected cost model

For a task attempt with success probability `p`, per-attempt model cost `c`,
verification/review overhead `h`, failure-detection probability `d`, and
silent-failure cost `S`, the failed-and-detected retry probability is
`q = (1-p) × d`. The expected cost per task is:

`E = (c+h) / (1-q) + ((1-p) × (1-d) / (1-q)) × S`

This charges overhead on every attempt. A failed attempt is retried only if it
is detected; an undetected failure terminates the task and incurs `S`.
For judged ops, `d=0`, so `E=c+h+(1-p)×S`. If `p=0` and `d=1`,
the expected number of attempts is infinite.

`accuracy`, `pass_rate`, and `error_rate` are fractions in 0..1; an error rate
converts to `p=1-rate`. Elo, indices, and partial-credit scores do not produce
E. They can only screen an unmeasured effort under the ceiling rule.

| Default | Value | Source and meaning |
|---------|-------|--------------------|
| `attempt_overhead_usd` | $0.50 per attempt | Orchestrator review of 2026-09-23: allowance for verification and orchestrator review on **each** attempt. This is a policy estimate, not measured spend. |
| `failure_detection_probability` for verified ops | 0.75 | Orchestrator review of 2026-09-23, informed by `ai/research/model-routing/escalation-triggers-on-verify-failure-not-verify-success-and-walks-the-model-effort-frontier.md` in the dotfiles research corpus. The note reports 28–76% gamed green passes across specific evaluations; those rates do **not** directly measure detection probability. 0.75 is a sensitivity-tested policy prior. |
| `failure_detection_probability` for judged ops | 0 | Orchestrator review of 2026-09-23: no automatic catch/retry for a review or advice miss. |

The same review supplies the following uncalibrated `silent_failure_cost_usd`
defaults. They mean roughly what a missed problem costs in dollars; raise a
value if misses hurt more. The original recommender brief already specified
$100 for `review.audit` and $50 for `advisor.deep`.

| Op | `silent_failure_cost_usd` | Reason for relative size |
|----|---------------------------|--------------------------|
| `implement.accuracy-first` | $50 | An undetected defect is especially costly in accuracy-first work. |
| `implement.standard` | $10 | A missed coding defect needs later repair. |
| `implement.quota-tight` | $5 | Small, quota-constrained patches have a lower assumed loss. |
| `review.audit` | $100 | A missed problem can pass silently through the checker. |
| `advisor.deep` | $50 | Wrong advice can steer later work. |
| `recover.report`, `fanout.explore`, `docs.lookup`, `ui.computer-use`, `grok.explore-only` | $2 each | Lower assumed loss for a missed factual, exploration, lookup, or UI task. |

### Candidate and evidence rules

The recommender preserves GA, tier, newest-on-reachable-lane, access, effort,
and CLI-surface filters. The default provider list names all six waspflow
lanes: `claude`, `codex`, `grok`, `antigravity`, `qwen`, `deepseek`.
`defaults.provider_restrictions` limits Grok to `grok.explore-only`, and that
op explicitly sets `allowed_providers=["grok"]`; it cannot recommend Codex.
A catalog model may produce more than one lane arm. The lane's CLI effort
surface takes precedence; missing CLI surfaces fall back to `api` and are
flagged. Antigravity dispatch ids with an effort suffix use that fixed effort.
`xhigh`, `max`, and `ultra` require an explicit override reason.

Per-group E choices retain independent-versus-vendor weights, source flags,
the cross-model consistency check, and exact missing-evidence reports. The
ceiling screen excludes a model only when its best measured score at any effort
is below the best allowed-effort score in an independent group; vendor groups
only flag this. `review.audit` excludes both makers' model families and shows
maker and checker success rates side by side in shared groups. If a maker is
unresolved, its current `expands_to` supplies the family constraint.

### Assumption sensitivity

The full dependency graph is rerun on a grid: overhead $0.10, $0.50, $1.00,
$2.00; detection probability 0.50, 0.75, 0.95 for verified ops (judged ops
stay at 0); and silent-failure cost 0.5×, 1×, 2× the op default. `CLEAR`
requires the same recommended arm in every grid cell. Every other result is
`ASSUMPTION_SENSITIVE`. If neighboring grid cells differ, the output reports
one approximate flip threshold found by bisection while holding the other
assumptions fixed. If no cell produces a winner, the output states that there
is no flip threshold because evidence is insufficient across the grid.
