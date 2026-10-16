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

The optional root `experimental_free_routes` array declares exact temporary
free routes. Each entry binds billing, provider, model and T3 account identity.
It adds no operating-point candidate and changes no primary recommendation.
Entries carry dated source evidence, authentication and rate-limit state, an
expiry, the public-data requirement and the mandatory prohibited-input list.

`qualified_operations` is a list of strict records with these fields:

| Field | Allowed value |
|-------|---------------|
| `id` | `implement.oracle-bounded`, `docs.lookup`, `fanout.dollar-tight` or `recover.report` |
| `proof_classes` | A non-empty subset of `oracle`, `judged`; `[]` revokes proof eligibility |
| `efforts` | A non-empty subset of the live catalog's `low`, `high`, `max` |
| `scope` | The reviewed public scope of that entry (`small_public_source_change_full_deterministic_oracle`; `bounded_public_test_design_checked_by_independent_checker`; `known_public_source_lookup_structured_fact_oracle`; `bounded_public_research_checked_by_ci_or_independent_checker`; `bounded_public_status_report_checked_by_independent_checker`) |
| `purposes` | Optional; restricts the entry to those launch purposes (`fanout.dollar-tight` is `["research"]`; the bounded test-design entry is `["test-design"]`) |
| `evidence_ref` | `evidence/deepseek-public-eval-2026-10-15.json`, shipped in the pack |

An operation may hold more than one entry only when each entry names a distinct
`scope`, and at most one of them omits `purposes` (the unrestricted entry). A
caller that states a listed `purpose` gets the purpose entry; every other caller
gets the unrestricted entry. Unknown fields and mismatched scope or evidence
references refuse validation.
An empty operation list or removal of an entry revokes qualification. An empty
`proof_classes` list revokes every proof for that entry. Absent or `none`
checking never qualifies: a trial step must be checked by a deterministic
oracle or an independent judge. Both CLI and pack validation use the same
contract. Qualification is an owner decision based on the referenced evidence,
not certification by the shape validator.

`selection` records the trial's authority honestly, and is required whenever
`qualified_operations` is non-empty:

| Field | Allowed value |
|-------|---------------|
| `mode` | `preferred_normal_candidate` — a preference, never a forced route |
| `authorization` | `owner_authorized_trial_2026-10-15` |
| `benchmark` | The immutable qualification evidence; it does not qualify these operations |
| `retrospective` | `true`; the trial is measured against the outcome ledger |
| `authority_note` | Non-empty prose stating the authority and its limits |
| `threshold` | Positive integer; unique closed consumer tasks before the retro is due |

`input_policy` is a required structured manifest policy for each free route:
`allowed_github_owners`, `excluded_github_owners` (the company namespace is
always excluded by a code-enforced floor a pack edit can only widen),
`other_owners` (`public_with_explicit_provenance`),
`prohibited_content` (must include `secrets`, `PDPP_data`, `Jev_text`,
`personal_data`), `credential_paths_denied` (`true`) and `caller_assertion_limit`.

Every input must be bound to an authorized repository or an explicit public
source. A `github_repo` entry names a repository owner and name; the owner's own
namespace and any other owner with explicit public provenance are allowed, and
the company namespace is refused. An explicit `public` entry names its source.
A local file entry must name the root it lives under; the path is normalized
against that root with strict symlink resolution, so a traversal or an escaping
symlink refuses, and a credential or secret path is refused by name before any
target is resolved or opened. Omitted, unknown or mixed provenance refuses:
`input_provenance_required`, `repo_binding_required`, `company_private_namespace`,
`public_provenance_required`, `path_escapes_root`, `credential_path`,
`classification_required` and their peers name the refusal.

The retrospective report (`outcomes.free_route_trial_retro`) carries the trial's
enforcement contract: the close boundary (`enforcement_boundary`) and the
scheduler trigger (`scheduler_trigger`) that a retrospective run uses to find
overdue tasks. The trigger is a deterministic, idempotent `due` signal; it starts
no recurring paid work and needs no webhook or secret.

For a qualified operation, `route` and `resolve` prefer the free route without
`--experimental-route`, without exact overrides and without a reason, and fall
back to a paid arm whenever a gate fails. The caller must still declare a
checked proof class and pass a structured `--input-provenance` manifest and
`--data-classification public`. A caller-pinned provider, model or account
override disables the preference and is authorized only when the same gates
hold; an effort override does not disable it. The separately authorized
`route fanout.explore` and `resolve --no-op` paths retain catalog-validated
efforts. They admit no other operations. Generic governed destination
submission refuses. Historical replay rechecks current eligibility using the
recorded proof class and input manifest; revocation suppresses targets and
submission instructions on early and locked append replay without rewriting the
historical ledger.

A recorded free-route decision must carry a `task_link`, because the trial
counts unique consumer tasks rather than receipts. Every free-route result also
requires an explicit close (`accepted`, `accepted_after_repair` or `rejected`),
an explicit repairs count and a later rework observation. A new free-route close
is refused without `--repairs`, and a judged close is refused unless it records
`--check independent_judged --judged-by independent`: the trial requires an
independent checker as the judge, not the maker. Closing with `--task-result` is
a workflow-completion claim that is refused while a sibling decision of the same
consumer task still owes a close; every free-route close records its task's
completion state as of that close. These checks gate new closes only, so a
historical close row still replays byte-identically. Absent later observation is
reported as `unknown`, never as no rework, and no provider terminal status closes
a task on its own.

Public classification, proof class and the input manifest are caller
assertions, not content scans, proof certification or a sandbox guarantee.
They do not prove repository visibility, prove that credential paths are the
only sensitive files, or give any runtime or OS isolation. `--sensitive` always
refuses the route, and `--inputs github` does not establish public contents.
`expires_at_utc` is a local recheck deadline, not a vendor end date. Missing,
invalid, future-dated or expired source evidence refuses dispatch.
The data policy retains upstream zero-retention/no-training attribution
separately from OpenCode's unpaid-content service-improvement caveat.
Shared provider host tools and injected `t3_thread_read` private-thread access
remain known risks; provider sandbox and MCP isolation remain later hardening.
`cost_usd` of 0 is a zero source fee only: provider usage, checking effort and
complete workflow cost remain unknown.

## `op-requirements.json` (DRAFT — owner review pending)

The recommender reads this policy and the catalog; it never edits `operating-points.json`.
JSON Schema: [`schemas/op-requirements-v1.schema.json`](schemas/op-requirements-v1.schema.json).

`task_families` is one op-to-family table. Each tagged metric in `metrics.json` is
selected by family; ops do not name metric ids. Current families are `coding`,
`agentic`, `research/browsing`, `knowledge/factuality`, `reasoning`, and
`computer-use`. A metric without an unambiguous family tag is not selected.
Rows are compared only within their `comparability_group`.

### Expected cost model

For success probability `p`, per-attempt model cost `c`, review overhead `h`,
detection probability `d`, and silent-failure cost `S`, `E` is expected cost
**per task**. A detected failure escalates to a measured, higher-scoring arm
in the same board. Its expected cost is `E_next`:

`E = c+h+(1-p)×(d×E_next+(1-d)×S)`

The fallback is the higher-scoring arm with the lowest expected cost. If no
measured escalation exists, same-arm retry is allowed only when the row has
`pass_at_k`; its conditional success rate is calibrated from `pass@1` and
`pass@k`. Otherwise the attempt ends and `E=c+h+(1-p)×S`, even when `d>0`.
This avoids treating repeated failures on one task as independent trials.

`accuracy`, `pass_rate`, and `error_rate` are fractions in 0..1; an error rate
converts to `p=1-rate`. Elo, indices, and partial-credit scores do not produce
E. They cannot exclude a model. A group with only one candidate arm cannot
exclude a model either. Where a row supplies `ci_lo`/`ci_hi` or `n`, the
recommender carries a 95% success interval into an E interval. Overlapping E
intervals are reported as ties. If the current `expands_to` is tied on an
independent board and no other independent board contradicts it, the result is
`RECOMMENDED=current`; missing comparisons still block moves. Other ties use
point expected cost per task, then newer model.
If `n` is present without CI, a Wilson interval is used. Published `pass_at_4`
and general `pass_at_<k>` fields calibrate same-arm retry alongside `pass_at_k`.

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
| `implement.oracle-bounded` | $5 | Same loss as quota-tight patches. Its detection probability is 0.95, because a deterministic oracle decides success. |
| `review.audit` | $100 | A missed problem can pass silently through the checker. |
| `advisor.deep` | $50 | Wrong advice can steer later work. |
| `recover.report`, `fanout.explore`, `fanout.dollar-tight`, `docs.lookup`, `ui.computer-use`, `grok.explore-only` | $2 each | Lower assumed loss for a missed factual, exploration, lookup, or UI task. |

### Candidate and evidence rules

The recommender preserves GA, tier, newest-in-tier across provider arms, access,
effort, and CLI-surface filters. The default provider list names:
`claude`, `codex`, `grok`, `antigravity`, `qwen`, `deepseek`.
The single `lane_scoped_freshness` flag defaults to `false`; changing it to
`true` would retain an older model when it is newest only on its own lane.
Freshness compares `models.json.released` when both models have dates, then
falls back to version parsing. Restricted models do not displace accessible ones.
`defaults.provider_restrictions` limits Grok to `grok.explore-only`, and that
op explicitly sets `allowed_providers=["grok"]`; it cannot recommend Codex.
A catalog model may produce more than one lane arm. The lane's CLI effort
surface takes precedence; missing CLI surfaces fall back to `api` and are
flagged. Antigravity dispatch ids use one effort arm; a dispatch without an
effort suffix uses medium when allowed.
`xhigh`, `max`, and `ultra` require an explicit override reason.

Vendor charts choose efforts only among the publisher's own models. A model
choice needs an independent cross-model board on which the arm meets the
incumbent and every rival it beats. An independent board that picks a rival
counts as a disagreement even if the arm is absent. Missing model/board pairs
are reported. A measured candidate absent from the deciding board blocks a
move until it has an independent comparison with the proposed arm. Independent
board rows with unknown effort remain visible: if their model-level ranking
contradicts an arm, the disagreement blocks that arm. A row explicitly marked
`effort_convention: vendor_default` uses the catalog API default effort with a
caveat. An independent success-rate group excludes an unmeasured allowed
effort only when its best measured effort is both lower-scoring and no cheaper
than a measured candidate. Vendor groups only flag this. `review.audit` excludes
both makers' **vendors**, and shows maker and checker success rates side by side
where available. If a maker is unresolved, its current `expands_to` supplies
the vendor constraint. This vendor choice for `review.audit` is
`OWNER_DECISION_PENDING`: the owner must decide vendor versus model-family
independence. The recommender keeps the current vendor constraint meanwhile.

The default policy horizon is 90 days from the pinned `defaults.price_as_of`
(initially the pack's `generated_at` date); `--price-as-of` overrides it.
The recommender uses the price valid at the horizon end. It scales each
benchmark task cost from the rate valid on that row's `observed_at` to the
horizon rate, including when `price_as_of` is after a promotion. It scales only when all
published token rates change by the same factor; otherwise the future task
cost is unknown and that arm cannot win on E. Pricing rows can record future
rates in `post_valid_until`. `--availability` reads a `clawmeter status --json`
snapshot and excludes arms whose relevant lane quota window is exhausted.
Token counts are shown per task on quota lanes when rows provide structured
counts and a task count.

### Assumption sensitivity

The dependency graph is rerun on the full overhead ($0.10–$2) × detection
(0.25–0.95 for verified ops) × silent-failure-cost (0.5–2×) grid. Separate
one-factor sweeps cover independent and vendor source weights (0.5–2), vendor
factor (0.25–1), majority threshold (0.4–0.6), and high-confidence group count
(1–3), plus tie rule, price date, and policy horizon. `CLEAR` requires the same recommendation **and confidence** in every run.
The output names the first changed assumption and outcome; it does not claim
a precise flip threshold between sampled values.
