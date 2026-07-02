// lane.mjs — `hone lane emit|gate|land`: the deterministic spine of one work packet,
// exposed as CLI subcommands so an EXTERNAL maker/judge host (the Claude Code Workflow
// substrate, workflows/hone-lane.js) can drive execution while the ENGINE keeps every
// receipt, gate, and ledger write. Design memo: tools/hone/SUBSTRATE.md.
//
// Control inversion vs work.mjs: `hone work` owns the whole pipeline and CALLS subprocess
// makers/judges; `hone lane` is called BY an orchestrator that hosts in-harness agents.
// The books are identical by construction — both substrates share work.mjs's exported
// spine (writeRungReceipt / writeTerminal / landCommit / buildLandClaims / checkExpect /
// revertAll / buildWorkingDiff), never fork it.
//
// Trust boundary (fail-CLOSED at every arrow):
//   emit   refuse: gate/status/pin/branch/dirty violations (exit 2, NO side effects).
//          Side effects begin here: packet -> in_progress, GREEN BASELINE run by the
//          engine (red baseline -> blocked terminal, exit 1), lane state persisted.
//   gate   the ONLY authority on evidence: re-runs every rung against the CURRENT tree
//          (maker's diff already applied), enforces touchset/no-diff/foreign-commit,
//          writes receipts. Exit 0 ONLY on full green; a green gate receipt is bound to
//          the exact tree state (head_sha + diff hash). Agent claims of green are never
//          trusted — an agent cannot fabricate a receipt the engine did not write.
//   land   refuses without a green gate receipt matching the CURRENT tree state (exit 2).
//          Records judge verdict + explicit token usage into the same claim/cost ledgers,
//          commits with the same touchset-containment one-commit discipline, or reverts
//          on any non-PASS verdict. Structural maker != judge at provider:model identity.
//
// Exit codes (same vocabulary as work.mjs): 0 green/landed/dry-run · 1 terminal
// non-landed or gate-red-awaiting-revision · 2 refused (no NEW side effects).
// stdout is ALWAYS one JSON object (machine interface); logs go to stderr.

import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { djb2 } from './util.mjs';
import { validatePacket } from './validate-packet.mjs';
import { loadPacket, writePacket } from './packet-io.mjs';
import { validateStageEntry } from './ledger.mjs';
import { loadRouting, resolveRouting, isBatchEligible } from './routing.mjs';
import { unmetDependencies } from './run.mjs';
import {
  gitContext, dirtyEntries, flatPaths, normalizeTouchEntry, revertAll,
  checkExpect, runShellCmd, isCompareVsHead, makerBrief, revisionBrief,
  parseMakerVerdict, tailClip, headClip, buildJudgeEvidence, buildWorkingDiff,
  writeRungReceipt, persistMakerBriefDigest, writeTerminal, landCommit, buildLandClaims,
  acquireWorkLock,
} from './work.mjs';

const PROVIDERS = ['claude', 'codex'];
const VERDICTS = ['PASS', 'REVISE', 'REJECT'];
// post-gate attempt ceiling: post, post-r1, post-r2 — mirrors work.mjs's maximum path
// (oracle revision + judge revision). The workflow enforces the ≤1-oracle-revision /
// ≤1-judge-revision interleaving; the engine enforces this hard ceiling fail-closed.
const MAX_GATE_ATTEMPTS = Number(process.env.HONE_LANE_MAX_GATE_ATTEMPTS ?? 3);

// ---------------------------------------------------------------- entry point

export async function runLane(flags) {
  if (flags['self-test']) {
    const { laneSelfTest } = await import('./test-lane.mjs');
    process.exitCode = await laneSelfTest({ verbose: !!flags.verbose });
    return;
  }
  const sub = flags._?.[0];
  const id = typeof flags.packet === 'string' ? flags.packet : null;
  const batchIds = typeof flags.batch === 'string'
    ? flags.batch.split(',').map((s) => s.trim()).filter(Boolean)
    : null;
  const usage = `usage: hone lane <emit|gate|land> (--packet <candidate-id> | --batch <id1,id2,...>) --repo PATH
  emit  --packet <id> [--maker claude|codex] [--judge claude|codex] [--dry-run]
        (batch members are emitted individually — emits keep the tree clean)
  gate  [--maker-summary FILE | --maker-summary-b64 B64] [--revision-note-b64 B64] [--usage FILE | --usage-b64 B64]
        --batch: combined tree, union touchset, ONE suite-level rung run; red auto-bisects
  land  (--judge-verdict FILE | --judge-verdict-b64 B64) (--usage FILE | --usage-b64 B64)
        | --abort --reason TEXT [--usage FILE | --usage-b64 B64]
        --batch: one judge amortized; each order lands as its OWN commit (per-order revertability)`;
  if (!['emit', 'gate', 'land'].includes(sub) || (!id && !batchIds)) throw new Error(usage);
  if (batchIds && sub === 'emit') throw new Error(`hone lane emit has no --batch: emit each member individually (emits never dirty the tree; identical baseline rungs are shared via the baseline cache)\n${usage}`);
  const repoRoot = resolve(flags.repo || '.');
  const log = (s) => process.stderr.write(s + '\n');
  let res;
  if (batchIds) {
    res = sub === 'gate'
      ? await executeLaneBatchGate({ ids: batchIds, repoRoot, log })
      : await executeLaneBatchLand({ ids: batchIds, repoRoot, verdictRaw: readInput(flags, 'judge-verdict'), usageRaw: readInput(flags, 'usage'), log });
  } else if (sub === 'emit') {
    res = await executeLaneEmit({
      id, repoRoot, log,
      makerProvider: String(flags.maker || 'claude'),
      judgeProvider: String(flags.judge || 'claude'),
      dryRun: !!flags['dry-run'],
    });
  } else if (sub === 'gate') {
    res = await executeLaneGate({
      id, repoRoot, log,
      makerSummary: readInput(flags, 'maker-summary'),
      revisionNote: readInput(flags, 'revision-note'),
      usageRaw: readInput(flags, 'usage'),
    });
  } else {
    res = await executeLaneLand({
      id, repoRoot,
      verdictRaw: readInput(flags, 'judge-verdict'),
      usageRaw: readInput(flags, 'usage'),
      abort: !!flags.abort,
      abortReason: typeof flags.reason === 'string' ? flags.reason : null,
    });
  }
  process.stdout.write(JSON.stringify(res.json ?? { ok: res.exitCode === 0 }, null, 2) + '\n');
  process.exitCode = res.exitCode;
}

/** read `--<name> FILE` or `--<name>-b64 B64` (b64 wins; returns null when neither given). */
export function readInput(flags, name) {
  const b64 = flags[`${name}-b64`];
  if (typeof b64 === 'string' && b64.length) {
    try { return Buffer.from(b64, 'base64').toString('utf8'); }
    catch { throw new Error(`--${name}-b64: invalid base64`); }
  }
  const file = flags[name];
  if (typeof file === 'string' && file.length) {
    if (!existsSync(file)) throw new Error(`--${name}: file not found: ${file}`);
    return readFileSync(file, 'utf8');
  }
  return null;
}

// ---------------------------------------------------------------- lane state
// quality/.lane/<id>/ — engine bookkeeping (inside quality/, so dirtyEntries ignores it):
//   state.json     head_sha, providers, touchset (normalized ONCE at emit, like work),
//                  accumulated receipt lines/slices/meta, gate attempts, gate receipt
//   baseline.json  full baseline rung results (checkExpect post-phase comparisons need
//                  the complete stdout/output, never a bounded slice)
// Removed on every terminal write: a reset + re-emit must start from fresh state.

const laneDir = (repoRoot, id) => join(repoRoot, 'quality', '.lane', id);
const statePath = (repoRoot, id) => join(laneDir(repoRoot, id), 'state.json');

function loadState(repoRoot, id) {
  const p = statePath(repoRoot, id);
  if (!existsSync(p)) return null;
  const state = JSON.parse(readFileSync(p, 'utf8'));
  if (state.candidate_id !== id) throw new Error(`lane state at ${p} names candidate '${state.candidate_id}', expected '${id}' — refusing (fail-closed)`);
  state.baseline = JSON.parse(readFileSync(join(laneDir(repoRoot, id), 'baseline.json'), 'utf8'));
  return state;
}

function saveState(repoRoot, id, state) {
  mkdirSync(laneDir(repoRoot, id), { recursive: true });
  const { baseline, ...rest } = state;
  writeFileSync(join(laneDir(repoRoot, id), 'baseline.json'), JSON.stringify(baseline));
  writeFileSync(statePath(repoRoot, id), JSON.stringify(rest, null, 2));
}

const clearState = (repoRoot, id) => rmSync(laneDir(repoRoot, id), { recursive: true, force: true });

const refuse = (id, sub, reason) => ({
  exitCode: 2,
  json: { ok: false, refused: true, candidate_id: id, reason, summary: `hone lane ${sub} — ${id}: REFUSED\n  ${reason}` },
});

/** terminal helper: shared writeTerminal + state cleanup + JSON envelope. At land time
 * makerName/judgeName are MODEL-QUALIFIED identities (e.g. claude:sonnet / claude:opus) —
 * the packet schema's structural must-differ rule then enforces the lane's identity form
 * of non-negotiable #1 in the books themselves. */
function laneTerminal({ repoRoot, id, state, packet, packetPath, tokens, judgeRan, makerName, judgeName, judgeResult, stages, quotaPts, batch, extras }, fields) {
  const res = writeTerminal({
    repoRoot, id, packet, packetPath, via: 'lane',
    startedAt: state?.started_at_ms ?? Date.now(),
    makerName: makerName ?? state?.maker_provider ?? 'claude',
    judgeName: judgeName ?? state?.judge_provider ?? 'claude',
    makerRan: state?.maker_ran ?? false,
    judgeRan: judgeRan ?? false,
    tokens: tokens ?? { inTok: null, outTok: null, total: null, usd: null },
    revisionCount: Math.max(0, (state?.gate_attempts ?? 0) - 1),
    judgeResult: judgeResult ?? null,
    receiptLines: state?.receiptLines ?? [],
    stages, quotaPts, batch,
    ...fields,
  });
  clearState(repoRoot, id);
  return {
    exitCode: res.exitCode,
    json: { ok: res.outcome === 'landed', terminal: res.outcome, candidate_id: id, commit: res.commit ?? null, summary: res.summary, ...(extras ?? {}) },
  };
}

// ---------------------------------------------------------------- emit

export async function executeLaneEmit({ id, repoRoot, makerProvider, judgeProvider, dryRun = false, log = () => {} }) {
  // ---- pure gates (no side effects) ----
  if (!PROVIDERS.includes(makerProvider)) return refuse(id, 'emit', `unknown maker provider '${makerProvider}' (known: ${PROVIDERS.join(', ')})`);
  if (!PROVIDERS.includes(judgeProvider)) return refuse(id, 'emit', `unknown judge provider '${judgeProvider}' (known: ${PROVIDERS.join(', ')})`);
  let loaded;
  try { loaded = loadPacket(repoRoot, id); }
  catch (e) { return refuse(id, 'emit', e.message); }
  const { packet, path: packetPath, rawText } = loaded;
  const schemaErrs = validatePacket(packet, { repoDir: repoRoot, warn: (m) => log(`  WARNING (validator): ${m}`) });
  if (schemaErrs.length) return refuse(id, 'emit', `malformed packet (schema v1.1):\n  - ${schemaErrs.join('\n  - ')}`);
  if (packet.execution_gate !== 'autonomous') {
    return refuse(id, 'emit', `execution_gate is '${packet.execution_gate}' — lane executes ONLY autonomous packets (owner_ratify goes to the owner, fail-closed)`);
  }
  if (packet.status !== 'pending') {
    return refuse(id, 'emit', `packet status is '${packet.status}' — never re-litigate a persisted outcome; reset status to pending only by owner decision`);
  }
  if (packet.maker_provider !== null && packet.maker_provider !== makerProvider) {
    return refuse(id, 'emit', `packet pins maker_provider='${packet.maker_provider}' but lane maker is '${makerProvider}' — route this packet through hone work`);
  }
  if (packet.judge_provider !== null && packet.judge_provider !== judgeProvider) {
    return refuse(id, 'emit', `packet pins judge_provider='${packet.judge_provider}' but lane judge is '${judgeProvider}' — route this packet through hone work`);
  }
  // dependency ordering — the SAME rule the run scheduler applies (run.mjs
  // unmetDependencies, shared not forked): a packet whose deps have not LANDED can
  // produce clean-looking work whose promised artifacts (e.g. test pins) don't exist
  // yet; rungs cannot verify prose claims about absent pins, so only the judge would
  // catch it (live run wf_67898fff). Emit is the right gate: refuse before any spend.
  const unmetDeps = unmetDependencies(packet, (d) => {
    try { return loadPacket(repoRoot, d).packet.status; } catch { return null; }
  });
  if (unmetDeps.length) {
    return refuse(id, 'emit', `depends_on not landed: ${unmetDeps.join(', ')} — dependencies must land before this packet is workable (a missing dep packet counts as unlanded, fail-closed)`);
  }

  const g = gitContext(repoRoot);
  if (g.branch === 'main' || g.branch === 'master') {
    return refuse(id, 'emit', `target repo is on '${g.branch}' — lane lands commits and never works on the default branch`);
  }
  const touchTop = packet.touchset.map((p) => normalizeTouchEntry(g, repoRoot, p));
  const dirty = dirtyEntries(g);
  if (dirty.length) {
    const inTouch = flatPaths(dirty).filter((p) => touchTop.includes(p));
    return refuse(id, 'emit', `target git tree is dirty (${flatPaths(dirty).length} path(s), full git-root scope): ${flatPaths(dirty).slice(0, 10).join(', ')}` +
      (inTouch.length ? ` — DIRTY TOUCHSET FILES: ${inTouch.join(', ')} (baseline would be unattributable)` : ''));
  }

  // L1 routing: class -> ordered maker tier list (+ L2 batch eligibility) — the script
  // consumes this; the engine never lets a maker choose its own tier. Routing failure is
  // a warning + null (economics lever, not a safety gate; callers fall back to defaults).
  let routing = null;
  try {
    const table = loadRouting();
    routing = { ...resolveRouting(packet, table), batch_eligible: isBatchEligible(packet, table) };
  } catch (e) { log(`  WARNING (routing): ${e.message} — no routing emitted`); }

  const brief = makerBrief(rawText, packet);
  if (dryRun) {
    return {
      exitCode: 0,
      json: {
        ok: true, dry_run: true, candidate_id: id,
        packet_path: packetPath, action: packet.action, proof_class: packet.proof_class,
        routing,
        touchset_toplevel: touchTop, not_allowed: packet.not_allowed,
        evidence: packet.evidence_required.map((r) => ({ rung: r.rung, command: r.command, expect: r.expect, expect_check: r.expect_check ?? null })),
        brief,
        would: 'mark in_progress → GREEN BASELINE (engine-run) → emit maker brief; then: maker edits → hone lane gate → judge → hone lane land',
      },
    };
  }

  // ---- side effects: lock the pending→in_progress transition, baseline, state ----
  let lock;
  try { lock = acquireWorkLock(repoRoot, id, log); }
  catch (e) { return refuse(id, 'emit', `cannot acquire packet lock: ${e.message}`); }
  if (!lock.ok) return refuse(id, 'emit', lock.reason);
  try {
    const startedAt = Date.now();
    const head = g.git(['rev-parse', 'HEAD']);
    packet.status = 'in_progress';
    writePacket(packetPath, packet);

    const receiptsDirRel = join('quality', 'receipts', id);
    const stripCtx = { qualityRel: g.topRel('quality'), candidateId: id };
    const state = {
      candidate_id: id, created: new Date().toISOString(), started_at_ms: startedAt,
      head_sha: head, branch: g.branch, packet_path: packetPath,
      maker_provider: makerProvider, judge_provider: judgeProvider, maker_ran: false,
      touchset_toplevel: touchTop, receipts_dir_rel: receiptsDirRel,
      receiptLines: [], receiptSlices: [], receiptMeta: [],
      gate_attempts: 0, gate: null, brief_count: 0, baseline: [],
    };

    for (const rung of packet.evidence_required) {
      if (isCompareVsHead(rung.command)) {
        log(`  WARNING [${rung.rung}]: command matches a compare-vs-HEAD pattern — structurally unwinnable before commit (warning only; see README "Authoring evidence rungs")`);
      }
    }
    // baseline cache: identical command at the SAME HEAD on a guaranteed-clean tree ⇒
    // identical result — batch members share one engine-run baseline instead of N suite
    // runs ("never pay for the same context twice"). Green results only; keyed by
    // (head_sha, command); receipts are byte-identical to a fresh run's.
    const cacheDir = join(repoRoot, 'quality', '.lane', '.baseline-cache', head);
    const cachePath = (cmd) => join(cacheDir, `${djb2(cmd)}.json`);
    for (const [i, rung] of packet.evidence_required.entries()) {
      let res = null;
      if (existsSync(cachePath(rung.command))) {
        try {
          res = JSON.parse(readFileSync(cachePath(rung.command), 'utf8'));
          log(`  [baseline] ${rung.rung}: ${rung.command} (shared: engine-run result reused from this HEAD)`);
        } catch { res = null; }
      }
      if (!res) {
        log(`  [baseline] ${rung.rung}: ${rung.command}`);
        res = await runShellCmd(rung.command, repoRoot);
      }
      const verdict = checkExpect(rung, res, 'baseline');
      if (verdict.pass && !existsSync(cachePath(rung.command))) {
        mkdirSync(cacheDir, { recursive: true });
        writeFileSync(cachePath(rung.command), JSON.stringify({ code: res.code, timedOut: res.timedOut, stdout: res.stdout, output: res.output, durationMs: res.durationMs }));
      }
      const r = writeRungReceipt({ repoRoot, receiptsDirRel, id, via: 'lane', phase: 'baseline', index: i, rung, res, verdict, stripCtx });
      state.receiptLines.push(r.line); state.receiptSlices.push(r.slice); state.receiptMeta.push(r.meta);
      state.baseline.push({ code: res.code, timedOut: res.timedOut, stdout: res.stdout, output: res.output, durationMs: res.durationMs });
      if (!verdict.pass) {
        return laneTerminal({ repoRoot, id, state, packet, packetPath }, {
          status: 'blocked',
          blockedOn: `red baseline: rung '${rung.rung}' failed BEFORE any change (${verdict.reason}) — never work on a red baseline`,
          lesson: `baseline rung '${rung.rung}' is red at repo_sha ${packet.repo_sha.slice(0, 12)}; the oracle must be green before this packet is workable`,
          claims: [
            { type: 'verified_fact', statement: `baseline evidence rung '${rung.rung}' fails before any change: ${verdict.reason}`, evidence: [{ command: rung.command, output_digest: r.digest }] },
            { type: 'remaining_work', statement: `fix the red baseline (rung '${rung.rung}'), reset packet status to pending, and re-run the lane on ${id}` },
          ],
          headline: `red baseline at rung '${rung.rung}': ${verdict.reason}`,
        });
      }
    }

    state.brief_count = 1;
    persistMakerBriefDigest({ repoRoot, receiptsDirRel, id, via: 'lane', attempt: 1, briefText: brief });
    state.maker_ran = true; // from here on a maker is presumed dispatched by the orchestrator
    saveState(repoRoot, id, state);
    // full brief on disk too: in-harness makers can Read it instead of a giant prompt relay
    const briefPath = join(laneDir(repoRoot, id), 'maker-brief.txt');
    writeFileSync(briefPath, brief);

    return {
      exitCode: 0,
      json: {
        ok: true, candidate_id: id, packet_path: packetPath,
        repo: { git_root: g.gitRoot, repo_root: repoRoot, branch: g.branch, head_sha: head },
        action: packet.action, proof_class: packet.proof_class, behavior_status: packet.behavior_status,
        routing,
        maker_tier: packet.maker_tier, judge_tier: packet.judge_tier,
        touchset_toplevel: touchTop, not_allowed: packet.not_allowed, plan: packet.plan,
        evidence: packet.evidence_required.map((r) => ({ rung: r.rung, command: r.command, expect: r.expect, expect_check: r.expect_check ?? null })),
        baseline: state.receiptLines.slice(),
        brief, brief_path: briefPath, packet_yaml: rawText,
        next: `run the maker (apply the brief), then: hone lane gate --packet ${id} --repo ${repoRoot}`,
      },
    };
  } finally {
    lock.release();
  }
}

// ---------------------------------------------------------------- gate

export async function executeLaneGate({ id, repoRoot, makerSummary = null, revisionNote = null, usageRaw = null, log = () => {} }) {
  let loaded;
  try { loaded = loadPacket(repoRoot, id); }
  catch (e) { return refuse(id, 'gate', e.message); }
  const { packet, path: packetPath } = loaded;
  if (packet.status !== 'in_progress') {
    return refuse(id, 'gate', `packet status is '${packet.status}' — gate requires an in_progress packet with lane state (run hone lane emit first)`);
  }
  let state;
  try { state = loadState(repoRoot, id); }
  catch (e) { return refuse(id, 'gate', e.message); }
  if (!state) return refuse(id, 'gate', `no lane state for ${id} (quality/.lane/${id}/) — this in_progress packet was not emitted by hone lane (crashed hone work? foreign state?); resolve manually`);
  let usage = null;
  if (usageRaw != null) {
    const u = parseUsageInput(usageRaw);
    if (u.errors.length) return refuse(id, 'gate', `malformed --usage:\n  - ${u.errors.join('\n  - ')}`);
    usage = u.entries;
  }

  const g = gitContext(repoRoot);
  const agg = usage ? aggregateUsage(usage) : null;
  const term = (fields, extras) => laneTerminal({
    repoRoot, id, state, packet, packetPath,
    tokens: agg ?? undefined, stages: usage ?? undefined, quotaPts: agg ? agg.quotaPts : undefined, extras,
  }, fields);

  // foreign-commit guard: an in-harness maker HAS Bash (unlike the subprocess maker) —
  // if anything committed since emit, the engine's revert guarantee is void. Fail closed,
  // record blocked, DO NOT revert (reverting against a foreign HEAD would destroy work).
  const head = g.git(['rev-parse', 'HEAD']);
  if (head !== state.head_sha) {
    return term({
      status: 'blocked',
      blockedOn: `foreign-commit: HEAD moved ${state.head_sha.slice(0, 12)} → ${head.slice(0, 12)} between lane emit and gate — the maker (or something else) committed; engine revert guarantees void`,
      lesson: 'a lane maker must never run git write operations; manual cleanup required (tree NOT auto-reverted against a foreign HEAD)',
      claims: [
        { type: 'uncertainty', statement: `lane gate for ${id} found HEAD moved from ${state.head_sha} to ${head}; working tree left untouched for manual review` },
        { type: 'remaining_work', statement: `packet ${id} blocked on foreign commit; inspect ${head.slice(0, 12)}, clean up manually, reset to pending to retry` },
      ],
      headline: `foreign commit detected — blocked, tree left for manual cleanup`,
    }, { manual_cleanup_required: true });
  }

  if (state.gate?.green) {
    return refuse(id, 'gate', `gate is already green for this tree state (attempt ${state.gate.attempt}) — proceed to hone lane land`);
  }

  const touchTop = state.touchset_toplevel;
  const receiptsDirRel = state.receipts_dir_rel;
  const stripCtx = { qualityRel: g.topRel('quality'), candidateId: id };

  // ---- maker-no-diff (every attempt: an empty tree can never be gated green) ----
  const changed = flatPaths(dirtyEntries(g));
  if (!changed.length) {
    const noDiffEvidence = [{ command: 'git status --porcelain=v1 -uall', output_digest: '(empty — no changes anywhere in the git root outside quality/)' }];
    const mv = parseMakerVerdict(makerSummary);
    const makerName = state.maker_provider;
    if (mv?.kind === 'validated-non-defect') {
      const why = headClip(mv.rationale, 240);
      return term({
        status: 'skipped',
        skipReason: `validated-non-defect(${why})`,
        lesson: `maker (${makerName}) validated the packet premise as a non-defect — a correct, permanent close, not a retry candidate`,
        claims: [
          { type: 'verified_fact', statement: `maker (${makerName}) produced no working-tree change for ${id} and explicitly validated the code as already correct (HONE-VERDICT: validated-non-defect — ${why}); baseline evidence was green with no change required`, evidence: noDiffEvidence },
        ],
        headline: `validated non-defect — no change needed (${headClip(why, 120)})`,
      });
    }
    if (mv?.kind === 'unactionable') {
      const why = headClip(mv.rationale, 240);
      return term({
        status: 'skipped',
        skipReason: `unactionable(${why})`,
        lesson: `maker (${makerName}) could not act on plan.instruction as written — rewrite the instruction before retrying`,
        claims: [
          { type: 'verified_fact', statement: `maker (${makerName}) produced no working-tree change for ${id}: instruction declared unactionable (HONE-VERDICT: unactionable — ${why})`, evidence: noDiffEvidence },
          { type: 'remaining_work', statement: `packet ${id} unexecuted (unactionable: ${why}); rewrite plan.instruction, reset to pending to retry` },
        ],
        headline: `maker declared instruction unactionable (${headClip(why, 120)})`,
      });
    }
    return term({
      status: 'skipped',
      skipReason: 'maker-no-diff: maker completed but modified nothing (no parseable HONE-VERDICT line)',
      lesson: `maker (${makerName}) replied without editing and without an explicit HONE-VERDICT; packet instruction may be unactionable as written`,
      claims: [
        { type: 'verified_fact', statement: `maker (${makerName}) produced no working-tree change for ${id}`, evidence: noDiffEvidence },
        { type: 'remaining_work', statement: `packet ${id} unexecuted; review plan.instruction actionability, reset to pending to retry` },
      ],
      headline: 'maker made no changes',
    });
  }

  // ---- touchset enforcement (structural — everything reverts on violation) ----
  const violations = changed.filter((p) => !touchTop.includes(p));
  if (violations.length) {
    revertAll(g);
    return term({
      status: 'skipped',
      skipReason: `touchset-violation: maker modified ${violations.join(', ')} outside touchset [${touchTop.join(', ')}] (both git-root-relative); ALL changes reverted`,
      lesson: `maker (${state.maker_provider}) violated the touchset; brief forbids it explicitly — treat as provider reliability signal`,
      claims: [
        { type: 'verified_fact', statement: `maker (${state.maker_provider}) modified files outside the packet touchset: ${violations.join(', ')}; everything reverted, nothing landed`, evidence: [{ command: 'git status --porcelain=v1 -uall', output_digest: `changed=[${changed.join(', ')}] touchset=[${touchTop.join(', ')}]` }] },
        { type: 'remaining_work', statement: `packet ${id} unexecuted after touchset violation; reset to pending to retry` },
      ],
      headline: `touchset violation: ${violations.join(', ')} — reverted`,
    });
  }

  // ---- deterministic oracle, attempt N (phase names mirror work: post, post-r1, post-r2) ----
  const attempt = state.gate_attempts + 1;
  const phase = attempt === 1 ? 'post' : `post-r${attempt - 1}`;
  state.gate_attempts = attempt;
  if (revisionNote) {
    const rel = join(receiptsDirRel, `revision-note-${attempt}.txt`);
    mkdirSync(join(repoRoot, 'quality', 'receipts', id), { recursive: true });
    writeFileSync(join(repoRoot, rel), `# hone lane ${id} — orchestrator revision note before gate attempt ${attempt}\n\n${revisionNote.slice(0, 8192)}`);
  }
  saveState(repoRoot, id, state); // attempt counted BEFORE running — crash-conservative

  let red = null;
  for (const [i, rung] of packet.evidence_required.entries()) {
    log(`  [${phase}] ${rung.rung}: ${rung.command}`);
    const res = await runShellCmd(rung.command, repoRoot);
    const verdict = checkExpect(rung, res, 'post', state.baseline[i]);
    const r = writeRungReceipt({ repoRoot, receiptsDirRel, id, via: 'lane', phase, index: i, rung, res, verdict, stripCtx });
    state.receiptLines.push(r.line); state.receiptSlices.push(r.slice); state.receiptMeta.push(r.meta);
    if (!verdict.pass) { red = { rung, verdict, res, digest: r.digest }; break; }
  }
  saveState(repoRoot, id, state);

  if (red) {
    if (attempt >= MAX_GATE_ATTEMPTS) {
      revertAll(g);
      return term({
        status: 'reverted',
        lesson: `transform failed its own evidence ladder at '${red.rung.rung}' — prior for ${packet.batch_key} down`,
        claims: [
          { type: 'verified_fact', statement: `evidence rung '${red.rung.rung}' still failing after ${attempt - 1} revision cycle(s) (${red.verdict.reason}); all changes reverted, nothing landed`, evidence: [{ command: red.rung.command, output_digest: red.digest }] },
          { type: 'remaining_work', statement: `packet ${id} reverted with a red oracle at '${red.rung.rung}'; needs a different approach or a better instruction` },
        ],
        headline: `oracle red at attempt ceiling (${attempt}/${MAX_GATE_ATTEMPTS}): '${red.rung.rung}' ${red.verdict.reason}`,
      });
    }
    const failureNote = `deterministic oracle rung '${red.rung.rung}' FAILED: ${red.verdict.reason}\ncommand: ${red.rung.command}\nexpect: ${red.rung.expect}\noutput tail:\n${tailClip(red.res.output, 4000)}`;
    const diff = buildWorkingDiff(g, touchTop);
    const brief = makerBrief(loaded.rawText, packet);
    const revBrief = revisionBrief(brief, failureNote, diff);
    state.brief_count += 1;
    persistMakerBriefDigest({ repoRoot, receiptsDirRel, id, via: 'lane', attempt: state.brief_count, briefText: revBrief });
    saveState(repoRoot, id, state);
    // full revision brief on disk: the orchestrator points the maker at the file instead
    // of relaying a 60KB-diff prompt through an agent pipe
    const revBriefPath = join(laneDir(repoRoot, id), `revision-brief-${attempt}.txt`);
    writeFileSync(revBriefPath, revBrief);
    return {
      exitCode: 1,
      json: {
        ok: false, green: false, candidate_id: id,
        red: { rung: red.rung.rung, command: red.rung.command, expect: red.rung.expect, reason: red.verdict.reason, output_tail: tailClip(red.res.output, 4000) },
        attempts_used: attempt, attempts_left: MAX_GATE_ATTEMPTS - attempt,
        revision_brief_path: revBriefPath,
        summary: `hone lane gate — ${id}: RED at '${red.rung.rung}' (attempt ${attempt}/${MAX_GATE_ATTEMPTS}) — tree preserved; run the maker on the revision brief at ${revBriefPath}, then re-gate. Next red at the ceiling reverts + terminalizes.`,
      },
    };
  }

  // ---- full green: bind the receipt to the exact tree state ----
  const diff = buildWorkingDiff(g, touchTop);
  const treeHash = djb2(state.head_sha + '\0' + diff);
  state.gate = { green: true, tree_hash: treeHash, at: new Date().toISOString(), attempt };
  saveState(repoRoot, id, state);
  const evidence = buildJudgeEvidence(state.receiptLines.map((line, i) => ({ line, slice: state.receiptSlices[i], ...state.receiptMeta[i] })));
  // judge context on disk (engine-written, so the judge reads trusted bytes directly —
  // no giant prompt relay): {packet_yaml, evidence, diff (150KB-clipped, work parity)}
  const judgeContextPath = join(laneDir(repoRoot, id), 'judge-context.json');
  writeFileSync(judgeContextPath, JSON.stringify({
    candidate_id: id, tree_hash: treeHash,
    packet_yaml: loaded.rawText, evidence, diff: tailClip(diff, 150000),
    receipts: state.receiptLines.slice(),
  }, null, 2));
  return {
    exitCode: 0,
    json: {
      ok: true, green: true, candidate_id: id, attempts_used: attempt, tree_hash: treeHash,
      receipts: state.receiptLines.slice(),
      judge_context_path: judgeContextPath,
      next: `independent judge (different model, fresh context) over ${judgeContextPath}, then: hone lane land --packet ${id} --repo ${repoRoot} --judge-verdict-b64 <b64> --usage-b64 <b64>`,
      summary: `hone lane gate — ${id}: GREEN (attempt ${attempt}; ${packet.evidence_required.length} rung(s); tree_hash ${treeHash})`,
    },
  };
}

// ---------------------------------------------------------------- land

export async function executeLaneLand({ id, repoRoot, verdictRaw = null, usageRaw = null, abort = false, abortReason = null }) {
  let loaded;
  try { loaded = loadPacket(repoRoot, id); }
  catch (e) { return refuse(id, 'land', e.message); }
  const { packet, path: packetPath } = loaded;
  if (packet.status !== 'in_progress') {
    return refuse(id, 'land', `packet status is '${packet.status}' — land requires an in_progress packet with lane state`);
  }
  let state;
  try { state = loadState(repoRoot, id); }
  catch (e) { return refuse(id, 'land', e.message); }
  if (!state) return refuse(id, 'land', `no lane state for ${id} (quality/.lane/${id}/) — nothing to land (fail-closed)`);

  let usage = null;
  if (usageRaw != null) {
    const u = parseUsageInput(usageRaw);
    if (u.errors.length) return refuse(id, 'land', `malformed --usage:\n  - ${u.errors.join('\n  - ')}`);
    usage = u.entries;
  }
  const tokens = usage ? aggregateUsage(usage) : { inTok: null, outTok: null, total: null, usd: null };
  const stages = usage ?? undefined;
  const quotaPts = usage ? tokens.quotaPts : undefined;

  const g = gitContext(repoRoot);
  const head = g.git(['rev-parse', 'HEAD']);
  if (head !== state.head_sha) {
    return laneTerminal({ repoRoot, id, state, packet, packetPath, tokens, stages, quotaPts, extras: { manual_cleanup_required: true } }, {
      status: 'blocked',
      blockedOn: `foreign-commit: HEAD moved ${state.head_sha.slice(0, 12)} → ${head.slice(0, 12)} between lane emit and land — engine revert/land guarantees void`,
      lesson: 'a lane maker must never run git write operations; manual cleanup required (tree NOT auto-reverted against a foreign HEAD)',
      claims: [
        { type: 'uncertainty', statement: `lane land for ${id} found HEAD moved from ${state.head_sha} to ${head}; nothing landed; working tree left untouched for manual review` },
        { type: 'remaining_work', statement: `packet ${id} blocked on foreign commit; inspect ${head.slice(0, 12)}, clean up manually, reset to pending to retry` },
      ],
      headline: 'foreign commit detected — blocked, tree left for manual cleanup',
    });
  }

  // ---- explicit abort: infrastructure/maker failure with no final verdict ----
  if (abort) {
    if (!abortReason || !abortReason.trim()) return refuse(id, 'land', `--abort requires --reason TEXT (negative results are recorded knowledge, never silent)`);
    revertAll(g);
    return laneTerminal({ repoRoot, id, state, packet, packetPath, tokens, stages, quotaPts }, {
      status: 'skipped',
      skipReason: `lane-abort(${headClip(abortReason, 240)})`,
      lesson: 'lane aborted by the orchestrator before a judge verdict; changes (if any) reverted; reset to pending to retry',
      claims: [
        { type: 'uncertainty', statement: `lane for ${id} aborted before a terminal gate decision: ${headClip(abortReason, 300)}; working tree reverted` },
        { type: 'remaining_work', statement: `packet ${id} unexecuted after lane abort; reset to pending to retry` },
      ],
      headline: `lane abort: ${headClip(abortReason, 120)} — reverted`,
    });
  }

  // ---- verdict + usage are REQUIRED for a non-abort land (fail-closed) ----
  if (verdictRaw == null) return refuse(id, 'land', 'missing --judge-verdict (or --judge-verdict-b64) — land never proceeds on an agent\'s say-so');
  if (usage == null) return refuse(id, 'land', 'missing --usage (or --usage-b64) — token usage is recorded explicitly; pass entries with null tokens if the harness cannot meter');
  const v = parseVerdictInput(verdictRaw);
  if (v.errors.length) return refuse(id, 'land', `malformed --judge-verdict:\n  - ${v.errors.join('\n  - ')}`);
  const verdict = v.verdict;

  // structural independence at provider:model identity (the lane's form of non-negotiable #1;
  // provider-level cross-checks remain available via hone work / codex retries)
  const makers = usage.filter((e) => e.role === 'maker');
  const judges = usage.filter((e) => e.role === 'judge');
  if (!makers.length || !judges.length) return refuse(id, 'land', '--usage must contain at least one maker entry and one judge entry');
  const identity = (e) => `${e.provider}:${e.model ?? ''}`;
  const clash = makers.find((m) => judges.some((j) => identity(j) === identity(m)));
  if (clash) return refuse(id, 'land', `maker == judge identity ('${identity(clash)}') — non-negotiable #1: the producer of a change cannot certify it (use a different judge model or route through hone work)`);
  if (makers.some((m) => m.provider !== state.maker_provider)) {
    return refuse(id, 'land', `usage maker provider disagrees with lane state (emitted maker_provider='${state.maker_provider}')`);
  }
  if (packet.judge_provider !== null && packet.judge_provider !== verdict.judge.provider) {
    return refuse(id, 'land', `packet pins judge_provider='${packet.judge_provider}' but verdict judge is '${verdict.judge.provider}'`);
  }

  // ---- the green-gate-receipt requirement, bound to THIS tree state ----
  if (!state.gate?.green) {
    return refuse(id, 'land', `no green gate receipt for ${id} — run hone lane gate to green before landing (agent claims of green are never trusted)`);
  }
  const treeHash = djb2(state.head_sha + '\0' + buildWorkingDiff(g, state.touchset_toplevel));
  if (treeHash !== state.gate.tree_hash) {
    return refuse(id, 'land', `tree state changed since the green gate (gate tree_hash ${state.gate.tree_hash}, current ${treeHash}) — re-run hone lane gate (fail-closed)`);
  }

  const judgeLabel = `${verdict.judge.provider}${verdict.judge.model ? `:${verdict.judge.model}` : ''}`;
  const makerLabel = `${makers[0].provider}${makers[0].model ? `:${makers[0].model}` : ''}`;
  const verdictLine = `${judgeLabel} ${verdict.verdict}${verdict.confidence != null ? ` (confidence ${verdict.confidence})` : ''}: ${verdict.reasoning}`;
  const revisionCount = Math.max(0, state.gate_attempts - 1);
  const base = { repoRoot, id, state, packet, packetPath, tokens, stages, quotaPts, judgeRan: true, makerName: makerLabel, judgeName: judgeLabel, judgeResult: verdict.verdict };

  if (verdict.verdict !== 'PASS') {
    revertAll(g);
    const kind = verdict.verdict === 'REJECT' ? 'REJECTED the change' : 'refused the change (final REVISE)';
    return laneTerminal(base, {
      status: 'reverted',
      judgeVerdict: verdictLine,
      lesson: `judge refused (${verdict.verdict}): ${headClip(verdict.reasoning, 240)}`,
      claims: [
        { type: 'judged_design_claim', statement: `independent judge ${kind}: ${verdict.reasoning}`, judge: { provider: judgeLabel, verdict: verdict.verdict } },
        { type: 'remaining_work', statement: `packet ${id} reverted on judge ${verdict.verdict}; address: ${headClip(verdict.reasoning, 240)}` },
      ],
      headline: `judge ${verdict.verdict} — reverted (never land without PASS)`,
    });
  }

  try {
    const commit = landCommit(g, {
      packet, id, touchTop: state.touchset_toplevel, receiptsDirRel: state.receipts_dir_rel,
      pipelineLabel: `hone lane: maker=${makerLabel} judge=${judgeLabel}`,
      confidence: verdict.confidence, revisionCount,
    });
    const claims = buildLandClaims({ packet, id, reasoning: verdict.reasoning, judgeProvider: judgeLabel, receiptLines: state.receiptLines, receiptsDirRel: state.receipts_dir_rel });
    return laneTerminal(base, {
      status: 'landed', commit,
      judgeVerdict: verdictLine,
      lesson: revisionCount ? `landed after ${revisionCount} revision cycle(s) — first attempt did not clear the gate` : null,
      claims,
      headline: `landed ${commit.slice(0, 12)} on ${g.branch}`,
    });
  } catch (e) {
    // fail-CLOSED: commit-time containment violation or unclean tree — revert + blocked
    try { revertAll(g); } catch (e2) { e.message += ` [AND REVERT FAILED: ${e2.message} — manual cleanup required]`; }
    return laneTerminal(base, {
      status: 'blocked',
      blockedOn: `internal-error: ${e.message.slice(0, 300)}`,
      lesson: 'engine fault at land time, not a packet fact — fix the engine, reset status to pending',
      claims: [
        { type: 'uncertainty', statement: `hone lane land aborted on internal error before/at commit: ${e.message.slice(0, 200)}` },
        { type: 'remaining_work', statement: `packet ${id} blocked on engine error; changes (if any) reverted; reset to pending after fixing` },
      ],
      headline: `internal error at land (fail-closed): ${e.message.slice(0, 120)}`,
    });
  }
}

// ---------------------------------------------------------------- batch mode (L2)
// N routine orders verified under ONE gate + ONE judge amortization: combined working
// tree, union touchset containment, one suite-level rung run; on red an automatic
// BISECT (group-testing over per-order changes) isolates offenders, reverts ONLY them,
// and the green remainder proceeds. Each order still lands as its OWN commit (per-order
// revertability). RISKY classes (auth/storage/behavior-visible — routing.json `batch`
// rules) are REFUSED: they stay per-order. Fail-closed everywhere.

const batchIdOf = (ids) => `b-${djb2([...ids].sort().join(','))}`;
const batchDir = (repoRoot, batchId) => join(repoRoot, 'quality', '.lane', '.batch', batchId);
const rungKey = (r) => JSON.stringify({ c: r.command, e: r.expect, x: r.expect_check ?? null });

const refuseBatch = (batchId, sub, reason) => ({
  exitCode: 2,
  json: { ok: false, refused: true, batch_id: batchId, reason, summary: `hone lane ${sub} --batch — ${batchId}: REFUSED\n  ${reason}` },
});

/** capture one member's working-tree change: tracked patch + untracked file contents. */
function captureMemberChange(g, touchTop) {
  const patch = g.git(['diff', '--', ...touchTop]);
  const untracked = [];
  for (const e of dirtyEntries(g)) {
    if (e.x !== '?') continue;
    for (const p of e.paths) {
      if (!touchTop.includes(p)) continue;
      untracked.push({ path: p, content_b64: readFileSync(join(g.gitRoot, p)).toString('base64') });
    }
  }
  return { patch, untracked };
}

/** restore the tree to HEAD, then apply exactly `changes` (used by bisect probes + remainder restore). */
function applyMemberChanges(g, changes) {
  revertAll(g);
  for (const ch of changes) {
    if (ch.patch && ch.patch.trim()) {
      const r = spawnSync('git', ['apply', '--whitespace=nowarn'], { cwd: g.gitRoot, input: ch.patch.endsWith('\n') ? ch.patch : ch.patch + '\n', encoding: 'utf8' });
      if (r.status !== 0) throw new Error(`bisect: git apply failed (${(r.stderr || '').slice(0, 300)}) — fail-closed`);
    }
    for (const u of ch.untracked) {
      mkdirSync(dirname(join(g.gitRoot, u.path)), { recursive: true });
      writeFileSync(join(g.gitRoot, u.path), Buffer.from(u.content_b64, 'base64'));
    }
  }
}

// batch gate takes NO usage: token accounting happens ONCE, at batch land, anchored on
// the first member (naive ledger sums stay honest). Gate-time terminals record null
// tokens — an honest unknown, never a divided fabrication.
export async function executeLaneBatchGate({ ids, repoRoot, log = () => {} }) {
  const uniq = [...new Set(ids)];
  const batchId = batchIdOf(uniq);
  if (uniq.length < 2) return refuseBatch(batchId, 'gate', `--batch needs >= 2 distinct packet ids (got ${uniq.length}) — a batch of one is just hone lane gate --packet`);

  // ---- load every member fail-closed (refusals have NO side effects) ----
  const members = [];
  for (const id of uniq) {
    let loaded;
    try { loaded = loadPacket(repoRoot, id); }
    catch (e) { return refuseBatch(batchId, 'gate', `[${id}] ${e.message}`); }
    if (loaded.packet.status !== 'in_progress') return refuseBatch(batchId, 'gate', `[${id}] status '${loaded.packet.status}' — every batch member must be emitted (in_progress) first`);
    let state;
    try { state = loadState(repoRoot, id); }
    catch (e) { return refuseBatch(batchId, 'gate', `[${id}] ${e.message}`); }
    if (!state) return refuseBatch(batchId, 'gate', `[${id}] no lane state — run hone lane emit first`);
    if (state.gate?.green) return refuseBatch(batchId, 'gate', `[${id}] already gate-green — land it (or reset) before batching`);
    members.push({ id, packet: loaded.packet, path: loaded.path, rawText: loaded.rawText, state });
  }

  // ---- RISKY-CLASS refusal (deterministic, data-driven; auth/storage/behavior-visible stay per-order) ----
  let routingTable;
  try { routingTable = loadRouting(); }
  catch (e) { return refuseBatch(batchId, 'gate', `routing.json unavailable (${e.message}) — batch eligibility cannot be proven, refusing (fail-closed)`); }
  const risky = members
    .map((m) => ({ id: m.id, ...isBatchEligible(m.packet, routingTable) }))
    .filter((r) => !r.eligible);
  if (risky.length) {
    return refuseBatch(batchId, 'gate', `risky-class members refuse batching (per-order only):\n  - ${risky.map((r) => `${r.id}: ${r.reason}`).join('\n  - ')}`);
  }

  // ---- disjoint touchsets (per-order commits are impossible over shared files) ----
  const seen = new Map();
  for (const m of members) {
    for (const p of m.state.touchset_toplevel) {
      if (seen.has(p)) return refuseBatch(batchId, 'gate', `touchset overlap on '${p}' (${seen.get(p)} and ${m.id}) — overlapping orders cannot land as separate commits; run them per-order`);
      seen.set(p, m.id);
    }
  }

  // ---- git state: one emit-head for the whole batch, HEAD unmoved ----
  const g = gitContext(repoRoot);
  const heads = [...new Set(members.map((m) => m.state.head_sha))];
  if (heads.length > 1) return refuseBatch(batchId, 'gate', `members were emitted at different HEADs (${heads.map((h) => h.slice(0, 12)).join(', ')}) — re-emit on one HEAD`);
  const head = g.git(['rev-parse', 'HEAD']);
  const memberTerm = (m, fields, extras) => laneTerminal({
    repoRoot, id: m.id, state: m.state, packet: m.packet, packetPath: m.path, extras,
  }, fields);
  if (head !== heads[0]) {
    const results = members.map((m) => memberTerm(m, {
      status: 'blocked',
      blockedOn: `foreign-commit: HEAD moved ${heads[0].slice(0, 12)} → ${head.slice(0, 12)} during batch ${batchId} — engine revert guarantees void`,
      lesson: 'a lane maker must never run git write operations; manual cleanup required (tree NOT auto-reverted against a foreign HEAD)',
      claims: [
        { type: 'uncertainty', statement: `batch ${batchId} gate found HEAD moved from ${heads[0]} to ${head}; working tree left untouched for manual review` },
        { type: 'remaining_work', statement: `packet ${m.id} blocked on foreign commit during batch; clean up manually, reset to pending to retry` },
      ],
      headline: 'foreign commit during batch — blocked, tree left for manual cleanup',
    }, { manual_cleanup_required: true }));
    return { exitCode: 1, json: { ok: false, terminal: 'blocked', batch_id: batchId, members: uniq, summary: results[0].json.summary } };
  }

  // ---- union containment + per-member no-diff ----
  const unionTouch = (ms) => ms.flatMap((m) => m.state.touchset_toplevel);
  const changed = flatPaths(dirtyEntries(g));
  const union = unionTouch(members);
  const violations = changed.filter((p) => !union.includes(p));
  if (violations.length) {
    revertAll(g);
    const results = members.map((m) => memberTerm(m, {
      status: 'skipped',
      skipReason: `touchset-violation (batch ${batchId}): changed paths outside the batch union: ${violations.join(', ')}; ALL changes reverted (unattributable to one member)`,
      lesson: 'a batch maker violated the union touchset; violation is unattributable — everything reverted',
      claims: [
        { type: 'verified_fact', statement: `batch ${batchId} contained changes outside the union touchset: ${violations.join(', ')}; everything reverted, nothing landed`, evidence: [{ command: 'git status --porcelain=v1 -uall', output_digest: `changed=[${changed.join(', ')}] union=[${union.join(', ')}]` }] },
        { type: 'remaining_work', statement: `packet ${m.id} unexecuted after batch touchset violation; reset to pending to retry` },
      ],
      headline: `batch touchset violation: ${violations.join(', ')} — everything reverted`,
    }));
    return { exitCode: 1, json: { ok: false, terminal: 'skipped', batch_id: batchId, members: uniq, summary: results[0].json.summary } };
  }
  let active = [];
  const noDiff = [];
  for (const m of members) {
    const mine = changed.filter((p) => m.state.touchset_toplevel.includes(p));
    if (mine.length) active.push(m);
    else noDiff.push(m);
  }
  const results = [];
  for (const m of noDiff) {
    results.push({ id: m.id, terminal: 'skipped', reason: 'maker-no-diff (batch)' });
    memberTerm(m, {
      status: 'skipped',
      skipReason: `maker-no-diff (batch ${batchId}): no change inside this member's touchset`,
      lesson: 'batch member arrived at gate with no diff; HONE-VERDICT closes are per-order — replay/validated-non-defect candidates must not ride a batch',
      claims: [
        { type: 'verified_fact', statement: `no working-tree change for ${m.id} inside its touchset at batch ${batchId} gate`, evidence: [{ command: 'git status --porcelain=v1 -uall', output_digest: `member touchset [${m.state.touchset_toplevel.join(', ')}] untouched` }] },
        { type: 'remaining_work', statement: `packet ${m.id} unexecuted; run it per-order (reset to pending)` },
      ],
      headline: 'maker made no changes for this batch member',
    });
  }
  if (!active.length) {
    return { exitCode: 1, json: { ok: false, terminal: 'skipped', batch_id: batchId, members: uniq, results, summary: `hone lane gate --batch — ${batchId}: every member had no diff; all skipped` } };
  }

  // ---- capture per-member changes (bisect fuel), then ONE union rung run ----
  for (const m of active) m.change = captureMemberChange(g, m.state.touchset_toplevel);
  mkdirSync(batchDir(repoRoot, batchId), { recursive: true });
  const receiptsDirRel = join('quality', 'receipts', batchId);
  const stripCtx = { qualityRel: g.topRel('quality'), candidateId: batchId };

  /** deduped union rung plan over `ms`, each rung bound to its first owner's baseline. */
  const rungPlan = (ms) => {
    const plan = [];
    const have = new Set();
    for (const m of ms) {
      for (const [i, rung] of m.packet.evidence_required.entries()) {
        const k = rungKey(rung);
        if (have.has(k)) continue;
        have.add(k);
        plan.push({ rung, key: k, baseline: m.state.baseline[i], ownerId: m.id });
      }
    }
    return plan;
  };
  /** run a rung plan; stop at first red. Receipts under the batch dir, phase-labelled. */
  let probeSeq = 0;
  const runPlan = async (plan, phase) => {
    const entries = [];
    for (const [i, p] of plan.entries()) {
      log(`  [${phase}] ${p.rung.rung}: ${p.rung.command}`);
      const res = await runShellCmd(p.rung.command, repoRoot);
      const verdict = checkExpect(p.rung, res, 'post', p.baseline);
      const r = writeRungReceipt({ repoRoot, receiptsDirRel, id: batchId, via: 'lane', phase, index: i, rung: p.rung, res, verdict, stripCtx });
      entries.push({ key: p.key, ...r });
      if (!verdict.pass) return { green: false, red: { plan: p, verdict, res, receipt: r }, entries };
    }
    return { green: true, entries };
  };
  /** append a run's receipt lines to every member of `ms` whose ladder contains the rung. */
  const bookkeep = (runRes, ms) => {
    for (const e of runRes.entries) {
      for (const m of ms) {
        if (!m.packet.evidence_required.some((r) => rungKey(r) === e.key)) continue;
        m.state.receiptLines.push(e.line);
        m.state.receiptSlices.push(e.slice);
        m.state.receiptMeta.push(e.meta);
      }
    }
  };
  const bindGreen = (ms, attemptNote) => {
    const diff = buildWorkingDiff(g, unionTouch(ms));
    const treeHash = djb2(head + '\0' + diff);
    const memberIds = ms.map((m) => m.id).sort();
    for (const m of ms) {
      m.state.gate_attempts = Math.max(1, m.state.gate_attempts + 1);
      m.state.gate = { green: true, tree_hash: treeHash, at: new Date().toISOString(), attempt: m.state.gate_attempts, batch: { id: batchId, ids: memberIds, note: attemptNote } };
      saveState(repoRoot, m.id, m.state);
    }
    // evidence: every member's baselines, then all post-phase lines deduped across
    // members (batch receipt lines were bookkept into every owning member's state)
    const evidenceEntries = [];
    const seenLines = new Set();
    for (const m of ms) {
      m.state.receiptMeta.forEach((meta, i) => {
        if (meta.phase === 'baseline') evidenceEntries.push({ line: m.state.receiptLines[i], slice: m.state.receiptSlices[i], ...meta });
      });
    }
    for (const m of ms) {
      m.state.receiptMeta.forEach((meta, i) => {
        if (meta.phase === 'baseline' || seenLines.has(m.state.receiptLines[i])) return;
        seenLines.add(m.state.receiptLines[i]);
        evidenceEntries.push({ line: m.state.receiptLines[i], slice: m.state.receiptSlices[i], ...meta });
      });
    }
    const judgeContextPath = join(batchDir(repoRoot, batchId), 'judge-context.json');
    writeFileSync(judgeContextPath, JSON.stringify({
      batch_id: batchId, members: memberIds, tree_hash: treeHash,
      packet_yamls: Object.fromEntries(ms.map((m) => [m.id, m.rawText])),
      evidence: buildJudgeEvidence(evidenceEntries),
      diff: tailClip(diff, 150000),
    }, null, 2));
    return { treeHash, judgeContextPath, memberIds };
  };

  let run = await runPlan(rungPlan(active), 'post');
  bookkeep(run, active);
  if (!run.green) {
    // ---- BISECT: group-test per-member changes to isolate offenders (log₂N probes) ----
    log(`  batch RED at '${run.red.plan.rung.rung}' — bisecting ${active.length} member(s)`);
    const offenders = []; // [{m, red}] — red = the SINGLETON probe's failure (the isolation evidence)
    const probe = async (subset) => {
      applyMemberChanges(g, subset.map((m) => m.change));
      return runPlan(rungPlan(subset), `bisect-${++probeSeq}-${subset.map((m) => m.id).join('+').slice(0, 40)}`);
    };
    const search = async (subset) => {
      if (!subset.length) return;
      const r = await probe(subset);
      if (r.green) return; // this whole subset is green
      if (subset.length === 1) { offenders.push({ m: subset[0], red: r.red }); return; }
      const mid = Math.ceil(subset.length / 2);
      await search(subset.slice(0, mid));
      await search(subset.slice(mid));
    };
    await search(active);
    for (const { m, red } of offenders) {
      writeFileSync(join(batchDir(repoRoot, batchId), `offender-${m.id}.diff`), m.change.patch + m.change.untracked.map((u) => `\n[untracked ${u.path}: ${u.content_b64.length} b64 bytes]`).join(''));
      m.state.receiptLines.push(`[bisect] batch ${batchId}: isolated as an offender — with ONLY this member's change applied, rung '${red.plan.rung.rung}' fails (${red.verdict.reason}); change reverted, saved to ${join('quality', '.lane', '.batch', batchId, `offender-${m.id}.diff`)}`);
      results.push({ id: m.id, terminal: 'reverted', reason: `bisect offender at '${red.plan.rung.rung}'` });
      memberTerm(m, {
        status: 'reverted',
        lesson: `batch bisect isolated this change as an offender at '${red.plan.rung.rung}' — prior for ${m.packet.batch_key} down`,
        claims: [
          { type: 'verified_fact', statement: `batch ${batchId} bisect: with ONLY ${m.id}'s change applied, rung '${red.plan.rung.rung}' fails (${red.verdict.reason}); change reverted, nothing landed`, evidence: [{ command: red.plan.rung.command, output_digest: red.receipt.digest }] },
          { type: 'remaining_work', statement: `packet ${m.id} reverted as a batch-bisect offender; retry per-order with a better instruction` },
        ],
        headline: `bisect offender at '${red.plan.rung.rung}' — reverted`,
      });
    }
    const offenderMembers = offenders.map((o) => o.m);
    const remainder = active.filter((m) => !offenderMembers.includes(m));
    if (!remainder.length) {
      revertAll(g);
      return { exitCode: 1, json: { ok: false, terminal: 'reverted', batch_id: batchId, members: uniq, results, summary: `hone lane gate --batch — ${batchId}: every member isolated as an offender; all reverted` } };
    }
    // restore the green remainder, then the AUTHORITATIVE final gate on the exact landing tree
    applyMemberChanges(g, remainder.map((m) => m.change));
    const final = await runPlan(rungPlan(remainder), 'post-bisect');
    bookkeep(final, remainder);
    if (!final.green) {
      // interaction effect: members green in isolation, red combined — fail-closed
      revertAll(g);
      for (const m of remainder) {
        results.push({ id: m.id, terminal: 'reverted', reason: 'batch-interaction' });
        memberTerm(m, {
          status: 'reverted',
          lesson: `batch ${batchId} interaction: remainder green in isolation but red combined at '${final.red.plan.rung.rung}' — batch these orders separately`,
          claims: [
            { type: 'verified_fact', statement: `batch ${batchId} remainder failed combined at rung '${final.red.plan.rung.rung}' (${final.red.verdict.reason}) after individual probes were green; all remaining changes reverted`, evidence: [{ command: final.red.plan.rung.command, output_digest: final.red.receipt.digest }] },
            { type: 'remaining_work', statement: `packet ${m.id} reverted on batch interaction; retry per-order` },
          ],
          headline: `batch interaction red at '${final.red.plan.rung.rung}' — remainder reverted`,
        });
      }
      return { exitCode: 1, json: { ok: false, terminal: 'reverted', batch_id: batchId, members: uniq, results, summary: `hone lane gate --batch — ${batchId}: interaction red after bisect; everything reverted` } };
    }
    const bound = bindGreen(remainder, `green after bisect (offenders: ${offenderMembers.map((o) => o.id).join(', ') || 'none'})`);
    return {
      exitCode: 0,
      json: {
        ok: true, green: true, batch_id: batchId, members: bound.memberIds, offenders: offenderMembers.map((o) => o.id),
        results, tree_hash: bound.treeHash, judge_context_path: bound.judgeContextPath,
        next: `judge over ${bound.judgeContextPath}, then: hone lane land --batch ${bound.memberIds.join(',')} --repo ${repoRoot} --judge-verdict-b64 <b64> --usage-b64 <b64>`,
        summary: `hone lane gate --batch — ${batchId}: GREEN remainder of ${bound.memberIds.length}/${active.length} after bisect (${offenders.length} offender(s) reverted)`,
      },
    };
  }

  const bound = bindGreen(active, 'green first pass');
  return {
    exitCode: 0,
    json: {
      ok: true, green: true, batch_id: batchId, members: bound.memberIds, offenders: [],
      results, tree_hash: bound.treeHash, judge_context_path: bound.judgeContextPath,
      next: `judge over ${bound.judgeContextPath}, then: hone lane land --batch ${bound.memberIds.join(',')} --repo ${repoRoot} --judge-verdict-b64 <b64> --usage-b64 <b64>`,
      summary: `hone lane gate --batch — ${batchId}: GREEN (${bound.memberIds.length} member(s), one suite-level run; tree_hash ${bound.treeHash})`,
    },
  };
}

export async function executeLaneBatchLand({ ids, repoRoot, verdictRaw = null, usageRaw = null, log = () => {} }) {
  const uniq = [...new Set(ids)].sort();
  const batchId = batchIdOf(uniq);
  // NOTE: land must be invoked with the GREEN membership from gate (post-bisect
  // remainder). Each member's gate receipt names that membership; mismatches refuse.
  const members = [];
  for (const id of uniq) {
    let loaded;
    try { loaded = loadPacket(repoRoot, id); }
    catch (e) { return refuseBatch(batchId, 'land', `[${id}] ${e.message}`); }
    if (loaded.packet.status !== 'in_progress') return refuseBatch(batchId, 'land', `[${id}] status '${loaded.packet.status}' — not landable`);
    let state;
    try { state = loadState(repoRoot, id); }
    catch (e) { return refuseBatch(batchId, 'land', `[${id}] ${e.message}`); }
    if (!state) return refuseBatch(batchId, 'land', `[${id}] no lane state — nothing to land (fail-closed)`);
    if (!state.gate?.green) return refuseBatch(batchId, 'land', `[${id}] no green gate receipt — run hone lane gate --batch first (agent claims of green are never trusted)`);
    if (!state.gate.batch || state.gate.batch.ids.join(',') !== uniq.join(',')) {
      return refuseBatch(batchId, 'land', `[${id}] gate receipt binds batch [${state.gate.batch?.ids?.join(',') ?? '(none — single-order gate)'}] but land was invoked for [${uniq.join(',')}] — land the exact green membership`);
    }
    members.push({ id, packet: loaded.packet, path: loaded.path, state });
  }
  if (verdictRaw == null) return refuseBatch(batchId, 'land', 'missing --judge-verdict — land never proceeds on an agent\'s say-so');
  if (usageRaw == null) return refuseBatch(batchId, 'land', 'missing --usage — token usage is recorded explicitly (null tokens allowed, absent usage is not)');
  const u = parseUsageInput(usageRaw);
  if (u.errors.length) return refuseBatch(batchId, 'land', `malformed --usage:\n  - ${u.errors.join('\n  - ')}`);
  const usage = u.entries;
  const v = parseVerdictInput(verdictRaw);
  if (v.errors.length) return refuseBatch(batchId, 'land', `malformed --judge-verdict:\n  - ${v.errors.join('\n  - ')}`);
  const verdict = v.verdict;

  const makers = usage.filter((e) => e.role === 'maker');
  const judges = usage.filter((e) => e.role === 'judge');
  if (!makers.length || !judges.length) return refuseBatch(batchId, 'land', '--usage must contain at least one maker entry and one judge entry');
  const identity = (e) => `${e.provider}:${e.model ?? ''}`;
  const clash = makers.find((m) => judges.some((j) => identity(j) === identity(m)));
  if (clash) return refuseBatch(batchId, 'land', `maker == judge identity ('${identity(clash)}') — non-negotiable #1`);
  for (const m of members) {
    if (makers.some((mk) => mk.provider !== m.state.maker_provider)) {
      return refuseBatch(batchId, 'land', `[${m.id}] usage maker provider disagrees with lane state (emitted maker_provider='${m.state.maker_provider}')`);
    }
    if (m.packet.judge_provider !== null && m.packet.judge_provider !== verdict.judge.provider) {
      return refuseBatch(batchId, 'land', `[${m.id}] packet pins judge_provider='${m.packet.judge_provider}' but verdict judge is '${verdict.judge.provider}'`);
    }
  }

  const g = gitContext(repoRoot);
  const head = g.git(['rev-parse', 'HEAD']);
  const emitHead = members[0].state.head_sha;
  const agg = aggregateUsage(usage);
  // usage lives ONCE on the anchor (first member): naive ledger sums stay honest;
  // non-anchor entries carry null tokens + the batch marker for report-side amortization.
  // A batch that bisect collapsed to ONE member is just a single land — no marker
  // (the ledger schema requires size >= 2 for a marker, deliberately).
  const batchMarker = members.length >= 2 ? { batch_id: batchId, size: members.length, anchor: members[0].id } : undefined;
  const termFor = (m, isAnchor, judgeMeta, fields) => laneTerminal({
    repoRoot, id: m.id, state: m.state, packet: m.packet, packetPath: m.path,
    tokens: isAnchor ? agg : undefined,
    stages: isAnchor ? usage : undefined,
    quotaPts: isAnchor ? agg.quotaPts : undefined,
    batch: batchMarker,
    ...judgeMeta,
  }, fields);

  if (head !== emitHead) {
    const rs = members.map((m, i) => termFor(m, i === 0, {}, {
      status: 'blocked',
      blockedOn: `foreign-commit: HEAD moved ${emitHead.slice(0, 12)} → ${head.slice(0, 12)} before batch land — engine guarantees void`,
      lesson: 'a lane maker must never run git write operations; manual cleanup required',
      claims: [
        { type: 'uncertainty', statement: `batch ${batchId} land found HEAD moved from ${emitHead} to ${head}; nothing landed; tree left for manual review` },
        { type: 'remaining_work', statement: `packet ${m.id} blocked on foreign commit; clean up manually, reset to pending to retry` },
      ],
      headline: 'foreign commit before batch land — blocked',
    }));
    return { exitCode: 1, json: { ok: false, terminal: 'blocked', batch_id: batchId, members: uniq, summary: rs[0].json.summary, manual_cleanup_required: true } };
  }
  const unionTouch = members.flatMap((m) => m.state.touchset_toplevel);
  const treeHash = djb2(emitHead + '\0' + buildWorkingDiff(g, unionTouch));
  if (members.some((m) => m.state.gate.tree_hash !== treeHash)) {
    return refuseBatch(batchId, 'land', `tree state changed since the green batch gate (gate ${members[0].state.gate.tree_hash}, current ${treeHash}) — re-run hone lane gate --batch (fail-closed)`);
  }

  const judgeLabel = `${verdict.judge.provider}${verdict.judge.model ? `:${verdict.judge.model}` : ''}`;
  const makerLabel = `${makers[0].provider}${makers[0].model ? `:${makers[0].model}` : ''}`;
  const verdictLine = `${judgeLabel} ${verdict.verdict}${verdict.confidence != null ? ` (confidence ${verdict.confidence})` : ''}: ${verdict.reasoning} [batch ${batchId}, ${members.length} order(s)]`;
  const judgeMeta = { judgeRan: true, makerName: makerLabel, judgeName: judgeLabel, judgeResult: verdict.verdict };

  if (verdict.verdict !== 'PASS') {
    revertAll(g);
    const kind = verdict.verdict === 'REJECT' ? 'REJECTED the combined change' : 'refused the combined change (final REVISE)';
    const rs = members.map((m, i) => termFor(m, i === 0, judgeMeta, {
      status: 'reverted',
      judgeVerdict: verdictLine,
      lesson: `judge refused the batch (${verdict.verdict}): ${headClip(verdict.reasoning, 240)} — retry per-order for attribution`,
      claims: [
        { type: 'judged_design_claim', statement: `independent judge ${kind} of batch ${batchId}: ${verdict.reasoning}`, judge: { provider: judgeLabel, verdict: verdict.verdict } },
        { type: 'remaining_work', statement: `packet ${m.id} reverted on batch judge ${verdict.verdict}; address: ${headClip(verdict.reasoning, 240)}` },
      ],
      headline: `judge ${verdict.verdict} on batch — reverted (never land without PASS)`,
    }));
    rmSync(batchDir(repoRoot, batchId), { recursive: true, force: true });
    return { exitCode: 1, json: { ok: false, terminal: 'reverted', batch_id: batchId, members: uniq, summary: rs[0].json.summary } };
  }

  // ---- PASS: land each order as its OWN commit (per-order revertability preserved) ----
  const landedResults = [];
  for (const [i, m] of members.entries()) {
    const remainingTouch = members.slice(i + 1).flatMap((x) => x.state.touchset_toplevel);
    log(`  [land] batch ${batchId} order ${i + 1}/${members.length}: ${m.id}`);
    try {
      const commit = landCommit(g, {
        packet: m.packet, id: m.id, touchTop: m.state.touchset_toplevel, receiptsDirRel: m.state.receipts_dir_rel,
        pipelineLabel: `hone lane batch ${batchId} (${members.length} orders): maker=${makerLabel} judge=${judgeLabel}`,
        confidence: verdict.confidence, revisionCount: 0, allowedLeftover: remainingTouch,
      });
      const claims = buildLandClaims({ packet: m.packet, id: m.id, reasoning: verdict.reasoning, judgeProvider: judgeLabel, receiptLines: m.state.receiptLines, receiptsDirRel: m.state.receipts_dir_rel });
      const r = termFor(m, i === 0, judgeMeta, {
        status: 'landed', commit, judgeVerdict: verdictLine, lesson: null, claims,
        headline: `landed ${commit.slice(0, 12)} on ${g.branch} (batch ${batchId}, order ${i + 1}/${members.length})`,
      });
      landedResults.push({ id: m.id, terminal: 'landed', commit, summary: r.json.summary });
    } catch (e) {
      // fail-CLOSED mid-sequence: earlier commits stand (their books are written);
      // everything not yet committed reverts, remaining members block.
      try { revertAll(g); } catch (e2) { e.message += ` [AND REVERT FAILED: ${e2.message}]`; }
      for (const rest of members.slice(i)) {
        termFor(rest, false, judgeMeta, {
          status: 'blocked',
          blockedOn: `internal-error during batch land: ${e.message.slice(0, 300)}`,
          lesson: 'engine fault mid-batch-land; earlier members landed, this one reverted — fix, reset to pending',
          claims: [
            { type: 'uncertainty', statement: `batch ${batchId} land aborted at ${rest.id}: ${e.message.slice(0, 200)}` },
            { type: 'remaining_work', statement: `packet ${rest.id} blocked mid-batch-land; changes reverted; reset to pending after fixing` },
          ],
          headline: `internal error mid-batch-land (fail-closed): ${e.message.slice(0, 120)}`,
        });
        landedResults.push({ id: rest.id, terminal: 'blocked', reason: e.message.slice(0, 160) });
      }
      rmSync(batchDir(repoRoot, batchId), { recursive: true, force: true });
      return { exitCode: 1, json: { ok: false, terminal: 'blocked', batch_id: batchId, members: uniq, results: landedResults, summary: `hone lane land --batch — ${batchId}: internal error mid-sequence; ${i} landed, rest blocked` } };
    }
  }
  rmSync(batchDir(repoRoot, batchId), { recursive: true, force: true });
  return {
    exitCode: 0,
    json: {
      ok: true, terminal: 'landed', batch_id: batchId, members: uniq, results: landedResults,
      commits: landedResults.map((r) => r.commit),
      summary: `hone lane land --batch — ${batchId}: LANDED ${landedResults.length} order(s), one commit each${batchMarker ? `; usage anchored on ${batchMarker.anchor}` : ' (single member — plain land accounting)'}`,
    },
  };
}

// ---------------------------------------------------------------- input schemas (fail-closed)

/**
 * usage entries: explicit token/cost accounting for the ledger — an ARRAY of per-STAGE
 * records {role: maker|judge|engine|planner, provider, model?, stage?: recon|edit|test|
 * judge|plan, tokens_in?, tokens_out?, tokens_total?, cache_read_tokens?, cost_usd?,
 * wall_s?, quota_pts?}. Back-compat: a bare JSON OBJECT is one single unattributed stage
 * (stage null), so pre-stage callers keep working. Unknown keys and wrong types are
 * refused — a malformed usage line must never reach the cost ledger half-parsed.
 */
export function parseUsageInput(raw) {
  const errors = [];
  let arr;
  try { arr = JSON.parse(raw); }
  catch (e) { return { entries: null, errors: [`not valid JSON: ${e.message}`] }; }
  if (arr !== null && typeof arr === 'object' && !Array.isArray(arr)) arr = [arr]; // bare object = single unattributed stage
  if (!Array.isArray(arr) || !arr.length) return { entries: null, errors: ['usage must be a non-empty JSON array (or a single stage object)'] };
  const entries = arr.map((e, i) => {
    if (e === null || typeof e !== 'object' || Array.isArray(e)) { errors.push(`[${i}]: not a map`); return null; }
    const normalized = {
      role: e.role, provider: e.provider, model: e.model ?? null, stage: e.stage ?? null,
      tokens_in: e.tokens_in ?? null, tokens_out: e.tokens_out ?? null, tokens_total: e.tokens_total ?? null,
      cache_read_tokens: e.cache_read_tokens ?? null, cost_usd: e.cost_usd ?? null,
      wall_s: e.wall_s ?? null, quota_pts: e.quota_pts ?? null,
    };
    const stageErrs = validateStageEntry({ ...normalized }, i); // the ledger's stage schema IS the input schema
    for (const k of Object.keys(e)) {
      if (!(k in normalized)) errors.push(`[${i}]: unknown key '${k}'`);
    }
    errors.push(...stageErrs.map((m) => m.replace(/^stages\[/, '[')));
    return normalized;
  });
  return errors.length ? { entries: null, errors } : { entries, errors: [] };
}

/** null-aware aggregation with the same semantics as work.mjs tokensOf() (+ quota_pts). */
export function aggregateUsage(entries) {
  let inTok = null, outTok = null, total = null, usd = null, quotaPts = null;
  const add = (cur, v) => (v == null ? cur : (cur ?? 0) + v);
  for (const e of entries) {
    inTok = add(inTok, e.tokens_in);
    outTok = add(outTok, e.tokens_out);
    total = add(total, e.tokens_total ?? (e.tokens_in != null && e.tokens_out != null ? e.tokens_in + e.tokens_out : null));
    usd = add(usd, e.cost_usd);
    quotaPts = add(quotaPts, e.quota_pts);
  }
  return { inTok, outTok, total, usd, quotaPts };
}

/** judge verdict: {verdict: PASS|REVISE|REJECT, reasoning, confidence?, judge:{provider, model?}}. */
export function parseVerdictInput(raw) {
  const errors = [];
  let v;
  try { v = JSON.parse(raw); }
  catch (e) { return { verdict: null, errors: [`not valid JSON: ${e.message}`] }; }
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return { verdict: null, errors: ['verdict must be a JSON object'] };
  const KEYS = ['verdict', 'reasoning', 'confidence', 'judge'];
  for (const k of Object.keys(v)) if (!KEYS.includes(k)) errors.push(`unknown key '${k}'`);
  if (!VERDICTS.includes(v.verdict)) errors.push(`verdict: must be one of [${VERDICTS.join(' | ')}], got ${JSON.stringify(v.verdict)}`);
  if (typeof v.reasoning !== 'string' || !v.reasoning.trim()) errors.push('reasoning: non-empty string required');
  let confidence = null;
  if (v.confidence != null) {
    const n = Number(v.confidence);
    if (!Number.isFinite(n) || n < 0 || n > 1) errors.push('confidence: number in [0,1] or null required');
    else confidence = n;
  }
  if (v.judge === null || typeof v.judge !== 'object' || Array.isArray(v.judge) ||
    typeof v.judge?.provider !== 'string' || !v.judge.provider.trim()) {
    errors.push('judge: {provider, model?} with non-empty provider required');
  } else if (v.judge.model != null && (typeof v.judge.model !== 'string' || !v.judge.model.trim())) {
    errors.push('judge.model: string|null required');
  }
  if (errors.length) return { verdict: null, errors };
  return { verdict: { verdict: v.verdict, reasoning: v.reasoning, confidence, judge: { provider: v.judge.provider, model: v.judge.model ?? null } }, errors: [] };
}
