# hone Workflow substrate — governance stays, execution rides the harness

Design memo for the `hone lane` CLI (lib/lane.mjs) + the Workflow driver
(workflows/hone-lane.js). Adopted synthesis from the owner's economics review
(pdpp-cq-sweep: `docs/research/fable-workflow-baseline-2026-07-02.md`): keep hone's
governance and books EXACTLY as they are — packets, gates, ledgers, receipts, agenda —
and let bulk-class execution ride Claude Code's in-harness Workflow tool, because the
measured Workflow substrate lands bulk changes at ~$1–2/land vs ~$12–15 all-in through
subprocess `claude -p`/`codex exec`. The verification floor (maker context + independent
checker + real test executions) is intrinsic and is NOT cut; only the process-hosting
overhead is.

## The control inversion

The Workflow tool exists only inside a Claude Code session — a hone subprocess cannot
call it. So the adapter inverts control:

```
hone work (subprocess substrate)          hone lane (Workflow substrate)
--------------------------------          -------------------------------------------
engine owns the loop,                     Workflow SCRIPT owns the loop,
CALLS subprocess maker/judge              CALLS the engine's deterministic verbs:

  load+gate ─ preflight ─ baseline          hone lane emit    (engine)
  maker subprocess                          maker agent       (in-harness, Sonnet-tier)
  touchset gate ─ oracle                    hone lane gate    (engine, authoritative)
  judge subprocess                          judge agent       (in-harness, different model)
  land/revert + ledgers                     hone lane land    (engine)
```

Both columns share ONE implementation of everything that writes the books: work.mjs's
exported spine (`writeRungReceipt`, `persistMakerBriefDigest`, `buildWorkingDiff`,
`writeTerminal`, `landCommit`, `buildLandClaims`, plus the gate helpers `checkExpect`,
`revertAll`, `dirtyEntries`, `normalizeTouchEntry`, `makerBrief`, `revisionBrief`).
Extraction, not fork — behavior-gated by `hone work --self-test` (358 checks, byte-identical
semantics for the subprocess path) and `hone lane --self-test` (129 checks, including the
real workflows/hone-lane.js executed end-to-end with mocked agents over the real engine).

## The trust boundary: engine-run rungs are the only receipts

Agent claims are NEVER trusted. Concretely:

- **Baseline and post-change evidence** are run by the engine (`emit` / `gate`), receipted
  to `quality/receipts/<id>/` with the same digest lines as `hone work`. The maker MAY run
  rung commands for fast feedback; those runs are advisory noise, never receipts.
- **A green gate receipt is bound to the exact tree state**: `djb2(head_sha + working diff)`
  stored at gate-green. `lane land` recomputes and REFUSES on any mismatch — a maker that
  edits after the gate, or a judge that "fixes one more thing", voids the receipt.
- **`lane land` refuses without that green receipt** (exit 2, fail-closed). A lying pipe,
  maker, or judge can waste a step; none of them can forge a land, because land IS the
  engine and re-verifies packet status, lane state, HEAD, and tree hash on disk.
- **Foreign-commit guard**: in-harness agents have Bash (the subprocess maker deliberately
  does not), so a moved HEAD between emit and gate/land is detected and terminalizes
  `blocked(foreign-commit)` WITHOUT auto-revert (reverting against a foreign HEAD would
  destroy work) — manual cleanup, honestly recorded.
- **The judge reads engine-written bytes**: gate-green writes
  `quality/.lane/<id>/judge-context.json` ({packet_yaml, evidence, diff}) built by the same
  `buildJudgeEvidence` budget machinery as `hone work`. No agent relays the record of
  judgment.
- **The dumb pipe — and the <1KB transport rule (learned live)**: Workflow scripts cannot
  import/require, so engine commands run through a minimal haiku agent that executes one
  command and echoes its JSON stdout verbatim (prior art: refactor-loop.js's scope-fn
  preflight pipe). A garbled relay is detected by JSON-parse failure and closes the lane
  via `land --abort` (honest `skipped(lane-abort)`), or at worst strands an `in_progress`
  packet that `hone reset` reopens. **Run wf_cdc171e4 proved multi-KB verbatim relay
  through a model is unreliable** (haiku re-typed a ~20KB emit JSON and silently dropped
  `brief_path`; the driver fail-closed as designed — wrong transport, not wrong engine):
  engine stdout consumed by the driver MUST stay under ~1KB, so every driver-facing lane
  command pipes through `workflows/project-lane-json.mjs`, which projects output onto the
  exact field contract the driver reads; large artifacts (briefs, revision briefs, judge
  contexts, receipts) stay on disk where maker/judge agents read them directly. Any NEW
  lane subcommand output the driver must see extends the projector's field contract — an
  unprojected field never reaches the driver. If the harness exposes a native shell
  primitive later, the pipe AND the size constraint collapse away with no design change.
- **Dependency ordering at emit**: `lane emit` refuses when any `depends_on` packet is not
  `landed` (a missing packet counts as unlanded, fail-closed) — the SAME rule the
  `hone run` scheduler applies, shared via run.mjs `unmetDependencies`, not forked.
  Learned live (wf_67898fff): an emitted packet with an unlanded evidence-dependency
  produced clean maker work whose promised test pins did not exist; rungs cannot verify
  prose claims about absent pins, and only the judge caught it. Defense-in-depth held;
  the cheap gate is at emit, before any spend.

## Books identical — with one honest identity note

Same packet outcome block, same claims.jsonl / cost.jsonl schemas, same receipt file and
digest formats, same commit discipline (author `Tim Nunamaker <tnunamak@gmail.com>`, one
commit per land, staged-containment check, touchset-only).

**Maker ≠ judge**: `hone work` enforces it at PROVIDER level (claude vs codex). In-harness
agents are all one provider, so the lane enforces it at **provider:model identity**
(`claude:sonnet` maker vs `claude:opus` judge; identical identities are refused at land AND
by the packet schema's structural must-differ rule, since the lane records model-qualified
identities in `maker_provider`/`judge_provider`). This is a documented relaxation of
non-negotiable #1's provider form, with compensating controls:

- the measured prior art for the same-provider/different-model pair is the overnight
  refactor-loop run: 34 lands, zero regressions, checker caught a real maker error;
- packets that PIN a provider (e.g. `judge_provider: codex`) are REFUSED by `lane emit`
  and route through `hone work` unchanged;
- cross-provider retries stay available (below).

**Single-lineage limitation, stated as policy**: the in-harness lane can only host
Anthropic-family agents, so a routing class whose `judge_constraint` requires
`different_lineage` — today `hard-ambiguous` (and `async-order-oracle`'s GPT-5.5-led
ladder) — CANNOT fully satisfy its judge constraint in-lane. Policy: those classes ride
`hone work` with a codex judge (the driver already skips no-claude-tier ladders as
`routed-to-subprocess`); if the owner deliberately runs one in-lane anyway, that is an
OWNER OVERRIDE and must be recorded in the books (a claim noting the waived lineage
constraint) — never a silent default.

## What stays subprocess

- **Codex cross-pair**: `hone work --maker claude --judge codex` (and inverse) remains the
  path for provider-level independence — REQUIRED for packets with provider pins, and the
  recommended escalation when a lane land is REVISE/REJECT-reverted and the owner wants a
  cross-provider second opinion before retrying.
- **High-scrutiny classes**: anything where `silent_wrongness_cost` is high (auth/storage/
  security proof classes) should keep the subprocess cross-provider judge until the lane
  substrate has its own measured discrimination record. The lane's v1 charter is the
  proven bulk tier: extraction / certified-transform / surface-repair packets.
- `hone run`'s campaign loop, agenda, report — untouched; they consume the same books.

## Cost accounting

`lane land` takes an explicit `--usage(-b64)` JSON: an ARRAY of per-STAGE records
`{role: maker|judge|engine|planner, provider, model?, stage?: recon|edit|test|judge|plan,
tokens_in?, tokens_out?, tokens_total?, cache_read_tokens?, cost_usd?, wall_s?, quota_pts?}`
(back-compat: a bare object = one unattributed stage) — validated fail-closed, aggregated
with the same null-aware semantics as work.mjs `tokensOf`, written to cost.jsonl with the
original fields PLUS optional additive `stages` (per-stage attribution; cache-read ratio
per stage is L4's measurement), `quota_pts` (the owner's real currency, honest-null), and
`batch` (below). The subprocess path writes none of these — old entries stay valid and the
report compiler tolerates both. Ledger semantics preserved: engine-only terminals record a
KNOWN 0; provider-ran-but-unmetered records honest nulls, never fabricated numbers.

Known limitation, stated plainly — OPEN INSTRUMENTATION ITEM: the Workflow runtime does
not expose per-agent token usage to the script, so lane entries carry
`cost_usd = null` and `tokens_* = null` (honest nulls) with model identities and stage
labels. Current procedure: per-run post-annotation from the workflow journal — the
orchestrator reads the run's journal/telemetry after the lane completes and records real
quota deltas in the run report next to the baseline memo (NOT into cost.jsonl — no
invented numbers in the ledger). If/when `agent()` returns usage metadata, the script
passes it through with zero CLI changes and the item closes.

## Model selection (L1): registry + policy + one deterministic chooser

Data / policy / mechanism are split so each evolves independently (L1 amendment,
token-economics-levers-2026-07-02):

- **`models.json` (registry, data)** — one entry per model: provider, exact id, short
  harness alias, lineage, pricing {in, out, cache_read}, quota_pool, supported efforts,
  capability tier_rank, status, and **calibration provenance**. `calibration: null` =
  routing-INELIGIBLE (fail-closed). Initial entries digitized from waspflow
  `docs/model-economics.md` (2026-06-30), provenance recorded in the file.
- **`routing.json` (policy, data)** — class → ORDERED candidates {registry name, effort} +
  two-strike escalation rule + judge constraint (different lineage, tier_rank ≥ maker) +
  quota pressure threshold + batch eligibility rules + the policy's own provenance.
  Policy authorship is judgment on a slow cadence (the agenda proposes diffs from the
  measured per-class×tier pass-rate table); application is deterministic.
- **`selectAgent(class, attemptNo, quotaState, registry, policy, opts?)`** (lib/routing.mjs)
  — THE runtime chooser; nothing with a context window picks a model at call time; packets
  pin `routing_class` only (the validator rejects model-shaped pins with the doctrine
  message). attemptNo = strikes (failed gates + judge REVISEs); every 2 strikes walks one
  candidate down, clamped. quotaState is an optional injected input ({pools: {pool:
  utilization}}, honest-null): a pressured pool shifts to a same-or-higher-tier candidate
  on another pool, else proceeds with a ledger-visible note. Wired into `hone work`
  (`HONE_QUOTA_STATE` env carries quotaState; subprocess makers AND judges get explicit
  `--model` and `--effort` / `model_reasoning_effort` — never a CLI default again) and
  into `hone lane emit`, whose JSON carries the materialized candidate ladder that the
  Workflow script consumes.
- **`hone calibrate --model X --replay N`** — onboards/refreshes a model by replaying
  already-LANDED orders from this repo's own ledger (ground truth = landed diff + green
  gates) in a scratch worktree, emitting a calibration report under quality/reports/.
  v1 is a seam-proving stub: real ledger→ground-truth→worktree mechanics, zero model
  calls, `measured` fields honest-null, report explicitly marked NOT eligibility
  evidence. The selectAgent registry gate is live regardless.

## Batch verification (L2): one gate + one judge over N routine orders

`hone lane gate --batch id1,id2,...` then `hone lane land --batch <green-members> ...`:

- Members are emitted individually (emits keep the tree clean; the **baseline cache**
  reuses identical engine-run rung results at the same HEAD, so N members don't pay N
  identical suite runs). The batch REFUSES fail-closed: risky classes per routing.json
  `batch` rules (non-preserve_refactor, non-certified proof classes, silent_wrongness
  above low, behavior-visible status, named property_at_risk), overlapping touchsets,
  unemitted members, mixed emit-HEADs.
- Gate runs the UNION of evidence rungs ONCE (deduped by command+expect), enforces union
  touchset containment, and binds the green receipt to the combined tree hash. On red it
  **auto-bisects**: group-testing over captured per-member changes (log₂N probes),
  offenders terminalize `reverted` with isolation evidence ("with ONLY this member's
  change applied, rung X fails"), the green remainder is restored and re-gated as the
  authoritative landing state (interaction-red after green probes reverts everything).
- Land takes ONE judge verdict over the combined diff and lands each member as its OWN
  commit (per-order revertability; batch-aware leftover containment in the shared
  landCommit). Usage is recorded ONCE on the anchor (first member); non-anchor entries
  carry null tokens plus `batch: {batch_id, size, anchor}`, so naive ledger sums never
  overcount. A batch collapsed to one member books as a plain land. Non-PASS reverts
  every member — per-order retries follow.

## Semantic deltas vs `hone work` (complete, honest list)

1. Maker/judge identity granularity: provider:model instead of provider (above).
2. The maker has Bash. Mitigations: brief + preamble forbid git writes; touchset gate and
   engine-only commits are structural; foreign-commit guard fail-closes the rest.
3. Revision interleaving (≤1 oracle revision, ≤1 judge revision) is script-side policy
   inside an ENGINE-enforced attempt ceiling (`HONE_LANE_MAX_GATE_ATTEMPTS`, default 3 =
   post/post-r1/post-r2; red at the ceiling auto-reverts + terminalizes). In `hone work`
   the interleaving itself is structural. Risk: a rogue script could spend its revisions
   differently; it cannot exceed them, land without green, or self-certify.
4. No signal teardown in lane CLI processes. `emit`/`gate`/`land` are short-lived; a kill
   mid-gate leaves an `in_progress` packet + lane state + dirty tree, all recoverable
   (`hone lane land --abort` or `hone reset` after manual revert). `hone work`'s in-process
   SIGTERM trap doesn't map onto a multi-process lane; the orchestrator must not abandon a
   lane mid-flight (same discipline as never ending a turn with a child running).
5. The packet lockfile guards only the pending→in_progress transition (emit). The longer
   emit→land window is guarded by packet status (`work` and a second `emit` both refuse
   non-pending) + lane state (`gate`/`land` refuse without it).

## Economics recap (measured, not aspirational)

From fable-workflow-baseline-2026-07-02: Workflow substrate ≈ $1–2 API-equiv per landed
bulk change (34 zero-regression lands overnight; ~0.24 quota-pts/cut) vs hone subprocess
$6.81/land machine-only, ~$12–15 all-in. Model shape per model-economics: Sonnet 5 high =
bulk maker value knee; Opus (or GPT-5.5 via `hone work`) = different-lineage judge; Fable =
orchestrator/chooser, not bulk maker. Hybrid full-codebase estimate: $800–1,500 API-equiv,
dominated by quota pacing.

## PILOT — one low-risk packet, then a landed-packet replay (no-op sanity)

Scope: ONE extraction-family packet, in a FRESH worktree — never the live campaign
worktree at `~/code/minnows`-driven `pdpp-cq-sweep`. All engine paths below refer to this
branch's checkout: `HONE_DIR=/home/tnunamak/.tmp/minnows-substrate/tools/hone` (until
`hone/workflow-substrate` merges).

0. Preconditions (all green before any agent spend):
   `cd $HONE_DIR && ./hone work --self-test && ./hone lane --self-test`
1. Isolated pilot worktree on a NEW branch (a shared branch would advance the live sweep):
   `git -C <pdpp-repo> worktree add -b hone/lane-pilot ~/.tmp/pdpp-lane-pilot <sweep-branch>`
2. Pick the packet: a `pending` packet with `action: preserve_refactor`,
   `proof_class: certified_transform`, `maker_tier: cheap`, low `silent_wrongness_cost`,
   `maker_provider: null`, `judge_provider: null` (pins refuse the lane by design).
   `grep -l 'status: pending' ~/.tmp/pdpp-lane-pilot/*/quality/packets/*.yaml` then read.
3. Engine dry-run (no side effects, inspect the brief + rungs):
   `$HONE_DIR/hone lane emit --packet <id> --repo <pilot-repo> --dry-run`
4. The one-command pilot invocation (orchestrator, in a Claude Code session):
   run the **Workflow tool** with script `$HONE_DIR/workflows/hone-lane.js` and
   `args = {"packets": ["<id>"], "repo": "<pilot-repo>", "honeDir": "$HONE_DIR"}`.
   Defaults: maker from the routing ladder (extraction → Sonnet@high), Opus judge.
   Never end the session's turn mid-lane.

   The pilot is a MEASUREMENT MATRIX, not a demo (levers doc): the same args accept the
   arm knobs —
   - maker tier arm: `"makerModel": "sonnet"` (pins the ladder; omit = routed tiering
     with two-strike escalation); the GPT-5.5 arm runs via `hone work --maker codex`;
   - verification arm: `"batch": true` with `"packets": [<N routine ids>]` (one
     engine gate + one judge, auto-bisect, per-order commits) vs the per-change default.
   Record per arm from the books: $/land + quota_pts/land (cost.jsonl `stages`/`quota_pts`
   with harness quota deltas post-annotated in the run report), wall/land, quality
   (revert rate, judge-overturn). Winning settings become routing.json defaults —
   committed, owner-visible, provenance updated.
5. Verify the books (the pilot's pass bar):
   - packet terminal on disk; if landed: `git -C <pilot-repo-git-root> show --stat HEAD`
     shows ONLY touchset files, author `tnunamak@gmail.com`, subject `[hone <id>]`;
   - one new cost.jsonl line (`outcome`, `judge_result`, model-qualified providers) and
     claims with receipt digests; receipts under `quality/receipts/<id>/` incl. baseline;
   - tree clean; `quality/.lane/<id>/` removed.
6. Replay an already-LANDED packet as a no-op sanity check (negative control): pick a
   landed packet whose change is in the pilot tree, then
   `$HONE_DIR/hone reset <landed-id> --repo <pilot-repo> --force --reason "substrate replay sanity check"`
   and run step 4 on it. EXPECTED: the maker finds the code already correct and replies
   `HONE-VERDICT: validated-non-defect`; gate terminalizes `skipped(validated-non-defect)`;
   NO commit, tree clean, honest skip in the books. Any other outcome (especially a diff
   or a land) = stop and investigate before any fan-out.
7. Record the run (harness quota delta, wall time, outcome) next to the baseline memo;
   only after both step 5 and step 6 pass does the lane earn batch-scale use, per the
   §E→§A→§B fan-out gate.

Cleanup: `git -C <pdpp-repo> worktree remove ~/.tmp/pdpp-lane-pilot` (branch keeps the
evidence; delete or merge by owner decision).
