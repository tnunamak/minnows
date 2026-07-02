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
- **The dumb pipe**: Workflow scripts cannot import/require, so engine commands run through
  a minimal haiku agent that executes one command and echoes its JSON stdout verbatim
  (prior art: refactor-loop.js's scope-fn preflight pipe). A garbled relay is detected by
  JSON-parse failure and closes the lane via `land --abort` (honest `skipped(lane-abort)`),
  or at worst strands an `in_progress` packet that `hone reset` reopens. If the harness
  exposes a native shell primitive later, the pipe collapses to it with no design change.

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

`lane land` (and optionally `gate`) takes an explicit `--usage(-b64)` JSON array:
`[{role: maker|judge, provider, model?, tokens_in?, tokens_out?, tokens_total?, cost_usd?}]`
— validated fail-closed, aggregated with the same null-aware semantics as work.mjs
`tokensOf`, written to the same cost.jsonl fields. Ledger semantics preserved: engine-only
terminals (red baseline) record a KNOWN 0; provider-ran-but-unmetered records honest nulls,
never fabricated numbers.

Known limitation, stated plainly: the Workflow runtime does not expose per-agent token
usage to the script, so v1 pilot entries carry `tokens_* = null` with model identities.
The orchestrator can post-annotate real quota deltas from harness telemetry into the run
report (NOT into cost.jsonl — no invented numbers in the ledger). If/when `agent()` returns
usage metadata, the script passes it through with zero CLI changes.

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
   Defaults: Sonnet maker, Opus judge. Never end the session's turn mid-lane.
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
