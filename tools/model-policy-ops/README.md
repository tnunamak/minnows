# model-policy-ops

Read the selected model-choice-policy pack, make an explicit delegation decision
(with `resolve`, or with `route` to apply quota, availability and directives),
record how it ended, and audit its use. This CLI never dispatches work or changes running threads.
`check`, `list`, and `show` keep their existing output contracts.

## Explicit consumer tasks and inspectable choices (v1)

These are optional, additive top-level fields on the existing schema-version-1
receipts and closes. They do not change routing, ranking, targets, dispatch,
judgment, or legacy per-decision reports, and create no new ledger.

Choose a consumer task ID yourself and keep it stable through planning, maker,
checker, repair, escalation, and workflow replans. A T3 child taskId is not the
consumer identity. Workflow and stage IDs describe the current attempt; roles
are planning, maker, checker, repair, escalation, rework, or other. IDs are
1-128 letters, digits, dots, underscores, colons, or hyphens, starting with a
letter or digit. No historical task identity is inferred.

Add the following flags to a normal resolve or route command:

```bash
model-policy-ops resolve implement.standard \
  --available capabilities.json --reason 'Implement; check against the task standard' \
  --record --decision-id t1-make \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD \
  --task-link '{"v":1,"consumer_task_id":"t1","workflow_id":"w1","stage_id":"make","role":"maker"}' \
  --choice '{"v":1,"reason":{"code":"complete_workflow"},"workflow_candidate":"maker-checker","considered":[{"kind":"workflow","candidate":"maker-checker","disposition":"selected","reason":{"code":"independent_check"}}]}'
```

Every fresh upgraded receipt gets choice v1, including when --choice is omitted.
Its considered entries reference the existing evaluated routing trace, final
override, destination consideration, or resolve selection/alternatives through
JSON pointers. Unexamined models are never enumerated. Dispatch arms, destinations,
and caller-considered workflows are distinct kinds; an unvalidated trace basis
stays unvalidated. Reasons describe the existing decision, not a new policy.

The bounded --choice JSON requires v:1 and reason:{code,detail?}; it optionally
takes workflow_candidate, considered (at most 16 unique workflows), and schedule.
Each caller considered item has exactly kind:"workflow", candidate, disposition
(selected, selected_waiting, not_selected, or rejected), and reason. It may add
predicted_total_cost_per_accepted_result:{state:"unknown",value:null,reason:"..."}.
This version permits only unknown predictions. Generated entries have that same
unknown/null structure. Token prices and quota headroom are not complete-workflow
cost or quality evidence. Reason codes are IDs; detail and unknown-cost reason
are single lines of at most 280 characters. JSON has an 8192-character limit,
exact allowed keys, and rejects duplicate keys.

To record a parent decision to wait for busy dot, --choice may include
schedule:{"state":"selected_waiting","candidate":"dot","reason":{"code":"busy"}}.
Schedule states are immediate or selected_waiting. This is a caller claim:
old launchers do not enforce it, the parent must withhold dispatch, and there is
no queue mechanism. The underlying model dispatch recommendation is unchanged.
Compact output exposes the claim under `waiting` and sets `dispatch` to null.
With either output format, check this claim before using any launch recipe below.
Explicit workflow_candidate/schedule names also appear among considered workflows.

The exact supplied choice is kept in choice.caller; derived entries and source
pointers are outside it. Supplied extensions must match their stored caller
metadata on replay, including the locked append-race path. Omitting new flags
replays old or new receipts unchanged apart from the existing replayed:true
response flag. Supplying metadata missing from a historical receipt rejects;
replay never retrofits task_link or choice. Neither field is put in request,
caller_intent, launch_facts, or evidence entries.

### Explicit whole-result close

An ordinary accepted maker or checker close still counts zero accepted consumer
tasks. Only an explicit --task-result declares that this close's existing
judgment covers the whole consumer result at the referenced acceptance standard.
The receipt must already have a matching task_link. For example, use existing
evidence formats and reference their zero-based indices:

```bash
model-policy-ops close t1-make --close-id t1-final \
  --outcome accepted --judged-by parent --check independent_judged \
  --evidence '{"type":"file","path":"/absolute/path/acceptance-standard-and-review.txt","sha256":"REPLACE_WITH_ACTUAL_64_CHARACTER_SHA256"}' \
  --task-result '{"v":1,"consumer_task_id":"t1","supersedes_task_close_id":null,"standard_evidence":[0]}'
```

Replace the file/hash with real evidence; existence or a matching file hash does
not prove acceptance. The new marker is an explicit caller judgment, not an
automatic acceptance check. All four keys are required, no extras are accepted,
and standard_evidence must be a nonempty list of unique valid evidence indices.
The marker has a 2048-character limit and never adds keys to evidence entries.

To correct a task result, supersedes_task_close_id must name its unique current
task head. It can cross decisions linked to the same consumer task. A correction
on a previously closed decision also requires ordinary --supersedes and --reason;
ordinary supersession remains strictly per decision. The outcomes exclusive lock
rejects duplicate declarations, forks, missing targets, and cross-task targets.
A legacy CLI's untagged successor invalidates task acceptance rather than
inheriting the marker. It becomes the recoverable task head; a later explicit
declaration can supersede it. Omitting --task-result when replaying a close keeps
the original close; supplying a changed or historically absent marker rejects.

The Python helper outcomes.task_projection(receipt_rows, outcome_rows) returns
per-task state, current_close_id, terminal_judgment, accepted_count, and total_cost,
plus the unique accepted-task total. It follows links, never newest timestamps.
Accepted and accepted_after_repair whole-result claims count one; other judgments
count zero. Absent, malformed, conflicting, or forked task metadata is unknown.
Legacy decision/outcome readers keep their existing semantics. The projection
does not rewrite history, and every total_cost remains null because parent,
native, planning, and other coverage gaps prevent complete cost accounting.
It is not wired into the legacy report formatter.

## Read-only decision reconciliation

The new report is a launch-cohort reconciliation of delegated decisions, not a
whole-task cost or success report. It never dispatches, observes live services,
records a close, changes a receipt, or strengthens a judgment.

```bash
model-policy-ops report --receipts /private/state/decisions.jsonl \
  --outcomes /private/state/outcomes.jsonl --db /private/t3/statev2.sqlite \
  --report-dir /private/reports/2026-10-15
# Or use --export /private/exports/audit.json instead of --db.

```

Exactly one of --db and --export is required. --observations defaults to
observations.jsonl beside the receipts, and --outcomes defaults to outcomes.jsonl
there. Receipts, outcomes, and the selected DB/export are mandatory, even when
empty. Missing or malformed optional observations are explicitly unavailable.
Malformed mandatory input fails before publishing artifacts.

--since accepts a timezone-bearing ISO timestamp. The default cohort starts
14 rolling days before collection, includes the lower bound and excludes the
upper bound (`[since, as_of)`), and uses the
receipt's recorded_at (UTC launch day), not call or close time. All current
closes are considered, including late closes. An explicit superseding rejection
removes an earlier acceptance. Completed runs and destination acceptance events
never create a decision close. --record and --now are refused; report accepts no
op or --thread. There is no simulation or source-ledger write path.

The report parent must already exist, be owned by the current user, and not be
writable by others. --report-dir must be new and outside every source directory.
Symlink traversal, existing destinations, aliased source files, and overlapping
input/output directories are refused. A private adjacent staging directory
publishes reconciliation.json and daily.md together by rename, with directory
mode 0700 and file modes 0600. A new directory is required for each report; old
reports are never overwritten or cleaned up. Stdout is only a small summary,
artifact paths, and coverage.

Provenance and interpretation:

- Each ledger/export is read once under a shared lock. Existing readers parse
  private immutable copies of those exact bytes; their SHA-256 hashes include
  whitespace. They never reopen a live ledger after hashing it.
- The DB reader uses its existing read transaction. Its hash is explicitly a
  canonical hash of the projected transaction, not a DB-file or WAL hash. No
  raw database copy is made. Collection metadata, cohort bounds, schema/report
  version, and the T3 counter-interpretation version are explicit.
- Rows use the top-level receipt target. A dot destination's selection.arm is
  only a model fallback. Caller account hints are never reported as known
  dispatch accounts. Model versions and supported requested options retain
  exact identifiers; unknown option surfaces are counted as dropped. Served
  models remain unattested. Prompts, titles, reasons, notes, and evidence text
  are excluded from artifacts.
- A usage unit is source namespace plus provider_turn_id, not token values.
  Duplicate joins count once; distinct turns with equal counters both count.
  Conflicting decision/run/attempt ownership or contradictory authoritative
  snapshots are retained as conflicts and excluded from attribution. Missing
  identities remain gaps. Historical usage observations are never a source
  revision and never substitute for current DB counters.
- At this base, audit export projects lifecycle/configuration but no turn usage.
  Export-backed report usage is therefore explicitly unknown_not_in_export.
  Use a DB projection for unique-turn usage. Extra export usage fields are not
  silently treated as an extension to the audit contract.
- The interpretation uses the inspected current Claude/Codex adapter version
  v0.0.46-nightly.20261014.3105: input includes cache reads and cache creation;
  fresh input is input minus those caches, and output already includes reasoning.
  Invalid partitions remain invalid, and missing fields are never zero-filled.
  Unknown provider semantics do not gain derived totals. Codex turn counters
  already include resets; the report never sums historical snapshots. This is
  an interpretation assumption, not attestation of the producing runtime for
  historical turns. That runtime and historical applicability remain unknown.
  Provider-instance IDs do not establish driver identity: custom instance
  mappings remain unknown rather than guessed from names.
- Only explicitly main-agent scope enters main-agent token aggregates and derived
  totals. Missing or unrecognized scope retains raw reported counters per turn,
  but contributes missing values and explicit scope-unverified coverage there.
- Complete main-agent usage does not mean complete nested or procedure usage.
  Parent overhead and native subagents remain unknown/unallocated. Explicit
  relaunch, escalation, and maker edges are nonadditive decision views, including
  ancestors outside the cohort; they never become task totals.
- Daily rows group UTC launch day, op, and destination. They retain all failed,
  repaired, and rejected attempts, current close/judge/check/repair missingness,
  known token components, coverage and conflicts. A zero accepted denominator
  does not produce a zero-dollar ratio. Receipt-to-close latency is labeled
  decision latency. Available historical run timestamps are labeled as such;
  parallel run durations are never summed into wall time.
- Pricing scenarios are deliberately not implemented. USD, credits, task cost,
  and task accepted-result counts remain null. A token price or benchmark prior
  cannot establish an invoice, subscription allocation, historical billed rate,
  or predicted task cost. Missing price bindings/regimes are explicit gaps.
- **Derived outcomes.** Each unclosed row has `derived_outcome`: a rule over recorded facts, never a close and never
  mixed into `decision_outcome`. First match wins: a later receipt escalates from it with `oracle_failed` or
  `judged_insufficient` -> `rejected` (`escalated_for_quality`); the destination recorded an `accepted` event whose
  actor kind is `owner` -> `accepted` (`destination_owner_accepted`); in the latest linked-PR snapshot of the child
  thread a PR is merged and no other decision ran in that thread -> `accepted` (`pull_request_merged`). Otherwise
  `unknown` with a reason: `pull_request_thread_shared`, `pull_requests_unreadable`, `pull_request_closed_unmerged`, `relaunched`,
  `run_failed`, `run_cancelled`, `run_interrupted`, `completed_no_disposition`, `no_child_run` or `no_facts`.
  Repairs stay `unknown`: a follow-up message to the same child is not a repair round (on 2026-10-15 the counts agreed
  with the parent's recorded `repairs` in 70 of 127 closes, because parents often repair through a new child), so a
  derived outcome is never `accepted_after_repair`. A judged close always replaces the derived outcome.
- **Outcomes by op and arm.** `by_arm` (and the last table in daily.md) groups the cohort by op, destination, model
  and effort; accounts are counted, not split. It shows judged and derived outcomes apart, `unknown` with reasons,
  `accepted` (judged accepted or accepted_after_repair, or derived accepted), the first-time share of judged
  acceptances, token sums, tokens per accepted (every attributed attempt, failed and rejected ones included, over
  accepted results; null when none) and the median run time (one duration per child run). Descriptive and confounded by task mix.
- With the same pinned inputs and cohort bounds, reconciliation is deterministic;
  collection timestamps are separate metadata.

Offline verification: tests/test_cost_report.py uses temporary fixture ledgers,
sanitized exports, and SQLite projections only. It never launches a model or
contacts a service.

The DB path currently loads all delegation history and hydrates usage per call.
Small fixtures do not establish scalability; measure the actual collection run
before scheduling it. Unknown usage scopes, named-instance driver mappings and
historical adapter versions remain coverage limits, not zero usage.

## Procedure

1. Choose an op from `list`, based on the task and its verification boundary.
2. Run `resolve`. Read `policy_context`, evidence, gaps, account choices, quota,
   and alternatives. Choose an account explicitly when more than one matches.
3. Record the decision with caller-supplied parent metadata and a unique decision
   ID. If `choice.warning` (or compact `waiting`) asks to withhold dispatch, wait.
   Otherwise pass the returned `target` unchanged and `decision_id` as
   `delegate_task.clientRequestId`. Supply the task separately to T3.
4. When you integrate or discard the result, run `close` (see Outcomes). Later
   rework goes in `followup`.
5. To let the tool pick the account and handle quota, availability, checker
   independence and owner directives, use `route` instead of `resolve` in step 2
   (see Route). `route` makes the same kind of receipt.
6. Run `audit` for the parent thread or a time range. Inspect unmatched calls,
   mismatches, missing effort, missing closes, and unknown native evidence.

From a clone, use `tools/model-policy-ops/model-policy-ops`. Installation supplies
`model-policy-ops` on PATH. Every policy command reads
`$DATA_PACKS_HOME/model-choice-policy/operating-points.json` when set, otherwise the
clone's pack. `--policy FILE` selects another pack. No model table is copied into
instructions. `policy_sha256` hashes canonical JSON of the full selected policy;
`catalog_ref` is the policy's pinned catalog reference, not a runtime catalog hash.
`resolve` also reports `policy_path`. The legacy `check`, `list`, and `show`
outputs have no extra metadata.

```bash
model-policy-ops list
model-policy-ops resolve implement.standard \
  --available auto --quota auto --account-hint ACCOUNT_INSTANCE \
  --quota-provider QUOTA_PROVIDER --quota-source QUOTA_SOURCE \
  --reason 'Task fits this op; tests and independent review decide acceptance' \
  --decision-id task-unique-id --record \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD > decision.json
```

`ACCOUNT_INSTANCE` must equal a T3 provider instance ID in the supplied catalog.
`QUOTA_PROVIDER` and `QUOTA_SOURCE` must equal clawmeter IDs. The caller supplies
this account-to-quota mapping; the tool does not guess from labels or paths.
Omit `--quota-source` for providers with no `sources` array. No account is selected
by quota headroom. Selecting an ambiguous account with `--account-hint` resolves
identity; changing an established choice with `--override account=...` requires a
reason.

The parent calls the existing T3 `delegate_task` with these exact fields:

```python
request = {
    "task": task_brief,
    "target": decision["target"],
    "clientRequestId": decision["decision_id"],
    "mode": "async",
}
```

Do not submit a null target or a decision whose `choice.warning` (compact `waiting`)
requires withholding dispatch. `launch_ready` means the exact model and explicit
supported effort are present in a runnable catalog account. It does not attest
authentication, quota, or suitability. An unresolved choice has `target: null`.
Alternatives are linked policy arms, with their complete contexts; they are never
substituted or launched automatically. Confidence is the pack's ordinal evidence
or prior assessment, not a success probability or a claim of optimality.

## Runtime inputs and explicit exceptions

`--available FILE|auto` accepts the `t3code --json models list` envelope
(`ok`, `data.providers`, `instanceId`, `driver`, `models[].slug`,
`options[].values`) or an `orchestrator_capabilities` object/`structuredContent`
(`providers`, `providerInstanceId`, `driverKind`, `models[].id`,
`options[].options`). Exact IDs are used; aliases are not guessed. Effort keys and
allowed values come from the selected model's option surface. Disabled or
nonrunnable accounts/models cannot produce a target. Unknown schema, missing
files, malformed JSON, command failure, and source-declared stale state are
explicit. Auto executes only those local CLIs, with a 30-second timeout per source.
It makes no model API request.

`--quota FILE|auto` accepts `clawmeter --json`, including
`providers.<id>.sources[].source.id` and nested `usage.windows`. Source/provider
IDs remain intact. Relevant all-model windows (`5h`, `7d`, `7d All`) and a matching
named tier window are separated from other tiers, bonus, and monetary extra usage.
Only a relevant reported utilization of at least 100 is called exhausted.
Expired reset times are stale. Unknown windows remain informational, not binding.
Fetched timestamps and source state are reported; there is no invented freshness
age cutoff, price estimate, near-floor threshold, or headroom allocation.
Absent or stale quota remains a caller judgment and does not change the model.

```bash
model-policy-ops resolve implement.standard --available capabilities.json \
  --override provider=PROVIDER --override model=MODEL --override effort=EFFORT \
  --override account=ACCOUNT_INSTANCE --reason 'Bounded task; independent review'
model-policy-ops resolve --no-op --available capabilities.json \
  --override provider=PROVIDER --override model=MODEL --override effort=EFFORT \
  --reason 'Existing ops do not describe this task'
```

Overrides accept only `provider`, `model`, `effort`, and `account`. All overrides
and `--no-op` require a nonblank reason. `xhigh` and `max` require a reason even
when selected by the pack. Parent model, provider instance, and thread are always
caller-supplied; unrelated environment variables are not evidence of the parent.
Reason and handoff text must be short explanations, without prompts or secrets.

### Free routes (normal declaration)

The DeepSeek V4.1 Flash Free zero-price route is declared under the pack's root `free_routes`. The retired
`experimental_free_routes` key is a pack error. `route` adds the route itself as a candidate, with no override and no flag, when the
caller asserts `--data-classification public` for one of its qualified operations with the qualified proof class and
the evidence is current (see "Qualified free candidates" in the contract). Otherwise it is usable only through exact
provider, model, effort and account flags with a reason. `--data-classification public` is required; omission
means unknown, and `--sensitive` always refuses the route. `--inputs github` does not classify its contents. The
route is bound to OpenCode's `opencode` provider and account plus `opencode/deepseek-v4.1-flash-free`; other OpenCode
models and provider/account aliases do not inherit its zero-cost metadata. The effort option key is read from the live
T3 catalog and remains `variant`. The `--experimental-route` flag is accepted only so that historical request shapes
still compare equal; a fresh request that uses it is refused. A receipt recorded under the retired contract is refused
on replay with `experimental_route_receipt_not_currently_authorized`, and no receipt is rewritten.
`fanout.explore` and `resolve --no-op` no longer authorize the DeepSeek route.

This model-arm path validates and emits the actual effort option, such as
`options.variant: high`. Generic destination submission does not support governed
OpenCode routes: its options-free target cannot carry that validation. A chosen
governed destination refuses even with exact public overrides, and
even when its configured target is absent from the catalog. Refusal removes all
targets, dispatch metadata, `submit`, `how_to`, and manual submission readiness
advice from full and brief output, including nested copies and receipt replay.
Receipts retain descriptive governed identity for current-policy checks; that
identity does not authorize submission. Ordinary destinations keep their existing
dispatch and manual submission behavior.

`public` is the caller's assertion, not content scanning or attestation. Missing
or invalid expiry evidence, and evidence at or beyond its local UTC recheck
deadline, refuses dispatch. The seven-day deadline is local policy and does not
claim a vendor end date. Undeclared OpenCode models with a `variant` option are
refused; this metadata does not make paid or other free OpenCode models
launchable.
The route qualifies exactly `implement.oracle-bounded` and `docs.lookup`, each
with `--proof-class oracle` and `--override effort=high`. Implementation covers
small PUBLIC source changes whose full acceptance standard has a deterministic
oracle. Lookup covers known PUBLIC sources with a structured fact oracle;
open-ended research and judged or absent proof are excluded. High is the only
benchmark-tested effort. Primary operating-point recommendations stay unchanged.
The exploratory paths retain other catalog-validated efforts, without admitting
other operations. Historical replay uses recorded proof facts and current
qualification; removed operations or changed proof eligibility refuse dispatch
on both replay paths without rewriting stored receipts.

The pack includes [sanitized benchmark evidence](../../data/model-choice-policy/evidence/deepseek-public-eval-2026-10-15.json):
eight task pairs from six source contexts, 16 deterministic gate passes and one
review inspection-scope failure. No repairs or throttles were observed. Rate
limits, measured provider cost and total workflow cost remain unknown; the $0
fee is temporary source pricing. These visible public tests establish only the
bounded admitted scopes, not general review or fleet quality.

Public classification and proof class are caller assertions, not oracle
certification or a runtime sandbox guarantee. Shared OpenCode server host tools
and injected `t3_thread_read` private-thread access remain known risks. Provider
sandbox and MCP isolation checks remain later hardening. The router does not
change shared T3 or provider settings. The data-policy record retains the upstream
zero-retention/no-training attribution separately from OpenCode's unpaid-service
content-improvement terms.

```bash
./tools/model-policy-ops/model-policy-ops route implement.oracle-bounded \
  --policy data/model-choice-policy/operating-points.json --available auto \
  --proof-class oracle --data-classification public \
  --override provider=opencode \
  --override model=opencode/deepseek-v4.1-flash-free \
  --override effort=high --override account=opencode \
  --reason 'Small public patch with a full deterministic acceptance oracle' \
  --decision-id deepseek-public-exploration-001 --record \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD
```

Do not dispatch if the result is refused or `target` is null. A sensitive
negative check adds `--sensitive`; the result must have `launch_ready: false`
and `target: null`.

## Receipts and escalation

Default resolve is read-only. `--record` requires a caller decision ID plus all
three parent metadata flags. Receipts default to
`$XDG_STATE_HOME/model-policy/decisions.jsonl`, or
`~/.local/state/model-policy/decisions.jsonl`. Use `--receipts FILE` to choose a
dedicated private state directory. Newly created receipt directories have mode
0700 and the file 0600. Existing parent directory permissions are preserved.
Recording refuses a group- or world-writable parent directory.
An exclusive lock protects append, identity checks, and escalation uniqueness.
Each write is flushed and fsynced. Receipts contain decision metadata and policy
context, with source hashes; no raw runtime paths, runtime source objects, or task
prompts are stored. They are local audit records, not tamper-proof attestations.

Repeating an ID with identical caller intent returns the original receipt with
`replayed: true`, including its original policy, target, availability, and quota
snapshot. Runtime source refresh does not change identity; replay does not fetch
sources again or modify the stored receipt. Caller intent includes the op,
overrides, account, quota mapping, reason, parent, and lineage fields. Changed
intent rejects reuse. A changed pack affects new IDs; an old ID stays pinned even
when its op is removed. To refresh a decision, use a new ID.

Escalation uses an explicit target op from the source receipt's `escalate_to`
links. Link order makes no claim about relative strength.

```bash
model-policy-ops resolve TARGET_OP --escalate-from ORIGINAL_DECISION_ID \
  --why oracle_failed --reason 'Describe the failed oracle' \
  --handoff-boundary 'New child; supply failing test and current diff' \
  --available capabilities.json --account-hint ACCOUNT_INSTANCE \
  --record --decision-id ESCALATED_DECISION_ID \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD
```

Quality escalation accepts `--why oracle_failed` or `judged_insufficient`.
Unavailability is not a quality signal. Only one linked escalation is allowed
from a recorded source, including its relaunch lineage; a linked child cannot
escalate again. Relaunching does not reset that limit. Further attempts require a new explicit reasoned override based
on parent judgment. Same-model/same-effort quality retry is not escalation.
Escalation selects a policy arm, so overrides cannot be combined with it. The
handoff names a new task boundary; the CLI does not edit or inspect a running task.

For an infrastructure, configuration, or availability relaunch, use a new
decision ID with `--relaunch-of EXISTING_DECISION_ID`, `--why infra|config|unavailable`,
and a nonblank `--reason`. The source receipt must exist and cannot be the new ID.
This records lineage without promotion or a quality escalation claim. Choose the
op and any reasoned overrides explicitly. Relaunch cannot be combined with
`--escalate-from` or `--handoff-boundary`. Original receipts stay unchanged.

## Route

`route` makes the decisions that a parent repeats by hand: which account serves the
model, which vendor substitutes when an arm is blocked, which model may check a
maker's work, and which owner instruction applies now. It reads local data, ranks
the candidate pairs with fixed rules, and explains each step. It does not dispatch
work and it does not learn from outcomes. The full spec is
[route-contract.md](route-contract.md).

### Procedure

1. Run `route` in place of `resolve`. Pass the same recording flags.
2. Check `choice.warning` (compact `waiting`) first and withhold dispatch when it
   records a waiting claim. Otherwise read `routing.judgment_required` and `routing.judgment_reasons`. When it is
   `false`, pass `target` unchanged to `delegate_task`, with `decision_id` as
   `clientRequestId`. When it is `true`, you decide. Accept the target, or use
   `--override ... --reason`. A recorded override keeps the routed choice and the
   final choice.
3. For a checker, name the maker: `--maker MAKER_DECISION_ID`, or
   `--maker-model PROVIDER:MODEL` for direct or native work.
4. Close the decision as before.

```bash
model-policy-ops route review.audit \
  --available auto --quota auto --db ~/.t3/userdata/statev2.sqlite \
  --maker MAKER_DECISION_ID --proof-class judged \
  --decision-id task-unique-id --record \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD
```

`route` takes an op ID only. It refuses `--no-op`, `--escalate-from`,
`--handoff-boundary`, `--account-hint` and `--quota-provider/--quota-source`. The
account map names the quota source, and `--override account=...` changes the account.
`--relaunch-of` works as in `resolve`. `--now RFC3339` evaluates directive expiry, the
failure lookback and quota staleness at that time, as a dry simulation. A route with
`--now` cannot be dispatched: `--record` with `--now` fails before any write, and the
output has every `target` key null (including `routing.chosen` and `routing.final`), `launch_ready: false`, the routed target in `simulated_target`,
and `simulation` (`now`, `real_clock_at_evaluation`, `note`). A stored receipt is never
replayed in a simulation. `--now` is read-only: `directive list` and `calibrate` accept
it; every other command (`resolve`, `close`, `followup`, `audit`, `directive add|end`)
refuses it, because they write state with the real clock.

### Implementation proof declarations

Every fresh `route implement.*` requires an explicit `--proof-class oracle|judged|none`,
including unrecorded routes and `--now` simulations. Explicit `none` declares that no
proof is being claimed; leaving the flag out is unknown and is refused.

Here `oracle` means a deterministic check can wholly accept or reject the bounded
work. Passing some tests does not establish that they cover the whole acceptance
standard. Use `implement.oracle-bounded --proof-class oracle` for that bounded case.
`implement.standard --proof-class oracle` is refused with guidance, never silently
swapped to another op or model. If checks cover only part of the standard, declare
`judged`. For deliberate broader, long-running or security-sensitive oracle work,
use explicit `resolve` with a `--reason` explaining why the selected op fits.
`implement.oracle-bounded` refuses `judged` and `none`; `implement.standard` accepts
both declarations. Other implementation ops require a declaration without gaining
an automatic classification or new model ranking.

This validates the declared task fit, not proof adequacy or whether the chosen
workflow is cheapest. `resolve` and linked escalation retain their existing rules.
An exact stored receipt replay returns the original, even with a missing or now
mismatched declaration. The guard runs after that replay check and before runtime
availability/quota reads, probes or quota/receipt writes. Unknown and nonimplementation
ops retain their existing behavior. Request and launch-fact shapes stay unchanged.

### Output

`route` prints one JSON object. It is a valid `resolve` receipt (`target`,
`selection`, `launch_ready`, and the other fields), plus these additions:

- `request.route` (`true`), `request.maker`, `request.maker_model` and
  `request.independence`.
- `routing`:
  - `router_version`, `params` (the pack `routing` object) and `params_sha256`;
  - `independence`: `required` level, `required_source` (`flag`, `pack` or
    `mechanism_default`) and `maker` (`provenance`, `vendor` `{value, basis}`,
    `requested`, `served_model: unattested`). A maker whose decision ID started several
    child runs of different instance or model has provenance `unknown`, issue
    `multiple_child_runs` and a `runs` list, and the checker differs from all of them;
  - `directives_applied`, `directives_unmatched` (active directives that matched
    no pair, often a typo), `directives_rejected` (ID and reason of a live row that
    lasts longer than `directive_max_days` or was recorded after the evaluation time;
    it is ignored) and `conflicts`;
  - `rank_keys`, and `trace`: one row per candidate and account, with the filter
    that removed it (`removed_by` and `detail`), or its `rank` keys. Each row
    shows `quota` (`pace`, `pace_reason`, `exhausted`, `projected_pct` and a `ref`)
    and `availability`. The full quota view (state, `fetched_at`, `cache`, windows
    with `resets_at`, `remaining_pct` and `projected_pct`, the binding window) is
    once per account in `routing.quota`, under that `ref`;
  - `chosen` (or `null`): `arm`, `instance`, `target`, `basis`, `selection_basis`,
    `evidence`, `prior`, `known_gaps`, `authorized_by`, `pace`, `quota_binding`;
    when quota substitution replaced the best pair, `selection_basis` is
    `quota_substitution_exception` (see Quota substitution);
  - `user_policy`: `{path, resolved_path, sha256, keys}` when a per-user overlay applied, else `null`
    (see User policy overlay);
  - `quota_substitution`, only when the pack or the user policy enables it: the constrained accounts, the
    thresholds, and the substitution if one happened (see Quota substitution);
  - `drain_accounts`, only when the pack or the user policy sets `routing.drain_accounts`: each entry with
    `applies`, `why` (`active`, `threshold_reached`, `expired` or `current_use_unknown`), `current_pct` and the
    entry's `reason`, and `chosen_drained` (see Drain an account);
  - `judgment_required` and `judgment_reasons` (`code`, `detail`, `applies_to`
    `routed` or `final`, and `informational` for the stale-pack note). After an
    override, `judgment_required` follows the final pair; the routed reasons stay listed;
  - `routed` and `final`: the router's choice, and the choice after overrides.
    `final` runs the final pair through the same filters and records `billing`,
    `filters_bypassed` (each filter that the pair would fail), `pace` and
    `quota_ref`. An override stays allowed. A bypass of the billing, `avoid` or
    exhaustion filter adds the judgment reason `final_metered_without_directive`,
    `final_avoided` or `final_quota_exhausted`. A bypass of the runnable, `requires` or
    independence filter adds `final_not_runnable`, `final_requires_not_met` or
    `final_independence_bypassed`. The final pair also gets the routed-pair rules (basis,
    pace, `auth_config`, independence provenance). A final pair with no target adds
    `final_target_null`. A final pair on a quota-constrained account adds
    `final_account_quota_constrained`;
  - `evidence` (label `descriptive_only`), `calibration_notes` and `inputs`.

The same inputs give the same output, apart from `recorded_at` (and a random
decision ID when you give none). A replay with the same `--decision-id` returns the
stored receipt and does not route again.

### Compact agent output

By default `route` and `resolve` print one small JSON line (revision 27): `decision_id`, `target`,
`destination`, `launch_ready`, `dispatch`, `judgment_required`, `judgment` (each reason as
`APPLIES_TO:CODE`), `reason` (one sentence: the chosen candidate and basis, any override, and why a
destination was not used), `receipt` (the ledger path, or why nothing was stored) and `replayed`,
plus `waiting`, `data_policy`, the simulation keys, and `destination_offer` only when a hand submit
needs it. The ledger always keeps the full receipt. Use `--json` for the full receipt on stdout.

Use `--brief` with `route` or `resolve` for the larger compact JSON line described below, with
judgment details and calibration notes. `--brief --json` is rejected, as is `--brief` on every
other command.

```bash
model-policy-ops route implement.quota-tight --proof-class judged --brief \
  --available capabilities.json --quota quota.json --accounts accounts.json \
  --decision-id task-unique-id --record \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD
```

The compact object contains:

- `decision_id`, `target`, `destination`, `launch_ready`, `launch_readiness_basis`, and `dispatch`. First check
  `waiting`: when present, withhold dispatch even if the target is runnable. Otherwise pass
  the exact target and dispatch fields unchanged. Destination dispatch keeps its root
  decision ID and first-line token; a model dispatch uses the decision ID.
- All `judgment_reasons`, including routed/final and informational tags, and
  `judgment_required` (null for resolve, which does not provide that judgment).
- `selected` with the final arm and account, plus `routed` with the original
  arm/basis. `selected.final_basis` is null because the receipt does not record
  it. `routed_basis` and `routed_selection_basis` are copied only for an exact
  arm match, never inferred for a changed effort/model or for resolve. For an
  external destination these arms are under `model_fallback`, and `selected`
  is null. Its hand-submission `destination_offer` is included without the capacity snapshot.
- Compact `calibration_notes`: small actionable notes remain structured.
  At most eight sections are shown; bulky arrays retain counts and up to three
  items, and values over 512 serialized characters become explicitly truncated
  previews. Omission counts point to the full receipt for further review.
- `receipt` with `stored`, the decision ID, and, only when recorded or replayed,
  the absolute JSONL path and replay instructions. Match the decision ID to read the exact
  stored row, or repeat identical caller-intent flags without `--brief`.
  Unrecorded evaluations and simulations instead say why no stored receipt exists.

This is only a print projection: execution, ranking, validation, errors and ledger
bytes are unchanged. Large catalog, quota, source and candidate-trace snapshots
stay in the full receipt. Waiting claims suppress the compact launch tuple and expose
the caller warning; the full receipt retains the underlying target. Required targets, dispatch identity and judgment reasons
are never truncated, so output size still depends on those exact fields.
Simulations retain `simulation` and `simulated_target`, with null targets,
false launch readiness and no dispatch; an existing real receipt is not presented
as the stored result of a simulation.

### Candidates and evidence basis

The pack holds the policy. `lib/routing.py` holds no model names. Each op can have
`candidates`: the alternate exact arms, in order. The primary is always the op's
`expands_to` (`basis: primary`) and is not repeated. Without `candidates`, there are
no alternates. `resolve`, `list` and `show` ignore `candidates`.

| `basis` | Meaning | Selection |
|---|---|---|
| `primary` | The op's own arm | `selection_basis: primary` |
| `task_benchmark_prior` | A prior: a board that the pack cites for this task family, the same effort, a clear margin. Not proof of fitness. The board harness is not T3, Claude Code or Codex | Automatic, `selection_basis: evidence_prior`, `evidence: prior` |
| `unvalidated` | Weaker arms, no comparable evidence, aggregate-only support, mixed boards, or another task family | Needs judgment, unless an `authorize` directive covers it |

A `task_benchmark_prior` candidate needs a `prior` object (`board`,
`board_cited_for_op`, `effort_matched`, `harness_matched`, `note`). `evidence_refs`,
`known_gaps` and `requires` (`{"proof_class": [...]}`, with `--proof-class` as the
fact) are optional. When a required fact is unknown, the candidate needs judgment.
When it is known and not listed, the candidate is removed. `check` validates all of
this. The pack `routing` object holds `pace_windows`, `failure_lookback_minutes`,
`failure_demote_count`, `review_task_families`, `independence_default`,
`directive_max_days`, `observation_max_age_hours` (default 3), `observation_window_days`
(default 14), `quota_substitution` and `quota_bands` (see Quota substitution; the shipped pack leaves both out) and `lineage_key`. `lineage_key` maps a provider to the
model-catalog field that names a model line (`{"claude": "family", "codex": "tier",
"grok": "family"}`). Absent fields turn the matching feature off: no pace, no
demotion, no directive lifetime cap, no model line. A review op with no
`independence_default` requires `vendor`.

### Filters and rank

Each candidate is paired with every live catalog account for its provider. Filters
run in this order, and the first one that fails removes the pair:

1. Not runnable (the `resolve` rules), or `requires` known and not met.
2. Billing is not `subscription` (metered, missing or unmapped), unless an
   `allow-metered` directive matches.
3. An `avoid` directive matches.
4. A relevant quota window has utilization of 100 or more.
5. The independence requirement fails against the maker.

The survivors are ordered by these keys, and the trace prints each one:
eligibility (`primary`, `task_benchmark_prior` and authorized candidates first), a
`prefer` directive, pace (`on_track`, `unknown`, `at_risk`), availability
(`healthy`, `unknown`, `demoted`), pack candidate order, lower `projected_pct`,
instance ID. The `projected_pct` key is a load-balancing heuristic. It is not a
measure of subscription value. There are no weights. With quota substitution on, a
best pair on a constrained account can be replaced after this ranking (next section).

Pace is `on_track` when every pace window that the account reports has a clawmeter
`forecast` below 100. It is `at_risk` at 100 or more. It is `unknown` for an
unmapped account, a source that is not a plain snapshot, a missing forecast, an
expired window, or no pace window. Unknown is never headroom. The 5-hour window
only excludes an account when it is exhausted.

### Quota substitution

Pace class ranks after eligibility, so a primary Codex arm that is projected over 100%
still beats an `unvalidated` Claude alternate. Quota substitution moves the choice off
an account that is about to run out. It is **off by default**: the shipped pack sets no
thresholds, because they are a personal preference. A user enables it in the user policy
overlay (see User policy overlay) with

```json
"quota_substitution": {"activate_projected_pct": 97, "release_projected_pct": 85}
```

The numbers above are an example, not a recommendation. A pack that sets the same key
also enables it, and the overlay value replaces the pack value.

`check` requires `0 < release_projected_pct < activate_projected_pct <= 200`.

**Forecast bands** (revision 29) replace the fixed pair with the user's own thermostat bands. A user
sets `routing.quota_bands` in the overlay: an ordered list, widest first, of
`{min_days_left, exclusive?, low, high}`. The first rule that matches the days left before the account's
window resets gives the band `{low, high}`: a far reset gets a wide band and a near reset a narrow one. The
last rule must have `min_days_left` 0 and not be `exclusive`. The values come from the user's own
scheduling policy; the pack has none and the code has no defaults. With bands set, they replace
`quota_substitution`, and:

- headroom is `high - projected_pct` for the pace window with the least headroom. Pace is `on_track` with
  positive headroom and `at_risk` without it, so an account inside its band is not `at_risk` just because it
  is projected above 100%;
- an account is constrained at `projected_pct >= high` and released below `low`, with the same per-window
  memory as the fixed thresholds;
- a substitute needs a known, positive headroom. Order: pack candidate order, availability, the most
  headroom, the earliest reset, the instance ID. `substitution_rank` does not apply. Among accounts of one arm
  with no substitution, the same headroom order replaces the lower-projection order;
- an unknown forecast, an expired window or an unusable reset time is never headroom (`pace_reason`
  `no_band_for_window`). An account is never a target when an `avoid` directive, a failed hard filter or
  independence removes it. The bands only schedule among pairs that the pack already qualifies for the op and
  effort: a model or effort never changes with budget;
- trace rows and `routing.quota[ref].binding` carry `band`, `days_left` and `headroom`. When the routed subscription
  pair has no known positive headroom (at risk, unknown or stale forecast, or an account still held constrained by
  hysteresis), the receipt is a **wait**: `target` is null (every nested target too), `launch_ready` is
  false, there is no `dispatch`, `waiting` is `{state: no_forecast_headroom, assertion: router_evidence}` and the
  judgment reasons add `wait_no_forecast_headroom` to `pace_not_on_track`. The router withholds; it does not queue.
  Route again after a fresh reading or a reset. An explicit `--override` is the caller's own intent and is not
  withheld for quota; its bypassed filters stay in `routing.final`. A verified metered overflow pair is not judged by
  headroom;
- **Order before a wait.** The wait comes last. A destination offer (for example dot) is decided first. Then, among
  the pairs that passed every filter, the first ranked pair that can launch wins: a funded prepaid or free account, or
  a subscription with headroom. `routing.pacing.state` is `alternative_before_wait` when that replaced a subscription
  pair that could not launch (unknown forecast, at risk, held), with the skipped pair in `skipped`. Only when no pair
  can launch does the route wait. `waiting` then says what a wait is: `dispatches: false`, `retry_after` (the earliest
  reset of an eligible subscription account, or null), a note that nothing retries on a timer, and
  `alternatives_considered` (the destination block and every metered pair with the filter that removed it). A qualified free route (public classification
  asserted, qualified op, proof class and effort, current evidence) is already a candidate and launches here before any
  wait; it is removed as `cooling_down` while its account cools and reconsidered by the next route. A route that does not qualify is
  evidence only, and the caller may opt in with the exact flags in `opt_in`. A subscription is never picked for a discretionary wait.
- **Drain an account** (revision 32). A user with a one-off quota reset credit wants to use an account's
  7-day allowance up quickly, fire the credit, and use it up again. A high projection makes the router steer away
  from that account, which is the opposite. The overlay key `routing.drain_accounts`, keyed by provider
  instance ID, reverses that for a while:

  ```json
  "drain_accounts": {"claude-dondo": {"until_current_pct": 95, "expires_at": "2026-10-22T00:00:00Z", "reason": "reset credit: burn the week, fire it, burn it again"}}
  ```

  An entry applies while `now < expires_at` and the account's current use (the highest `utilization` among
  the pace windows) is known and below `until_current_pct`. While it applies: the account's pace is
  `on_track` whatever its projection (the projection stays visible in `routing.quota`), so it is neither
  constrained, nor held, nor a wait under bands; and its pairs rank first among the pairs that passed every
  filter, right after the evidence class (rank key `drain_account`; substitution also prefers them). Every
  filter still runs first: `avoid`, independence, `requires`, billing, funding, `not_runnable` and
  `quota_exhausted` remove a drained account's pair as they do any other, and an evidence class 1 pair
  (for example an unvalidated candidate) still ranks behind a class 0 pair. An explicit `--override` is
  unchanged. Unknown use never applies an entry. Once use reaches `until_current_pct` or the entry expires,
  routing is as without the key (stored hysteresis memory for the account is left as it was). The receipt
  records `routing.drain_accounts` and, when the chosen pair is on a draining account, the informational
  judgment reason `drain_account_active` (`instance`, `reason`, `until_current_pct`, `current_pct`,
  `expires_at`), so the exception is as visible as a quota reason. `check` requires an object keyed by
  non-empty instance IDs; each value has exactly `until_current_pct` (a number, `0 <` and `<= 100`),
  `expires_at` (an ISO 8601 time with a zone) and a non-empty `reason`. The shipped pack has no entry: this
  is a per-user setting.
- **Cooldown.** An account with at least `failure_demote_count` rate-limit or transport failures inside
  `failure_lookback_minutes` is `cooling_down`. `retry_after` is when enough events leave the lookback: the account is available once the clock is later than it (the lookback is inclusive, so at the instant itself it still cools). Nothing is
  stored: every route reads the telemetry again, so a cooldown ends by itself and one failure never marks an account
  unavailable for longer than the lookback. An exact override onto a cooling account is kept and adds the
  informational reason `final_account_cooling_down` with the same `retry_after`.
- **`--critical-path`** (route only; a launch fact, `launch_facts.critical_path: true`; absent means false and old
  receipts stay valid; the flag takes no value). Work that must not wait on pacing sets it. It changes nothing unless
  bands are set. When no pair can launch, the router picks, among pairs that passed every filter, the subscription with
  the least overshoot: projected percent minus the band upper limit of the window that binds that account's pace, in
  percentage points, so each account is held to its own band and days left. Order: eligibility class, `prefer`,
  availability (healthy before unknown before demoted), overshoot, candidate order, earliest reset, instance ID. An
  unknown, stale or exhausted account has no overshoot and is never picked. Avoid, independence, `requires`, funding
  and privacy filters are unchanged, because they run first. The receipt records `routing.pacing` (`state`
  `critical_path_exception`, the skipped pair, the ordered `candidates` with overshoot, band, window, reset and
  `quota_ref` into `routing.quota`) and the informational reason `critical_path_pacing_exception`. If no account has a
  known overshoot, the route still waits and `waiting.critical_path.satisfied` is false. `--now` simulations never
  launch, with or without the flag. `--brief` and the default line print `critical_path` and `pacing`.
- a qualified free arm that the router chose (no override) is routed again on the next route: it is removed while it
  cools down and chosen again after the cooldown.
- a free arm that the caller chose by explicit override stays exact. After it fails
  (for example, rate limited), route again without the override: the pack's subscription arms then compete, and the
  one above (adequate arm, then most headroom) wins, or the route waits.

**Funding evidence.** `route` cannot read prepaid credit, and an `allow-metered` directive is permission, not proof
of funds. An account-map entry may carry `funding: {"verified_until": "<zoned ISO time>"}`: the time until which the
owner's own check of that account's credit holds, written by whoever did the check. An account with a `funding`
key is a target only while `verified_until` is in the future; `null`, a past time or a malformed value removes it
(`funding_unverified`). The same check runs on the final target whoever chose it: an override or a destination
target on an account with null or past `verified_until` is a wait (`waiting.state` `funding_unverified`, reason
`wait_funding_unverified`; `final_funding_unverified` records the bypass). A malformed `funding` value fails the
whole accounts file. A recorded receipt that was ready replays as a historical receipt and is not a current funding
check: the consumer re-checks `verified_until` at dispatch. The router does not verify the claim, cannot
bound other spenders or an auto-reload, and a balance never reserves a task budget. An account with no `funding`
key keeps the directive-only rule, so declare `funding` on every prepaid account that the guarantee should cover. Keep funding data in the local account map, never in the public pack.

An unmapped metered account with a current `funding` check and an `allow-metered` directive has pace `unknown`,
which ranks after any subscription with headroom and before one without. It can be overflow work only that way.

Per mapped subscription account, `route` takes the binding pace-window `projected_pct`
(the same value as the pace class). The account is **constrained** when that value is at
or above `activate_projected_pct`. It stays constrained until the value falls below
`release_projected_pct`. Between the two thresholds the previous state holds, so the
mechanism does not flap. A value of exactly `release_projected_pct` still holds. Unknown
or stale quota is never headroom: an account with no known projection is not
constrained and is never a substitute. Its stored state is kept, not erased.

The memory is `quota-substitution.json` beside the receipts (`--quota-state FILE`
changes it). It holds `{since, window}` for each constrained account and follows the
private-ledger rules: mode 0600, an exclusive lock on `quota-substitution.json.lock`
for the read, the decision and the write, a temporary file that is written, fsynced and
closed before it replaces the state, and a refusal to name the same file as another
ledger. It is written only on a transition (or when it is missing). `route --now` is a
simulation: it reads the state and never writes it. A replay of a recorded decision ID
returns the stored receipt and does not read or write the state. A missing state starts
empty. A state that is unreadable (malformed, a symlink, a file that group or world can
write, a directory that others can write) is never rewritten: `route` decides without
memory (constrained if and only if the value is at or above `activate_projected_pct`)
and records `state: unreadable` with `state_issue`.

The state is committed only after the route is accepted: after every validation passes and, for
`--record`, only when this call appended its receipt (never for a replay). A rejected route writes
no state. A recorded receipt is a snapshot taken before that commit, so it carries
`state_commit: "after_receipt"` and `state_written: false`; `state_written: false` there does not
mean that no write happened. If the post-receipt write fails, `route` prints a warning on stderr
and the stored receipt is unchanged. The quota lock is held from evaluation through any
destination capacity probe and the receipt append, so a slow capacity command delays other
routes that share the receipts directory, up to its timeout.

**Ranking effect.** Every hard filter runs first and is unchanged. Then, when the best
pair sits on a constrained account, `route` looks among the surviving pairs of the op's
pack candidates (the primary and the alternates, so the pack's model tier and effort
stay) for pairs on an account that is not constrained and has a known projection below
`activate_projected_pct`. Order: pack candidate order, then the account's
`substitution_rank` (see Account map; lower first, none last), then the lower
projection, then the instance ID. The first pair is chosen.

The substitute keeps its evidence `basis`: `unvalidated` stays `unvalidated`.
`selection_basis` is `quota_substitution_exception`. The pack or the user policy that
enables the feature authorizes it, so a substitution to an `unvalidated` arm does not raise
`unvalidated_candidate`, the same way an `authorize` directive does not. Every other
judgment reason (independence, `requires`, `auth_config` and the rest) still applies.
When no such pair exists, the normal choice stays and the judgment reason
`quota_substitution_unavailable` lists each constrained account with its projection.
Destinations are unchanged: the dot-first decision runs after model ranking. An
override to a constrained account adds `final_account_quota_constrained`.

`routing.quota_substitution`:

- `active_accounts`: `{instance: {projected_pct, utilization, window, resets_at, state,
  since}}` for each account that is constrained or was released in this evaluation.
  `utilization` is observed use; `projected_pct` is the quota source's forecast. `state`
  is `activated` (at or above the activation threshold), `held_by_hysteresis` (between
  the thresholds, held from before in the same window), `held_unknown` (quota unknown,
  stored window not yet reset), `released`, or `ended_with_window` (quota unknown and
  the stored window has reset: no longer constrained, still not a target). `since` is
  the time the constraint began, or the release time. `previous_window` appears when a
  reset dropped a stored constraint (`{window, resets_at}`), is `"not_recorded"` for a
  row stored before window identity existed, or `"not_comparable"` when the observed
  reset time has no zone or cannot be parsed (hysteresis then still applies).
  `released` and `ended_with_window` rows appear once, in the evaluation that removes the
  stored row. A constraint never crosses a known reset; a different window name is a
  different window, and the same name is the same window when reset times are within
  60 seconds;
- `thresholds`, `substituted`, `unknown_accounts` (mapped accounts with no known
  projection), `state` (`loaded`, `missing` or `unreadable`), `state_written` and, when
  they apply, `state_issue` and `state_write_error`;
- when substituted: `from` and `to` (`instance`, `model`, `effort`, `projected_pct`),
  a fixed `reason` and `authorized_by`: `"user policy overlay <path>"` when the overlay
  set `quota_substitution`, else `"pack routing.quota_substitution"`.

The block is part of the receipt's routing record. It is not part of the request
identity, and the state path is not either.

### User policy overlay

A user tunes a few `routing` parameters and sets some operating-point defaults without editing the pack.
`~/.config/model-policy/user-policy.json` (`$XDG_CONFIG_HOME` honored; `--user-policy FILE` names another file;
`--no-user-policy` skips it) holds

```json
{"schema_version": 1, "routing": {"quota_substitution": {"activate_projected_pct": 97, "release_projected_pct": 85}}}
```

(example values). `routing` may set only `quota_substitution`, `quota_bands`, `drain_accounts`,
`observation_max_age_hours` and `observation_window_days`. Each key replaces the pack value whole (no deep merge).
The pack keeps its evidence: `basis`, `evidence_refs`, `known_gaps`, `frontier_assumption`, independence and
`lineage_key` stay as shipped. A missing file at the default path means no overlay; a file named with
`--user-policy` that does not exist is an error, so a typo cannot silently turn the overlay off.

The optional `operating_points` object sets choices for named ops. Each key is an existing op id. An entry may set
any of these, and nothing else:

- `expands_to`: `{provider, model, effort, mode?}`, the op's default arm. It replaces the pack's arm and keeps the
  pack's alternates. `route` still requires the arm in the live catalog.
- `candidates`: the op's alternates, in the pack's format. An overlay alternate must use `basis: "unvalidated"`, so a
  route to it needs judgment, and it may not carry a `prior`.
- `purpose`: the `--purpose` that `route` uses for this op when the flag is omitted. For example, `review` sends a
  review to a destination that lists the `review` purpose. An explicit `--purpose` wins.
- `reason`: required when the entry's arm or alternate uses `xhigh` or `max` effort. `route` accepts this reason where
  it would otherwise need `--reason`, so the justification is written once in the overlay.

```json
{"schema_version": 1, "routing": {}, "operating_points": {
  "implement.standard": {"expands_to": {"provider": "codex", "model": "gpt-6-luna", "effort": "max", "mode": "standard"},
                          "reason": "example: standard implementation at max"},
  "review.audit": {"purpose": "review"}}}
```

An unknown op id, an unknown key, a malformed value, or a missing `reason` for `xhigh` or `max` is an error for
`route` and `check`. The shipped pack has no `operating_points`. A decision that used an overlay default lists the
overridden op ids in `routing.user_policy.operating_points` (below), so the receipt shows where its arm came from.

`route` and `check` read it. An unknown key, a bad value (the pack validator checks the merged
`routing`), a dangling symlink, a file that is not regular, a file owned by another user, or a
file that group or world can write is an error: `route` exits 1 with `FAIL ...` and `check`
fails. A symlink is followed (for config deployed from a dotfiles repo), and the checks apply to
the target. `check --user-policy FILE` validates a file before you deploy it.

Every `route` output and receipt records `routing.user_policy`: `{path, resolved_path, sha256,
keys}` when an overlay applied, else `null`. It also lists `operating_points` (the overridden op ids) when the overlay
set any. Receipts also hold `policy_sha256` (the pack, as
before) and `effective_policy_sha256` (the pack with the overlay). None of this is in `request`,
so a replay returns the stored receipt.

### Account map

`~/.config/model-policy/accounts.json` (or `$XDG_CONFIG_HOME/model-policy/`, or
`--accounts FILE`). It is local machine configuration, not pack data. A missing file
maps nothing, so every instance is excluded for unknown billing.

```json
{"schema_version": 1, "accounts": {
  "claude-work":  {"quota_provider": "claude", "quota_source": "work", "billing": "subscription"},
  "codex":       {"quota_provider": "openai", "quota_source": null,  "billing": "subscription"},
  "claude-api":  {"quota_provider": null,     "quota_source": null,  "billing": "metered"}}}
```

`quota_provider` and `quota_source` must equal the IDs in `clawmeter --json`. Use
`null` for a provider that has no `sources` array.

An entry can also have `substitution_rank`, a non-negative integer: the owner's order
among accounts when quota substitution picks a substitute (lower first; an account with
no rank comes after every ranked one).

### Directives

`directive add --id ID --effect avoid|prefer|authorize|allow-metered --match KEY=VALUE ...
--until RFC3339 --reason TEXT --source owner|lead`, then `directive end ID --reason TEXT`
and `directive list`. The match keys are `provider`, `model`, `account` and `op`,
and every key must match. The `--until`, `--reason` and `--source` options are
required, and no directive is permanent: `--until` cannot be later than the pack
`directive_max_days` (14), and `add` and `end` record the real clock (`--now` is refused). A
reader ignores a row that lasts longer than the cap or was recorded after the evaluation
time; `route` lists it in `directives_rejected` and `directive list` shows state
`rejected`. Standing rules go in the pack. `add` rejects a
`provider`, `model` or `op` that the pack, the live catalog and the model catalog
do not declare. An `account` that the live catalog and the account map do not
declare gives a `warnings` entry. A directives file that group or world can write is
refused on every read and write.
Directives are in `directives.jsonl` beside `--receipts` (or `--directives FILE`).
The file follows the receipt rules: mode 0600, locked append, fsync, no symlinks,
and a refused group- or world-writable parent. It is a different file from the
receipts and outcomes. Expired directives are ignored, and `directive list` shows
`active`, `expired`, `ended` and `rejected`.

- `avoid` is a hard exclusion.
- `prefer` is a rank key only. It never lifts a pair over a filter or over
  eligibility.
- `authorize` lets you select an `unvalidated` candidate without a new judgment. It
  does not change the basis. The output keeps `basis: unvalidated`, and adds
  `selection_basis: authorized_exception` with the directive ID, source and expiry.
  Directives match provider, model, account and op, not effort, so an `authorize`
  for a model covers every effort of that model.
- When `avoid` and `prefer` or `authorize` match the same pair, `avoid` wins and
  `routing.conflicts` lists it. `judgment_required` is then true.

### Availability

With `--db`, `route` reads T3 terminal-failure turn items read-only (query-only, one
transaction): items whose ID contains `terminal-failure` and whose status is
`failed`, from the last `failure_lookback_minutes`. They join to the instance
through the run. Classes, from the message text: `rate_limit` ("rate limit
reached"), `transport` ("Connection error", "stream closed", "stream disconnected",
"Connection refused"), `auth_config` ("could not authenticate", "No conversation
found", "Insufficient context allowance", "still running background agents"),
`content_policy` ("flagged for possible"), and `unknown`. Only `rate_limit` and
`transport` count toward `failure_demote_count` and demote the instance.
`auth_config` events on the chosen instance make `judgment_required` true. The
other classes are only reported. A recovered item, a cancellation and an
interruption are not failures. A missing database, table or column gives
availability `unknown`, which ranks below `healthy`. It never means healthy. A
failure event that does not join to an instance could belong to any of them, so
any such event makes availability `unknown` for all instances (`unjoined` has the count).

### Independence and maker provenance

For ops whose `task_family` is in `review_task_families`, the required level is
`--independence vendor|family|model`, or the pack `independence_default`. For other
ops there is no default.

- `vendor`: the checker's provider differs from the maker's. The T3 driver kind
  fixes the vendor, so this holds from provenance `t3_run_config` or
  `receipt_only` without judgment.
- `family` and `model`: within one vendor they rest on the requested model only
  (`independence_requested_only` makes `judgment_required` true). The family is the
  model line: the vendor and the model-catalog field that the pack `lineage_key`
  names for that provider (`family` for Claude, `tier` for Codex). A different
  version of the same line is not independent (`gpt-5.6-sol` and `gpt-6.1-sol`). A
  provider with no mapping, or a model that the model catalog lacks, has no line:
  the pair is removed (`line_unknown`) and `independence_line_unknown` is raised.
  The model catalog is `--model-catalog FILE`.
- Provenance: `t3_run_config` (the maker's T3 child run has the same instance and
  model as the receipt target; needs `--db`), `receipt_only`, `caller_claim`
  (`--maker-model`; it needs judgment) and `unknown`. When the run is found, the
  vendor is the driver kind of the run's instance and the model is the run's, never
  the receipt's. An effort difference is in `effort_mismatch` and does not change
  provenance. A run that differs in instance or model gives `unknown`; the checker
  is still filtered against the run, and `independence_maker_unknown` is raised. The
  vendor basis is `t3_driver_kind`, `receipt_arm` (the receipt's instance is not in
  the live catalog) or `caller_claim`. `--maker-model` takes a provider that the
  pack or the live catalog names.
- T3 records the requested model, not the served model. The output always says
  `served_model: unattested`.
- A review op with no maker needs judgment (`review_without_maker`). An explicit `--independence`
  with no maker also needs judgment (`independence_without_maker`).

### Judgment reasons

`no_eligible_pair`, `unvalidated_candidate`, `requires_unknown`, `pace_not_on_track`,
`auth_config_events`, `independence_requested_only`, `independence_caller_claim`,
`independence_maker_unknown`, `independence_line_unknown`, `final_metered_without_directive`,
`final_avoided`, `final_quota_exhausted`, `final_not_runnable`, `final_requires_not_met`,
`final_independence_bypassed`, `final_target_null`, `final_account_quota_constrained`,
`quota_substitution_unavailable`, `review_without_maker`, `independence_without_maker`, `directive_conflict`, `destination_inputs_unknown`,
`destination_independence_unknown`, and
`newer_ga_model_not_in_pack` (informational: a newer GA model on the same
model line is in the live catalog and not in the pack; an informational reason is listed
but does not set `judgment_required`). With any reason, `route` still
returns its best target unless it is `null`. The parent decides.

### Destinations

A destination is an external worker, such as a prepaid worker that resets each day, that
can take some purposes instead of a model. `route` asks it first and chooses it when it
is eligible and has capacity. The destination is an adapter. `route` stays the only chooser.

Config: `~/.config/model-policy/destinations.json`, or `--destinations FILE`. A missing
file means no destinations, and `route` behaves as before. A malformed file is an error.

```json
{"schema_version": 1, "destinations": [{
  "name": "worker", "purposes": ["review", "research", "test-design"],
  "capacity_command": ["/abs/path/capacity", "--capacity"], "timeout_seconds": 5,
  "vendor": "codex", "how_to": "/abs/path/or/url", "submit": {"request_id": "<request_id>", "kind": "<purpose>"}}]}
```

`vendor` is optional. It names the destination's model vendor, in the same spelling as the
maker vendor in `routing.independence.maker.vendor` (the T3 driver kind, for example `claude`
or `codex`). For a review op, or with `--independence`, it is compared with the maker vendor:
see Independence below.

The list is ordered. `purposes` come from the launch-fact purposes. `submit` is a free-form
object; `route` replaces the literal tokens `<request_id>` and `<purpose>` in every string.
The capacity command prints one JSON object, `{"available": bool, "reason": str, ...}`;
extra fields are kept. It runs without a shell, with stdin closed and the timeout enforced.
The output is read in a stream and is limited to 64 KiB; on overflow the probe is killed and
the result is `output_too_large` (a truncated prefix is never parsed). The probe session is
killed whenever the probe ends, also when the leader has already exited, so an ordinary
background child does not outlive `route`. The read pipe is closed, so the total time is the
timeout plus a one-second grace, even if a detached (`setsid`) child keeps the pipe open; such
a child can live on.
A timeout, a non-zero exit, output that is not JSON (or has no boolean `available`) and a
missing binary all mean `available: false`, with reason `capacity_probe_error: KIND`
(`timeout`, `output_too_large`, `nonzero_exit`, `invalid_json`, `invalid_shape`, `missing_binary`, `os_error`).

Rule, after `route` ranks the models as usual, for a purpose that a destination lists
(`--purpose` is required for this):

1. **Facts.** `--inputs local` gives `local-inputs`, `--urgent` gives `synchronous` and
   `--sensitive` gives `credential`. Any of them means model, with
   `routing.destination.fallback` = `{destination, reasons, source: "facts"}`.
2. **Parent skip.** `--skip-destination local-inputs|synchronous|credential|unavailable|other`
   (repeatable) means model, with source `parent` and the `--reason` text. Any use needs
   `--reason`; `other` is the catch-all. `route` only.
3. **Capacity.** The probe runs for each listed destination in order. When all are
   unavailable: model, reasons `["unavailable"]`, source `capacity` and `probe_reason`.
   This needs no parent reason.
4. **Chosen.** The first destination with capacity wins. See the offer below.

Independence. A purpose that needs independence is a review op (`review_task_families`) or any
op with `--independence`. If the destination `vendor` equals the maker vendor and the required
level is `vendor`, the destination is not eligible and is not probed: `considered` state
`declined_by_independence`, and when none is left, `fallback` = `{destination, reasons:
["independence"], source: "facts"}`, then `route` picks a model as usual. When some are
declined and the rest are unavailable, `fallback` = `{destination, reasons: ["independence",
"unavailable"], source: "capacity", probe_reason, by_destination}`, where `by_destination` maps
each name to its reason. A destination declined by a launch fact keeps that fact's reason there (for example `local-inputs`),
and `independence` appears only for a destination that was actually declined for the maker's vendor. A maker with several runs is checked against every known run vendor; one
run with an unknown vendor still adds the judgment reason. If the destination has
no `vendor`, the maker vendor is unknown for any maker run, or the required level is finer than `vendor` (family
or model, which a destination cannot show), the destination can still be chosen, and the judgment
reason `destination_independence_unknown` is added.

With `--now` (simulation) no probe runs. The destination state is `not_probed_in_simulation`
and the output is today's simulation. A purpose that no destination lists is routed as before; `routing.destination` is
`{chosen: "model", considered: [], not_eligible: [...]}`, which names each destination and why. `--override` does not suppress the destination; add
`--skip-destination` as well to dispatch the override.

When an ordinary destination is chosen, the output is a valid receipt with `target: null`,
`launch_ready: false`, every nested `target` key null, a top-level `destination: NAME`
and `destination_offer`: `{name, request_id, purpose, how_to, submit, capacity}`.
`request_id` is the `decision_id`, so the worker's reports join to the receipt. When
`--inputs` is unknown, the judgment reason `destination_inputs_unknown` is added: the
destination needs one pushed GitHub input; if inputs are local, re-run with
`--inputs local`. Every nested `target` key is cleared after the offer, the capacity JSON and
the `submit` object are in place, so no field from the config or the probe can bring one back.

**Destination target (revision 20).** A destination that T3 can run as a provider instance
(for example a local ACP worker pool) may declare how to dispatch to it directly:

```json
{"target": {"providerInstanceId": "pool", "driverKind": "acpRegistry", "model": "pool"},
 "brief_token": "[pool-decision:<root_decision_id>]", "max_chain_failures": 2,
 "request_id_pattern": "[-\\w]{8,120}"}
```

`target` takes only those three keys (no options, so effort is null). `brief_token` is one
line holding `<root_decision_id>` once, and `max_chain_failures` (1–10) is required with
`target`. `request_id_pattern` is optional: the root decision IDs the destination accepts as
its own request ID. These values describe one machine's setup and belong in the local
config, not in the pack.

Governed OpenCode routes are unsupported through this generic destination contract.
Use the validated model-arm override described under free routes.
An unsupported destination returns `data_policy.state: refused` and suppresses
its manual offer instructions as well as its T3 dispatch target.

When an ordinary destination with a target is chosen, and the live catalog (`--available`) lists the instance,
the driver kind and the model, and both the instance and the model can run a child task now, the output also has:

- `target`: the destination's target, unchanged, and `launch_ready: true`;
- `dispatch`: `{clientRequestId, mode: "async", brief_first_line, root_decision_id}`.
  Call `delegate_task` with `target`, `clientRequestId` and `mode` as given, and make
  `brief_first_line` the task's first line. The root is the first decision of the relaunch
  chain, so a relaunch keeps the same line and the destination keeps one request.

Every other `target` stays cleared (the model fallback is never dispatchable here), and
`destination_offer` stays, for hand submission. `routing.destination.target_state` is
`in_catalog`, `not_in_catalog` or `decision_id_not_accepted`; in the last two cases `target`
is null and only the offer remains. A simulation never returns the destination target.

Relaunch chain. With `--relaunch-of`, `route` walks the chain of `relaunch.from` links
back to its first decision. Each earlier route to a destination in that chain counts as one
failure, because a relaunch follows a child that did not deliver. When a destination with
`max_chain_failures` has reached it, it is declined (`considered` state
`declined_by_chain`, with `chain_failures`), and when none is left, `fallback` =
`{destination, reasons: ["chain_failures"], source: "chain", chain_failures}` and `route`
picks a model. Routes to a model in the chain do not count.

**Op scope (revision 28).** A destination entry may add `"op_scope": {"PURPOSE": ["OP", ...]}`. A purpose
listed there is eligible only for those ops; purposes not in `op_scope` keep every op. Each key must be one of the
entry's `purposes` and each list must be non-empty. For example a worker that may build only small, fully specified
changes on pushed code lists `"purposes": ["review", "research", "test-design", "execution"]` with
`"op_scope": {"execution": ["implement.oracle-bounded"]}`. A request outside the scope is `not_eligible` with reason
`op_not_in_scope`. Launch facts still apply (`--inputs local` declines it), and the op's own proof rules still apply
(`implement.oracle-bounded` needs `--proof-class oracle`). The worker returns a patch; the caller applies it and runs
the oracle.

**Typed destinations (`schema_version` 2, `kind: "typed-http"`).** An opt-in entry for a whole-answer typed judgment
worker. A schema 1 file is read exactly as before (and refuses the keys below); an older CLI rejects a schema 2 file.
Router behavior: it offers a handoff (`destination_offer.submit` and `.typed.contract`), calls nothing, returns no model
target (`launch_ready` false), and the model pair stays the fallback. The entry names no model in router code; everything
below is the entry's own declaration:

- `op_scope` covers every purpose, and its ops are exactly the keys of `contracts`:
  `{op: {"whole_answer": "typed", "revision": "...", "sha256": "<64 hex>"}}`. Other ops (`review.audit`, `implement.*`) are
  `not_eligible`; nothing is routed by semantic guess. No `target`, `brief_token`, `max_chain_failures` or `request_id_pattern`.
- `capability`: `{"caller_text": bool, "synchronous": bool}`. `synchronous` true lifts `--urgent` for this entry only.
  `caller_text` true lifts `--inputs local` only with `route --inputs local --input-mode caller-text` (stored as launch fact
  `input_mode`; the one legal mode; not arbitrary file or tree access). `--sensitive` and every `--skip-destination` never lift.
- `evidence_contract`: `{evaluated_model, baseline_model, baseline_effort, typed_version, currency, accepted_rule_ids}`.
  `<op>` is also substituted in `submit`. The entry's `vendor` is optional; with none, required independence is
  `destination_independence_unknown` (no vendor independence is claimed for it).

`capacity_command` is the evidence gate (no second eval store): it reads the adapter's current manifest, makes no model call,
and prints the evaluation already made. It reports `evaluated_model`, not a live serving model; the real adapter must validate
the served model at dispatch. Healthy only when every field equals the contract and the clock (the route's evaluation clock,
real for any dispatchable route; a simulation never probes) is in `[evaluated_at, valid_until)` with aware times. Otherwise the
destination is `evaluation_required` (`capacity.reason: "evaluation_required: <code>"`, fallback reason `evaluation_required`).
A probe with `available: false` stays `unavailable`. Example probe output:

```json
{"available": true, "op": "review.rule-check", "evaluated_model": "<evidence_contract.evaluated_model>",
 "contract_sha256": "<contracts[op].sha256>", "accepted_rule_ids": ["<same list, same order>"],
 "evaluation": {"cases": 4, "positive": 2, "negative": 2, "definitive": 4, "label_disagreements": 0, "baseline_disagreements": 0,
  "sample_sha256": "<64 hex>", "test_evidence_metadata": {"label_source": "human-labelled", "sample_sha256": "<same>"},
  "versions": {"baseline_model": "...", "baseline_effort": "...", "typed_version": "..."},
  "evaluated_at": "2026-10-09T00:00:00Z", "valid_until": "2026-10-20T00:00:00Z",
  "cost": {"basis": "api_equivalent", "currency": "USD", "scope": "all_candidates_sample_mean", "priced_at": "2026-10-09T00:00:00Z",
           "per_decision": 0.002, "baseline_per_decision": 0.02,
           "selected_rule_set_per_decision": null, "baseline_selected_rule_set_per_decision": null}}}
```

Only the fields above are kept: the router rebuilds `destination_offer.capacity` and the receipt from validated fields, so
extra or nested probe text (`debug_payload`, `response_body`, extra metadata) is never stored. `label_source` must be
`human-labelled`, `oracle-labelled` or `model-reviewed-synthetic`; these describe provenance, not a quality guarantee. An `available: false` probe keeps only `{available: false, reason}` with `reason` one of
`capacity_probe_error: <kind>` or `adapter_reported_unavailable` (never adapter text). A typed probe's numbers must be standard
JSON: `Infinity`, `-Infinity` and `NaN` are `invalid_json`, and a cost that overflows to infinity (`1e309`) is `cost_unproven`.
An offer is current only as of validation: the evidence must be in its window at the route clock and again at the route clock
plus all monotonic time elapsed since that clock was sampled (runtime-source, quota and routing reads before the probe, and the probe itself). A replayed typed receipt is a historical record, never a live offer: the stored row is
unchanged, but the output moves the offer to `historical_destination_offer` (no `submit`, `historical: true`), sets
`historical_non_actionable: true` and `launch_ready: false`, and has no `destination_offer`; re-route for a current one.

Needs cases >= 4, positive and negative >= 2 summing to cases, every case definitive yes/no, zero label and baseline
disagreements. `cost` is a dated, measured, all-candidates sample mean (`scope` must say so): an average over the whole saved sample, not
a bound on any decision or on the selected rules. `selected_rule_set_per_decision` and `baseline_selected_rule_set_per_decision`
must be present and `null` (unknown) and are echoed as `null`; an adapter claiming a number there is `cost_unproven`. A price
must be a finite positive number; a JSON integer too large for a float (`1` followed by 400 zeros) is `cost_unproven`, not a
crash. The selected request itself is unmeasured and no precise task cost is claimed. A simulation, `resolve` and replay never probe.

**Every receipt says why a destination was not used (revision 26).** `routing.destination.not_eligible` lists each
destination whose `purposes` or `op_scope` exclude the request: `{name, reason: purpose_not_listed|purpose_unknown|op_not_in_scope, purpose}`. It is
present even when another destination is chosen. When an earlier destination is chosen, each later eligible one is in
`considered` with state `not_probed_earlier_chosen` (it is not probed). When no config was read, `routing.destination`
is `{chosen: "model", considered: [], not_considered: "destinations_missing"}`. `resolve` never consults a destination;
its receipt always has `destination_skip` `{reason: "explicit_resolution", destinations: [{name, listed_for_purpose}]}`,
or `{reason, destinations_state: missing|unreadable}` when no config was read. A bad or unreadable file never blocks
resolve. `choice.considered` repeats each of these as a `destination` row with the reason code.

Judgment when a destination is chosen. The model-pair reasons (pace, unvalidated candidate,
quota, auth_config, model independence, model freshness) stay in `judgment_reasons`, tagged
`applies_to: "model_fallback"` and `informational: true`. `judgment_required` then comes only
from destination-level reasons (`applies_to: "destination"`): `destination_inputs_unknown`,
`review_without_maker`, `independence_without_maker`, `destination_independence_unknown`. When the model is dispatched,
judgment is unchanged. `--record`, replay, `close` and `followup` work as for any receipt.

`request` gains `skip_destination` and `destinations_sha256` when a destinations file
exists or a skip is given, so a changed skip or config on the same `--decision-id` is
rejected. The probe result is a runtime input like quota: stored in
`routing.destination.considered[].capacity`, not part of identity.

Audit adds `destination_routes` and `spend_first` (see the audit reference). The counts
are descriptive: "offered" is not proof that the work was submitted or used.

Limits: the probe says the worker has capacity now, not that it will accept or finish the
task. A destination receipt has no model target, so an older CLI that reads it still
audits, closes and follows it up, but replaying it with the base flags fails because
`request` has the new keys.

### Calibrate

```bash
model-policy-ops calibrate --available auto --db ~/.t3/userdata/statev2.sqlite --since 2026-10-08T00:00:00Z
```

`calibrate` reads receipts, outcomes, observations, directives, the live catalog and the
model-catalog pack. It prints proposals, and it writes nothing. It prints:

- `override_clusters`: the same op with the same final provider, model, effort,
  account and overridden fields, in at least two receipts, with the reasons (free
  text, counted and never classified) and a proposal (a `prefer` or
  `authorize` directive, or a pack candidate). A directive expires. A pack change
  stands and needs a reviewed pack release.
- `stale_pack`: pack models missing from the live catalog, and newer GA models. `unchecked`
  lists pack models whose line or release date is unknown, and the state is then `partial`.
- `quality_signals`: per op and exact arm, closes by outcome, check type and launch
  `purpose` (`unknown` when absent), and `rejections`. Only the outcome `rejected`
  is a rejection; `abandoned_by_choice` and `superseded` are never counted as
  rejections or arm failures. The block carries a fixed `outcome_scope`: a close
  describes the delegated result only, and for `purpose=review` it describes the
  review, not the reviewed work. The `evidence` block of each `route` has the same
  split. Oracle and independent checks are separate from maker or parent claims. The
  output says that the numbers are descriptive and confounded.
- `availability` and `coverage` (routed receipts, closes, overrides, directives).

Each `route` output embeds the `calibration_notes` for its op. Outcomes never
change the ranking.

`route` also reads `observations.jsonl` (see Observe) and adds
`calibration_notes.observations`, a list, only when a note applies. With no note the
key is absent. The notes are descriptive: they never reorder candidates, change
`judgment_required` or enter `request`; they are in the stored routing block. Live T3
failure events still demote instances through `availability`; these notes do not repeat that.

- Evidence missing or stale: `{kind, last_run_at, age_hours, sources}`. `kind` is
  `observations_missing` (the file is absent and a destination has an `observe_command`, or it
  is unreadable or holds no `source_run`), `observations_stale` (the latest `source_run` is older
  than `observation_max_age_hours`) or `observation_source_not_ok` (a source in that run is not
  `ok`). A destination that reports `unavailable` with `no_observe_command` is a valid setup and
  is not listed. No file and no `observe_command` gives no note.
- `destination_blocked_share`, only when a destination is chosen. It counts decisions
  whose receipt chose this destination, whose launch purpose equals the route's `--purpose` and
  whose `recorded_at` is within `observation_window_days`. Each `decision_id` counts once, by its
  latest observed report outcome (latest report time, then ledger order); a relaunch is its own
  decision. It appears when at least 3 decisions have a result and at least one is `blocked`:
  `{kind, destination, task_kind, window_days, decisions_with_result, blocked, completed,
  accepted_by_owner, labels}`. `accepted_by_owner` counts those decisions with an `accepted`
  event. completed = worker filed a report; accepted = owner disposition; neither is correctness.
  The note has no model or effort breakdown, because a destination has neither.

### Limits

- The ranking never learns from outcomes. Arms are not assigned at random, closes
  are mostly parent claims, and parent overhead is unknown.
- A `task_benchmark_prior` is a prior from a benchmark. It is not local validation.
- Quota and failure data are only as fresh as the last `clawmeter` and T3 reads.
  Authentication is not attested.
- The T3 read depends on the observed `orchestration_v2_projection_*` schema. A
  schema change gives `unknown`, not an error.
- `route` does not price metered API use. It excludes it unless an
  `allow-metered` directive matches.
- `audit` reports `route_coverage`: the delegate calls whose receipt came from
  `route`, from `resolve`, or from no receipt. Use by other threads is measured
  there, not assumed.

## Audit

```bash
model-policy-ops audit --db T3_DATABASE --thread PARENT_THREAD \
  --since 2026-10-08T00:00:00Z --json
model-policy-ops audit --export delegation-export.json --thread PARENT_THREAD
```

Use exactly one of `--db` or `--export`. The database reader opens SQLite read-only
and query-only, in a single transaction. It reads only the established
`orchestration_v2_projection_turn_items`, `orchestration_v2_projection_runs`,
and optional `orchestration_v2_projection_subagents` columns. SQL projects metadata
so prompt text is not exported. Schema mismatch fails explicitly. `--since` is an ISO-8601 timestamp with a timezone;
`--thread` scopes parent calls. `--since` is normalized to extended UTC ISO before
SQL and Python filtering, including accepted basic ISO and offset inputs.
Database scope filters and orders by the delegate item's `startedAt`, named
`delegate_call.startedAt` in `scope.time_anchor`. A long-running parent's older
`requested_at` does not exclude later calls; item updates do not move calls into
the window. Export scope uses `export.timestamp`; the exporter must use call-start
time. Unmatched receipt scope uses `receipt.recorded_at`, named separately. The database is a
current projection, not a replay of historical events.

The export format is `{"schema_version":1,"delegations":[...]}`. Each delegation
requires `call_id`, `thread_id`, `timestamp`, `input`, and `output`. `input` is the
actual app-owned delegate call with `clientRequestId` and `target`. `output` is
its actual direct or MCP `structuredContent` result, including `childRunId` when
present; failed string/null outputs are allowed. Optional fields are `status`,
`parent_provider`, `parent_model`, `child_status`, `child_provider`,
`child_requested_model`, and `child_requested_options`. Exporters must project
these from actual records, not construct them from receipts.

`coverage.metric` is `app_owned_receipt_coverage`, not universal delegation
compliance. `counts` separates `call_attempts`, `unique_child_runs` (actual nonnull
child run IDs), and `distinct_receipt_decisions` (matched receipt IDs). Coverage,
effort, reason, and request-match rates use call attempts as their denominator.
`overrides`, `override_reasons`, `escalations`, and `relaunches` count distinct
matched receipt decisions, so retries cannot inflate them. Per-call lineage stays
available. `repeated_decision_ids_multiple_children` lists IDs spanning multiple
actual child runs, and `decision_id_multiple_children` flags their calls.

Database `out_of_scope_delegations` reports metadata-only counts with state
`observed_t3_projection_records`: `provider_native` counts subagent rows with
`origin=provider_native`, scoped by parent `thread_id` and `started_at`;
`top_level_threads` counts `t3_thread_launch` dynamic-tool calls by parent
`thread_id` and payload `startedAt`, including failed calls. These are observed
T3 records, without a receipt/decision-ID join or a claim of universal native
capture. An absent optional table returns null with `unknown_table_absent`;
other schema mismatches fail explicitly. Small exports retain null counts with
`unknown_not_observed`. Unknown never means zero.

Every scoped app-owned delegate call stays in the denominator, including failed
calls and calls without a receipt. Unmatched receipts are listed separately.
Joins use `clientRequestId` and actual output `childRunId`, never a guessed first
run. Reports compare the receipt with requested target, explicit effort, parent
metadata (thread, provider instance, and model), and child requested configuration.
Duplicate receipt decision IDs fail explicitly regardless of order or scope.
`options_state` marks malformed call, receipt, or child options; malformed options
never match, including empty arrays, nonobjects, invalid option IDs, and duplicate
IDs. An empty option object is valid. `receipt_before_call` compares receipt
`recorded_at <= startedAt` (export `timestamp`), or is null without a receipt;
a late receipt remains visible but does not prove a decision preceded dispatch.
Lifecycle completion is not success; configuration is not served-model evidence. Native observed model/effort and
requested-versus-observed remain unknown pending independent native-prefix
inspection. Native harness delegations and direct work are outside this audit's
coverage. T3 export evidence is caller-supplied; the tool cannot attest its origin.

## Observe

`observe` records **facts** that other systems already hold about recorded decisions. It
needs no action from the parent. `close` stays the only judged record: an observation
never creates or changes a close or a quality verdict (`report` derives labelled, facts-only outcomes
from observations for unclosed decisions; see Report), and
`audit` keeps `outcome_distribution` and every other existing key as before.

```bash
model-policy-ops observe --db ~/.t3/userdata/statev2.sqlite \
  [--since RFC3339] [--receipts FILE] [--outcomes FILE] [--observations FILE] \
  [--destinations FILE] [--git-window-days N] [--observe-state FILE] \
  [--observe-time-budget-seconds N]
```

It appends new fact rows to `observations.jsonl`, beside the receipts (`--observations`
overrides it), then ONE `source_run` row, and prints that row. The file follows the
private-ledger rules: mode 0600, exclusive lock, fsync, no symlink, a refused group- or
world-writable parent, and it must be a different file from the receipts, outcomes,
directives, destinations config, and T3 database. Observe also refuses a state, lock,
or sidecar file that aliases any of those inputs. Older CLIs never read the observation
ledger. Each read of a source is read-only. `--now` is refused.
`--since` limits the decisions in scope by `recorded_at`.

The previous T3 reader scanned the full `turn_items` projection on every hourly run.
On the 32 GB database, that took about four minutes and peaked at 5.8 GB RSS. Its long
read snapshot delayed WAL checkpoints. T3 auth lookups then took 10–36 seconds instead
of 0.1 ms, slowing other application database work.

The reader stores private progress in `$XDG_STATE_HOME/model-policy/observe-<ledger-key>.json`
(or `~/.local/state/model-policy` when `XDG_STATE_HOME` is unset, or `--observe-state`).
The key comes from the resolved observation-ledger path, so two
ledgers do not share a cursor. A first run starts at the newest 50,000 `turn_items`
rowids; rows before it receive no historical observations. Later runs start after the saved
high-water rowid. The rowid scan uses SQLite's primary index and reads at most 500 rows
per transaction. The 500-row cap limits each query's result and payload memory; a 200 ms
SQLite interrupt bounds how long a slow query can keep its read snapshot open. Observe
closes each scan transaction before it resolves run, usage,
and thread facts, so WAL checkpoints can advance between batches. Detail lookups cap
each result page at 500 rows and use indexed keys; the rowid scan and usage pages also
have a 200 ms SQLite interrupt, while other detail reads rely on the overall source
deadline. The append step still validates the existing ledger outside that source
deadline. It streams and validates one ledger row at a time, rejects rows over 4 MiB,
and uses a temporary disk-backed ID index so memory does not grow with ledger size.
A quarantined single-row retry increases its
read cap gradually, up to two seconds, so an unusually large delegation payload can be
processed without blocking the main rowid cursor. Successful rereads clear quarantine.
The observation ledger writes all new rows in one
append and one sync, so per-row durability work does not multiply with the fact count.

A private SQLite sidecar stores matched delegation rows and their refresh status. Calls
whose call and child-run statuses are still active are revisited first. Terminal calls
remain in round-robin refresh because later usage and thread facts can change; with more
than 500 known calls, those later changes can take several timer runs to appear. A
settled-call backlog alone does not mark T3 partial. A separate cursor resumes thread
snapshots. An unowned run before the first known owner can be a delegate call outside the
observed window, so observe marks the sharing count unknown. Later unowned follow-up runs
do not change the legacy owner count.

The 60-second default budget (`--observe-time-budget-seconds`, range 1–3600) starts before
CLI imports and reserves 60% for T3, 20% for destinations, and 20% for git. The hourly
timer leaves room between runs while the budget prevents catch-up from running without a
limit. Each source saves its cursor with the observation append; if its time slice ends,
the next run continues from that point. The T3 cursor is bound to the database identity
and the observation-ledger prefix, so appending a source-run row does not reset it, while
replacing or editing the ledger does. If a required lookup index is missing, T3 reports
the missing index instead of scanning. State writes use a private lock and atomic
replacement. Progress counts and the bounded first window appear under `incremental`.

`EXPLAIN QUERY PLAN` on the copied T3 database, using the production batch SQL,
showed these indexed lookups:

```text
batch rowid range:      MATERIALIZE selected; SEARCH i USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)
refresh rowids:         MATERIALIZE selected; SEARCH i USING INTEGER PRIMARY KEY (rowid=?)
parent run:             SEARCH p USING INDEX sqlite_autoindex_orchestration_v2_projection_runs_1 (run_id=?) LEFT-JOIN
child run:              SEARCH c USING INDEX sqlite_autoindex_orchestration_v2_projection_runs_1 (run_id=?) LEFT-JOIN
bounded result:         SCAN selected (materialized result contains at most 500 rows)
run status:             SEARCH orchestration_v2_projection_runs USING INDEX sqlite_autoindex_orchestration_v2_projection_runs_1 (run_id=?)
attempts by run_id:     SEARCH orchestration_v2_projection_run_attempts USING INDEX orchestration_v2_projection_run_ordinal_idx (run_id=? AND attempt_ordinal>?)
NULL attempts:          SEARCH orchestration_v2_projection_run_attempts USING INDEX orchestration_v2_projection_run_ordinal_idx (run_id=?) + temp B-tree for ORDER BY
failures by run_id:     SEARCH orchestration_v2_projection_turn_items USING INDEX orchestration_v2_projection_turn_items_run_ordinal_idx (run_id=? AND ordinal>?)
user follow-ups:        SEARCH orchestration_v2_projection_turn_items USING INDEX orchestration_v2_projection_turn_items_user_message_idx (thread_id=? AND ordinal>?) + temp B-tree for final ORDER BY term
runs by thread:         SEARCH orchestration_v2_projection_runs USING INDEX orchestration_v2_projection_runs_thread_ordinal_idx (thread_id=? AND ordinal>?)
thread snapshot:        SEARCH orchestration_v2_projection_threads USING INDEX sqlite_autoindex_orchestration_v2_projection_threads_1 (thread_id=?)
usage attempts:         SEARCH orchestration_v2_projection_run_attempts USING INDEX orchestration_v2_projection_run_ordinal_idx (run_id=?)
turn by provider ID:    SEARCH orchestration_v2_projection_provider_turns USING INDEX sqlite_autoindex_orchestration_v2_projection_provider_turns_1 (provider_turn_id=?)
```

The updated usage-turn page query was checked on the copied T3 database with
`EXPLAIN QUERY PLAN`: SQLite searches `orchestration_v2_projection_provider_turns_thread_idx` by
`thread_id=? AND rowid>?`, with no temporary sort.

The T3 projection has no index on `provider_turns.run_attempt_id`; observe resolves
turns through the indexed `thread_id` and filters matching attempt IDs in that result.
Rowid keyset paging avoids sorting the full thread for each page. Each usage page has
a 200 ms read slice. If a page cannot finish inside it, usage is reported as unavailable
for that run and can be retried later. A turn whose `thread_id`
differs from its attempt's thread and has no direct `provider_turn_id` link cannot be
found through those indexes. If an attempt has no thread ID, even a direct turn link
cannot prove that all of its turns were found, so usage is reported as unavailable.
Neither case appeared in the copied window; parity is verified for the observed rows only.

The benchmark used a low-priority raw file copy of the database and WAL, then ran
`PRAGMA wal_checkpoint(TRUNCATE)` on the copy. The first observe run scanned the 50,000
row window in 19.24 seconds and peaked at 489,848 KiB RSS. The next run scanned no new
rowids, refreshed 169 known calls, and took 16.35 seconds with 489,572 KiB peak RSS.
The first-window setting bounds catch-up; the 60-second budget bounds each hourly
invocation and reserves time for destinations and git.

For that same window, the old collector and incremental collector both produced 309
observations for 169 matched calls, with identical observation IDs and no missing or
extra facts. A concurrent indexed reader and SQLite writer ran on the copy during both
runs. Reader p95 was 0.033–0.035 ms and writer p95 was 15.242–15.443 ms, below the
50 ms target with no SQLite errors. The former full scan took about four minutes and
peaked at 5.8 GB RSS. The full model-policy-ops suite passed 1,863 tests, skipped 9,
and passed 171 subtests.

**Facts only.** Every observation has a source ID (T3 run ID, dot request ID or git sha),
the source timestamps, and `unknown` for fields that the source did not give, each with a
reason. Nothing is guessed. No transcript, prompt, report body or free text is copied. Every
retained string is a member of a fixed per-field set (listed under Sources) or a validated
ID or timestamp. A destination string outside its set is stored as `redacted`; a T3 status,
usage scope or turn status outside its set is stored as `other` with a dropped flag.
A T3 id outside the id grammar (`[A-Za-z0-9%:._-]`, up to 1024 characters) or an ordinal that is not a non-negative integer is stored as null with `unknown.<field>` set to `invalid_id` or `invalid_ordinal`; the row is kept.
These labels are fixed text in the output:

- completed ≠ accepted
- consumed ≠ correct
- tokens ≠ subscription cost

`observation_id` is the sha256 of (source, subject ID, fact, source timestamps, unknown). Collection
time is not part of it. A run appends only new IDs, so a repeat run with unchanged sources
appends 0 fact rows, and a changed state (a T3 run that goes from `running` to `completed`,
a completion time that was unknown and becomes known, a new destination lifecycle event)
appends new rows. The
old rows stay. The `source_run` row is `{run_id, started_at, finished_at, sources}`; each source
is `{state: ok|partial|unavailable|error, reason, subjects_checked, new_observations}` (git also has
`skipped` and `failed`). A source failure is recorded and exits 0, so it differs from zero events. Only a
bad ledger, bad arguments or a bad destinations file exit non-zero.

Sources:

- `t3` (`--db`): for receipts joined to `delegate_task` calls by `clientRequestId` and
  `childRunId`: the call, the child run status and timestamps, the **requested** provider
  instance, model and options/effort (with `served_model: unattested`), attempt statuses,
  terminal failures classed with `classify_failure` (the message is not copied), and
  per-turn token usage as T3 reports it. Usage is never summed; scopes can overlap.
  For the child run's thread it also records `t3_child_followups` `{child_thread_id, user_messages,
  followup_messages}` (messages after the brief; not repair rounds) and, when the thread has linked pull requests,
  `t3_child_pull_requests` `{child_thread_id, state: listed|unreadable, pull_requests: [{repository, number, state,
  merged_at?, closed_at?, synced_at?}], dropped, shared_with_decisions}` from T3's own snapshot, which may lag GitHub.
  It is recorded for every child thread, including an empty list, and timed by the thread's projection update
  (`thread_updated_at`), so a removed or unreadable link replaces an earlier merged snapshot. `shared_with_decisions`
  counts the decisions whose delegate calls ran in the thread. PR states are `open merged closed draft`, else
  `redacted`. Malformed entries are dropped and counted; a malformed payload never aborts the T3 source. No titles or
  message text are copied. An older T3 schema without these columns records neither fact.
  Requested config is a projection, not a copy. Option ids are `effort`, `reasoningEffort`,
  `thinking`, `fastMode`, `serviceTier` and `contextWindow`; a value is a bool or one of
  `minimal none low medium high xhigh max default priority flex 200k 1m`. Anything else is
  counted in `options_dropped`. The provider instance and the model must be known
  identifiers (driver-named instances and the model IDs of the catalog) or the ones the
  decision's receipt named; else they are `other` with `unknown` set. Statuses (`pending queued
  running completed failed cancelled interrupted`) and the usage scope (`main_agent`) are enums;
  an unknown value becomes `other` (`usage_scope_dropped`, `turn_status_dropped`).
- `destination:NAME`: for receipts whose chosen destination is NAME. The destination entry
  may add `"observe_command": ["/abs/path/observe"]` (an absolute argv). `observe` runs it
  with `--stdin`, sends the request IDs one per line (25 at a time), and reads
  `{"requests": {ID: {...}}}`, or `{"error": ...}` with exit 1. It uses the same bounded runner
  as the capacity probe (no shell, time limit, output limit). Per request it records the assignment
  status, the report outcomes and the lifecycle events (type, time, actor kind, reason).
  `{"found": false}` is a fact. The key is ignored by `route` and does not change the
  recorded `destinations_sha256`. A destination name must match `^[a-z0-9][a-z0-9_-]{0,31}$`
  (`model` is reserved); the config reader rejects any other name, and `observe` checks every
  row and the `source_run` against the same rule before it appends anything.

  **Adapter contract.** The command's output is untrusted. `observe` enforces the sets
  below itself, so an adapter that sends more is not trusted more, and an adapter should
  still send only these fields: `found` (bool); `status`, `task_kind`, `source`, `created_at`,
  `cancelled_at`, `cancelled_reason`; `reports[]` with `outcome`, `created_at`, `receipt_id`;
  `events[]` with `event_id` (integer), `type`, `at`, `actor_kind`, `reason`. Times are ISO
  8601 with a zone, else they are recorded as unknown. A string outside its set is stored as
  `redacted`; free text (report bodies, notes, check names) must not be sent.
  - `status`: `queued claimed finished cancelled`; `task_kind`: `audit audit-review dot-fix review unknown`;
    `source`: `drainer dot-fix agent unknown`; `actor_kind`: `owner dot system operator dot-fix`;
    `cancelled_reason`: `expired`; report `outcome`: `completed blocked failed cancelled abandoned`.
  - event `type`: `queued claimed working waiting blocked completed consumed accepted integrated
    abandoned cancelled delivery_sent delivery_uncertain reconciled check_started check_passed
    check_failed check_skipped`.
  - event `reason`: `new reclaim resume takeover wait get rejected expired` or `outcome=` plus a
    report outcome. Delivery kinds, check IDs and notes are not in the set; send nothing or they become `redacted`.
  - `receipt_id` has the shape `report_<hex and dashes>`; else it is dropped.
- `git`: only for closes whose evidence holds a `commit` reference in an absolute repo path
  that is a git repository root and holds the sha. No other repo is read. For each such close
  the window runs from the evidenced commit time to `min(now, close time +
  --git-window-days)` (default 14). In that window it records `same_file_later_commit`
  {sha, committed_at, overlapping_file_count} for commits that are not ancestors of the
  evidenced commit and touch a file it touched, and `explicit_revert_reference` {evidenced sha,
  reverting sha} when a later message says `This reverts commit <sha>` (git matches it; the
  message is never read). Every git row records the scope (repo, sha, file count) and window.
  **These are candidate follow-up signals, not fixes, rework or outcomes.** Every git call runs
  read-only: `GIT_*` cleared, `--no-optional-locks`, `GIT_NO_LAZY_FETCH=1` (git 2.44 or newer), so a
  partial clone never fetches objects into the repository (`close` verifies a commit the same way).
  Evidence that observe may not read is counted in `skipped` (`repo_missing`,
  `not_a_repository_root`, `sha_missing`: an explicit `missing` line from `git cat-file --batch-check`, never a failed command). An operational failure, including a repository-root probe or
  `cat-file` that fails (dubious ownership, unreadable config), is counted in `failed`
  (`git_timeout`, `git_output_too_large`, `git_failed`, `git_missing_local_object` for a missing
  object in a partial clone, `git_bad_output`). Any failure makes the source `partial` (some
  subjects were read) or `error` (none were), with the counts and reasons in the `source_run`
  and in the `latest_source_runs` of `audit`. A failed subject appends no facts.

`calibrate` adds the same `commit_evidence_verification` counts over current closes, and
`observe` prints (does not store) `recorded_commit_verification` for the closes it scans. These
are the close-time answers, beside the live re-check `observe` makes now.

`audit` and `calibrate` add an `observations` section: `state` (`observed`, `absent` or
`unreadable`), per-source `coverage` (decisions with at least one observation over decisions
in scope, as two numbers), the `latest_source_runs` states, `fact_type_counts` and the
`labels`. `calibrate` also lists, under `candidates_for_parent_followup_review`, the decisions
with an `explicit_revert_reference`; it gives no verdict.

Scheduling: the owner's existing scheduler runs `observe` hourly (for example a systemd user
timer or cron entry). Disable it by stopping that schedule. Roll back by deleting
`observations.jsonl`: nothing else reads it, and `audit` then shows `absent`.

## Launch facts

`resolve` accepts these optional facts about the task: `--purpose
execution|review|research|exploration|test-design`, `--proof-class oracle|judged|none`,
`--urgent`, `--irreversible`, `--inputs github|local` (where the task inputs are) and
`--sensitive` (the task touches credential or signing code). `route` also accepts `--critical-path` (work that must
not wait on quota pacing; see the wait order under forecast bands). Absent means unknown, never false. They are stored
in a top-level `launch_facts` object (with its own `schema_version`) and never in
`request`, so `request` keeps the 4ce5a3e shape and an older CLI can still replay a
new receipt with the base flags. A replay checks `request` and `launch_facts`
separately; a difference in either is rejected. A legacy receipt has no
`launch_facts`: replay without fact flags works, and adding facts is rejected. Facts
cannot be added later. Use a new decision ID.

## Outcomes

Each new implementation close includes optional top-level `op_proof_assessment`,
computed from the recorded receipt's op and launch proof class, never current flags
or the current policy pack. Its `state` is `missing_proof`,
`standard_oracle_review_required`, `oracle_bounded_requires_oracle`, or
`declared_consistent`; it also records `op`, `proof_class` and its limited `meaning`.
Missing proof takes precedence over a mismatch. This is only a declared op/proof
relationship signal, not proof adequacy, wrongdoing or acceptance. It never changes
outcome/check judgments or rejects a historical close. Nonimplementation closes omit it.

The assessment is optional on read, outside evidence entries and close replay
identity, with schema version 1 unchanged. Older readers can read and replay new
rows. An exact close replay returns its original metadata; an old close without the
field stays without it. No historical rows or evidence fields are rewritten.

`close` and `followup` write to `outcomes.jsonl`, beside the receipts file (override
with `--outcomes FILE`). A separate file keeps `decisions.jsonl` readable by older
CLIs, which never read it. The file follows the receipt rules: mode 0600, locked
append, fsync, no symlinks, and a refused group- or world-writable parent directory.
`--receipts` and `--outcomes` must be different files. `close`, `followup` and
`audit` compare real paths and device/inode, so a symlink or hardlink to the receipts
file is refused before any read or write.

Every read of the outcomes file (`close`, `followup`, `audit`) validates the whole
file and fails closed. `FAIL outcomes.jsonl line N: reason` names the first bad line:
not JSON, unknown `kind`, wrong `schema_version`, a missing key, a value outside its
enum, a bad ID, a reused ID, or broken lineage (a second first close for a decision;
`supersedes` or a follow-up `close_id` that is not an earlier close of the same
decision; superseding a close twice). The tool never skips, repairs or drops a row. New outcome fields must be optional
on read, or use a new schema version with a reader for existing versions.

To recover from a rejected ledger, stop its writers and preserve a private copy
of the original file. Repair the named line in a separate copy and validate that
copy with `audit --outcomes REPAIRED_FILE`. Replace the active file only after the
audit succeeds, preserving mode 0600, and keep the original as evidence. Do not
edit the active ledger while writers are running.

The unit is **one delegated decision and its attempts**. This is not the cost or
quality of a whole procedure or top-level task. A maker and a checker are two
decisions, and nothing here links them.

```bash
model-policy-ops close DECISION_ID --close-id CLOSE_ID \
  --outcome accepted --judged-by parent --check oracle \
  --evidence '{"type":"commit","repo":"/abs/repo","sha":"<40-hex>"}' \
  --evidence '{"type":"check","label":"unit","exit_code":0,"log":"/abs/log.txt","sha256":"<64-hex>"}' \
  --repairs 0 --owner-input unknown --note 'merged after tests' \
  --closer-thread CLOSING_THREAD

# Correct it later. The old record stays. Only the current close can be superseded.
model-policy-ops close DECISION_ID --close-id CLOSE_ID_2 --supersedes CLOSE_ID \
  --reason 'regression found' --outcome rejected --judged-by owner --check none

model-policy-ops followup DECISION_ID --followup-id FOLLOWUP_ID \
  --finding no_rework_found --checked-scope 'git log -- touched paths, 14 days'
```

- `--close-id` and `--followup-id` are required. Repeating an ID with identical
  inputs returns the stored record (`replayed: true`) and does not verify again, so
  the first observation stays. A changed repeat is rejected.
- A decision has one first close. A change needs `--supersedes CURRENT_CLOSE_ID` and
  `--reason`. A second correction of the same close is rejected under the lock.
- `--closer-thread` (optional, close only) records which thread closed the decision.
  `audit` compares it with the receipt's parent thread and reports
  `closer_thread_differs_from_parent` (`true`, `false`, or `null` when not given). That
  is a flag for review. The tool does not decide who may close a decision, and the
  value is a caller claim.
- `--outcome`: `accepted`, `accepted_after_repair`, `rejected`, `abandoned_by_choice`,
  `superseded`, `unknown`. The outcome `superseded` means the delegated result was
  not used because other work replaced it. It has no link to `--supersedes`, which
  corrects an earlier *close record*. `--judged-by parent|independent|owner` and `--check
  oracle|independent_judged|maker_judged|none` are **caller claims**, stored as such.
  Use `unknown` rather than guess. `--repairs` absent means unknown, not zero.
  `--owner-input` defaults to `unknown`; `none_observed` means the parent looked.
- `--check oracle` needs a `check` reference. A check with a nonzero exit cannot back
  an accepted outcome. A check reference only says what the parent reports about a
  run; the tool never reruns it.
- `followup` needs a current close. Findings: `no_rework_found`, `fix_commit`,
  `revert`, `reopened`, `defect_reported`. The last four need evidence.
  `--checked-scope` is required. **No follow-up means not checked.**
  `no_rework_found` holds only within its stated scope and time.

### Evidence references

`--evidence` (repeatable, up to 8) takes one JSON object. JSON avoids a delimiter
grammar that breaks on `#` or `@` in paths, and unknown keys are rejected.

| type | keys | verified_state |
|---|---|---|
| `commit` | `repo` (absolute), `sha` (40 hex) | `exists` or `unverified_missing` (`git cat-file`, no shell); the row also records `evidence_verification` |
| `file` | `path` (absolute), `sha256` | `hash_matches`, `mismatch`, `missing`, `unreadable`, `too_large` |
| `check` | `label`, `exit_code` 0-255, `log` (absolute), `sha256` | `log_hash_matches`, `mismatch`, `missing`, `unreadable`, `too_large` |
| `pr` | `repo` (`owner/name`), `number` | `claim_only` (never fetched) |

Verification checks existence or a file hash. It never shows that a check ran, or
that the work is good. A file is read without blocking and only if it is a regular
file (a FIFO, directory or device is `unreadable`); reading stops at 256 MiB
(`too_large`). Commit checks ignore `GIT_*` environment variables. A failed
verification is stored and shown, and it does not count in
`accepted_with_hash_matched_check_log`: true only for an accepted close whose
`oracle` check logs all hash-match. The `exit_code` is the caller's claim; the summary
lists it as `exit_codes_claimed`. Malformed references are rejected.

**Commit verification.** `unverified_missing` is the legacy value and also covers a git
failure. A row with commit evidence therefore also carries an optional top-level
`evidence_verification`: a list with one item per evidence entry, `null` for a non-commit
entry and `{"state": "exists"|"missing"|"timeout"|"error", "recorded_at": ISO}` for a
commit. Only an explicit `missing` answer from git is `missing`; a timeout, signal,
non-zero exit or unreadable repository is `timeout` or `error`, and `verified_state` stays
`unverified_missing` for those. The key sits beside `evidence`, not inside an entry,
because older CLIs ignore an unknown top-level key but treat a changed evidence entry as
changed replay input. A row written before this field has no `evidence_verification`;
every reader reports its commit evidence as `not_recorded`. It is never reclassified as
missing or failed, because nothing was observed at the time. Rows are not re-verified.

`--note`, `--reason` and `--checked-scope` hold one line of up to 280 characters.
The caller supplies them and **the tool does not filter them for secrets**. Do not
put prompts, results or credentials in them. Records hold enums, counts, hashes, references and these short caller-supplied notes.

### Audit additions

`audit` adds keys; existing keys keep their meaning. `audit` reads `--outcomes` (or
the file beside `--receipts`). A missing file means every decision is unclosed.

- `outcomes`: `descriptive_only`, `causal_claims: none`, a confounding statement,
  `denominators` (call attempts and distinct decisions), `close_coverage` (decisions
  and calls), the outcome distribution over **distinct decisions** with unclosed
  counted explicitly, `judged_by`, `check`, `owner_input`, superseded closes,
  follow-up coverage, strata by launch `purpose`, `proof_class` and `op`
  (`unknown` when absent), and child-usage coverage. Repeated calls on one
  decision do not inflate any distribution.
- Per delegation: `decision_outcome` (state, current close, chain), `followups`,
  `launch_facts`, `observed_child_usage`, `parent_overhead_usage: unknown`, and
  `quota` (the launch snapshot from the receipt, labeled `unattributed_account_state`).
- `observed_child_usage` (database mode only) joins `child_run_id` to
  `run_attempts.run_id`, then to `provider_turns` by `run_attempt_id` or
  `provider_turn_id`. It lists every attempt and turn separately and projects
  only `turnTokenUsage` status, scope, `hasSubagents` and five token counts. It
  never sums; scopes can overlap. `partial` keeps its reported counts, labeled
  partial. `unavailable` or missing status gives null counts. `hasSubagents` other
  than `false` means nested usage is unknown. Absent tables give
  `unknown_table_absent`, a changed schema gives `unavailable`, and neither stops the
  audit. This is T3-reported main-agent turn usage. It is not quota, not dollars, and
  not procedure cost.

Closes are caller claims and can lean optimistic, especially when the parent
judges its own delegation. The audit reports close coverage and does not assume compliance. See
[routing-guidance.md](routing-guidance.md) for how to use these records.

## Offline journey and JSON shapes

You can run the whole flow with two hand-written files and no T3 or network.

<!-- example:catalog -->
```json
{"data": {"providers": [{"instanceId": "codex-main", "driver": "codex",
  "models": [{"slug": "gpt-6-luna",
              "options": [{"id": "reasoningEffort",
                           "values": [{"id": "medium"}, {"id": "high"}]}]}]}]}}
```

Catalog rules (`--available FILE`): `providers[].instanceId` is the account;
`driver` is `codex`, `claudeAgent` or `grok`; `models[].slug` must equal the pack
model exactly. The effort option is an object `{"id": ..., "values": [...]}` in
`options`, and `id` must be `effort`, `reasoningEffort` or `thinking`. **Each value
is an object `{"id": "medium"}`, not a bare string.** A bare-string list gives
`schema_mismatch`; an option id such as `reasoning_effort`, or a missing option, gives
`unknown_effort_surface`; a value that is not listed gives `unsupported_effort`. All
three leave `target` null. The model here is the one that the
`fanout.dollar-tight` op names; run `show OP` for the model and effort that another
op needs.

<!-- example:export -->
```json
{"schema_version": 1, "delegations": [
  {"call_id": "call-1", "thread_id": "PARENT_THREAD", "timestamp": "2026-10-08T12:00:00Z",
   "input": {"clientRequestId": "DECISION_ID",
             "target": {"providerInstanceId": "codex-main", "model": "gpt-6-luna",
                        "options": {"reasoningEffort": "medium"}}},
   "output": {"childRunId": "child-1"}}]}
```

```bash
model-policy-ops resolve fanout.dollar-tight --available catalog.json \
  --purpose execution --proof-class oracle --record --decision-id DECISION_ID \
  --parent-model PARENT_MODEL --parent-provider-instance PARENT_INSTANCE \
  --parent-thread PARENT_THREAD --receipts state/decisions.jsonl
model-policy-ops close DECISION_ID --receipts state/decisions.jsonl --close-id CLOSE_ID \
  --outcome accepted --judged-by parent --check none
model-policy-ops audit --export export.json --receipts state/decisions.jsonl --thread PARENT_THREAD
```

`resolve` prints one JSON object. Read `target` (pass it to `delegate_task` unchanged)
and `decision_id`. `close` and `followup` print the stored record (`replayed: true`
on an exact repeat). `audit` prints JSON by default; `--json` is accepted.

### Audit JSON reference

Top-level keys: `schema_version`, `scope`, `coverage`, `counts`, `effort_explicitness`,
`reason_coverage`, `request_match`, `route_coverage`, `overrides`, `override_reasons`, `escalations`,
`relaunches`, `repeated_decision_ids_multiple_children`, `unmatched_delegations`,
`unmatched_receipts`, `destination_routes`, `spend_first`, `out_of_scope_delegations`, `limits`, `delegations`, `outcomes`, `observations`.

- `counts`: `call_attempts`, `unique_child_runs`, `distinct_receipt_decisions`.
- `observations`: `state`, `reason`, `labels`, `file`, `coverage` (`{SOURCE: {decisions_with_observation, decisions_in_scope}}`),
  `coverage_meaning`, `latest_source_runs`, `fact_type_counts`, `source_runs`. See Observe.
- `route_coverage`: `route`, `resolve`, `no_receipt` (call attempts by receipt origin), `denominator`,
  `rate` (route calls over all calls) and `meaning`.
- `destination_routes`: `{DESTINATION: {"decisions": n, "ids": [DECISION_ID, ...]}}` for route receipts whose
  chosen destination is not `model`. Those receipts never appear in `unmatched_receipts`.
- `spend_first`: `eligible` (route receipts whose purpose a destination listed at record time), `offered`
  (destination chosen), `fallback` (`facts`, `parent`, `capacity`, each `{REASON: count}`) and `meaning`.
- `spend_first_daily`: one row per UTC day of `recorded_at`: `receipts`; `classes`, which is exclusive and sums
  to `receipts` (`offered`, `fallback`, `not_eligible`, `resolve_listed`, `resolve_not_listed`, `not_considered`,
  `unrecorded`); `reasons` per class (`{"SOURCE:REASON": n}` for a fallback, `{"REASON:PURPOSE:OP": n}` for not
  eligible, for example `purpose_not_listed:execution:review.audit`), counting each receipt once per reason; `eligible`
  (`offered` + `fallback`), `offered` and `offered_share_of_eligible`. `unrecorded` holds receipts written before
  revision 26 and route receipts with an empty block.
- `outcomes` (descriptive only, over distinct decisions):
  - `unit`, `descriptive_only`, `causal_claims`, `confounding`, `closes_are_claims`:
    fixed statements.
  - `denominators`: `call_attempts`, `distinct_decisions`, `calls_without_receipt`.
  - `close_coverage`: `decisions_closed`, `decisions`, `rate`, `calls_on_closed_decisions`,
    `call_attempts`.
  - `outcome_distribution`: `{"closed": {OUTCOME: count}, "unclosed_or_unreadable": n}`.
  - `judged_by`, `check`, `owner_input`: `{VALUE: count}` over closed decisions.
  - `accepted_with_hash_matched_check_log`: count of accepted decisions whose check logs hash-match.
  - `commit_evidence_verification`: over the current close of each closed decision,
    `denominator` (commit evidence entries), `exists`, `missing`, `operational_failure`
    (`timeout` or `error`), `not_recorded` (rows without `evidence_verification`) and `meaning`.
  - `closer_thread`: `differs_from_parent`, `not_recorded`, `meaning`.
  - `superseded_closes`, `broken_chains`, `outcome_rows_for_unknown_decisions`.
  - `followup_coverage`: `closed_decisions_with_followup`, `closed_decisions`, `meaning`.
  - `strata`: `purpose`, `proof_class`, `op`, each `{VALUE: {"decisions": n, "closed": {...},
    "unclosed_or_unreadable": n}}`; `unknown` when the fact was not given.
  - `child_usage_coverage`: `unique_child_runs`, `by_state`, `label`.
- `delegations[]`, one per call: the receipt-match fields (`call_id`, `decision_id`,
  `matched_receipt`, `request_match`, `parent_match`, `receipt_before_call`,
  `options_state`, and others), plus
  - `decision_outcome`: `{"state": "unclosed"|"closed"|"broken_chain"|"no_receipt",
    "current": {...}, "chain": [CLOSE_ID, ...]}`. `current` is null unless `closed`, and
    holds `close_id`, `outcome`, `judged_by`, `check`, `repairs`, `owner_input`,
    `recorded_at`, `evidence_summary`, `supersedes`, `commit_verification` (one of
    `exists`, `missing`, `operational_failure`, `not_recorded` per commit evidence entry),
    `closer_thread` and `closer_thread_differs_from_parent`. Read the close
    result here. The older per-call key `outcome` is always `unknown`.
  - `followups`: `{"state": "observed"|"not_checked", "items": [...]}`; each item also has
    `commit_verification`, as for a close.
  - `launch_facts`, `observed_child_usage`, `parent_overhead_usage`, `quota`.


Typed historical replays also expose `historical_non_actionable: true` in default and `--brief` output; the default reason instructs callers to re-route for current evidence. The stored receipt is unchanged.
