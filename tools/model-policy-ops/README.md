# model-policy-ops

Read the selected model-choice-policy pack, make an explicit delegation decision
(with `resolve`, or with `route` to apply quota, availability and directives),
record how it ended, and audit its use. This CLI never dispatches work or changes running threads.
`check`, `list`, and `show` keep their existing output contracts.

## Procedure

1. Choose an op from `list`, based on the task and its verification boundary.
2. Run `resolve`. Read `policy_context`, evidence, gaps, account choices, quota,
   and alternatives. Choose an account explicitly when more than one matches.
3. Record the decision with caller-supplied parent metadata and a unique decision
   ID. Pass the returned `target` unchanged and `decision_id` as
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

Do not submit a null target. `launch_ready` means the exact model and explicit
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
2. Read `routing.judgment_required` and `routing.judgment_reasons`. When it is
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
    `final_target_null`;
  - `evidence` (label `descriptive_only`), `calibration_notes` and `inputs`.

The same inputs give the same output, apart from `recorded_at` (and a random
decision ID when you give none). A replay with the same `--decision-id` returns the
stored receipt and does not route again.

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
`directive_max_days` and `lineage_key`. `lineage_key` maps a provider to the
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
measure of subscription value. There are no weights.

Pace is `on_track` when every pace window that the account reports has a clawmeter
`forecast` below 100. It is `at_risk` at 100 or more. It is `unknown` for an
unmapped account, a source that is not a plain snapshot, a missing forecast, an
expired window, or no pace window. Unknown is never headroom. The 5-hour window
only excludes an account when it is exhausted.

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
- A review op with no maker needs judgment (`review_without_maker`).

### Judgment reasons

`no_eligible_pair`, `unvalidated_candidate`, `requires_unknown`, `pace_not_on_track`,
`auth_config_events`, `independence_requested_only`, `independence_caller_claim`,
`independence_maker_unknown`, `independence_line_unknown`, `final_metered_without_directive`,
`final_avoided`, `final_quota_exhausted`, `final_not_runnable`, `final_requires_not_met`,
`final_independence_bypassed`, `final_target_null`, `review_without_maker`, `directive_conflict`, `destination_inputs_unknown`,
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
each name to its reason. A maker with several runs is checked against every known run vendor; one
run with an unknown vendor still adds the judgment reason. If the destination has
no `vendor`, the maker vendor is unknown for any maker run, or the required level is finer than `vendor` (family
or model, which a destination cannot show), the destination can still be chosen, and the judgment
reason `destination_independence_unknown` is added.

With `--now` (simulation) no probe runs. The destination state is `not_probed_in_simulation`
and the output is today's simulation. A purpose that no destination lists is unchanged,
except that `routing.destination` is `{chosen: "model", considered: []}` when a
destinations file exists. `--override` does not suppress the destination; add
`--skip-destination` as well to dispatch the override.

When a destination is chosen, the output is a valid receipt with `target: null`,
`launch_ready: false`, every nested `target` key null, a top-level `destination: NAME`
and `destination_offer`: `{name, request_id, purpose, how_to, submit, capacity}`.
`request_id` is the `decision_id`, so the worker's reports join to the receipt. When
`--inputs` is unknown, the judgment reason `destination_inputs_unknown` is added: the
destination needs one pushed GitHub input; if inputs are local, re-run with
`--inputs local`. Every nested `target` key is cleared after the offer, the capacity JSON and
the `submit` object are in place, so no field from the config or the probe can bring one back.

Judgment when a destination is chosen. The model-pair reasons (pace, unvalidated candidate,
quota, auth_config, model independence, model freshness) stay in `judgment_reasons`, tagged
`applies_to: "model_fallback"` and `informational: true`. `judgment_required` then comes only
from destination-level reasons (`applies_to: "destination"`): `destination_inputs_unknown`,
`review_without_maker`, `destination_independence_unknown`. When the model is dispatched,
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
never creates, changes or implies a close, an outcome label or a quality verdict, and
`audit` keeps `outcome_distribution` and every other existing key as before.

```bash
model-policy-ops observe --db ~/.t3/userdata/statev2.sqlite \
  [--since RFC3339] [--receipts FILE] [--outcomes FILE] [--observations FILE] \
  [--destinations FILE] [--git-window-days N]
```

It appends new fact rows to `observations.jsonl`, beside the receipts (`--observations`
overrides it), then ONE `source_run` row, and prints that row. The file follows the
private-ledger rules: mode 0600, exclusive lock, fsync, no symlink, a refused group- or
world-writable parent, and it must be a different file from the receipts and the
outcomes. Older CLIs never read it. Each read of a source is read-only. `--now` is refused.
`--since` limits the decisions in scope by `recorded_at`.

**Facts only.** Every observation has a source ID (T3 run ID, dot request ID or git sha),
the source timestamps, and `unknown` for fields that the source did not give, each with a
reason. Nothing is guessed. No transcript, prompt, report body or free text is copied. Every
retained string is a member of a fixed per-field set (listed under Sources) or a validated
ID or timestamp. A destination string outside its set is stored as `redacted`; a T3 status,
usage scope or turn status outside its set is stored as `other` with a dropped flag.
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
  `not_a_repository_root`, `sha_missing`). An operational failure is counted in `failed`
  (`git_timeout`, `git_output_too_large`, `git_failed`, `git_missing_local_object` for a missing
  object in a partial clone, `git_bad_output`). Any failure makes the source `partial` (some
  subjects were read) or `error` (none were), with the counts and reasons in the `source_run`
  and in the `latest_source_runs` of `audit`. A failed subject appends no facts.

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
`--sensitive` (the task touches credential or signing code). Absent means unknown, never false. They are stored
in a top-level `launch_facts` object (with its own `schema_version`) and never in
`request`, so `request` keeps the 4ce5a3e shape and an older CLI can still replay a
new receipt with the base flags. A replay checks `request` and `launch_facts`
separately; a difference in either is rejected. A legacy receipt has no
`launch_facts`: replay without fact flags works, and adding facts is rejected. Facts
cannot be added later. Use a new decision ID.

## Outcomes

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
| `commit` | `repo` (absolute), `sha` (40 hex) | `exists` or `unverified_missing` (`git cat-file`, no shell) |
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
- `outcomes` (descriptive only, over distinct decisions):
  - `unit`, `descriptive_only`, `causal_claims`, `confounding`, `closes_are_claims`:
    fixed statements.
  - `denominators`: `call_attempts`, `distinct_decisions`, `calls_without_receipt`.
  - `close_coverage`: `decisions_closed`, `decisions`, `rate`, `calls_on_closed_decisions`,
    `call_attempts`.
  - `outcome_distribution`: `{"closed": {OUTCOME: count}, "unclosed_or_unreadable": n}`.
  - `judged_by`, `check`, `owner_input`: `{VALUE: count}` over closed decisions.
  - `accepted_with_hash_matched_check_log`: count of accepted decisions whose check logs hash-match.
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
    `recorded_at`, `evidence_summary`, `supersedes`, `closer_thread` and
    `closer_thread_differs_from_parent`. Read the close
    result here. The older per-call key `outcome` is always `unknown`.
  - `followups`: `{"state": "observed"|"not_checked", "items": [...]}`.
  - `launch_facts`, `observed_child_usage`, `parent_overhead_usage`, `quota`.
