# model-policy-ops

Read the selected model-choice-policy pack, make an explicit delegation decision,
and audit its use. This CLI never dispatches work or changes running threads.
`check`, `list`, and `show` keep their existing output contracts.

## Procedure

1. Choose an op from `list`, based on the task and its verification boundary.
2. Run `resolve`. Read `policy_context`, evidence, gaps, account choices, quota,
   and alternatives. Choose an account explicitly when more than one matches.
3. Record the decision with caller-supplied parent metadata and a unique decision
   ID. Pass the returned `target` unchanged and `decision_id` as
   `delegate_task.clientRequestId`. Supply the task separately to T3.
4. Run `audit` for the parent thread or a time range. Inspect unmatched calls,
   mismatches, missing effort, and unknown native evidence.

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
