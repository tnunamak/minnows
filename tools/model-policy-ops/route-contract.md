# `route` delivery contract (v1, revision 5)

Status: revision 5, 2026-10-10. Revision 5 applies the checker's round-2 findings (report r2): all
child runs of a maker, the directive cap on write and on read, and final-pair judgment after an
override (see the changelog). Revision 4 applies the independent checker's
findings (report r1): maker provenance from the T3 child run, a model line key for
independence and freshness, override safety, directive validation, alternates-only
`candidates`, and the AC6 clustering key. Revision 3 applied the scope review decision on evidence classification and the amendments from the owner-side
scope review (Astra). The bounded v1 direction is accepted. The implementation is
not accepted yet. This file is the spec for the maker, the independent checker,
and the acceptance journey.

## Motivation and scope of the evidence

In a local decision ledger, parents overrode the pack in most recorded
decisions. Their override reasons showed six kinds of decision that `resolve`
cannot make, so parents made them by hand, often repeating the same reason:

| ID | Decision | Addressed by `route`? |
|----|----------|-----------------------|
| D1 | Which account serves the model, given quota state and reset times | Yes |
| D2 | Which vendor substitutes when the op's arm is blocked | Yes, within quality eligibility |
| D3 | Checker independence from the maker | Yes |
| D4 | Time-bounded owner instructions, repeated on each launch | Yes, as directives with an expiry |
| D5 | A pack model that is missing from the catalog, or a newer model on the same line | Yes (no override seen; a silent failure is possible) |
| D6 | An account that is failing now (rate limits, transport errors) | Yes |

A large share of overrides chose a different arm to fit a specific task. `route`
does not automate that: it stays parent judgment, and `calibrate` reports
repeated clusters as candidate new ops. The ledger is one owner's, over about
one day. It is not a population rate, and it does not prove that each feature is
necessary.

## What v1 is, and what it is not

v1 is **operational adaptation plus proposals**. It is not automatic quality
calibration.

- Operational adaptation is automatic at each `route` call. The inputs are quota
  pace and exhaustion (clawmeter), availability classified from T3 failure
  events, live catalog presence, and directive expiry. These inputs change the
  route without a human step.
- Quality evidence (outcomes, closes) is descriptive decision support. It never
  reorders candidates. Assignment is not random, closes are mostly parent claims,
  procedure costs overlap, and parent overhead is unknown.
- Proposals (`calibrate`, and the notes embedded in each `route` output) suggest
  changes to the pack. They never apply a change.

Non-goals: learned quality routing, dot routing (the dot-first skill rule stays),
dispatch, dollar estimates, T3 app changes, network calls other than the existing
local CLIs, and redistribution of AA data.

## Mechanism and policy are separate

The **mechanism** is the code in `lib/routing.py`. It contains no model names and
no preferences. The **policy** is the pack data. Each op has an optional
`candidates` list. Every candidate is an exact arm with eligibility data:

```json
{"provider": "claude", "model": "claude-opus-5-5", "effort": "high",
 "basis": "primary | task_benchmark_prior | unvalidated",
 "evidence_refs": ["catalog://..."], "prior": {"board": "AA Terminal-Bench 4.0", "board_cited_for_op": true, "effort_matched": true,
           "harness_matched": false, "note": "48.0 vs 29.8 at medium (pack gap text, 2026-10-07)"},
 "known_gaps": ["No local validation as a reviewer"],
 "requires": {"proof_class": ["oracle"]}}
```

- `candidates` lists the **alternates only**. The primary is always the op's
  `expands_to` (`basis: primary`), implied and not repeated. When `candidates` is
  absent, there are no alternates. A `candidates` row has the basis
  `task_benchmark_prior` or `unvalidated`. `resolve`, `check`, `list` and `show`
  do not depend on candidates beyond their schema.
- `task_benchmark_prior` is a prior. It is not proof that the arm is adequate for the
  task. It is allowed only when all three of these conditions are true:
  - the board is one that the pack cites for this op's task family;
  - the comparison is at the same effort;
  - the candidate is clearly at or above the primary on that board.

  Even then, the harness differs: the board's harness is not T3, Claude Code or
  Codex. These candidates are eligible for automatic selection, and the output
  labels them `evidence: prior`.
- `unvalidated` covers these cases:
  - weaker arms;
  - arms with no comparable evidence;
  - aggregate-only support (an intelligence index or ARC);
  - mixed boards that disagree;
  - a board from another task family (task transfer).

  An unvalidated candidate stays available, but selecting it needs judgment.
- `requires` is optional. `proof_class` uses the existing launch-fact values. When
  the launch fact is absent (unknown), a candidate that requires a value is not
  eligible without judgment.

## Inputs

1. **Pack**: `candidates`, and an optional `routing` object with these declared
   parameters:
   - `pace_windows`, for example `["7d", "7d All"]`;
   - `failure_lookback_minutes` and `failure_demote_count`;
   - `review_task_families`;
   - `independence_default`, for example `vendor` for review. A review op
     (its `task_family` is in `review_task_families`) with no `independence_default`
     requires `vendor`; other ops require nothing;
   - `directive_max_days` (14): no directive may expire later than this;
   - `lineage_key`: maps each provider to the model-catalog field that names a
     model line, for example `{"claude": "family", "codex": "tier", "grok": "family"}`.
     A **line** is (provider, value of that field). Independence by family and the
     D5 freshness check both use it. A provider with no mapping, or a model that the
     model-catalog pack lacks, has line `unknown`.

   `check` validates all of them.
2. **Live catalog**: `--available FILE|auto`. The existing reader is unchanged.
3. **Quota**: `--quota FILE|auto` (`clawmeter --json`). It adds
   `forecast.windows.<name>.projected_pct` and records `fetched_at`, the cache
   state, and `resets_at` for each window. Pace class:
   - `exhausted`: the existing rule, utilization of 100 or more on a relevant
     window;
   - `at_risk`: projected 100 or more on a pace window;
   - `on_track`: all pace windows have a projection below 100 and the data is not
     stale;
   - `unknown`: no forecast, an expired reset time, a source error, or an
     unmapped account.

   Unknown is never treated as headroom.
4. **Account map**: `~/.config/model-policy/accounts.json` (override it with
   `--accounts`). It maps each T3 instance to `{quota_provider, quota_source,
   billing}`. Only an explicit `billing: subscription` passes. `metered`, a missing
   value, or an unmapped instance is excluded, unless an active `allow-metered`
   directive matches. Unknown billing never bypasses the metered exclusion.
5. **Directives**: `directives.jsonl` beside the receipts. Effects:
   - `avoid`: hard exclusion.
   - `prefer`: a rank key only.
   - `authorize`: a scoped permission to act on an `unvalidated` candidate without
     a new judgment. It does not change the evidence basis or remove a gap. The
     output keeps `basis: unvalidated` and adds `selection_basis:
     authorized_exception` with the directive ID, source and expiry. This is
     different from `selection_basis: primary` and from `selection_basis:
     evidence_prior`.
   - `allow-metered`.

   The match keys are `provider`, `model`, `account` and `op`. `--until` (RFC 3339),
   `--reason` and `--source owner|lead` are required. `--until` cannot be later than
   `directive_max_days`. `directive add` and `directive end` record the real clock and refuse `--now`, so
   a directive cannot be dated into the past or future. `route --now T` is a simulation: it cannot be recorded, every `target` key in its output is null (nested ones included), and it has `launch_ready: false`, `simulated_target` and `simulation`, and it never replays a stored receipt. A reader ignores a live row, reports it in
   `directives_rejected` (ID and reason), and `directive list` shows it as state `rejected`, when
   `until - recorded_at` exceeds `directive_max_days` or `recorded_at` is later than the
   evaluation time. `directive add` rejects a `provider`, `model` or `op` that the
   pack, the live catalog and the model catalog do not declare, and warns for an
   unknown `account`. `route` reports `directives_unmatched`: the active directives
   that matched no pair. A directives file that group or world can write is refused.
   A `prefer` never defeats a
   hard filter or quality eligibility. When an `avoid` and a `prefer` or
   `authorize` match the same pair, `avoid` wins and the conflict is reported.
   Expired directives are ignored. `directive list` shows active, expired, ended and rejected
   directives.
6. **Harness telemetry** (read-only): `--db ~/.t3/userdata/statev2.sqlite`.
   - Failure events come from terminal-failure turn items. They join to the
     instance through the run. If any event does not join, availability is
     `unknown` for all instances, and the count is reported.
   - Classes, with patterns on the observed text:
     - `rate_limit`: "rate limit reached".
     - `transport`: "Connection error", "stream closed", "stream disconnected",
       "Connection refused".
     - `auth_config`: "could not authenticate", "No conversation found", "Insufficient
       context allowance", "still running background agents".
     - `content_policy`: "flagged for possible".
     - `unknown`: everything else, for example "Claude API unknown" and "Provider
       turn failed".
     - User cancellations and interruptions are not failures.
   - Only `rate_limit` and `transport` count toward demotion. `auth_config` on an
     instance is reported and makes `judgment_required` true when it is the
     chosen instance. The other classes are reported only.
   - A missing db or a changed schema gives `availability: unknown`. It does not
     mean healthy, and route does not crash.
7. **Model catalog pack**: family, tier and status. With `lineage_key` it gives the
   model line, which is used for D5 and for family-level independence.
8. **Maker provenance**: `--maker DECISION_ID`, or `--maker-model PROVIDER:MODEL`
   as a caller claim for direct or native work.
   - Provenance levels:
     - `t3_run_config`: the maker's T3 child run has the same instance and model
       as the receipt target. Find the run through the audit join. An effort
       difference does not change provenance; it is reported in `effort_mismatch`.
     - `receipt_only`
     - `caller_claim`
     - `unknown`: the receipt has no target, or the run's instance or model differs
       from the receipt, or the run's instance is not in the live catalog, or the
       decision ID started several runs that differ in instance or model
       (issue `multiple_child_runs`; `runs` lists each one).
   - When the run is found, the vendor is the driver kind of the run's instance and
     the requested model is the run's `modelSelection`, never the receipt's. If the
     run differs from the receipt, the checker is still filtered against the run.
     With several runs, the checker is filtered against the vendors, lines and models of
     all of them. Runs that agree keep `t3_run_config`; an effort-only difference goes in
     `effort_mismatch`.
     `--maker-model` takes a provider that the pack or the live catalog names.
   - Two facts are kept apart in the output:
     - `maker_vendor: {value, basis}`, with basis `t3_driver_kind`, or `receipt_arm`
       when the receipt's instance is not in the live catalog, or `caller_claim`.
       A Claude driver cannot serve an OpenAI model, so the driver establishes the
       vendor.
     - `served_model: unattested`. T3 records the requested model, not the
       served model. So the served model is
     always `unverified`.
   - Vendor-level independence holds from provenance `t3_run_config` or
     `receipt_only`, because the T3 driver kind fixes the vendor.
   - Family-level and model-level independence rest on the requested model only.
     When the chosen pair depends on them (same vendor), `judgment_required` is
     true.
   - A different version of the same line is not independent (`gpt-5.6-sol` and
     `gpt-6.1-sol` share the line (codex, sol); `gpt-6-luna` is another line). When
     a line is `unknown`, family independence does not pass: the pair is removed and
     `independence_line_unknown` is a judgment reason.
   - `--independence vendor|family|model` sets the requirement. Default: the
     pack's `independence_default` for review families. `routing.independence.required_source`
     says where the level came from: `flag`, `pack` or `mechanism_default`.

## Algorithm

`route OP` expands each candidate to (arm, account) pairs.

**Filters** apply in this order. Each removal records its reason.

1. Not runnable (the existing `resolve_arm` rules).
2. Billing is not `subscription`, and no `allow-metered` directive matches.
3. An `avoid` directive matches.
4. A relevant window is exhausted.
5. The independence requirement fails against the maker.

An override (`--override`) stays allowed, because the parent decides. The final
pair after an override goes through the same filters and the same judgment rules as a
routed pair. The receipt records `final.billing`, `final.filters_bypassed` and
`final.pace`. Each bypassed filter raises a `final_*` reason: `final_not_runnable`,
`final_requires_not_met`, `final_metered_without_directive`, `final_avoided`,
`final_quota_exhausted`, `final_independence_bypassed`. The pair rules below (basis,
`requires`, pace, `auth_config`, independence provenance, `review_without_maker`, newer GA
model) apply to the final pair too. A final pair that the pack does not name is
`unvalidated`; a pair matches a pack candidate by exact provider, model and effort, so
an effort-only override is `unvalidated`.
A final pair with no target raises `final_target_null`.

**Rank** is lexicographic, and every key is printed:

1. Eligibility. `primary` and `task_benchmark_prior` (with `requires` met), and
   `authorize`-d exceptions, rank first. Candidates that need judgment rank after
   them.
2. A `prefer` directive.
3. Pace class: `on_track`, then `unknown`, then `at_risk`.
4. Availability: healthy, then unknown, then demoted.
5. Pack candidate order.
6. Load balancing: lower `projected_pct` on the binding pace window first.
   Unknown sorts last.
7. Instance ID.

Key 6 is a declared **load-balancing heuristic**, not a measure of subscription
value. The clawmeter projection already includes current use and the time to
reset. The output also reports `remaining_pct` and `resets_at`. No weights or
scalar utilities are used. Identical inputs give identical output.

**`judgment_required`** is true when any reason in this list applies. The output
lists the reasons. Each reason has `applies_to`: `routed` or `final`. Without an override,
all are `routed`. With an override, the list keeps the routed reasons for the record, and
`judgment_required` is true only when a non-informational `final` reason exists.
`independence_line_unknown` and `directive_conflict` describe the routing and are `routed`.

- There is no eligible pair (`target` is null).
- The chosen candidate is `unvalidated` and no `authorize` directive covers it.
- A `requires` fact is unknown.
- The chosen pair is not `on_track`.
- `auth_config` events exist on the chosen instance.
- Independence rests on requested-only family or model provenance, or the maker
  is `unknown` (whatever the reason), or a family line is `unknown`.
- The final pair after an override fails a filter (see above), has no target, or
  meets any rule in this list for the pair.
- A review op has no maker.
- Directives conflict for the chosen pair.
- A newer GA model on the same line as the chosen model is in the live catalog
  but not in the pack. This reason is informational: it is listed with
  `informational: true` and does not set `judgment_required`.

Even with judgment required, the best target is returned (unless it is null), and
the parent decides. `--override ... --reason` records both the routed choice and
the final choice.

**Evidence and notes (decision support only)**:

- For the chosen exact arm and op: decision counts, close counts by outcome, by
  check type and by launch `purpose` (`unknown` when absent), and follow-up
  coverage. The label is `descriptive_only`. The block carries a fixed
  `outcome_scope`: a close describes the delegated result only; for
  `purpose=review` it describes the review (findings accepted), not the success of
  the reviewed work or the cost of the whole procedure. `abandoned_by_choice` and
  `superseded` never count as rejections or arm failures.
- Recent failure events for the chosen instance, by class.
- `calibration_notes` for this op: override clusters, stale pack entries, and
  oracle-checked or independently checked rejections.

Evidence is keyed on the exact model, effort and provider. A new version inherits
nothing.

## Receipts, use and calibration flow

- `route --record` writes a valid `resolve` receipt, so the existing `audit`,
  `close`, `followup`, replay and `--escalate-from` work. The receipt adds
  `request.route: true`, `request.maker`, and a `routing` block:
  `router_version`, the parameters hash, the applied and unmatched directive IDs,
  the trace, `chosen`, the judgment reasons, and the evidence snapshot. Each trace
  row keeps only the pace class, `projected_pct`, the exhaustion flag and a `ref`
  to the single per-account quota view in `routing.quota`.
- `audit` adds `route_coverage`: the app-owned delegate calls whose receipt came
  from `route`, from `resolve`, or from no receipt.
- Ordinary data reaches the router with no extra report step:
  - quota, catalog and T3 failure events are read at each call;
  - receipts are written by `route --record` during normal delegation;
  - closes are written by the parent's integration step, as the skill already
    requires.
  - `calibration_notes` appear in the `route` output, so proposals reach the
    parent during normal delegation.
  - `calibrate` gives the full report on demand.
- Changes to pack policy:
  - **Routine** changes need an independent review, not the owner. Examples:
    - replace a model with a newer GA version in the same tier, by rule 1, when
      the pack's evidence is at least as strong;
    - refresh evidence references;
    - remove a model that is missing from the catalog;
    - add a candidate with `basis: task_benchmark_prior` from evidence that
      is already in the catalog.
  - **Consequential** changes need the owner. Examples:
    - change an op's primary tier or vendor;
    - authorize an `unvalidated` substitute permanently;
    - spend metered dollars;
    - set efforts of xhigh or max;
    - change the objective or a preference.
  - A rollback is the previous pack, or `--policy`.

## Acceptance criteria

- **AC1**: fixture tests cover every filter, every rank key, every judgment
  trigger, the directive effects with expiry and conflict, metered and
  unknown-billing exclusion, the failure classes, the provenance levels, and
  determinism.
- **AC2**: all existing tests pass (193 at baseline `82f2b80`). On fixtures, the
  legacy commands give byte-identical output. A pack without the new fields still
  validates.
- **AC3**: a `route` receipt and an export fixture of the delegate call give
  `request_match` true and `route_coverage` 1 in `audit`.
- **AC4**: a pinned snapshot of the live catalog, quota and T3 failure events,
  captured 2026-10-10 and scrubbed, is committed under
  `tests/fixtures/route-live-20261010/`. The tests assert the routed outputs for
  these cases:
  - `implement.quota-tight`;
  - `review.audit` with a Sonnet maker;
  - `review.audit` with an `avoid provider=codex` directive;
  - an account that is exhausted;
  - a metered instance.

  Live state is not asserted.
- **AC5**: real journey. The independent checker for this PR is launched with
  `route --record` and `delegate_task`, using the routed target and the decision
  ID. Its close is `--judged-by independent --check independent_judged`, and it
  carries the checker's report as evidence. The pytest log is separate `check`
  evidence. A hash of a test log does not validate a judged verdict. `audit --db`
  shows the matched receipt.
- **AC6**: `calibrate` on the live ledger shows override clusters as proposals, with
  counts. It changes no file. The clusters are keyed by (op, final arm, account, the
  overridden fields). The tool does not classify the free-text reasons by keyword; it
  lists them with counts. The hand-classified table in the Motivation section is a
  manual analysis and is not an acceptance target.
- **AC7**: adaptation tests on the pinned snapshot, with one input changed at a
  time:
  1. A Codex window changes from `on_track` to `at_risk`. The
     `implement.quota-tight` route stays on Claude. For `fanout.dollar-tight`, the
     route moves to the `task_benchmark_prior` Sonnet candidate with no judgment.
     For `review.audit`, the route keeps the at-risk primary, sets
     `judgment_required`, and lists the Opus alternative as `unvalidated`.
  2. `rate_limit` events on an instance move the route to another account.
  3. `auth_config` events do not demote, and they make `judgment_required` true.
  4. A user cancellation changes nothing.
  5. A model is removed from the catalog. The route falls back, and
     `judgment_required` is true when the fallback is `unvalidated`.
- **AC8**: the pack `candidates` and `routing` values are in a separate commit,
  marked for review, with the evidence in the table below. The mechanism ships
  even if those values change.
- **AC9**: the t3-orchestrate skill makes `route` the default path for app-owned
  delegation. The real delegations in this thread use it after install. Use by
  other threads is monitored with `route_coverage` and is not assumed.
- **AC10**: an independent reviewer that uses a model other than the maker's finds
  no unresolved P0 or P1 finding.

## Proposed candidates (pack evidence only, no new research)

The metrics are quoted from the pack's own `known_gaps` text (catalog v0.5.12 and
the 2026-10-07 re-checks). The test for `task_benchmark_prior` is: a board the
pack cites for this op's task family, the same effort, and a clear margin. No row
has a harness match.

| Op (primary) | Candidate | Basis | Reason |
|---|---|---|---|
| implement.quota-tight (claude-sonnet-5-5 medium) | gpt-6.1-sol medium | task_benchmark_prior | AA TB4 at medium, 48.0 vs 29.8. The implementation board is cited for this op |
| fanout.dollar-tight (gpt-6-luna medium) | claude-sonnet-5-5 medium | task_benchmark_prior | AutomationBench at medium, 54.9 vs 40.5. This board is cited for fanout ops |
| review.audit (gpt-6.1-sol high) | claude-opus-5-5 high | unvalidated | Only aggregate support (AA Intelligence Index 53.6 vs 50.2, ARC-AGI-2). No audit-quality board |
| | claude-sonnet-5-5 high | unvalidated | No comparable evidence |
| implement.standard (claude-opus-5-5 medium) | gpt-6.1-sol medium | unvalidated | Weaker: Vals TB4 55.1 vs 65.2 |
| implement.oracle-bounded (gpt-6-luna high) | claude-sonnet-5-5 medium | unvalidated | No board at matched efforts. Requires `proof_class: oracle` |
| fanout.explore (gpt-6.1-sol medium) | claude-opus-5-5 medium | unvalidated | Mixed boards: AutomationBench 61.2 vs 62.6, GDPval-AA higher |
| | claude-sonnet-5-5 medium | unvalidated | Weaker: AutomationBench 54.9 vs 62.6 |
| recover.report, docs.lookup (claude-haiku-5-5 medium) | claude-sonnet-5-5 medium | unvalidated | TB4 does not measure docs or recovery work (task transfer) |
| advisor.deep, implement.accuracy-first (claude-opus-5-5 high) | gpt-6.1-sol high | unvalidated | Weaker on the aggregate index (50.2 vs 53.6) |
| ui.computer-use (gpt-6.1-sol medium) | claude-opus-5-5 medium | unvalidated | Mixed and borderline: AutomationBench 61.2 vs 62.6 |

The two `task_benchmark_prior` substitutes cost more than their primaries. They
protect quality, not quota.

## Changelog

- Revision 6 (2026-10-10): `--now` is a read-only simulation. `route --now --record` fails before any write; `route --now` nulls every `target` key in its output and returns `launch_ready: false`, `simulated_target` and `simulation`, so a synthetic clock cannot revive an expired `authorize` or `allow-metered` directive inside a dispatchable receipt. Commands that write state (`resolve`, `close`, `followup`, `audit`, `directive add|end`) refuse `--now`.
- Revision 5 (2026-10-10): checker report r2 fixes. R1: every T3 child run of the maker decision is read (`multiple_child_runs`). R2: `directive add` refuses `--now`; readers reject rows beyond the cap or recorded after the evaluation time (`directives_rejected`, state `rejected`). R3: an override with a null target raises `final_target_null`. R4: reasons carry `applies_to`; the final pair gets the same judgment rules; `judgment_required` follows the final pair. R5: `freshness.unchecked` and state `partial`. Also `independence.required_source`.
- Revision 4 (2026-10-10): checker report r1 fixes (maker provenance from the run, `lineage_key`, override filters, directive validation and lifetime cap, alternates-only `candidates`, AC6 key); revision 3 was the scope review decision on evidence classification.
