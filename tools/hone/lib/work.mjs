// work.mjs — `hone work <id>`: execute ONE candidate packet through the full gate.
//
// pipeline (fail-CLOSED at every arrow — any ambiguity/timeout/parse-failure ends in
// refuse/blocked/revert, never a silent land):
//
//   load + gate            refuse: not-autonomous gate, non-pending status, maker==judge,
//                          unknown provider, packet-pinned provider mismatch
//   preflight              refuse: default branch, dirty tree (quality/ engine state exempt);
//                          then mark in_progress and run evidence_required = GREEN BASELINE
//                          (red baseline → status blocked; never work on a red baseline)
//   maker                  subprocess agent (claude -p / codex exec), edit-enabled, cwd=repo,
//                          prompt = packet YAML + binding brief; afterwards changed-files ⊆
//                          touchset or EVERYTHING reverts (skipped: touchset-violation)
//   deterministic oracle   re-run every rung; expect checked (exit 0 + recognized patterns);
//                          one maker revision cycle on failure, then revert
//   independent judge      providers/ layer, judge ≠ maker (structural); PASS lands,
//                          REVISE gets one maker revision + one re-judge, REJECT reverts
//   land / terminalize     commit (author Tim Nunamaker <tnunamak@gmail.com>), then EVERY
//                          terminal path (landed/reverted/skipped/blocked) rewrites the
//                          packet outcome and appends claims.jsonl + cost.jsonl — negative
//                          results are the product
//
// Gate REFUSALS exit 2 with NO side effects (nothing was attempted, nothing to record);
// terminal non-landed paths exit 1; landed and --dry-run exit 0.
//
// `hone work --self-test` runs the offline matrix: real git fixture repos + real shell
// evidence commands, mock maker/judge (zero LLM calls). Green self-test is the gate for
// any real run (SPEC acceptance test #1 lives here: work refuses maker==judge).
//
// Deterministic-oracle honesty note: `expect` strings are prose for humans + judge; the
// engine deterministically enforces (a) exit code 0 on every rung, (b) `prints TOKEN`,
// (c) scope-fn `found=true` / `cognitive_before < N` / `red_scan unchanged` (parsed from
// the collector's JSON), (d) the optional machine-checkable `expect_check` spec
// ({type: exit_code|stdout_includes|stdout_regex|scope_fn_lt|file_excess_lt, value} —
// enforced deterministically, fail-closed), and (e) NO NEW SKIPS on test rungs: skip
// counts parsed from test output at baseline and post; post > baseline is oracle RED
// (the skip-mask trap — a change that newly skips tests must never pass on exit 0).
// Skip-count parsing is defensive telemetry: unparseable output is noted, never fatal;
// enforcement applies only when BOTH phases parsed. Unrecognized expect clauses are NOT
// silently trusted: the full receipt goes to the independent judge, which is instructed
// that insufficient evidence alone justifies REVISE/REJECT. Baseline enforces only
// (a)+(b) + the identity expect_check types — improvement clauses like
// `cognitive_before < N` / `file_excess_lt` are post-change goals by construction.

import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { parseYaml, stringifyYaml } from './yaml.mjs';
import { validatePacket, assertValidPacket } from './validate-packet.mjs';
import { deepEqual, djb2, slug } from './util.mjs';
import { loadPacket, writePacket } from './packet-io.mjs';
import { executeReset } from './reset.mjs';
import { appendClaim, appendCostEntry, nextClaimSeq, nextJobAttempt, readJsonl, claimsPath, costPath } from './ledger.mjs';
import { runCli } from '../providers/provider.mjs';

const PROVIDERS = ['claude', 'codex'];
const TERMINAL = ['landed', 'reverted', 'skipped', 'blocked'];
const AUTHOR_NAME = 'Tim Nunamaker';
const AUTHOR_EMAIL = 'tnunamak@gmail.com';
const MAKER_TIMEOUT_MS = Number(process.env.HONE_MAKER_TIMEOUT_MS ?? 20 * 60 * 1000);
const EVIDENCE_TIMEOUT_MS = Number(process.env.HONE_EVIDENCE_TIMEOUT_MS ?? 45 * 60 * 1000);
const MAX_BUFFER = 64 * 1024 * 1024;

// ---------------------------------------------------------------- entry point

export async function runWork(flags) {
  if (flags['self-test']) {
    process.exitCode = await selfTest({ verbose: !!flags.verbose });
    return;
  }
  const id = flags._?.[0];
  if (!id || typeof id !== 'string') {
    throw new Error("usage: hone work <candidate-id> --repo PATH [--maker claude|codex] [--judge codex|claude] [--dry-run]\n(put flags AFTER the id; bare flags greedily consume a following bare word)");
  }
  const res = await executeWork({
    id,
    repoRoot: resolve(flags.repo || '.'),
    makerName: String(flags.maker || 'claude'),
    judgeName: String(flags.judge || 'codex'),
    dryRun: !!flags['dry-run'],
  }, realDeps());
  process.stdout.write(res.summary + '\n');
  process.exitCode = res.exitCode;
}

function realDeps() {
  return {
    maker: async (name, prompt, opts) => (name === 'claude' ? claudeMaker : codexMaker)(prompt, opts),
    judge: async (name) => (await import(`../providers/${name}.mjs`)).default,
    log: (s) => process.stderr.write(s + '\n'),
  };
}

// ---------------------------------------------------------------- maker adapters
// The providers/ layer is judge-only by design (read-only sandbox, fresh empty cwd).
// A maker is the OPPOSITE contract — edit-enabled, cwd = the target repo — so its two
// adapters live here. Both reuse providers/runCli (process-group kill on timeout).

async function claudeMaker(prompt, { cwd, timeoutMs = MAKER_TIMEOUT_MS } = {}) {
  const model = process.env.HONE_CLAUDE_MODEL || 'sonnet';
  // claude 2.1.198 print mode has no --permission-mode; --allowedTools grants edit
  // permission non-interactively. Bash is deliberately NOT allowed: evidence commands
  // are the engine's job, and a maker that can't run git can't commit or stage.
  const args = ['-p', '--model', model, '--output-format', 'json', '--no-session-persistence',
    '--allowedTools', 'Read,Glob,Grep,Edit,Write'];
  const { stdout, durationMs } = await runCli('claude', args, { input: prompt, timeoutMs, cwd });
  let envelope;
  try { envelope = JSON.parse(stdout); }
  catch { throw Object.assign(new Error(`claude maker emitted non-JSON envelope: ${stdout.slice(0, 300)}`), { kind: 'bad-envelope' }); }
  if (envelope.is_error) {
    throw Object.assign(new Error(`claude maker returned is_error: ${String(envelope.result).slice(0, 300)}`), { kind: 'provider-error' });
  }
  return {
    text: envelope.result ?? '',
    meta: {
      provider: 'claude', model, durationMs,
      costUsd: envelope.total_cost_usd ?? null,
      tokens: envelope.usage
        ? { input: envelope.usage.input_tokens ?? null, output: envelope.usage.output_tokens ?? null }
        : null,
    },
  };
}

async function codexMaker(prompt, { cwd, timeoutMs = MAKER_TIMEOUT_MS } = {}) {
  const model = process.env.HONE_CODEX_MODEL || 'gpt-5.5';
  const dir = mkdtempSync(join(tmpdir(), 'hone-maker-'));
  const outFile = join(dir, 'last-message.txt');
  const args = ['exec', '--ephemeral', '--skip-git-repo-check', '-s', 'workspace-write',
    '--color', 'never', '-m', model, '-o', outFile, '-'];
  const { stdout, stderr, durationMs } = await runCli('codex', args, { input: prompt, timeoutMs, cwd });
  let text = '';
  try { text = readFileSync(outFile, 'utf8'); }
  catch {
    throw Object.assign(new Error(`codex maker produced no last-message file; stderr: ${stderr.slice(0, 300)}`), { kind: 'no-output' });
  }
  const m = /tokens used[^\d]*([\d,]+)/i.exec(stdout + '\n' + stderr);
  return {
    text,
    meta: { provider: 'codex', model, durationMs, costUsd: null, tokens: { total: m ? Number(m[1].replaceAll(',', '')) : null } },
  };
}

// ---------------------------------------------------------------- git plumbing

function gitContext(repoRoot) {
  const boot = spawnSync('git', ['rev-parse', '--show-toplevel'], { cwd: repoRoot, encoding: 'utf8' });
  if (boot.status !== 0) throw new Error(`--repo is not inside a git repository: ${repoRoot}`);
  const gitRoot = boot.stdout.trim();
  const git = (args, opts = {}) => {
    const r = spawnSync('git', args, { cwd: gitRoot, encoding: 'utf8', maxBuffer: MAX_BUFFER });
    if (r.status !== 0 && !opts.allowFail) {
      throw new Error(`git ${args.join(' ')} failed (exit ${r.status}): ${(r.stderr || r.stdout || '').slice(0, 500)}`);
    }
    return (r.stdout || '').replace(/\n$/, '');
  };
  const prefix = relative(gitRoot, repoRoot); // '' when repoRoot IS the git toplevel
  const topRel = (p) => (prefix ? `${prefix}/${p}` : p);
  const branch = git(['rev-parse', '--abbrev-ref', 'HEAD']);
  return { gitRoot, prefix, branch, git, topRel };
}

const unquotePath = (p) => (p.startsWith('"') ? JSON.parse(p) : p);

/** porcelain-v1 entries scoped to the --repo subtree; paths are toplevel-relative. */
function statusEntries(g) {
  const out = g.git(['status', '--porcelain=v1', '-uall', '--', g.prefix || '.']);
  return out.split('\n').filter(Boolean).map((line) => ({
    x: line[0], y: line[1],
    paths: line.slice(3).split(' -> ').map(unquotePath),
  }));
}

/** dirty entries EXCLUDING quality/ engine state (packets, ledgers, receipts are ours). */
function dirtyEntries(g) {
  const q = g.topRel('quality');
  return statusEntries(g).filter((e) => !e.paths.every((p) => p === q || p.startsWith(q + '/')));
}

const flatPaths = (entries) => [...new Set(entries.flatMap((e) => e.paths))];

/** restore the worktree to HEAD for everything dirty outside quality/. Throws if it can't. */
function revertAll(g) {
  let entries = dirtyEntries(g);
  if (!entries.length) return;
  g.git(['reset', '-q', '--', ...flatPaths(entries)], { allowFail: true }); // unstage anything the maker staged
  entries = dirtyEntries(g);
  const tracked = flatPaths(entries.filter((e) => !(e.x === '?' && e.y === '?')));
  if (tracked.length) g.git(['checkout', '--', ...tracked]);
  for (const e of dirtyEntries(g)) {
    if (e.x === '?') for (const p of e.paths) rmSync(join(g.gitRoot, p), { force: true });
  }
  const remain = dirtyEntries(g);
  if (remain.length) {
    throw new Error(`REVERT FAILED — tree still dirty after restore: ${flatPaths(remain).join(', ')} (manual cleanup required; refusing to continue)`);
  }
}

// ---------------------------------------------------------------- evidence + oracle

function runShellCmd(cmd, cwd, timeoutMs = EVIDENCE_TIMEOUT_MS) {
  const startedAt = Date.now();
  const r = spawnSync('/bin/bash', ['-c', cmd], {
    cwd, encoding: 'utf8', timeout: timeoutMs, maxBuffer: MAX_BUFFER, killSignal: 'SIGKILL',
  });
  const timedOut = r.error?.code === 'ETIMEDOUT';
  return {
    code: timedOut ? null : (r.status ?? null),
    timedOut,
    stdout: r.stdout || '',
    output: (r.stdout || '') + (r.stderr || ''),
    durationMs: Date.now() - startedAt,
  };
}

function lastJson(stdout) {
  try { return JSON.parse(stdout.trim()); } catch { /* fall through */ }
  const a = stdout.indexOf('{'), b = stdout.lastIndexOf('}');
  if (a === -1 || b <= a) return null;
  try { return JSON.parse(stdout.slice(a, b + 1)); } catch { return null; }
}

/**
 * skip count from test-runner output, best-effort across formats: node --test TAP
 * (`# skipped 3`) / spec reporter (`ℹ skipped 3`), vitest (`2 skipped`), jest
 * (`Tests: 1 skipped, …`), mocha (`3 pending`). Returns null when unparseable —
 * callers treat null as "telemetry unavailable", never as a verdict.
 */
export function parseSkipCount(output) {
  const s = String(output);
  let m = s.match(/^\s*[#ℹ]\s*skip(?:ped)?[:\s]+(\d+)\b/mi);
  if (m) return Number(m[1]);
  m = s.match(/(\d+)\s+skip(?:ped|s)?\b/i);
  if (m) return Number(m[1]);
  m = s.match(/(\d+)\s+pending\b/i);
  if (m) return Number(m[1]);
  return null;
}

const EXPECT_CHECK_TYPES = ['exit_code', 'stdout_includes', 'stdout_regex', 'scope_fn_lt', 'file_excess_lt'];

/** multiset subset: every name in `post` appears at least as often in `base`. */
function isSubMultiset(post, base) {
  const tally = new Map();
  for (const n of base) tally.set(n, (tally.get(n) || 0) + 1);
  for (const n of post) {
    const left = (tally.get(n) || 0) - 1;
    if (left < 0) return false;
    tally.set(n, left);
  }
  return true;
}

/**
 * the machine-checkable half of a rung: `expect_check` {type, value} enforced
 * deterministically (fail-closed — the whole point is that the maker cannot talk past it).
 * Identity types (exit_code / stdout_includes / stdout_regex) apply at BOTH phases;
 * improvement types (scope_fn_lt / file_excess_lt) are post-change goals only.
 */
function checkExpectCheck(ec, res, phase, baselineRes) {
  const fail = (reason) => ({ pass: false, reason: `expect_check[${ec.type}]: ${reason}` });
  switch (ec.type) {
    case 'exit_code':
      return res.code === ec.value ? null : fail(`exit ${res.code}, expected ${ec.value}`);
    case 'stdout_includes':
      return res.stdout.includes(String(ec.value)) ? null : fail(`stdout does not contain '${ec.value}'`);
    case 'stdout_regex': {
      let re;
      try { re = new RegExp(String(ec.value)); }
      catch (e) { return fail(`invalid regex '${ec.value}' (${e.message}) — fail-closed`); }
      return re.test(res.stdout) ? null : fail(`stdout does not match /${ec.value}/`);
    }
    case 'scope_fn_lt': {
      if (phase !== 'post') return null; // improvement goal, not a baseline precondition
      const j = lastJson(res.stdout);
      if (!j) return fail('output has no parseable JSON');
      if (j.found !== true) return fail(`scope-fn found=${JSON.stringify(j.found)} — target function missing post-change (renamed or deleted?)`);
      if (!(typeof j.cognitive_before === 'number' && j.cognitive_before < Number(ec.value))) {
        return fail(`cognitive_before=${j.cognitive_before}, expected < ${ec.value} — no measured complexity reduction`);
      }
      return null;
    }
    case 'file_excess_lt': {
      if (phase !== 'post') return null; // improvement goal, not a baseline precondition
      const j = lastJson(res.stdout);
      if (!j) return fail('output has no parseable JSON');
      if (j.found !== true) return fail(`file collector found=${JSON.stringify(j.found)} — touchset file missing post-change?`);
      if (!(typeof j.file_excess === 'number' && j.file_excess < Number(ec.value))) {
        return fail(`file_excess=${j.file_excess}, expected < ${ec.value} (packet baseline) — whole-file Σ excess-cc did not decrease`);
      }
      const b = baselineRes ? lastJson(baselineRes.stdout) : null;
      if (!b || typeof b.file_excess !== 'number') return fail('baseline collector JSON unavailable — cannot prove strict decrease (fail-closed)');
      if (!(j.file_excess < b.file_excess)) {
        return fail(`file_excess ${b.file_excess} → ${j.file_excess} — no strict decrease vs measured baseline`);
      }
      const names = (r) => (Array.isArray(r.flagged) ? r.flagged.map((f) => f.fn ?? '<anon>') : []);
      if (!isSubMultiset(names(j), names(b))) {
        return fail(`NEW function above the cog threshold post-change (baseline flagged=[${names(b).join(',')}] post=[${names(j).join(',')}]) — complexity moved, not reduced`);
      }
      return null;
    }
    default:
      return fail(`unknown expect_check type — fail-closed (known: ${EXPECT_CHECK_TYPES.join(', ')})`);
  }
}

/**
 * deterministic expect check. `phase`='baseline' enforces runnability (exit 0 + prints +
 * identity expect_check types); 'post' additionally enforces the recognized
 * improvement/identity clauses and the no-new-skips rule on test rungs.
 */
function checkExpect(rung, res, phase, baselineRes = null) {
  if (res.timedOut) return { pass: false, reason: `TIMEOUT after ${Math.round(res.durationMs / 1000)}s (fail-closed)` };
  const ec = (rung.expect_check && typeof rung.expect_check === 'object') ? rung.expect_check : null;
  const expectedExit = ec?.type === 'exit_code' && Number.isInteger(ec.value) ? ec.value : 0;
  if (res.code !== expectedExit) return { pass: false, reason: `exit ${res.code} (expected ${expectedExit})` };
  const expect = String(rung.expect);
  const notes = [];

  const prints = expect.match(/prints\s+([A-Z][A-Z0-9_-]{2,})/);
  if (prints && !res.output.includes(prints[1])) {
    return { pass: false, reason: `output does not contain '${prints[1]}'` };
  }

  if (ec) {
    const bad = checkExpectCheck(ec, res, phase, baselineRes);
    if (bad) return bad;
  }

  const cc = expect.match(/cognitive_before\s*<\s*(\d+)/);
  if (cc && phase === 'post') {
    const j = lastJson(res.stdout);
    if (!j) return { pass: false, reason: 'expect references cognitive_before but output has no parseable JSON' };
    if (j.found !== true) return { pass: false, reason: `scope-fn found=${JSON.stringify(j.found)} — target function missing post-change (renamed or deleted?)` };
    if (!(typeof j.cognitive_before === 'number' && j.cognitive_before < Number(cc[1]))) {
      return { pass: false, reason: `cognitive_before=${j.cognitive_before}, expected < ${cc[1]} — no measured complexity reduction` };
    }
  }
  if ((cc || ec?.type === 'scope_fn_lt' || ec?.type === 'file_excess_lt') && phase === 'post' && /red_scan unchanged/.test(expect)) {
    const j = lastJson(res.stdout);
    const b = baselineRes ? lastJson(baselineRes.stdout) : null;
    if (!j || !b) return { pass: false, reason: 'red_scan-unchanged required but collector JSON unavailable' };
    if (!deepEqual(j.red_scan, b.red_scan)) {
      return { pass: false, reason: `red_scan changed: ${JSON.stringify(b.red_scan)} → ${JSON.stringify(j.red_scan)}` };
    }
  }

  // no-new-skips (the skip-mask trap): a post-edit run that newly SKIPS tests must not
  // pass on exit 0 alone. Strict when parsed at both phases; telemetry note otherwise.
  if (phase === 'post' && baselineRes && /test/i.test(String(rung.rung))) {
    const before = parseSkipCount(baselineRes.output);
    const after = parseSkipCount(res.output);
    if (before != null && after != null && after > before) {
      return { pass: false, reason: `new skips: baseline skipped=${before}, post skipped=${after} — skipping tests is not passing them` };
    }
    notes.push(before != null && after != null ? `skips ${before}→${after} OK` : 'skip-count unparsed (telemetry only, not enforced)');
  }

  return { pass: true, reason: `exit ${expectedExit}; deterministic expect clauses satisfied${notes.length ? ` (${notes.join('; ')})` : ''}` };
}

function tailClip(s, n) {
  const t = String(s);
  return t.length <= n ? t : `…[${t.length - n} bytes clipped]…\n` + t.slice(-n);
}

/** for prose (judge reasoning in lessons/claims): keep the head, clip the tail. */
function headClip(s, n) {
  const t = String(s);
  return t.length <= n ? t : t.slice(0, n) + '…';
}

// ---------------------------------------------------------------- prompts

function makerBrief(rawPacketYaml, packet) {
  return [
    'You are the MAKER in a repo-quality engine, executing exactly ONE work packet in this repository (your current working directory). The packet below is the entire contract.',
    'Binding rules:',
    `- Modify ONLY these files (the touchset): ${packet.touchset.join(', ')}. Creating, editing, deleting, or renaming ANY other file voids the whole run — the engine reverts everything.`,
    `- Obey every not_allowed item: ${packet.not_allowed.join(', ')}.`,
    '- Do exactly what plan.instruction says. No scope creep, no drive-by fixes, no comment or formatting churn outside the named functions.',
    '- Do NOT commit, stage, branch, or run any git write operation. Do NOT add dependencies. Do NOT touch anything under quality/.',
    "- Do NOT run the test suite; the engine runs the packet's evidence_required commands itself after you finish.",
    '- When done, reply with a short summary: which functions changed, what transform you applied, and why behavior is preserved.',
    '== WORK PACKET (YAML) ==',
    rawPacketYaml,
  ].join('\n');
}

function revisionBrief(base, failureNote, diffText) {
  return [
    base,
    '== REVISION REQUIRED ==',
    'A previous attempt at this packet produced the working-tree changes shown in the diff below, but the attempt FAILED verification. The changes are still applied in your working directory. Fix the failure while keeping every rule above (touchset, not_allowed). Do not weaken or edit tests or evidence commands.',
    '-- failure --',
    failureNote,
    '-- current working-tree diff (already applied) --',
    tailClip(diffText, 60000),
  ].join('\n\n');
}

// ---------------------------------------------------------------- the executor

export async function executeWork(opts, deps) {
  const { id, repoRoot, makerName, judgeName, dryRun } = opts;
  const startedAt = Date.now();
  const log = deps.log;
  const refuse = (reason) => ({
    outcome: 'refused', exitCode: 2,
    summary: `hone work — ${id}: REFUSED (no side effects)\n  ${reason}`,
  });

  // ---- 1. load + gate (fail-closed; refusals have NO side effects) ----
  if (!PROVIDERS.includes(makerName)) return refuse(`unknown maker provider '${makerName}' (known: ${PROVIDERS.join(', ')})`);
  if (!PROVIDERS.includes(judgeName)) return refuse(`unknown judge provider '${judgeName}' (known: ${PROVIDERS.join(', ')})`);
  if (makerName === judgeName) return refuse(`maker == judge ('${makerName}') — non-negotiable #1: the producer of a change cannot certify it`);

  let loaded;
  try { loaded = loadPacket(repoRoot, id); }
  catch (e) { return refuse(e.message); }
  const { packet, path: packetPath, rawText } = loaded;

  const schemaErrs = validatePacket(packet);
  if (schemaErrs.length) return refuse(`malformed packet (schema v1.1):\n  - ${schemaErrs.join('\n  - ')}`);
  if (packet.execution_gate !== 'autonomous') {
    return refuse(`execution_gate is '${packet.execution_gate}' — work executes ONLY autonomous packets (owner_ratify goes to the owner, fail-closed)`);
  }
  if (packet.status !== 'pending') {
    const kind = TERMINAL.includes(packet.status) ? 'terminal' : 'stale in_progress lock';
    return refuse(`packet status is '${packet.status}' (${kind}) — never re-litigate a persisted outcome; reset status to pending only by owner decision`);
  }
  if (packet.maker_provider !== null && packet.maker_provider !== makerName) {
    return refuse(`packet pins maker_provider='${packet.maker_provider}' but --maker=${makerName}`);
  }
  if (packet.judge_provider !== null && packet.judge_provider !== judgeName) {
    return refuse(`packet pins judge_provider='${packet.judge_provider}' but --judge=${judgeName}`);
  }

  // ---- 2. preflight: git state (still read-only) ----
  const g = gitContext(repoRoot);
  if (g.branch === 'main' || g.branch === 'master') {
    return refuse(`target repo is on '${g.branch}' — work lands commits and never works on the default branch`);
  }
  const dirty = dirtyEntries(g);
  if (dirty.length) {
    const touchTop = packet.touchset.map(g.topRel);
    const inTouch = flatPaths(dirty).filter((p) => touchTop.includes(p));
    return refuse(`target tree is dirty (${flatPaths(dirty).length} path(s)): ${flatPaths(dirty).slice(0, 10).join(', ')}` +
      (inTouch.length ? `\n  DIRTY TOUCHSET FILES: ${inTouch.join(', ')} — refusing: baseline would be unattributable` : ''));
  }

  const touchTop = packet.touchset.map(g.topRel);

  if (dryRun) {
    return {
      outcome: 'dry-run', exitCode: 0,
      summary: [
        `hone work — DRY RUN — ${id} (no side effects)`,
        `  packet: ${packetPath} (status pending, gate autonomous)`,
        `  action=${packet.action} proof_class=${packet.proof_class} behavior_status=${packet.behavior_status}`,
        `  maker=${makerName}(${packet.maker_tier}) judge=${judgeName}(${packet.judge_tier})  [maker ≠ judge OK]`,
        `  repo: ${repoRoot} (branch ${g.branch}, clean)`,
        `  touchset: ${packet.touchset.join(', ')}`,
        `  not_allowed: ${packet.not_allowed.join(', ')}`,
        `  evidence rungs (baseline, then post-change oracle):`,
        ...packet.evidence_required.map((r, i) => `    ${i + 1}. [${r.rung}] ${r.command}\n       expect: ${r.expect}${r.expect_check ? `\n       expect_check (machine-enforced): ${r.expect_check.type} ${JSON.stringify(r.expect_check.value)}` : ''}`),
        `  plan.instruction: ${packet.plan.instruction}`,
        `  would: mark in_progress → GREEN BASELINE → maker → touchset gate → oracle (≤1 revision) → judge (≤1 revise) → land/revert`,
      ].join('\n'),
    };
  }

  // ---- everything below has side effects; terminal paths write packet + ledgers ----
  const receiptsDirRel = join('quality', 'receipts', id);
  const receiptLines = [];       // single-line receipt strings for packet.outcome.evidence_receipts
  const makerMetas = [];
  const judgeMetas = [];
  let revisionCount = 0;
  let judgeResult = null;

  const writeReceipt = (phase, i, rung, res, verdict) => {
    const rel = join(receiptsDirRel, `${phase}-${i + 1}-${slug(rung.rung)}.txt`);
    const abs = join(repoRoot, rel);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, `# hone work ${id} — ${phase} rung '${rung.rung}'\n# command: ${rung.command}\n# expect: ${rung.expect}\n# exit: ${res.timedOut ? 'TIMEOUT' : res.code}  duration: ${Math.round(res.durationMs / 1000)}s  verdict: ${verdict.pass ? 'PASS' : `FAIL (${verdict.reason})`}\n\n${res.output}`);
    const digest = `exit=${res.timedOut ? 'TIMEOUT' : res.code} djb2=${djb2(res.output)} bytes=${res.output.length} receipt=${rel}`;
    receiptLines.push(`[${phase}] ${rung.rung}: ${rung.command} -> ${res.timedOut ? 'TIMEOUT' : `exit ${res.code}`} (${Math.round(res.durationMs / 1000)}s) ${verdict.pass ? 'PASS' : `FAIL: ${verdict.reason}`}; ${digest}`);
    return digest;
  };

  const tokensOf = () => {
    let inTok = null, outTok = null, total = null, usd = null;
    const add = (cur, v) => (v == null ? cur : (cur ?? 0) + v);
    for (const m of [...makerMetas, ...judgeMetas]) {
      if (!m) continue;
      inTok = add(inTok, m.tokens?.input ?? null);
      outTok = add(outTok, m.tokens?.output ?? null);
      total = add(total, m.tokens?.total ?? ((m.tokens?.input ?? null) != null && (m.tokens?.output ?? null) != null ? m.tokens.input + m.tokens.output : null));
      usd = add(usd, m.costUsd ?? null);
    }
    return { inTok, outTok, total, usd };
  };

  /** the single terminal writer: packet outcome + claims + cost, every path, no exceptions. */
  const terminalize = ({ status, commit = null, skipReason = null, blockedOn = null, judgeVerdict = null, lesson = null, claims, headline }) => {
    const { inTok, outTok, total, usd } = tokensOf();
    packet.status = status;
    packet.maker_provider = makerMetas.length ? makerName : null;
    packet.judge_provider = judgeMetas.length ? judgeName : null;
    packet.outcome = {
      commit, skip_reason: skipReason, blocked_on: blockedOn, judge_verdict: judgeVerdict,
      evidence_receipts: [...receiptLines], tokens_actual: total, lesson,
    };
    writePacket(packetPath, packet);

    let seq = nextClaimSeq(repoRoot, id);
    for (const c of claims) {
      appendClaim(repoRoot, {
        claim_id: `clm-${id}-${seq++}`,
        created: new Date().toISOString(),
        candidate_id: id,
        type: c.type,
        statement: c.statement,
        evidence: c.evidence ?? [],
        judge: c.judge ?? null,
      });
    }
    const attempt = nextJobAttempt(repoRoot, id);
    appendCostEntry(repoRoot, {
      job_id: `job-${id}-${attempt}`,
      created: new Date().toISOString(),
      candidate_id: id,
      workflow: packet.action,
      maker: { provider: makerName, tier: packet.maker_tier },
      judge: { provider: judgeName, tier: packet.judge_tier },
      tokens_in: inTok, tokens_out: outTok,
      cost_usd: usd == null ? null : Math.round(usd * 10000) / 10000,
      wall_time_s: Math.round((Date.now() - startedAt) / 100) / 10,
      landed: status === 'landed',
      revision_count: revisionCount,
      judge_result: judgeResult,
      outcome: status,
      followup_created: [],
    });
    const exitCode = status === 'landed' ? 0 : 1;
    return {
      outcome: status, exitCode, commit,
      summary: [
        `hone work — ${id}: ${status.toUpperCase()}`,
        `  ${headline}`,
        `  maker=${makerName} judge=${judgeName} revisions=${revisionCount} judge_result=${judgeResult ?? 'n/a'} wall=${Math.round((Date.now() - startedAt) / 100) / 10}s`,
        `  packet: ${packetPath}`,
        `  claims: +${claims.length} → ${claimsPath(repoRoot)}`,
        `  cost:   job-${id}-${attempt} → ${costPath(repoRoot)}`,
      ].join('\n'),
    };
  };

  // ---- 3. mark in_progress, then GREEN BASELINE ----
  packet.status = 'in_progress';
  writePacket(packetPath, packet);
  log(`hone work — ${id}: baseline (${packet.evidence_required.length} rung(s))`);

  const baselineRes = [];
  try {
    for (const [i, rung] of packet.evidence_required.entries()) {
      log(`  [baseline] ${rung.rung}: ${rung.command}`);
      const res = runShellCmd(rung.command, repoRoot);
      const verdict = checkExpect(rung, res, 'baseline');
      const digest = writeReceipt('baseline', i, rung, res, verdict);
      baselineRes.push(res);
      if (!verdict.pass) {
        return terminalize({
          status: 'blocked',
          blockedOn: `red baseline: rung '${rung.rung}' failed BEFORE any change (${verdict.reason}) — never work on a red baseline`,
          lesson: `baseline rung '${rung.rung}' is red at repo_sha ${packet.repo_sha.slice(0, 12)}; the oracle must be green before this packet is workable`,
          claims: [
            { type: 'verified_fact', statement: `baseline evidence rung '${rung.rung}' fails before any change: ${verdict.reason}`, evidence: [{ command: rung.command, output_digest: digest }] },
            { type: 'remaining_work', statement: `fix the red baseline (rung '${rung.rung}'), reset packet status to pending, and re-run hone work ${id}` },
          ],
          headline: `red baseline at rung '${rung.rung}': ${verdict.reason}`,
        });
      }
    }

    // ---- 4. maker ----
    const brief = makerBrief(rawText, packet);
    log(`  maker: ${makerName} (timeout ${Math.round(MAKER_TIMEOUT_MS / 60000)}m)`);
    let makerRun;
    try {
      makerRun = await deps.maker(makerName, brief, { cwd: repoRoot, timeoutMs: MAKER_TIMEOUT_MS });
      makerMetas.push(makerRun.meta);
    } catch (e) {
      revertAll(g); // fail-closed: whatever half-state the maker left, remove it
      return terminalize({
        status: 'skipped',
        skipReason: `maker-error: ${makerName} subprocess failed (${e.kind ?? 'error'}: ${e.message.slice(0, 200)})`,
        lesson: `maker subprocess failure is a skip, not a revert-of-nothing: ${e.kind ?? 'error'}`,
        claims: [
          { type: 'uncertainty', statement: `maker (${makerName}) subprocess failed before producing a reviewable diff: ${e.kind ?? 'error'}` },
          { type: 'remaining_work', statement: `re-run hone work ${id} after resolving the maker failure (status must be reset to pending by owner)` },
        ],
        headline: `maker subprocess failed: ${e.message.slice(0, 120)}`,
      });
    }

    // ---- 5. touchset enforcement (structural, after EVERY maker call) ----
    const enforceTouchset = () => {
      const changed = flatPaths(dirtyEntries(g));
      const violations = changed.filter((p) => !touchTop.includes(p));
      return { changed, violations };
    };
    let { changed, violations } = enforceTouchset();
    if (!changed.length) {
      return terminalize({
        status: 'skipped',
        skipReason: 'maker-no-diff: maker completed but modified nothing',
        lesson: `maker (${makerName}) replied without editing; packet instruction may be unactionable as written`,
        claims: [
          { type: 'verified_fact', statement: `maker (${makerName}) produced no working-tree change for ${id}`, evidence: [{ command: `git status --porcelain=v1 -uall -- ${g.prefix || '.'}`, output_digest: '(empty — no changes outside quality/)' }] },
          { type: 'remaining_work', statement: `packet ${id} unexecuted; review plan.instruction actionability, reset to pending to retry` },
        ],
        headline: 'maker made no changes',
      });
    }
    if (violations.length) {
      revertAll(g);
      return terminalize({
        status: 'skipped',
        skipReason: `touchset-violation: maker modified ${violations.join(', ')} outside touchset [${packet.touchset.join(', ')}]; ALL changes reverted`,
        lesson: `maker (${makerName}) violated the touchset; brief forbids it explicitly — treat as provider reliability signal`,
        claims: [
          { type: 'verified_fact', statement: `maker (${makerName}) modified files outside the packet touchset: ${violations.join(', ')}; everything reverted, nothing landed`, evidence: [{ command: `git status --porcelain=v1 -uall -- ${g.prefix || '.'}`, output_digest: `changed=[${changed.join(', ')}] touchset=[${touchTop.join(', ')}]` }] },
          { type: 'remaining_work', statement: `packet ${id} unexecuted after touchset violation; reset to pending to retry` },
        ],
        headline: `touchset violation: ${violations.join(', ')} — reverted`,
      });
    }

    // ---- 6. deterministic oracle (≤1 maker revision cycle) ----
    const runOracle = (phase) => {
      for (const [i, rung] of packet.evidence_required.entries()) {
        log(`  [${phase}] ${rung.rung}: ${rung.command}`);
        const res = runShellCmd(rung.command, repoRoot);
        const verdict = checkExpect(rung, res, 'post', baselineRes[i]);
        const digest = writeReceipt(phase, i, rung, res, verdict);
        if (!verdict.pass) return { green: false, rung, verdict, res, digest };
      }
      return { green: true };
    };

    const reverted = (failNote, claims, headline, lesson) => {
      revertAll(g);
      return terminalize({ status: 'reverted', lesson, claims, headline, judgeVerdict: failNote.judgeVerdict ?? null });
    };

    let oracle = runOracle('post');
    if (!oracle.green) {
      revisionCount++;
      log(`  oracle RED at '${oracle.rung.rung}' — one maker revision cycle`);
      const failureNote = `deterministic oracle rung '${oracle.rung.rung}' FAILED: ${oracle.verdict.reason}\ncommand: ${oracle.rung.command}\nexpect: ${oracle.rung.expect}\noutput tail:\n${tailClip(oracle.res.output, 4000)}`;
      try {
        const rev = await deps.maker(makerName, revisionBrief(brief, failureNote, g.git(['diff', '--', ...touchTop])), { cwd: repoRoot, timeoutMs: MAKER_TIMEOUT_MS });
        makerMetas.push(rev.meta);
      } catch (e) {
        return reverted({}, [
          { type: 'verified_fact', statement: `post-change oracle rung '${oracle.rung.rung}' failed (${oracle.verdict.reason}) and the revision maker call errored; changes reverted`, evidence: [{ command: oracle.rung.command, output_digest: oracle.digest }] },
          { type: 'remaining_work', statement: `packet ${id} reverted; oracle failure unresolved: ${oracle.verdict.reason}` },
        ], `oracle red + maker revision error — reverted`, `revision maker call failed: ${e.kind ?? 'error'}`);
      }
      ({ changed, violations } = enforceTouchset());
      if (violations.length) {
        revertAll(g);
        return terminalize({
          status: 'skipped',
          skipReason: `touchset-violation (revision cycle): ${violations.join(', ')}; ALL changes reverted`,
          lesson: `maker (${makerName}) violated the touchset during revision`,
          claims: [
            { type: 'verified_fact', statement: `revision maker call modified files outside touchset: ${violations.join(', ')}; everything reverted`, evidence: [{ command: `git status --porcelain=v1 -uall -- ${g.prefix || '.'}`, output_digest: `changed=[${changed.join(', ')}]` }] },
            { type: 'remaining_work', statement: `packet ${id} unexecuted after revision touchset violation` },
          ],
          headline: `touchset violation in revision — reverted`,
        });
      }
      oracle = runOracle('post-r1');
      if (!oracle.green) {
        return reverted({}, [
          { type: 'verified_fact', statement: `evidence rung '${oracle.rung.rung}' still failing after 1 maker revision (${oracle.verdict.reason}); all changes reverted, nothing landed`, evidence: [{ command: oracle.rung.command, output_digest: oracle.digest }] },
          { type: 'remaining_work', statement: `packet ${id} reverted with a red oracle at '${oracle.rung.rung}'; needs a different approach or a better instruction` },
        ], `oracle red after revision: '${oracle.rung.rung}' ${oracle.verdict.reason}`, `transform failed its own evidence ladder at '${oracle.rung.rung}' — prior for ${packet.batch_key} down`);
      }
    }

    // ---- 7. independent judge (maker ≠ judge; ≤1 REVISE cycle) ----
    const buildDiff = () => {
      let diff = g.git(['diff', '--', ...touchTop]);
      for (const e of dirtyEntries(g)) {
        if (e.x !== '?') continue;
        for (const p of e.paths) {
          if (!touchTop.includes(p)) continue;
          const r = spawnSync('git', ['diff', '--no-index', '--', '/dev/null', p], { cwd: g.gitRoot, encoding: 'utf8', maxBuffer: MAX_BUFFER });
          diff += '\n' + (r.stdout || ''); // exit 1 is expected for --no-index with differences
        }
      }
      return diff;
    };
    const evidenceText = () => receiptLines.map((l) => l).join('\n');
    const judgeProvider = await deps.judge(judgeName);
    const judgeOnce = async () => {
      log(`  judge: ${judgeName}`);
      const v = await judgeProvider.judge({ diff: tailClip(buildDiff(), 150000), evidence: evidenceText(), packet: rawText });
      for (const a of v.raw?.attempts ?? []) if (a.meta) judgeMetas.push(a.meta);
      return v;
    };

    let verdict = await judgeOnce();
    judgeResult = verdict.verdict;
    const verdictLine = (v) => `${judgeName} ${v.verdict}${v.confidence != null ? ` (confidence ${v.confidence})` : ''}: ${v.reasoning}`;

    if (verdict.verdict === 'REVISE') {
      revisionCount++;
      log(`  judge REVISE — one maker revision + one re-judge`);
      try {
        const rev = await deps.maker(makerName, revisionBrief(brief, `independent judge (${judgeName}) verdict REVISE: ${verdict.reasoning}`, buildDiff()), { cwd: repoRoot, timeoutMs: MAKER_TIMEOUT_MS });
        makerMetas.push(rev.meta);
      } catch (e) {
        return reverted({ judgeVerdict: verdictLine(verdict) }, [
          { type: 'judged_design_claim', statement: `judge required revision and the revision maker call errored: ${verdict.reasoning}`, judge: { provider: judgeName, verdict: 'REVISE' } },
          { type: 'remaining_work', statement: `packet ${id} reverted mid-revision (maker error); judge concerns unaddressed` },
        ], 'judge REVISE + maker revision error — reverted', `revision maker call failed: ${e.kind ?? 'error'}`);
      }
      ({ changed, violations } = enforceTouchset());
      if (violations.length) {
        revertAll(g);
        return terminalize({
          status: 'skipped',
          skipReason: `touchset-violation (judge-revision cycle): ${violations.join(', ')}; ALL changes reverted`,
          lesson: `maker (${makerName}) violated the touchset while addressing judge feedback`,
          claims: [
            { type: 'verified_fact', statement: `judge-revision maker call modified files outside touchset: ${violations.join(', ')}; everything reverted`, evidence: [{ command: `git status --porcelain=v1 -uall -- ${g.prefix || '.'}`, output_digest: `changed=[${changed.join(', ')}]` }] },
            { type: 'remaining_work', statement: `packet ${id} unexecuted after judge-revision touchset violation` },
          ],
          headline: 'touchset violation in judge-revision — reverted',
        });
      }
      oracle = runOracle('post-r2');
      if (!oracle.green) {
        return reverted({ judgeVerdict: verdictLine(verdict) }, [
          { type: 'verified_fact', statement: `judge-requested revision broke evidence rung '${oracle.rung.rung}' (${oracle.verdict.reason}); all changes reverted`, evidence: [{ command: oracle.rung.command, output_digest: oracle.digest }] },
          { type: 'remaining_work', statement: `packet ${id} reverted; judge concern (${headClip(verdict.reasoning, 240)}) still open` },
        ], `revision broke the oracle at '${oracle.rung.rung}' — reverted`, 'judge-driven revision regressed the oracle; REVISE cycles need the oracle re-gate (it held)');
      }
      const second = await judgeOnce();
      judgeResult = second.verdict;
      if (second.verdict !== 'PASS') {
        return reverted({ judgeVerdict: `${verdictLine(verdict)} || after revision: ${verdictLine(second)}` }, [
          { type: 'judged_design_claim', statement: `independent judge refused the change after one revision cycle: ${second.reasoning}`, judge: { provider: judgeName, verdict: second.verdict } },
          { type: 'remaining_work', statement: `packet ${id} reverted on judge ${second.verdict}; address: ${headClip(second.reasoning, 240)}` },
        ], `judge ${second.verdict} after revision — reverted (never land without PASS)`, `judge refused twice (REVISE→${second.verdict}); packet bar not reachable by this maker`);
      }
      verdict = second;
    } else if (verdict.verdict === 'REJECT') {
      return reverted({ judgeVerdict: verdictLine(verdict) }, [
        { type: 'judged_design_claim', statement: `independent judge REJECTED the change: ${verdict.reasoning}`, judge: { provider: judgeName, verdict: 'REJECT' } },
        { type: 'remaining_work', statement: `packet ${id} reverted on judge REJECT; address: ${headClip(verdict.reasoning, 240)}` },
      ], 'judge REJECT — reverted (never land without PASS)', `judge rejected: ${headClip(verdict.reasoning, 240)}`);
    }

    // ---- 8. land ----
    g.git(['add', '--', ...touchTop]);
    const staged = g.git(['diff', '--cached', '--name-only']).split('\n').filter(Boolean);
    const rogue = staged.filter((p) => !touchTop.includes(p));
    if (rogue.length) throw new Error(`staged paths outside touchset at commit time: ${rogue.join(', ')} — refusing to commit`);
    const commitType = packet.action === 'preserve_refactor' || packet.action === 'idealize_rewrite' ? 'refactor' : 'chore';
    const msg = `${commitType}(${packet.subsystem}): ${packet.plan.transform_class} [hone ${id}]\n\nhone work: maker=${makerName} judge=${judgeName} verdict=PASS${verdict.confidence != null ? ` (confidence ${verdict.confidence})` : ''}, revisions=${revisionCount}.\nEvidence: ${packet.evidence_required.length} rung(s) green at baseline and post-change (receipts: ${receiptsDirRel}/).`;
    g.git(['-c', `user.name=${AUTHOR_NAME}`, '-c', `user.email=${AUTHOR_EMAIL}`, 'commit', '-q',
      `--author=${AUTHOR_NAME} <${AUTHOR_EMAIL}>`, '-m', msg]);
    const commit = g.git(['rev-parse', 'HEAD']);
    const leftover = dirtyEntries(g);
    if (leftover.length) throw new Error(`tree not clean after landing commit: ${flatPaths(leftover).join(', ')}`);

    const claims = [
      {
        type: 'behavior_preserved',
        statement: `all ${packet.evidence_required.length} evidence_required rung(s) for ${id} green at baseline and post-change (${packet.evidence_required.map((r) => r.rung).join(', ')})`,
        evidence: packet.evidence_required.map((r) => ({ command: r.command, output_digest: receiptLines.filter((l) => l.includes(`] ${r.rung}:`)).pop() ?? `see ${receiptsDirRel}/` })),
      },
      {
        type: 'judged_design_claim',
        statement: `independent judge PASS: ${verdict.reasoning}`,
        judge: { provider: judgeName, verdict: 'PASS' },
      },
    ];
    const ccRung = packet.evidence_required.find((r) =>
      /cognitive_before\s*</.test(String(r.expect)) || ['scope_fn_lt', 'file_excess_lt'].includes(r.expect_check?.type));
    if (ccRung) {
      claims.push({
        type: 'verified_fact',
        statement: `measured cognitive-complexity reduction for ${id}: '${ccRung.expect}' satisfied post-change`,
        evidence: [{ command: ccRung.command, output_digest: receiptLines.filter((l) => l.includes(`] ${ccRung.rung}:`)).pop() ?? `see ${receiptsDirRel}/` }],
      });
    }
    return terminalize({
      status: 'landed', commit,
      judgeVerdict: verdictLine(verdict),
      lesson: revisionCount ? `landed after ${revisionCount} revision cycle(s) — first attempt did not clear the gate` : null,
      claims,
      headline: `landed ${commit.slice(0, 12)} on ${g.branch}`,
    });
  } catch (e) {
    // fail-CLOSED on any unexpected error after side effects began: revert what we can,
    // record blocked(internal-error) — never leave a half-applied change or a silent packet.
    try { revertAll(g); } catch (e2) { e.message += ` [AND REVERT FAILED: ${e2.message} — manual cleanup required]`; }
    return terminalize({
      status: 'blocked',
      blockedOn: `internal-error: ${e.message.slice(0, 300)}`,
      lesson: 'engine fault, not a packet fact — fix the engine, reset status to pending',
      claims: [
        { type: 'uncertainty', statement: `hone work aborted on internal error before a terminal gate decision: ${e.message.slice(0, 200)}` },
        { type: 'remaining_work', statement: `packet ${id} blocked on engine error; changes (if any) reverted; reset to pending after fixing` },
      ],
      headline: `internal error (fail-closed): ${e.message.slice(0, 120)}`,
    });
  }
}

// ============================================================================
// --self-test: the offline matrix. Real git repos + real (trivial) shell
// evidence commands; mock maker/judge. Zero LLM calls, zero network.
// ============================================================================

const ST_ORIG = `function clamp(n, lo, hi) {
  if (n < lo) {
    return lo;
  } else {
    if (n > hi) {
      return hi;
    } else {
      return n;
    }
  }
}
module.exports = { clamp };
`;
const ST_GOOD = `function clamp(n, lo, hi) {
  if (n < lo) return lo;
  if (n > hi) return hi;
  return n;
}
module.exports = { clamp };
`;
const ST_BAD = ST_GOOD.replace('if (n > hi) return hi;', 'if (n > hi) return lo;'); // boundary-adjacent behavior break
const ST_TEST = `const { clamp } = require('./src/util.js');
const ok = clamp(5, 0, 10) === 5 && clamp(-1, 0, 10) === 0 && clamp(11, 0, 10) === 10;
if (!ok) { console.error('FAIL clamp'); process.exit(1); }
console.log('PASS 3/3');
`;

function stBasePacket(id, overrides = {}) {
  return {
    candidate_id: id,
    created: new Date().toISOString(),
    repo_sha: 'selftest0000',
    subsystem: 'src',
    files: ['src/util.js'],
    symbols: ['clamp'],
    public_surface: [],
    behavior_status: 'likely_intended',
    ownership: 'OWN',
    action: 'preserve_refactor',
    proof_class: 'certified_transform',
    execution_gate: 'autonomous',
    why_this_matters: 'self-test fixture: nested-else clamp flattening',
    plan: { transform_class: 'certified-local-tidy', instruction: 'Flatten the nested else branches of clamp in src/util.js into guard clauses. Behavior identical.' },
    expected_quality_gain: 'low',
    owner_attention_reduction: 'low',
    product_impact: 'none',
    risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: null },
    authoring_evidence: [],
    evidence_required: [{ rung: 'direct-test', command: 'node test.js', expect: 'exit 0' }],
    not_allowed: ['behavior-change', 'new-dependency'],
    maker_tier: 'cheap',
    judge_tier: 'standard',
    maker_provider: null,
    judge_provider: null,
    batch_key: 'preserve_refactor×certified_transform×src',
    touchset: ['src/util.js'],
    estimates: { tokens: 1000, evidence_cost: 'low' },
    depends_on: [],
    unlocks: [],
    status: 'pending',
    outcome: { commit: null, skip_reason: null, blocked_on: null, judge_verdict: null, evidence_receipts: [], tokens_actual: null, lesson: null },
    ...overrides,
  };
}

function stRepo(id, { branch = 'quality-sweep', packetOverrides = {} } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'hone-selftest-'));
  const run = (args) => {
    const r = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
    if (r.status !== 0) throw new Error(`selftest git ${args.join(' ')}: ${r.stderr}`);
  };
  run(['init', '-q']);
  run(['symbolic-ref', 'HEAD', `refs/heads/${branch}`]);
  run(['config', 'user.email', 'selftest@example.com']);
  run(['config', 'user.name', 'Self Test']);
  mkdirSync(join(root, 'src'), { recursive: true });
  writeFileSync(join(root, 'src/util.js'), ST_ORIG);
  writeFileSync(join(root, 'test.js'), ST_TEST);
  writeFileSync(join(root, 'README.md'), '# selftest fixture\n');
  run(['add', '-A']);
  run(['commit', '-q', '-m', 'init fixture']);
  const packet = stBasePacket(id, packetOverrides);
  assertValidPacket(packet, `selftest fixture ${id}`);
  mkdirSync(join(root, 'quality/packets'), { recursive: true });
  writeFileSync(join(root, 'quality/packets', `${id}.yaml`), stringifyYaml(packet));
  return root;
}

function stMockDeps(script, log) {
  let m = 0;
  let j = 0;
  const state = { makerCalls: 0, judgeCalls: 0 };
  return {
    state,
    deps: {
      maker: async (name, _prompt, { cwd }) => {
        state.makerCalls++;
        const fn = script.makers?.[m++];
        if (!fn) throw Object.assign(new Error('mock maker: no scripted call left'), { kind: 'mock-exhausted' });
        if (fn === 'ERROR') throw Object.assign(new Error('scripted maker failure'), { kind: 'timeout' });
        fn(cwd);
        return { text: 'mock maker done', meta: { provider: name, model: 'mock', durationMs: 1, costUsd: 0.01, tokens: { input: 100, output: 50 } } };
      },
      judge: async (name) => ({
        name,
        judge: async () => {
          state.judgeCalls++;
          const v = script.judges?.[j++];
          if (!v) return { verdict: 'REVISE', reasoning: 'mock judge: no scripted verdict left', confidence: 0, raw: { provider: name, attempts: [] } };
          return { ...v, raw: { provider: name, attempts: [{ meta: { provider: name, tokens: { total: 500 } } }] } };
        },
      }),
      log: log ?? (() => {}),
    },
  };
}

const stEditUtil = (content) => (cwd) => writeFileSync(join(cwd, 'src/util.js'), content);
const stEditUtilAndReadme = (cwd) => {
  writeFileSync(join(cwd, 'src/util.js'), ST_GOOD);
  writeFileSync(join(cwd, 'README.md'), '# selftest fixture\ntouched by maker\n');
};

async function selfTest({ verbose = false } = {}) {
  const results = [];
  const w = (s) => process.stdout.write(s + '\n');
  const log = verbose ? (s) => process.stderr.write('    ' + s + '\n') : () => {};
  const ID = 'selftest-util-t0-00000001';
  const read = (root, p) => (existsSync(join(root, p)) ? readFileSync(join(root, p), 'utf8') : null);
  const packetOnDisk = (root) => parseYaml(read(root, `quality/packets/${ID}.yaml`));
  const claims = (root) => readJsonl(claimsPath(root));
  const costs = (root) => readJsonl(costPath(root));
  const treeClean = (root) => {
    const r = spawnSync('git', ['status', '--porcelain=v1', '-uall'], { cwd: root, encoding: 'utf8' });
    return r.stdout.split('\n').filter((l) => l && !l.slice(3).startsWith('quality/')).length === 0;
  };
  const headSubject = (root) => spawnSync('git', ['log', '-1', '--format=%s|%ae|%H'], { cwd: root, encoding: 'utf8' }).stdout.trim();

  async function scenario(name, fn) {
    const checks = [];
    const check = (label, cond, detail = '') => checks.push({ label, ok: !!cond, detail });
    try { await fn(check); }
    catch (e) { checks.push({ label: 'no-unexpected-throw', ok: false, detail: e.message }); }
    results.push({ name, checks });
  }

  const exec = (root, deps, extra = {}) =>
    executeWork({ id: ID, repoRoot: root, makerName: 'claude', judgeName: 'codex', dryRun: false, ...extra }, deps);

  // ---- gate refusals (no side effects) ----
  await scenario('refuse: owner_ratify gate', async (check) => {
    const root = stRepo(ID, { packetOverrides: { execution_gate: 'owner_ratify' } });
    const before = read(root, `quality/packets/${ID}.yaml`);
    const r = await exec(root, stMockDeps({}, log).deps);
    check('refused', r.outcome === 'refused', r.summary);
    check('exit 2', r.exitCode === 2);
    check('packet unchanged', read(root, `quality/packets/${ID}.yaml`) === before);
    check('no ledgers', !existsSync(claimsPath(root)) && !existsSync(costPath(root)));
  });

  await scenario('refuse: maker == judge (structural #1)', async (check) => {
    const root = stRepo(ID);
    const r = await exec(root, stMockDeps({}, log).deps, { judgeName: 'claude' });
    check('refused', r.outcome === 'refused' && /maker == judge/.test(r.summary), r.summary);
    check('exit 2', r.exitCode === 2);
    check('no ledgers', !existsSync(claimsPath(root)));
  });

  await scenario('refuse: terminal status (landed)', async (check) => {
    const root = stRepo(ID, { packetOverrides: { status: 'landed' } });
    const r = await exec(root, stMockDeps({}, log).deps);
    check('refused', r.outcome === 'refused' && /terminal/.test(r.summary), r.summary);
    check('no ledgers', !existsSync(claimsPath(root)));
  });

  await scenario('refuse: dirty touchset file', async (check) => {
    const root = stRepo(ID);
    writeFileSync(join(root, 'src/util.js'), ST_ORIG + '// pre-existing local edit\n');
    const r = await exec(root, stMockDeps({}, log).deps);
    check('refused', r.outcome === 'refused' && /DIRTY TOUCHSET/.test(r.summary), r.summary);
    check('no ledgers', !existsSync(claimsPath(root)));
  });

  await scenario('refuse: dirty tree outside touchset', async (check) => {
    const root = stRepo(ID);
    writeFileSync(join(root, 'stray.txt'), 'not committed\n');
    const r = await exec(root, stMockDeps({}, log).deps);
    check('refused', r.outcome === 'refused' && /dirty/.test(r.summary), r.summary);
  });

  await scenario('refuse: default branch (main)', async (check) => {
    const root = stRepo(ID, { branch: 'main' });
    const r = await exec(root, stMockDeps({}, log).deps);
    check('refused', r.outcome === 'refused' && /'main'/.test(r.summary), r.summary);
  });

  // ---- dry run ----
  await scenario('dry-run: plan printed, zero side effects', async (check) => {
    const root = stRepo(ID);
    const before = read(root, `quality/packets/${ID}.yaml`);
    const { deps, state } = stMockDeps({}, log);
    const r = await exec(root, deps, { dryRun: true });
    check('exit 0', r.exitCode === 0 && r.outcome === 'dry-run', r.summary);
    check('plan shows rungs + gate order', /direct-test/.test(r.summary) && /GREEN BASELINE/.test(r.summary));
    check('packet byte-unchanged', read(root, `quality/packets/${ID}.yaml`) === before);
    check('no ledgers, no maker/judge calls', !existsSync(claimsPath(root)) && state.makerCalls === 0 && state.judgeCalls === 0);
    check('tree clean', treeClean(root));
  });

  // ---- terminal paths (each MUST write packet outcome + claims + cost) ----
  await scenario('red baseline → blocked', async (check) => {
    const root = stRepo(ID, {
      packetOverrides: { evidence_required: [{ rung: 'direct-test', command: 'node -e "process.exit(3)"', expect: 'exit 0' }] },
    });
    const { deps, state } = stMockDeps({}, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('exit nonzero, blocked', r.exitCode === 1 && r.outcome === 'blocked', r.summary);
    check('packet status blocked + blocked_on', p.status === 'blocked' && /red baseline/.test(p.outcome.blocked_on ?? ''));
    check('receipts recorded', p.outcome.evidence_receipts.length === 1 && /FAIL/.test(p.outcome.evidence_receipts[0]));
    check('claims written (verified_fact + remaining_work)', claims(root).map((c) => c.type).join(',') === 'verified_fact,remaining_work', JSON.stringify(claims(root)));
    check('cost written outcome=blocked', costs(root).length === 1 && costs(root)[0].outcome === 'blocked' && costs(root)[0].landed === false);
    check('maker never ran on red baseline', state.makerCalls === 0);
    check('tree clean', treeClean(root));
  });

  await scenario('touchset violation → revert + skipped', async (check) => {
    const root = stRepo(ID);
    const { deps, state } = stMockDeps({ makers: [stEditUtilAndReadme] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('skipped', r.outcome === 'skipped' && r.exitCode === 1, r.summary);
    check('skip_reason names violation + file', /touchset-violation/.test(p.outcome.skip_reason ?? '') && /README\.md/.test(p.outcome.skip_reason ?? ''));
    check('util.js reverted byte-identical', read(root, 'src/util.js') === ST_ORIG);
    check('README reverted byte-identical', read(root, 'README.md') === '# selftest fixture\n');
    check('tree clean after revert', treeClean(root));
    check('claims + cost written', claims(root).some((c) => c.type === 'verified_fact') && costs(root)[0]?.outcome === 'skipped');
    check('judge never called', state.judgeCalls === 0);
  });

  await scenario('maker no-diff → skipped', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({ makers: [() => {}] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('skipped(maker-no-diff)', r.outcome === 'skipped' && /maker-no-diff/.test(p.outcome.skip_reason ?? ''), r.summary);
    check('ledgers written', claims(root).length >= 1 && costs(root).length === 1);
  });

  await scenario('maker subprocess error → revert + skipped', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({ makers: ['ERROR'] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('skipped(maker-error)', r.outcome === 'skipped' && /maker-error/.test(p.outcome.skip_reason ?? ''), r.summary);
    check('uncertainty claim (no fabricated evidence)', claims(root).some((c) => c.type === 'uncertainty'));
    check('cost written', costs(root).length === 1 && costs(root)[0].outcome === 'skipped');
  });

  await scenario('oracle red twice → reverted, judge never called', async (check) => {
    const root = stRepo(ID);
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_BAD), stEditUtil(ST_BAD)] }, log);
    const r = await exec(root, deps);
    check('reverted', r.outcome === 'reverted' && r.exitCode === 1, r.summary);
    check('util.js restored', read(root, 'src/util.js') === ST_ORIG);
    check('revision_count=1 in cost', costs(root)[0]?.revision_count === 1 && costs(root)[0]?.judge_result === null);
    check('judge never called (fail before judgment)', state.judgeCalls === 0);
    check('verified_fact evidence present', claims(root).find((c) => c.type === 'verified_fact')?.evidence.length >= 1);
    check('tree clean', treeClean(root));
  });

  await scenario('oracle red → one revision fixes → judge PASS → landed', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_BAD), stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean guard-clause flattening', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('landed', r.outcome === 'landed' && r.exitCode === 0, r.summary);
    check('revision_count=1, judge PASS', costs(root)[0]?.revision_count === 1 && costs(root)[0]?.judge_result === 'PASS');
    check('commit recorded in packet', typeof p.outcome.commit === 'string' && p.outcome.commit.length === 40);
    check('lesson notes the revision', /revision/.test(p.outcome.lesson ?? ''));
  });

  await scenario('judge REJECT → reverted', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'REJECT', reasoning: 'relocation dressed as refactoring', confidence: 0.95 }] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('reverted', r.outcome === 'reverted' && r.exitCode === 1, r.summary);
    check('util.js restored byte-identical', read(root, 'src/util.js') === ST_ORIG);
    check('judge_verdict recorded verbatim-gist', /REJECT/.test(p.outcome.judge_verdict ?? '') && /relocation/.test(p.outcome.judge_verdict ?? ''));
    check('judged_design_claim with judge field', deepEqual(claims(root).find((c) => c.type === 'judged_design_claim')?.judge, { provider: 'codex', verdict: 'REJECT' }));
    check('cost judge_result=REJECT, landed=false', costs(root)[0]?.judge_result === 'REJECT' && costs(root)[0]?.landed === false);
    check('no commit created', headSubject(root).startsWith('init fixture'));
  });

  await scenario('judge REVISE → revision → re-judge PASS → landed', async (check) => {
    const root = stRepo(ID);
    const { deps, state } = stMockDeps({
      makers: [stEditUtil(ST_GOOD), stEditUtil(ST_GOOD)],
      judges: [{ verdict: 'REVISE', reasoning: 'name the guard intent', confidence: 0.6 }, { verdict: 'PASS', reasoning: 'revised acceptably', confidence: 0.85 }],
    }, log);
    const r = await exec(root, deps);
    check('landed after REVISE cycle', r.outcome === 'landed', r.summary);
    check('two judge calls, two maker calls', state.judgeCalls === 2 && state.makerCalls === 2);
    check('revision_count=1, final judge PASS', costs(root)[0]?.revision_count === 1 && costs(root)[0]?.judge_result === 'PASS');
  });

  await scenario('judge REVISE twice → reverted (never land without PASS)', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({
      makers: [stEditUtil(ST_GOOD), stEditUtil(ST_GOOD)],
      judges: [{ verdict: 'REVISE', reasoning: 'insufficient', confidence: 0.5 }, { verdict: 'REVISE', reasoning: 'still insufficient', confidence: 0.5 }],
    }, log);
    const r = await exec(root, deps);
    check('reverted', r.outcome === 'reverted', r.summary);
    check('util.js restored', read(root, 'src/util.js') === ST_ORIG);
    check('cost judge_result=REVISE', costs(root)[0]?.judge_result === 'REVISE');
  });

  await scenario('judge PASS → landed (commit, author, ledgers)', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'behavior preserved, real simplification', confidence: 0.92 }] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    const head = headSubject(root); // subject|author-email|sha
    check('landed exit 0', r.outcome === 'landed' && r.exitCode === 0, r.summary);
    check('commit references candidate id', head.includes(`[hone ${ID}]`), head);
    check('author email tnunamak@gmail.com', head.split('|')[1] === AUTHOR_EMAIL, head);
    check('packet landed + commit sha matches HEAD', p.status === 'landed' && p.outcome.commit === head.split('|')[2]);
    check('providers recorded', p.maker_provider === 'claude' && p.judge_provider === 'codex');
    check('receipts in outcome', p.outcome.evidence_receipts.length === 2); // baseline + post
    check('behavior_preserved + judged_design_claim', ['behavior_preserved', 'judged_design_claim'].every((t) => claims(root).some((c) => c.type === t)), JSON.stringify(claims(root).map((c) => c.type)));
    check('behavior_preserved has evidence receipts', claims(root).find((c) => c.type === 'behavior_preserved')?.evidence.every((e) => /djb2=/.test(e.output_digest)));
    check('cost landed=true revision_count=0', costs(root)[0]?.landed === true && costs(root)[0]?.revision_count === 0);
    check('cost tokens summed (maker 150 + judge 500)', costs(root)[0]?.tokens_in === 100 && costs(root)[0]?.tokens_out === 50);
    check('tree clean after land', treeClean(root));
  });

  // ---- deterministic expect-pattern micro-checks (no repo needed) ----
  await scenario('checkExpect: recognized patterns', async (check) => {
    const mk = (code, stdout, output = stdout) => ({ code, timedOut: false, stdout, output, durationMs: 10 });
    check('exit-0 pass', checkExpect({ expect: 'exit 0' }, mk(0, ''), 'post').pass);
    check('nonzero fail', !checkExpect({ expect: 'exit 0' }, mk(2, ''), 'post').pass);
    check('timeout fail-closed', !checkExpect({ expect: 'exit 0' }, { code: null, timedOut: true, stdout: '', output: '', durationMs: 1 }, 'post').pass);
    check('prints TOKEN pass', checkExpect({ expect: 'prints GUARDS-UNTOUCHED — no marker touched' }, mk(0, 'GUARDS-UNTOUCHED\n'), 'post').pass);
    check('prints TOKEN fail', !checkExpect({ expect: 'prints GUARDS-UNTOUCHED — no marker touched' }, mk(0, 'diff hunk with marker\n'), 'post').pass);
    const ccExpect = { expect: 'found=true and cognitive_before < 9 (packet baseline); red_scan unchanged' };
    const base = mk(0, JSON.stringify({ found: true, cognitive_before: 9, red_scan: [] }));
    check('cc improved pass', checkExpect(ccExpect, mk(0, JSON.stringify({ found: true, cognitive_before: 4, red_scan: [] })), 'post', base).pass);
    check('cc unimproved fail', !checkExpect(ccExpect, mk(0, JSON.stringify({ found: true, cognitive_before: 9, red_scan: [] })), 'post', base).pass);
    check('fn missing fail', !checkExpect(ccExpect, mk(0, JSON.stringify({ found: false, cognitive_before: null, red_scan: [] })), 'post', base).pass);
    check('red_scan changed fail', !checkExpect(ccExpect, mk(0, JSON.stringify({ found: true, cognitive_before: 4, red_scan: ['bearer'] })), 'post', base).pass);
    check('cc clause NOT enforced at baseline (goal, not precondition)', checkExpect(ccExpect, base, 'baseline').pass);
    check('unparseable JSON fail-closed', !checkExpect(ccExpect, mk(0, 'not json'), 'post', base).pass);
  });

  // ---- no-new-skips: parser + deterministic enforcement (the skip-mask trap) ----
  await scenario('no-new-skips: parseSkipCount formats + checkExpect enforcement', async (check) => {
    const mk = (code, stdout, output = stdout) => ({ code, timedOut: false, stdout, output, durationMs: 10 });
    check('node --test TAP', parseSkipCount('# tests 5\n# pass 3\n# skipped 2\n') === 2);
    check('node --test spec reporter', parseSkipCount('ℹ pass 40\nℹ skipped 3\n') === 3);
    check('vitest', parseSkipCount('      Tests  2 skipped | 38 passed (40)') === 2);
    check('jest', parseSkipCount('Tests:       1 skipped, 39 passed, 40 total') === 1);
    check('mocha pending', parseSkipCount('  39 passing\n  3 pending\n') === 3);
    check('unparseable → null (telemetry, never a verdict)', parseSkipCount('all good, no summary line') === null);
    const rung = { rung: 'direct-test', expect: 'all tests pass; 0 fail; no new skips' };
    const b1 = mk(0, '# tests 5\n# skipped 1\n');
    check('post > baseline → RED', !checkExpect(rung, mk(0, '# tests 5\n# skipped 2\n'), 'post', b1).pass);
    check('RED reason names the trap', /new skips/.test(checkExpect(rung, mk(0, '# skipped 2\n'), 'post', b1).reason));
    check('post == baseline → pass', checkExpect(rung, mk(0, '# tests 5\n# skipped 1\n'), 'post', b1).pass);
    check('post < baseline → pass', checkExpect(rung, mk(0, '# tests 5\n# skipped 0\n'), 'post', b1).pass);
    const unparsed = checkExpect(rung, mk(0, 'no summary at all'), 'post', b1);
    check('unparseable post → warn-and-continue, not a failure', unparsed.pass && /unparsed/.test(unparsed.reason));
    check('non-test rung NOT skip-enforced', checkExpect({ rung: 'collector', expect: 'exit 0' }, mk(0, '# skipped 9\n'), 'post', mk(0, '# skipped 0\n')).pass);
  });

  await scenario('no-new-skips: post-edit skip regression → oracle red → reverted', async (check) => {
    // the evidence command emulates a test runner whose skip count depends on the source:
    // baseline (ST_ORIG) has no SKIP_ME marker → skipped 0; the maker's edit adds one → skipped 1.
    const skipCmd = `node -e "const fs=require('fs');const n=/SKIP_ME/.test(fs.readFileSync('src/util.js','utf8'))?1:0;console.log('# tests 3');console.log('# pass '+(3-n));console.log('# fail 0');console.log('# skipped '+n);"`;
    const root = stRepo(ID, {
      packetOverrides: { evidence_required: [{ rung: 'direct-test', command: skipCmd, expect: 'all tests pass; 0 fail; no new skips' }] },
    });
    const skipMasked = ST_GOOD + '// SKIP_ME: flaky, disabled\n';
    const { deps, state } = stMockDeps({ makers: [stEditUtil(skipMasked), stEditUtil(skipMasked)] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('reverted (exit 0 alone did not launder the skip)', r.outcome === 'reverted' && r.exitCode === 1, r.summary);
    check('util.js restored byte-identical', read(root, 'src/util.js') === ST_ORIG);
    check('receipt names new skips', p.outcome.evidence_receipts.some((l) => /new skips: baseline skipped=0, post skipped=1/.test(l)), JSON.stringify(p.outcome.evidence_receipts));
    check('judge never called', state.judgeCalls === 0);
    check('tree clean', treeClean(root));
  });

  // ---- expect_check: the machine-checkable half of a rung ----
  await scenario('expect_check: deterministic enforcement per type', async (check) => {
    const mk = (code, stdout, output = stdout) => ({ code, timedOut: false, stdout, output, durationMs: 10 });
    const rg = (ec) => ({ rung: 'x', expect: 'machine-checked', expect_check: ec });
    check('exit_code 0 pass', checkExpect(rg({ type: 'exit_code', value: 0 }), mk(0, ''), 'post').pass);
    check('exit_code 0 fail on 2', !checkExpect(rg({ type: 'exit_code', value: 0 }), mk(2, ''), 'post').pass);
    check('exit_code 1 expected: 1 passes (overrides the default exit-0 gate)', checkExpect(rg({ type: 'exit_code', value: 1 }), mk(1, ''), 'post').pass);
    check('exit_code 1 expected: 0 fails', !checkExpect(rg({ type: 'exit_code', value: 1 }), mk(0, ''), 'post').pass);
    check('stdout_includes pass', checkExpect(rg({ type: 'stdout_includes', value: 'module.exports' }), mk(0, 'x\nmodule.exports = {};\n'), 'post').pass);
    check('stdout_includes fail', !checkExpect(rg({ type: 'stdout_includes', value: 'module.exports' }), mk(0, 'nothing here'), 'post').pass);
    check('stdout_includes enforced at baseline too (identity, not goal)', !checkExpect(rg({ type: 'stdout_includes', value: 'TOKEN' }), mk(0, 'nope'), 'baseline').pass);
    check('stdout_regex pass', checkExpect(rg({ type: 'stdout_regex', value: '\\b3 passed\\b' }), mk(0, 'ok — 3 passed'), 'post').pass);
    check('stdout_regex fail', !checkExpect(rg({ type: 'stdout_regex', value: '^PASS$' }), mk(0, 'FAIL'), 'post').pass);
    check('stdout_regex invalid pattern fail-closed', !checkExpect(rg({ type: 'stdout_regex', value: '(' }), mk(0, 'anything'), 'post').pass);
    const sf = (cc, found = true) => mk(0, JSON.stringify({ found, cognitive_before: cc, red_scan: [] }));
    check('scope_fn_lt improved pass', checkExpect(rg({ type: 'scope_fn_lt', value: 9 }), sf(4), 'post', sf(9)).pass);
    check('scope_fn_lt unimproved fail', !checkExpect(rg({ type: 'scope_fn_lt', value: 9 }), sf(9), 'post', sf(9)).pass);
    check('scope_fn_lt fn missing fail', !checkExpect(rg({ type: 'scope_fn_lt', value: 9 }), sf(null, false), 'post', sf(9)).pass);
    check('scope_fn_lt NOT enforced at baseline (goal)', checkExpect(rg({ type: 'scope_fn_lt', value: 9 }), sf(9), 'baseline').pass);
    const ff = (excess, flagged) => mk(0, JSON.stringify({ found: true, file_excess: excess, flagged, red_scan: [] }));
    const fBase = ff(11, [{ fn: 'a', cc: 9 }, { fn: 'b', cc: 7 }]);
    const fe = rg({ type: 'file_excess_lt', value: 11 });
    check('file_excess_lt decreased pass', checkExpect(fe, ff(8, [{ fn: 'a', cc: 9 }]), 'post', fBase).pass);
    check('file_excess_lt unimproved fail', !checkExpect(fe, ff(11, [{ fn: 'a', cc: 9 }, { fn: 'b', cc: 7 }]), 'post', fBase).pass);
    check('file_excess_lt below packet value but not below measured baseline fail', !checkExpect(fe, ff(10, [{ fn: 'a', cc: 9 }]), 'post', ff(9, [{ fn: 'a', cc: 9 }])).pass);
    check('file_excess_lt NEW flagged fn fail (relocation self-defeating)', !checkExpect(fe, ff(8, [{ fn: 'a', cc: 9 }, { fn: 'helperC', cc: 7 }]), 'post', fBase).pass);
    check('file_excess_lt duplicate-name multiset fail', !checkExpect(fe, ff(8, [{ fn: 'a', cc: 7 }, { fn: 'a', cc: 6 }]), 'post', fBase).pass);
    check('file_excess_lt baseline JSON missing fail-closed', !checkExpect(fe, ff(8, [{ fn: 'a', cc: 9 }]), 'post', mk(0, 'not json')).pass);
    check('file_excess_lt NOT enforced at baseline (goal)', checkExpect(fe, fBase, 'baseline').pass);
  });

  await scenario('expect_check: violated end-to-end → oracle red → reverted', async (check) => {
    const root = stRepo(ID, {
      packetOverrides: {
        evidence_required: [{
          rung: 'export-marker', command: 'cat src/util.js',
          expect: 'file still exports clamp (machine-checked via expect_check)',
          expect_check: { type: 'stdout_includes', value: 'module.exports' },
        }],
      },
    });
    const noExport = ST_GOOD.replace('module.exports = { clamp };\n', '');
    const { deps, state } = stMockDeps({ makers: [stEditUtil(noExport), stEditUtil(noExport)] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('reverted', r.outcome === 'reverted' && r.exitCode === 1, r.summary);
    check('receipt names expect_check failure', p.outcome.evidence_receipts.some((l) => /expect_check\[stdout_includes\]/.test(l)), JSON.stringify(p.outcome.evidence_receipts));
    check('util.js restored', read(root, 'src/util.js') === ST_ORIG);
    check('judge never called', state.judgeCalls === 0);
  });

  await scenario('schema: expect_check + resets validation (additive, strict when present)', async (check) => {
    const withEv = (ec) => stBasePacket(ID, { evidence_required: [{ rung: 'x', command: 'true', expect: 'exit 0', ...(ec !== undefined ? { expect_check: ec } : {}) }] });
    check('rung without expect_check still valid', validatePacket(withEv(undefined)).length === 0);
    check('valid expect_check accepted', validatePacket(withEv({ type: 'stdout_includes', value: 'OK' })).length === 0);
    check('unknown type rejected', validatePacket(withEv({ type: 'bogus', value: 1 })).some((e) => /expect_check\.type/.test(e)));
    check('exit_code non-int value rejected', validatePacket(withEv({ type: 'exit_code', value: '0' })).some((e) => /int required/.test(e)));
    check('file_excess_lt non-int value rejected', validatePacket(withEv({ type: 'file_excess_lt', value: 'low' })).some((e) => /int required/.test(e)));
    check('invalid regex rejected', validatePacket(withEv({ type: 'stdout_regex', value: '(' })).some((e) => /invalid regex/.test(e)));
    check('extra key rejected', validatePacket(withEv({ type: 'exit_code', value: 0, note: 'x' })).some((e) => /exactly \{type, value\}/.test(e)));
    const withResets = (resets) => stBasePacket(ID, { resets });
    check('valid resets accepted', validatePacket(withResets([{ at: new Date().toISOString(), from_status: 'reverted', reason: 'retry with better instruction' }])).length === 0);
    check('resets missing reason rejected', validatePacket(withResets([{ at: new Date().toISOString(), from_status: 'reverted' }])).some((e) => /resets\[0\]/.test(e)));
    check('resets bad from_status rejected', validatePacket(withResets([{ at: new Date().toISOString(), from_status: 'nope', reason: 'x' }])).some((e) => /from_status/.test(e)));
    check('resets non-array rejected', validatePacket(withResets({ at: 'x' })).some((e) => /resets: array/.test(e)));
  });

  // ---- reset verb: deliberately reopen a terminal packet ----
  await scenario('reset: reverted → pending, recorded, then workable end-to-end', async (check) => {
    const root = stRepo(ID, { packetOverrides: { status: 'reverted', maker_provider: 'claude', judge_provider: 'codex' } });
    const rr = executeReset({ id: ID, repoRoot: root });
    const p1 = packetOnDisk(root);
    check('reset exit 0', rr.outcome === 'reset' && rr.exitCode === 0, rr.summary);
    check('status pending', p1.status === 'pending');
    check('reset recorded {at, from_status, reason}', p1.resets?.length === 1 && p1.resets[0].from_status === 'reverted' && typeof p1.resets[0].at === 'string' && p1.resets[0].reason.length > 0, JSON.stringify(p1.resets));
    check('provider pins cleared', p1.maker_provider === null && p1.judge_provider === null);
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    const p2 = packetOnDisk(root);
    check('reopened packet lands', r.outcome === 'landed', r.summary);
    check('resets survive the terminal write', p2.resets?.length === 1 && p2.resets[0].from_status === 'reverted', JSON.stringify(p2.resets));
  });

  await scenario('reset: refuses landed without --force', async (check) => {
    const root = stRepo(ID, { packetOverrides: { status: 'landed', outcome: { commit: 'a'.repeat(40), skip_reason: null, blocked_on: null, judge_verdict: null, evidence_receipts: [], tokens_actual: null, lesson: null } } });
    const before = read(root, `quality/packets/${ID}.yaml`);
    const rr = executeReset({ id: ID, repoRoot: root });
    check('refused exit 2', rr.outcome === 'refused' && rr.exitCode === 2, rr.summary);
    check('refusal names the landed commit risk', /landed/.test(rr.summary) && /--force/.test(rr.summary));
    check('packet byte-unchanged', read(root, `quality/packets/${ID}.yaml`) === before);
    const forced = executeReset({ id: ID, repoRoot: root, force: true });
    const p = packetOnDisk(root);
    check('--force resets landed', forced.outcome === 'reset' && p.status === 'pending' && p.resets?.[0]?.from_status === 'landed', forced.summary);
  });

  await scenario('reset: refusals (already pending, unknown id, unsupported --to) + append-on-repeat', async (check) => {
    const root = stRepo(ID);
    const rr1 = executeReset({ id: ID, repoRoot: root });
    check('already pending refused', rr1.outcome === 'refused' && /already pending/.test(rr1.summary), rr1.summary);
    const rr2 = executeReset({ id: 'no-such-packet', repoRoot: root });
    check('unknown id refused', rr2.outcome === 'refused' && /not found/.test(rr2.summary), rr2.summary);
    const rr3 = executeReset({ id: ID, repoRoot: root, to: 'landed' });
    check("--to != 'pending' refused", rr3.outcome === 'refused' && /unsupported/.test(rr3.summary), rr3.summary);
    // two consecutive reopenings APPEND (the reset history is never overwritten)
    const blocked = { status: 'blocked', outcome: { commit: null, skip_reason: null, blocked_on: 'missing oracle', judge_verdict: null, evidence_receipts: [], tokens_actual: null, lesson: null } };
    writePacket(join(root, 'quality/packets', `${ID}.yaml`), { ...packetOnDisk(root), ...blocked });
    executeReset({ id: ID, repoRoot: root, reason: 'first reopen' });
    writePacket(join(root, 'quality/packets', `${ID}.yaml`), { ...packetOnDisk(root), ...blocked });
    const rr4 = executeReset({ id: ID, repoRoot: root, reason: 'second reopen' });
    const p = packetOnDisk(root);
    check('resets appended, order preserved', rr4.outcome === 'reset' && p.resets?.length === 2 && p.resets[0].reason === 'first reopen' && p.resets[1].reason === 'second reopen', JSON.stringify(p.resets));
  });

  // ---- report ----
  let pass = 0, fail = 0;
  for (const s of results) {
    const bad = s.checks.filter((c) => !c.ok);
    w(`${bad.length ? 'FAIL' : ' ok '} ${s.name}`);
    for (const c of s.checks) {
      if (!c.ok || verbose) w(`      ${c.ok ? 'ok  ' : 'FAIL'} ${c.label}${c.ok ? '' : `  — ${c.detail}`}`);
      c.ok ? pass++ : fail++;
    }
  }
  w(`\nhone work --self-test: ${pass} checks passed, ${fail} failed, ${results.length} scenarios (no LLM calls)`);
  return fail === 0 ? 0 : 1;
}
