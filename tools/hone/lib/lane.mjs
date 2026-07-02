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
import { join, resolve } from 'node:path';
import { djb2 } from './util.mjs';
import { validatePacket } from './validate-packet.mjs';
import { loadPacket, writePacket } from './packet-io.mjs';
import {
  gitContext, dirtyEntries, flatPaths, normalizeTouchEntry, revertAll,
  checkExpect, runShellCmd, isCompareVsHead, makerBrief, revisionBrief,
  parseMakerVerdict, tailClip, headClip, buildJudgeEvidence, buildWorkingDiff,
  writeRungReceipt, persistMakerBriefDigest, writeTerminal, landCommit, buildLandClaims,
  acquireWorkLock,
} from './work.mjs';

const PROVIDERS = ['claude', 'codex'];
const VERDICTS = ['PASS', 'REVISE', 'REJECT'];
const USAGE_ROLES = ['maker', 'judge'];
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
  const usage = `usage: hone lane <emit|gate|land> --packet <candidate-id> --repo PATH
  emit  [--maker claude|codex] [--judge claude|codex] [--dry-run]
  gate  [--maker-summary FILE | --maker-summary-b64 B64] [--revision-note-b64 B64] [--usage FILE | --usage-b64 B64]
  land  (--judge-verdict FILE | --judge-verdict-b64 B64) (--usage FILE | --usage-b64 B64)
        | --abort --reason TEXT [--usage FILE | --usage-b64 B64]`;
  if (!['emit', 'gate', 'land'].includes(sub) || !id) throw new Error(usage);
  const common = { id, repoRoot: resolve(flags.repo || '.'), log: (s) => process.stderr.write(s + '\n') };
  let res;
  if (sub === 'emit') {
    res = await executeLaneEmit({
      ...common,
      makerProvider: String(flags.maker || 'claude'),
      judgeProvider: String(flags.judge || 'claude'),
      dryRun: !!flags['dry-run'],
    });
  } else if (sub === 'gate') {
    res = await executeLaneGate({
      ...common,
      makerSummary: readInput(flags, 'maker-summary'),
      revisionNote: readInput(flags, 'revision-note'),
      usageRaw: readInput(flags, 'usage'),
    });
  } else {
    res = await executeLaneLand({
      ...common,
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
function laneTerminal({ repoRoot, id, state, packet, packetPath, tokens, judgeRan, makerName, judgeName, judgeResult, extras }, fields) {
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

  const brief = makerBrief(rawText, packet);
  if (dryRun) {
    return {
      exitCode: 0,
      json: {
        ok: true, dry_run: true, candidate_id: id,
        packet_path: packetPath, action: packet.action, proof_class: packet.proof_class,
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
    for (const [i, rung] of packet.evidence_required.entries()) {
      log(`  [baseline] ${rung.rung}: ${rung.command}`);
      const res = await runShellCmd(rung.command, repoRoot);
      const verdict = checkExpect(rung, res, 'baseline');
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

    return {
      exitCode: 0,
      json: {
        ok: true, candidate_id: id, packet_path: packetPath,
        repo: { git_root: g.gitRoot, repo_root: repoRoot, branch: g.branch, head_sha: head },
        action: packet.action, proof_class: packet.proof_class, behavior_status: packet.behavior_status,
        maker_tier: packet.maker_tier, judge_tier: packet.judge_tier,
        touchset_toplevel: touchTop, not_allowed: packet.not_allowed, plan: packet.plan,
        evidence: packet.evidence_required.map((r) => ({ rung: r.rung, command: r.command, expect: r.expect, expect_check: r.expect_check ?? null })),
        baseline: state.receiptLines.slice(),
        brief, packet_yaml: rawText,
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
  const term = (fields, extras) => laneTerminal({
    repoRoot, id, state, packet, packetPath,
    tokens: usage ? aggregateUsage(usage) : undefined, extras,
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
    return {
      exitCode: 1,
      json: {
        ok: false, green: false, candidate_id: id,
        red: { rung: red.rung.rung, command: red.rung.command, expect: red.rung.expect, reason: red.verdict.reason, output_tail: tailClip(red.res.output, 4000) },
        attempts_used: attempt, attempts_left: MAX_GATE_ATTEMPTS - attempt,
        revision_brief: revBrief,
        summary: `hone lane gate — ${id}: RED at '${red.rung.rung}' (attempt ${attempt}/${MAX_GATE_ATTEMPTS}) — tree preserved; run the maker on revision_brief, then re-gate. Next red at the ceiling reverts + terminalizes.`,
      },
    };
  }

  // ---- full green: bind the receipt to the exact tree state ----
  const diff = buildWorkingDiff(g, touchTop);
  const treeHash = djb2(state.head_sha + '\0' + diff);
  state.gate = { green: true, tree_hash: treeHash, at: new Date().toISOString(), attempt };
  saveState(repoRoot, id, state);
  const evidence = buildJudgeEvidence(state.receiptLines.map((line, i) => ({ line, slice: state.receiptSlices[i], ...state.receiptMeta[i] })));
  return {
    exitCode: 0,
    json: {
      ok: true, green: true, candidate_id: id, attempts_used: attempt, tree_hash: treeHash,
      receipts: state.receiptLines.slice(),
      evidence,
      diff: tailClip(diff, 150000),
      packet_yaml: loaded.rawText,
      next: `independent judge (different model, fresh context) over {packet_yaml, evidence, diff}, then: hone lane land --packet ${id} --repo ${repoRoot} --judge-verdict-b64 <b64> --usage-b64 <b64>`,
      summary: `hone lane gate — ${id}: GREEN (attempt ${attempt}; ${packet.evidence_required.length} rung(s); tree_hash ${treeHash})`,
    },
  };
}

// ---------------------------------------------------------------- land

export async function executeLaneLand({ id, repoRoot, verdictRaw = null, usageRaw = null, abort = false, abortReason = null, log = () => {} }) {
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

  const g = gitContext(repoRoot);
  const head = g.git(['rev-parse', 'HEAD']);
  if (head !== state.head_sha) {
    return laneTerminal({ repoRoot, id, state, packet, packetPath, tokens, extras: { manual_cleanup_required: true } }, {
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
    return laneTerminal({ repoRoot, id, state, packet, packetPath, tokens }, {
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
  const base = { repoRoot, id, state, packet, packetPath, tokens, judgeRan: true, makerName: makerLabel, judgeName: judgeLabel, judgeResult: verdict.verdict };

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

// ---------------------------------------------------------------- input schemas (fail-closed)

/**
 * usage entries: explicit token/cost accounting for the ledger. JSON array of
 * {role: maker|judge, provider, model?, tokens_in?, tokens_out?, tokens_total?, cost_usd?}.
 * Unknown keys and wrong types are refused — a malformed usage line must never reach
 * the cost ledger half-parsed.
 */
export function parseUsageInput(raw) {
  const errors = [];
  let arr;
  try { arr = JSON.parse(raw); }
  catch (e) { return { entries: null, errors: [`not valid JSON: ${e.message}`] }; }
  if (!Array.isArray(arr) || !arr.length) return { entries: null, errors: ['usage must be a non-empty JSON array'] };
  const KEYS = ['role', 'provider', 'model', 'tokens_in', 'tokens_out', 'tokens_total', 'cost_usd'];
  const entries = arr.map((e, i) => {
    if (e === null || typeof e !== 'object' || Array.isArray(e)) { errors.push(`[${i}]: not a map`); return null; }
    for (const k of Object.keys(e)) if (!KEYS.includes(k)) errors.push(`[${i}]: unknown key '${k}'`);
    if (!USAGE_ROLES.includes(e.role)) errors.push(`[${i}].role: must be one of [${USAGE_ROLES.join(' | ')}]`);
    if (typeof e.provider !== 'string' || !e.provider.trim()) errors.push(`[${i}].provider: non-empty string required`);
    if (e.model != null && (typeof e.model !== 'string' || !e.model.trim())) errors.push(`[${i}].model: string|null required`);
    for (const k of ['tokens_in', 'tokens_out', 'tokens_total']) {
      if (e[k] != null && !Number.isInteger(e[k])) errors.push(`[${i}].${k}: int|null required`);
    }
    if (e.cost_usd != null && !(typeof e.cost_usd === 'number' && Number.isFinite(e.cost_usd))) errors.push(`[${i}].cost_usd: number|null required`);
    return { role: e.role, provider: e.provider, model: e.model ?? null, tokens_in: e.tokens_in ?? null, tokens_out: e.tokens_out ?? null, tokens_total: e.tokens_total ?? null, cost_usd: e.cost_usd ?? null };
  });
  return errors.length ? { entries: null, errors } : { entries, errors: [] };
}

/** null-aware aggregation with the same semantics as work.mjs tokensOf(). */
export function aggregateUsage(entries) {
  let inTok = null, outTok = null, total = null, usd = null;
  const add = (cur, v) => (v == null ? cur : (cur ?? 0) + v);
  for (const e of entries) {
    inTok = add(inTok, e.tokens_in);
    outTok = add(outTok, e.tokens_out);
    total = add(total, e.tokens_total ?? (e.tokens_in != null && e.tokens_out != null ? e.tokens_in + e.tokens_out : null));
    usd = add(usd, e.cost_usd);
  }
  return { inTok, outTok, total, usd };
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
