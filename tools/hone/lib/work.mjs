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
//                          touchset or EVERYTHING reverts (skipped: touchset-violation).
//                          ALL working-tree checks run against the FULL GIT ROOT (not the
//                          --repo subtree); touchset entries + observed paths are both
//                          normalized to git-root-relative before comparison
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
// ({type: exit_code|stdout_includes|stdout_regex|scope_fn_lt|file_excess_lt|failing_test_named,
// value} — enforced deterministically, fail-closed), and (e) NO NEW SKIPS on test rungs: skip
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
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { parseYaml, stringifyYaml } from './yaml.mjs';
import { validatePacket, assertValidPacket } from './validate-packet.mjs';
import { deepEqual, djb2, slug } from './util.mjs';
import { loadPacket, writePacket } from './packet-io.mjs';
import { executeReset } from './reset.mjs';
import { appendClaim, appendCostEntry, nextClaimSeq, nextJobAttempt, readJsonl, claimsPath, costPath } from './ledger.mjs';
import { loadRegistry, loadRouting, resolveRoutingClass, selectAgent } from './routing.mjs';
import { HONE_ROOT } from './profile.mjs';
import { runCli } from '../providers/provider.mjs';

const PROVIDERS = ['claude', 'codex'];
export const TERMINAL = ['landed', 'reverted', 'skipped', 'blocked'];
export const AUTHOR_NAME = 'Tim Nunamaker';
export const AUTHOR_EMAIL = 'tnunamak@gmail.com';
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

async function claudeMaker(prompt, { cwd, timeoutMs = MAKER_TIMEOUT_MS, model: routedModel, effort: routedEffort } = {}) {
  // model + effort precedence: owner env override > selectAgent routing (L1) > explicit
  // default. BOTH always passed explicitly — never the CLI's silent default (the
  // Opus-default oversight the levers doc flags; effort is first-class per the amendment).
  const model = process.env.HONE_CLAUDE_MODEL || routedModel || 'sonnet';
  const effort = process.env.HONE_CLAUDE_EFFORT || routedEffort || 'high';
  // claude 2.1.198 print mode has no --permission-mode; --allowedTools grants edit
  // permission non-interactively. Bash is deliberately NOT allowed: evidence commands
  // are the engine's job, and a maker that can't run git can't commit or stage.
  const args = ['-p', '--model', model, '--effort', effort, '--output-format', 'json', '--no-session-persistence',
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

async function codexMaker(prompt, { cwd, timeoutMs = MAKER_TIMEOUT_MS, model: routedModel, effort: routedEffort } = {}) {
  const model = process.env.HONE_CODEX_MODEL || routedModel || 'gpt-5.5';
  const effort = process.env.HONE_CODEX_EFFORT || routedEffort || 'high';
  const dir = mkdtempSync(join(tmpdir(), 'hone-maker-'));
  const outFile = join(dir, 'last-message.txt');
  // network_access: codex's workspace-write sandbox blocks localhost binding by default, which blinds
  // the maker to any test that starts a local server — it sees red where the engine sees green (proven
  // by no-model probe, campaign-rescue 2026-07-02: `listen EPERM 127.0.0.1` without the flag).
  const args = ['exec', '--ephemeral', '--skip-git-repo-check', '-s', 'workspace-write',
    '-c', 'sandbox_workspace_write.network_access=true',
    '-c', `model_reasoning_effort="${effort}"`,
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

export function gitContext(repoRoot) {
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
const slashRel = (p) => p.split('\\').join('/');
const isInsidePath = (parent, child) => {
  const rel = slashRel(relative(parent, child));
  return rel === '' || (rel !== '..' && !rel.startsWith('../'));
};

/**
 * porcelain-v1 entries for the FULL git root, never just the --repo subtree: the maker can
 * write anywhere in the repository, so every working-tree check (dirty preflight, touchset
 * enforcement, no-diff detection, revert, post-land cleanliness) must see the whole tree.
 * Paths are toplevel-relative. (Dogfood packet 9: subtree-scoped status let an
 * out-of-subtree edit survive a "revert" and produced a FALSE all-reverted ledger claim.)
 */
function statusEntries(g) {
  const out = g.git(['status', '--porcelain=v1', '-uall']);
  return out.split('\n').filter(Boolean).map((line) => ({
    x: line[0], y: line[1],
    paths: line.slice(3).split(' -> ').map(unquotePath),
  }));
}

/** dirty entries EXCLUDING quality/ engine state (packets, ledgers, receipts are ours). */
export function dirtyEntries(g) {
  const q = g.topRel('quality');
  return statusEntries(g).filter((e) => !e.paths.every((p) => p === q || p.startsWith(q + '/')));
}

export const flatPaths = (entries) => [...new Set(entries.flatMap((e) => e.paths))];

/**
 * normalize ONE touchset entry to a git-root-relative path (the coordinate system of
 * statusEntries): an entry that exists under --repo resolves there; otherwise an entry
 * that exists relative to the git root is taken as already git-root-relative; a path that
 * exists at neither (e.g. a file the packet expects the maker to create) defaults to
 * --repo-relative. Rule documented in schemas/candidate-packet.yaml (touchset). (Dogfood
 * packet 8: a git-root-relative entry was compared unnormalized against a --repo-relative
 * observed path — the violation message printed the identical string on both sides.)
 */
export function normalizeTouchEntry(g, repoRoot, entry) {
  if (g.prefix && existsSync(join(g.gitRoot, entry))) return slashRel(relative(g.gitRoot, resolve(g.gitRoot, entry)));
  const repoAbs = resolve(repoRoot, entry);
  if (isInsidePath(g.gitRoot, repoAbs)) return slashRel(relative(g.gitRoot, repoAbs));
  return slashRel(g.topRel(entry));
}

export function touchsetLeavesRepoRoot(g, touchTop) {
  if (!g.prefix) return false;
  const prefix = `${g.prefix}/`;
  return touchTop.some((p) => p !== g.prefix && !p.startsWith(prefix));
}

/** restore the worktree to HEAD for everything dirty outside quality/. Throws if it can't. */
export function revertAll(g) {
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

/**
 * run one evidence rung in its OWN process group; on timeout the whole group gets
 * SIGKILL (negative pid), not just the direct bash child. (Engine-iteration-4 fix 3,
 * run-2 finding: spawnSync's timeout killed bash but left a `node --test` grandchild
 * alive for minutes after "SIGKILL". Mirrors providers/runCli.)
 */
export function runShellCmd(cmd, cwd, timeoutMs = EVIDENCE_TIMEOUT_MS, extraEnv = null) {
  return new Promise((resolvePromise) => {
    const startedAt = Date.now();
    const child = spawn('/bin/bash', ['-c', cmd], {
      cwd, stdio: ['ignore', 'pipe', 'pipe'], detached: true, // own process group
      ...(extraEnv ? { env: { ...process.env, ...extraEnv } } : {}),
    });
    let stdout = '', stderr = '', total = 0, truncated = false, timedOut = false;
    const append = (cur, d) => {
      if (truncated) return cur;
      total += d.length;
      if (total > MAX_BUFFER) { truncated = true; return cur + '\n…[output truncated at 64MB]…'; }
      return cur + d;
    };
    const killGroup = () => {
      try { process.kill(-child.pid, 'SIGKILL'); } // whole process group
      catch { try { child.kill('SIGKILL'); } catch { /* already gone */ } }
    };
    const timer = setTimeout(() => { timedOut = true; killGroup(); }, timeoutMs);
    child.stdout.on('data', (d) => { stdout = append(stdout, String(d)); });
    child.stderr.on('data', (d) => { stderr = append(stderr, String(d)); });
    const settle = (code) => {
      clearTimeout(timer);
      resolvePromise({
        code: timedOut ? null : code,
        timedOut,
        stdout,
        output: stdout + stderr,
        durationMs: Date.now() - startedAt,
      });
    };
    child.on('error', (err) => { stderr += `spawn error: ${err.message}`; settle(null); });
    child.on('close', (code) => settle(code));
  });
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

const EXPECT_CHECK_TYPES = ['exit_code', 'stdout_includes', 'stdout_regex', 'scope_fn_lt', 'file_excess_lt', 'failing_test_named'];

/**
 * a TAP/spec-reporter line that REPORTS A FAILURE: TAP `not ok …`, node spec / jest
 * `✖ ✗ ✕ …`, mocha's numbered failure list `  1) …`. Used by failing_test_named to
 * assert the RIGHT test failed — run-3 t1b-0012's kind-swap rung exited 0 while the
 * wrong tests (setup 409s) were the ones failing; only the judge caught it.
 */
const isFailureLine = (l) => /(^|\s)not ok\b/.test(l) || /[✖✗✕]/.test(l) || /^\s*\d+\)\s/.test(l);

// known compare-vs-HEAD evidence patterns: structurally unwinnable BEFORE a commit (the
// maker's uncommitted diff is exactly what they flag). Warning only, no behavior change —
// see README "Authoring evidence rungs". (Dogfood packet 9: check:generated burned $6.83.)
const COMPARE_VS_HEAD_PATTERNS = [/git\s+diff\b[^|;&\n]*--exit-code/, /\bcheck:generated\b/];
export const isCompareVsHead = (cmd) => COMPARE_VS_HEAD_PATTERNS.some((re) => re.test(String(cmd)));

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
 * improvement/goal types (scope_fn_lt / file_excess_lt / failing_test_named) are
 * post-change only.
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
    case 'failing_test_named': {
      // POST-ONLY by construction: generate_evidence packets legitimately print the
      // guard fallback (e.g. `oracle-not-yet-authored`) at baseline while the oracle
      // file does not exist yet — enforcing here at baseline would recreate the run-1
      // $0-block class. The rung must exit 0 overall (mutation-seed → test → restore).
      if (phase !== 'post') return null;
      const name = String(ec.value);
      const hit = String(res.output).split('\n').some((l) => isFailureLine(l) && l.includes(name));
      return hit ? null : fail(`no TAP/spec failure line naming '${name}' — a failure of the WRONG test (or no failure at all) is not the seeded red (run-3 t1b-0012: kind-swap rung exited 0 on setup 409s)`);
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
export function checkExpect(rung, res, phase, baselineRes = null) {
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

export function tailClip(s, n) {
  const t = String(s);
  return t.length <= n ? t : `…[${t.length - n} bytes clipped]…\n` + t.slice(-n);
}

/** for prose (judge reasoning in lessons/claims): keep the head, clip the tail. */
export function headClip(s, n) {
  const t = String(s);
  return t.length <= n ? t : t.slice(0, n) + '…';
}

// judge-context evidence bounds (fix for dogfood packet 1: receipts carried only exit
// codes + digests, so the judge REVISEd honest work for not SHOWING red/green output)
const RUNG_SLICE_MAX_LINES = 40;
const RUNG_SLICE_MAX_BYTES = 2048;
const JUDGE_EVIDENCE_MAX_BYTES = 16384;
// budget floor for protected slices in buildJudgeEvidence (engine-iteration-5 fix 3)
const SHRUNK_SLICE_TAIL_LINES = 10;

/**
 * remeasure-class rungs (scope-fn / file-excess / complexity remeasures) carry decisive
 * NUMBERS the judge must be able to verify. (Engine-iteration-5 fix 3 — run-5 hm-0003
 * second REVISE: "file-excess evidence is clipped before the values needed to verify that
 * rung" — the collector prints its scalars at the HEAD of a ~400-line JSON, so the
 * tail-keeping per-rung slice lost them.)
 */
const REMEASURE_RUNG_RE = /remeasure|excess/i;

/**
 * drop lines that reference the ENGINE'S OWN quality/ state dir from judge-facing rung
 * output. (Engine-iteration-5 fix 1 — run-5 docs-query-cookbook-expand-advisory-0003:
 * a packet's own `git diff --stat` rung listed 25 changed files, 24 of them the engine's
 * tracked quality/ writes (agenda artifacts, ledgers, packet YAMLs, receipts); the judge
 * twice called the maker's change "semantically aligned" and still refused on the
 * diff-scope gate — a false negative that burned two judge rounds. The engine's OWN scope
 * gates already exclude quality/ (dirtyEntries); the judge-facing slices did not.)
 *
 * Matched line classes (best-effort textual; the disk receipt keeps the RAW output):
 *  - a path under the git-root-relative engine state dir (`<prefix>/quality/…`);
 *  - a `quality/…` path at a token boundary, including git-stat's `…/quality/…` abbreviation;
 *  - the engine's own receipt/brief basenames (`baseline-1-….txt`, `post-r2-….txt`,
 *    `maker-brief-N.digest.txt`) — long receipt paths abbreviate to bare basenames in
 *    `git diff --stat` output and lose the `quality/` marker entirely;
 *  - the candidate's own packet YAML basename (rewritten by the engine at in_progress);
 *  - abbreviated engine packet paths (`…/packets/<id>.yaml` — OTHER candidates' packets
 *    rewritten by concurrent agenda/plan activity abbreviate past the quality/ marker;
 *    observed in the run-5 receipt) and the engine ledger/agenda basenames
 *    (AGENDA.json/.md, claims.jsonl, cost.jsonl, selection-ledger.jsonl, not-chosen.json).
 * When lines are dropped an explicit note is APPENDED (tail-safe under outputSlice) so
 * changed-file COUNT lines (`N files changed, …`) that still include engine files are
 * self-explaining to the judge.
 */
export function stripEngineQualityLines(output, { qualityRel = 'quality', candidateId = null } = {}) {
  const esc = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const bound = `(?:^|[\\s"'(=:]|\\.{3}/)`;
  const res = [
    new RegExp(`${bound}${esc(qualityRel)}/`),
    new RegExp(`${bound}quality/`),
    /(^|[\s/("'=:])(?:(?:baseline|post(?:-r\d+)?)-\d+-[A-Za-z0-9_-]+\.txt|maker-brief-\d+\.digest\.txt)\b/,
    /\.{3}\/packets\/[^/\s]+\.yaml\b/,
    /(^|[\s/("'=:])(?:AGENDA\.(?:json|md)|claims\.jsonl|cost\.jsonl|selection-ledger\.jsonl|not-chosen\.json)\b/,
  ];
  if (candidateId) res.push(new RegExp(`(^|[\\s/("'=:])${esc(candidateId)}\\.yaml\\b`));
  const lines = String(output).split('\n');
  const kept = lines.filter((l) => !res.some((re) => re.test(l)));
  const dropped = lines.length - kept.length;
  if (!dropped) return { text: String(output), dropped: 0 };
  const note = `[engine] ${dropped} line(s) referencing the engine \`quality/\` state dir omitted — that dir is engine bookkeeping (packets, receipts, ledgers, agenda), written by the engine itself, never maker work; any changed-file COUNTS remaining in this output may still include those engine files`;
  return { text: [...kept, note].join('\n'), dropped };
}

/**
 * the judge-facing slice of one rung's output: engine quality/-state lines stripped
 * (fix 1), tail-bounded by outputSlice, and — for remeasure-class rungs — prefixed with
 * a compact clip-proof summary of the collector JSON's scalars (fix 3: the decisive
 * numbers print at the HEAD of the collector's long JSON; a tail slice loses them).
 * Judge context only; the on-disk receipt keeps the raw output.
 */
export function judgeSlice(rungName, res, stripCtx) {
  const { text } = stripEngineQualityLines(res.output, stripCtx);
  let s = outputSlice(text);
  if (REMEASURE_RUNG_RE.test(String(rungName))) {
    const j = lastJson(res.stdout);
    if (j && typeof j === 'object' && !Array.isArray(j)) {
      const compact = {};
      for (const [k, v] of Object.entries(j)) {
        if (v === null || ['number', 'string', 'boolean'].includes(typeof v)) compact[k] = v;
        else if (Array.isArray(v)) {
          const json = JSON.stringify(v);
          if (json.length <= 256) compact[k] = v;
          else compact[`${k}_count`] = v.length;
        }
      }
      s = `[engine] remeasure summary (scalars parsed from this rung's JSON output; immune to slice clipping): ${JSON.stringify(compact)}\n` + s;
    }
  }
  return s;
}

/** last ≤maxLines lines of a rung's combined output, additionally byte-bounded (tail wins). */
export function outputSlice(output, maxLines = RUNG_SLICE_MAX_LINES, maxBytes = RUNG_SLICE_MAX_BYTES) {
  const lines = String(output).replace(/\n+$/, '').split('\n');
  let s = lines.slice(-maxLines).join('\n');
  if (lines.length > maxLines) s = `…[${lines.length - maxLines} earlier line(s) omitted]…\n` + s;
  if (s.length > maxBytes) s = `…[clipped]…` + s.slice(-maxBytes);
  return s;
}

/**
 * judge-facing evidence: every receipt digest line PLUS a bounded tail of that rung's
 * REAL output. (Engine-iteration-4 fix 1 — run-4 hm-0003: the old single head-truncated
 * global cap silently dropped the BASELINE receipts first, so the judge REVISEd honest
 * work "no baseline receipt reached it", burning a $1+ revision round.)
 *
 * Guarantees, in priority order under the total byte cap:
 *   1. every receipt digest LINE is always present (baseline receipts at minimum as
 *      summary lines — they are never truncated away);
 *   2. every FAILING run's output slice is kept (the defect under judgment);
 *   3. the FINAL green run's slice is kept (the state under judgment);
 *   4. remeasure-class rungs (name matches remeasure|excess — decisive numbers the
 *      judge must verify) are never dropped: under budget pressure they SHRINK to a
 *      floor — any leading [engine] summary line(s) plus the last ~10 tail lines —
 *      never below it (engine-iteration-5 fix 3, run-5 hm-0003 clip);
 *   5. remaining green slices are dropped verbose-first (post-phase greens before
 *      baseline greens) until the total fits; dropped slices leave an explicit marker.
 * Per-rung slices are already bounded by outputSlice (≤40 lines / ≤2KB each).
 *
 * `entries`: [{line, slice, phase, pass, rung}] in receipt order.
 */
export function buildJudgeEvidence(entries, maxBytes = JUDGE_EVIDENCE_MAX_BYTES) {
  const lastGreen = entries.reduce((acc, e, i) => (e.pass ? i : acc), -1);
  const included = entries.map((e) => Boolean(e.slice));
  const mustKeep = entries.map((e, i) => Boolean(e.slice) && (!e.pass || i === lastGreen));
  // remeasure-class slices are shrinkable-not-droppable (guarantee 4)
  const protectedRemeasure = entries.map((e, i) =>
    Boolean(e.slice) && !mustKeep[i] && REMEASURE_RUNG_RE.test(String(e.rung ?? '')));
  const shrunk = entries.map(() => false);
  const shrinkSlice = (slice) => {
    const lines = String(slice).split('\n');
    const head = [];
    while (lines.length && lines[0].startsWith('[engine] ')) head.push(lines.shift());
    const tail = lines.slice(-SHRUNK_SLICE_TAIL_LINES);
    const omitted = lines.length - tail.length;
    if (omitted > 0) head.push(`…[${omitted} line(s) shrunk away — evidence budget; full receipt on disk]…`);
    return [...head, ...tail].join('\n');
  };
  const render = () => entries.map((e, i) => {
    if (!e.slice) return e.line;
    if (!included[i]) return `${e.line}\n  [rung output tail omitted — evidence budget; full receipt on disk]`;
    return `${e.line}\n  --- rung output tail ---\n${shrunk[i] ? shrinkSlice(e.slice) : e.slice}\n  --- end tail ---`;
  }).join('\n');
  // droppable greens: post-phase (non-final) first, then baseline; verbose first within each
  const droppable = entries
    .map((e, i) => ({ i, size: e.slice ? e.slice.length : 0, isBaseline: e.phase === 'baseline' }))
    .filter(({ i }) => included[i] && !mustKeep[i] && !protectedRemeasure[i])
    .sort((a, b) => (Number(a.isBaseline) - Number(b.isBaseline)) || (b.size - a.size));
  let text = render();
  for (const d of droppable) {
    if (text.length <= maxBytes) break;
    included[d.i] = false;
    text = render();
  }
  if (text.length > maxBytes) {
    // still over: shrink protected remeasure slices to the floor, verbose first —
    // never drop them, never cut below the [engine] head + last-10-lines tail
    const shrinkable = entries
      .map((e, i) => ({ i, size: e.slice ? e.slice.length : 0 }))
      .filter(({ i }) => protectedRemeasure[i] && !shrunk[i])
      .sort((a, b) => b.size - a.size);
    for (const d of shrinkable) {
      if (text.length <= maxBytes) break;
      shrunk[d.i] = true;
      text = render();
    }
  }
  if (text.length > maxBytes) {
    // safety valve (must-keep content alone exceeds the cap — pathological): hard head
    // truncation, newest (failing/final) content survives at the tail.
    text = `…[${text.length - maxBytes} bytes truncated from head; newest rungs retained]…\n` + text.slice(-maxBytes);
  }
  return text;
}

export function currentJudgeEvidenceEntries(entries) {
  const baseline = [];
  const latestPostByRung = new Map();
  for (const e of entries) {
    if (e.phase === 'baseline') {
      baseline.push(e);
    } else {
      latestPostByRung.set(e.rung, e);
    }
  }
  return [...baseline, ...latestPostByRung.values()];
}

export function buildCurrentJudgeEvidence(entries, maxBytes = JUDGE_EVIDENCE_MAX_BYTES) {
  return buildJudgeEvidence(currentJudgeEvidenceEntries(entries), maxBytes);
}

// ---------------------------------------------------------------- prompts

/**
 * bounded PRIOR-ATTEMPT section (fix 4, engine-iteration-3): a reopened packet
 * (non-empty `resets` + a preserved prior outcome — reset.mjs keeps the outcome block
 * until the next terminal write) surfaces the judge's exact demands to the maker
 * instead of leaving them buried in the packet YAML. Returns null when there is
 * nothing to thread. Hard-bounded ~2KB.
 */
function priorAttemptSection(packet) {
  if (!Array.isArray(packet.resets) || !packet.resets.length) return null;
  const o = packet.outcome ?? {};
  const fields = [
    ['judge_verdict', o.judge_verdict],
    ['lesson', o.lesson],
    ['skip_reason', o.skip_reason],
    ['blocked_on', o.blocked_on],
  ].filter(([, v]) => typeof v === 'string' && v.trim());
  if (!fields.length) return null;
  const last = packet.resets[packet.resets.length - 1];
  return headClip([
    "== PRIOR ATTEMPT — the judge's exact demands ==",
    `This packet was attempted before and deliberately reopened (reset #${packet.resets.length}, from ${last.from_status}: ${last.reason}). The prior outcome below is the exact bar the previous attempt failed to clear — address it directly; do not repeat the failed approach.`,
    ...fields.map(([k, v]) => `- ${k}: ${v}`),
  ].join('\n'), 2048);
}

export function makerBrief(rawPacketYaml, packet, { cwdNote = null } = {}) {
  const prior = priorAttemptSection(packet);
  return [
    'You are the MAKER in a repo-quality engine, executing exactly ONE work packet in this repository (your current working directory). The packet below is the entire contract.',
    ...(cwdNote ? [cwdNote] : []),
    'Binding rules:',
    `- Modify ONLY these files (the touchset): ${packet.touchset.join(', ')}. Creating, editing, deleting, or renaming ANY other file voids the whole run — the engine reverts everything.`,
    `- Obey every not_allowed item: ${packet.not_allowed.join(', ')}.`,
    '- Do exactly what plan.instruction says. No scope creep, no drive-by fixes, no comment or formatting churn outside the named functions.',
    '- Do NOT commit, stage, branch, or run any git write operation. Do NOT add dependencies. Do NOT touch anything under quality/.',
    "- Do NOT run the test suite; the engine runs the packet's evidence_required commands itself after you finish.",
    '- When done, reply with a short summary: which functions changed, what transform you applied, and why behavior is preserved.',
    '- If you conclude the code is ALREADY CORRECT and the packet premise is a non-defect, make NO edits and end your reply with one line exactly of the form: HONE-VERDICT: validated-non-defect — <one-line rationale>',
    '- If you CANNOT act on plan.instruction as written (target missing, contradiction with the actual code, instruction not executable), make NO edits and end your reply with one line exactly of the form: HONE-VERDICT: unactionable — <one-line reason>',
    ...(prior ? [prior] : []),
    '== WORK PACKET (YAML) ==',
    rawPacketYaml,
  ].join('\n');
}

/**
 * parse the maker's explicit no-change verdict from its final output text (bounded scan,
 * LAST match wins). Returns {kind: 'validated-non-defect'|'unactionable', rationale} or
 * null — unparseable keeps the generic maker-no-diff behavior (fail-safe). Fix for the
 * dogfood negative control: a correct "nothing to fix" and an unactionable instruction
 * were indistinguishable, and the correct close got a "reset to pending to retry" claim.
 */
export function parseMakerVerdict(text) {
  const bounded = String(text ?? '').slice(-8000);
  const re = /^[ \t]*HONE-VERDICT:[ \t]*(validated-non-defect|unactionable)[ \t]*(?:[—–:-]+)?[ \t]*(.*)$/gim;
  let last = null;
  for (const m of bounded.matchAll(re)) last = m;
  if (!last) return null;
  return { kind: last[1].toLowerCase(), rationale: last[2].trim() || '(no rationale given)' };
}

export function revisionBrief(base, failureNote, diffText) {
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

// ---------------------------------------------------------------- packet lock
// engine-iteration-3 fix (batch-2a race): two `hone work` processes ran the SAME packet
// concurrently — land-time git atomicity defused it, but only by luck and $0.56 of waste.
// One O_EXCL lockfile per packet closes the window between "read status pending" and
// "write status in_progress". A live holder (pid alive) refuses; a stale lock (pid dead,
// e.g. the earlier SIGKILLed-work incident) is broken with a logged note.

function pidAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (e) { return e.code === 'EPERM'; } // EPERM = exists but not ours = alive
}

export function acquireWorkLock(repoRoot, id, log) {
  // no quality/packets → nothing to race on; loadPacket will refuse. Skipping avoids
  // creating directories inside an arbitrary --repo path on a refusal.
  if (!existsSync(join(repoRoot, 'quality', 'packets'))) return { ok: true, release: () => {} };
  const dir = join(repoRoot, 'quality', '.locks');
  const path = join(dir, `${id}.lock`);
  mkdirSync(dir, { recursive: true });
  const take = () => writeFileSync(path, JSON.stringify({ pid: process.pid, started: new Date().toISOString() }) + '\n', { flag: 'wx' });
  const release = () => {
    try { if (JSON.parse(readFileSync(path, 'utf8')).pid === process.pid) rmSync(path, { force: true }); }
    catch { /* already gone or foreign — never remove another holder's lock */ }
  };
  try { take(); return { ok: true, release, path }; }
  catch (e) {
    if (e.code !== 'EEXIST') throw e;
    let holder = null;
    try { holder = JSON.parse(readFileSync(path, 'utf8')); } catch { /* unparseable = stale */ }
    if (Number.isInteger(holder?.pid) && pidAlive(holder.pid)) {
      return { ok: false, reason: `packet is LOCKED by a live hone work process (pid ${holder.pid}, since ${holder.started ?? 'unknown'}) — concurrent execution of the same packet is refused (lock: ${path})` };
    }
    log?.(`hone work — ${id}: breaking STALE lock (pid ${holder?.pid ?? 'unparseable'} not alive; lock: ${path})`);
    rmSync(path, { force: true });
    try { take(); return { ok: true, release, path, brokeStale: true }; }
    catch { return { ok: false, reason: `lost the race re-taking a broken stale lock: ${path}` }; }
  }
}

// ---------------------------------------------------------------- shared engine spine
// These are the deterministic, books-writing pieces shared verbatim between the
// subprocess pipeline (`hone work`, this file) and the Workflow-substrate lane CLI
// (`hone lane`, lane.mjs). Extraction, not fork: both hosts MUST produce identical
// receipt/packet/claim/cost shapes — the ledgers are the product, the substrate is
// an implementation detail. Behavior gate: `hone work --self-test` covers every
// terminal path through these functions.

// ---------------------------------------------------------------- portable rung commands
// Live finding (pilot run, packet t1b-retained-size-top-row-values-0010): rung commands
// carry ABSOLUTE paths baked from the worktree they were AUTHORED on (`cd /…/pdpp-cq-sweep
// /… && pnpm test`, collector calls under a different engine checkout). Executed verbatim
// against a DIFFERENT --repo worktree, the maker's diff lands in one tree while every rung
// measures the OTHER: gates go vacuously green (or guaranteed red for `hone work`).
// THE RULE: a rung must never measure outside the current repo. Foreign paths are either
// structurally REWRITTEN into the current tree / running engine, or the rung is REFUSED
// (fail-closed). Same-tree commands are untouched (identity). Rung shells additionally get
// REPO_ROOT / GIT_ROOT / HONE_ROOT so future packets can be authored portably.

// pseudo-filesystems that can never be "another repo" (skip the .git-walk stat churn)
const NEVER_REPO_PREFIXES = ['/dev/', '/proc/', '/sys/'];
// absolute path tokens with >= 2 segments (single-segment tokens like `cd /x` fixtures or
// sed deletions are ignored); the lookbehind keeps URL slashes (postgres://…) unmatched
const ABS_PATH_RE = /(?<=^|[\s"'=(:])\/(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+/g;
const ENGINE_PATH_RE = /(?<=^|[\s"'=(:])(\/(?:[A-Za-z0-9._-]+\/)*tools\/hone)(?=\/)/g;

/**
 * is this absolute token inside SOME git checkout? — the precise structural test for
 * "another repo" (a blanket /tmp-style allowlist is a hole: worktrees live there too).
 * Walk to the deepest EXISTING ancestor, then up, looking for a .git marker (dir for
 * clones, FILE for linked worktrees). Non-repo tool/scratch/system paths return false.
 */
function insideGitCheckout(tok) {
  let p = tok;
  while (p.length > 1 && !existsSync(p)) p = p.slice(0, Math.max(1, p.lastIndexOf('/')));
  while (p.length > 1) {
    if (existsSync(join(p, '.git'))) return true;
    p = p.slice(0, Math.max(1, p.lastIndexOf('/')));
  }
  return false;
}

/**
 * make a rung command safe to execute against the CURRENT tree:
 *   1. engine-layout paths (`…/tools/hone/…`) -> the RUNNING engine's own dir;
 *   2. a leading `cd <foreign-abs> &&` -> the current tree by longest-suffix match
 *      (`…/reference-implementation` -> <git_root>/reference-implementation; a bare
 *      authoring root -> <git_root>) — worst case a rung goes red HERE, never green THERE;
 *   3. any OTHER foreign absolute path: mapped into the current repo when it carries the
 *      repo-root suffix (`…/<prefix>[/rest]` -> <repo_root>[/rest], e.g. collector --repo
 *      args), otherwise the rung is REFUSED — never run a rung that measures outside the
 *      current repo. System prefixes (/dev, /tmp, /usr, …) are always allowed.
 * Returns {command, rewritten, notes, refused}. Same-tree commands: identity.
 */
export function portableRungCommand(command, { gitRoot, repoRoot, prefix }) {
  const notes = [];
  let cmd = String(command);
  const inside = (p, root) => p === root || p.startsWith(root + '/');

  // 1. engine paths -> the running engine
  cmd = cmd.replace(ENGINE_PATH_RE, (m) => {
    if (m === HONE_ROOT) return m;
    notes.push(`engine path ${m} -> ${HONE_ROOT} (the running engine)`);
    return HONE_ROOT;
  });

  // 2. leading cd
  const mCd = cmd.match(/^(\s*cd\s+)("([^"]+)"|'([^']+)'|([^\s;&|]+))(\s*(?:&&|;))/);
  if (mCd) {
    const raw = mCd[3] ?? mCd[4] ?? mCd[5];
    if (raw.startsWith('/') && !inside(raw, gitRoot)) {
      const segs = raw.split('/').filter(Boolean);
      let mapped = gitRoot; // bare/unknown authoring root -> the current git root
      for (let take = Math.min(segs.length, 6); take >= 1; take--) {
        const tail = segs.slice(-take).join('/');
        if (existsSync(join(gitRoot, tail))) { mapped = join(gitRoot, tail); break; }
      }
      notes.push(`leading cd ${raw} -> ${mapped} (authored-worktree path mapped into the current tree)`);
      cmd = `${mCd[1]}"${mapped}"${mCd[6]}${cmd.slice(mCd[0].length)}`;
    }
  }

  // 3. remaining absolute tokens: in-tree/engine paths pass; repo-suffix maps (collector
  // --repo args); then the structural repo test — a token inside ANOTHER git checkout
  // refuses the rung; everything else passes: non-repo tool/scratch/system paths cannot
  // vacuously green a gate, and a token that exists nowhere (incl. shell/JS literals the
  // scanner over-matches, e.g. `/RE/.test(...)`) at worst goes honestly red.
  let refused = null;
  cmd = cmd.replace(ABS_PATH_RE, (tok) => {
    if (inside(tok, gitRoot) || inside(tok, HONE_ROOT)) return tok;
    if (NEVER_REPO_PREFIXES.some((a) => tok.startsWith(a))) return tok;
    if (prefix) {
      const marker = '/' + prefix;
      if (tok.endsWith(marker)) { notes.push(`${tok} -> ${repoRoot}`); return repoRoot; }
      const at = tok.indexOf(marker + '/');
      if (at !== -1) {
        const mapped = repoRoot + tok.slice(at + marker.length);
        notes.push(`${tok} -> ${mapped}`);
        return mapped;
      }
    }
    if (!insideGitCheckout(tok)) return tok;
    refused = refused ?? `foreign absolute path '${tok}' — inside another git checkout, not the current git root (${gitRoot}), not the running engine, and no repo-suffix mapping applies. A rung must never measure outside the current repo: rewrite the packet command repo-relative or use $REPO_ROOT/$GIT_ROOT (exported to rung shells)`;
    return tok;
  });
  if (refused) return { command: String(command), rewritten: false, notes, refused };
  return { command: cmd, rewritten: cmd !== String(command), notes, refused: null };
}

/**
 * the ONE way both substrates execute an evidence rung: portable-path rewrite (or
 * fail-closed refusal, rendered as a failing verdict with an unexecuted-receipt), the
 * REPO_ROOT/GIT_ROOT/HONE_ROOT env contract, then the deterministic expect check.
 * Returns {res, verdict, executedCommand} — executedCommand !== rung.command marks a
 * rewrite for the receipt record; null means the rung was refused and never ran.
 */
export function makeRungExecutor({ gitRoot, repoRoot, prefix, log = () => {} }) {
  const ctx = { gitRoot, repoRoot, prefix };
  const env = { REPO_ROOT: repoRoot, GIT_ROOT: gitRoot, HONE_ROOT };
  return async function execRung(rung, phase, baselineRes = null) {
    const port = portableRungCommand(rung.command, ctx);
    if (port.refused) {
      log(`  [portable-path] ${rung.rung}: REFUSED — ${port.refused}`);
      return {
        res: { code: null, timedOut: false, stdout: '', output: `[engine] rung NOT EXECUTED — ${port.refused}`, durationMs: 0 },
        verdict: { pass: false, reason: `portable-path refusal: ${port.refused}` },
        executedCommand: null,
      };
    }
    if (port.rewritten) log(`  [portable-path] ${rung.rung}: ${port.notes.join('; ')}`);
    const res = await runShellCmd(port.command, repoRoot, undefined, env);
    return { res, verdict: checkExpect(rung, res, phase, baselineRes), executedCommand: port.command };
  };
}

/**
 * write one evidence-rung receipt file + return the bookkeeping strings the caller
 * accumulates ({digest, line, slice, meta}). File + digest + line formats are the
 * ledger-visible receipt contract — identical for every execution substrate. When the
 * executed command differs from the authored one (portable-path rewrite, or a refusal
 * that never ran), the receipt records BOTH — the books never hide a rewrite.
 */
export function writeRungReceipt({ repoRoot, receiptsDirRel, id, via = 'work', phase, index, rung, res, verdict, stripCtx, executedCommand = undefined }) {
  const rel = join(receiptsDirRel, `${phase}-${index + 1}-${slug(rung.rung)}.txt`);
  const abs = join(repoRoot, rel);
  mkdirSync(dirname(abs), { recursive: true });
  const rewritten = executedCommand !== undefined && executedCommand !== rung.command;
  const execLine = rewritten
    ? (executedCommand === null
      ? `# executed-command: (NOT EXECUTED — portable-path refusal)\n`
      : `# executed-command (portable-path rewrite): ${executedCommand}\n`)
    : '';
  writeFileSync(abs, `# hone ${via} ${id} — ${phase} rung '${rung.rung}'\n# command: ${rung.command}\n${execLine}# expect: ${rung.expect}\n# exit: ${res.timedOut ? 'TIMEOUT' : res.code}  duration: ${Math.round(res.durationMs / 1000)}s  verdict: ${verdict.pass ? 'PASS' : `FAIL (${verdict.reason})`}\n\n${res.output}`);
  const digest = `exit=${res.timedOut ? 'TIMEOUT' : res.code} djb2=${djb2(res.output)} bytes=${res.output.length} receipt=${rel}`;
  return {
    digest,
    line: `[${phase}] ${rung.rung}: ${rung.command} -> ${res.timedOut ? 'TIMEOUT' : `exit ${res.code}`} (${Math.round(res.durationMs / 1000)}s) ${verdict.pass ? 'PASS' : `FAIL: ${verdict.reason}`}; ${digest}${!rewritten ? '' : executedCommand === null ? ' [NOT EXECUTED — portable-path refusal]' : ' [portable-path rewrite]'}`,
    slice: judgeSlice(rung.rung, res, stripCtx),
    meta: { phase, pass: verdict.pass, rung: rung.rung },
  };
}

/**
 * persist a maker-brief digest receipt (first ~4KB + sha256 of the FULL brief) —
 * auditability without huge files; written BEFORE the maker runs so even a crashed
 * attempt leaves its brief on disk (engine-iteration-4 fix 2).
 */
export function persistMakerBriefDigest({ repoRoot, receiptsDirRel, id, via = 'work', attempt, briefText }) {
  const rel = join(receiptsDirRel, `maker-brief-${attempt}.digest.txt`);
  const abs = join(repoRoot, rel);
  mkdirSync(dirname(abs), { recursive: true });
  const sha = createHash('sha256').update(briefText, 'utf8').digest('hex');
  writeFileSync(abs, `# hone ${via} ${id} — maker brief digest (attempt ${attempt})\n# sha256(full brief)=${sha} bytes=${Buffer.byteLength(briefText, 'utf8')}\n# first 4096 chars follow\n\n${briefText.slice(0, 4096)}`);
  return rel;
}

/** the maker's working-tree diff: tracked touchset paths + untracked touchset files via --no-index. */
export function buildWorkingDiff(g, touchTop) {
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
}

/**
 * THE single terminal writer, every path, every substrate, no exceptions: rewrite the
 * packet outcome, append claims.jsonl + cost.jsonl, return the result envelope.
 * `tokens` = {inTok, outTok, total, usd}; when NO provider call ran (makerRan and
 * judgeRan both false, e.g. blocked at a red baseline) cost/tokens are a known 0,
 * not an unknown null (dogfood packet 5).
 */
export function writeTerminal({
  repoRoot, id, packet, packetPath, startedAt, via = 'work',
  makerName, judgeName, makerRan, judgeRan, tokens, revisionCount, judgeResult, receiptLines,
  status, commit = null, skipReason = null, blockedOn = null, judgeVerdict = null, lesson = null,
  claims, headline,
  // OPTIONAL instrumentation (lane substrate): per-stage attribution, quota points,
  // batch-amortization marker. undefined = keys omitted — the subprocess path's cost
  // entries stay byte-identical.
  stages = undefined, quotaPts = undefined, batch = undefined,
}) {
  const { inTok, outTok, total, usd } = tokens;
  packet.status = status;
  packet.maker_provider = makerRan ? makerName : null;
  packet.judge_provider = judgeRan ? judgeName : null;
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
  const providerRan = makerRan || judgeRan;
  appendCostEntry(repoRoot, {
    job_id: `job-${id}-${attempt}`,
    created: new Date().toISOString(),
    candidate_id: id,
    workflow: packet.action,
    maker: { provider: makerName, tier: packet.maker_tier },
    judge: { provider: judgeName, tier: packet.judge_tier },
    tokens_in: providerRan ? inTok : 0,
    tokens_out: providerRan ? outTok : 0,
    cost_usd: providerRan ? (usd == null ? null : Math.round(usd * 10000) / 10000) : 0,
    wall_time_s: Math.round((Date.now() - startedAt) / 100) / 10,
    landed: status === 'landed',
    revision_count: revisionCount,
    judge_result: judgeResult,
    outcome: status,
    followup_created: [],
    ...(stages !== undefined ? { stages } : {}),
    ...(quotaPts !== undefined ? { quota_pts: quotaPts } : {}),
    ...(batch !== undefined ? { batch } : {}),
  });
  const exitCode = status === 'landed' ? 0 : 1;
  return {
    outcome: status, exitCode, commit,
    summary: [
      `hone ${via} — ${id}: ${status.toUpperCase()}`,
      `  ${headline}`,
      `  maker=${makerName} judge=${judgeName} revisions=${revisionCount} judge_result=${judgeResult ?? 'n/a'} wall=${Math.round((Date.now() - startedAt) / 100) / 10}s`,
      `  packet: ${packetPath}`,
      `  claims: +${claims.length} → ${claimsPath(repoRoot)}`,
      `  cost:   job-${id}-${attempt} → ${costPath(repoRoot)}`,
    ].join('\n'),
  };
}

/**
 * the one-commit-per-land discipline: stage ONLY the touchset, refuse rogue staged
 * paths, commit as Tim Nunamaker <tnunamak@gmail.com>, verify the tree is clean
 * after. Throws on any violation (callers fail-closed to blocked). Returns the sha.
 * `pipelineLabel` names the executing substrate honestly (e.g. `hone work: maker=claude
 * judge=codex`); everything else is byte-identical across substrates.
 */
export function landCommit(g, { packet, id, touchTop, receiptsDirRel, pipelineLabel, confidence, revisionCount, allowedLeftover = [] }) {
  g.git(['add', '--', ...touchTop]);
  const staged = g.git(['diff', '--cached', '--name-only']).split('\n').filter(Boolean);
  const rogue = staged.filter((p) => !touchTop.includes(p));
  if (rogue.length) throw new Error(`staged paths outside touchset at commit time: ${rogue.join(', ')} — refusing to commit`);
  const commitType = packet.action === 'preserve_refactor' || packet.action === 'idealize_rewrite' ? 'refactor' : 'chore';
  const msg = `${commitType}(${packet.subsystem}): ${packet.plan.transform_class} [hone ${id}]\n\n${pipelineLabel} verdict=PASS${confidence != null ? ` (confidence ${confidence})` : ''}, revisions=${revisionCount}.\nEvidence: ${packet.evidence_required.length} rung(s) green at baseline and post-change (receipts: ${receiptsDirRel}/).`;
  g.git(['-c', `user.name=${AUTHOR_NAME}`, '-c', `user.email=${AUTHOR_EMAIL}`, 'commit', '-q',
    `--author=${AUTHOR_NAME} <${AUTHOR_EMAIL}>`, '-m', msg]);
  const commit = g.git(['rev-parse', 'HEAD']);
  // allowedLeftover: batch lands commit one order at a time — the OTHER orders' not-yet-
  // committed touchset files may legitimately remain dirty. Default [] = any leftover
  // throws (single-order discipline unchanged).
  const leftover = flatPaths(dirtyEntries(g)).filter((p) => !allowedLeftover.includes(p));
  if (leftover.length) throw new Error(`tree not clean after landing commit: ${leftover.join(', ')}`);
  return commit;
}

/** the landed-path claim set: behavior_preserved + judged_design_claim (+ measured-cc verified_fact). */
export function buildLandClaims({ packet, id, reasoning, judgeProvider, receiptLines, receiptsDirRel }) {
  const claims = [
    {
      type: 'behavior_preserved',
      statement: `all ${packet.evidence_required.length} evidence_required rung(s) for ${id} green at baseline and post-change (${packet.evidence_required.map((r) => r.rung).join(', ')})`,
      evidence: packet.evidence_required.map((r) => ({ command: r.command, output_digest: receiptLines.filter((l) => l.includes(`] ${r.rung}:`)).pop() ?? `see ${receiptsDirRel}/` })),
    },
    {
      type: 'judged_design_claim',
      statement: `independent judge PASS: ${reasoning}`,
      judge: { provider: judgeProvider, verdict: 'PASS' },
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
  return claims;
}

// ---------------------------------------------------------------- the executor

export async function executeWork(opts, deps) {
  const { id, repoRoot, makerName, judgeName } = opts;
  const refuse = (reason) => ({
    outcome: 'refused', exitCode: 2,
    summary: `hone work — ${id}: REFUSED (no side effects)\n  ${reason}`,
  });
  // pure gates first (no lock, no filesystem writes)
  if (!PROVIDERS.includes(makerName)) return refuse(`unknown maker provider '${makerName}' (known: ${PROVIDERS.join(', ')})`);
  if (!PROVIDERS.includes(judgeName)) return refuse(`unknown judge provider '${judgeName}' (known: ${PROVIDERS.join(', ')})`);
  if (makerName === judgeName) return refuse(`maker == judge ('${makerName}') — non-negotiable #1: the producer of a change cannot certify it`);

  let lock;
  try { lock = acquireWorkLock(repoRoot, id, deps.log); }
  catch (e) { return refuse(`cannot acquire packet lock: ${e.message}`); }
  if (!lock.ok) return refuse(lock.reason);

  // engine-iteration-3 fix (killed-work incident): a SIGTERM/SIGINT mid-run must not
  // strand maker residue + an in_progress packet. `signal.arm` is installed by the
  // attempt once revert/terminalize machinery exists; before that there is nothing to
  // clean (refusals are side-effect-free), so teardown is just lock release + exit.
  const signal = { arm: null };
  const onSignal = (sig) => {
    try { deps.log(`hone work — ${id}: caught ${sig} — best-effort revert + blocked + ledger, then exit`); } catch { /* ignore */ }
    try { signal.arm?.(sig); }
    catch (e) { try { deps.log(`signal teardown failed: ${e.message} — manual cleanup may be required`); } catch { /* ignore */ } }
    lock.release();
    process.exit(1);
  };
  const onTerm = () => onSignal('SIGTERM');
  const onInt = () => onSignal('SIGINT');
  process.on('SIGTERM', onTerm);
  process.on('SIGINT', onInt);
  try {
    return await executeWorkAttempt(opts, deps, signal);
  } finally {
    process.off('SIGTERM', onTerm);
    process.off('SIGINT', onInt);
    lock.release();
  }
}

async function executeWorkAttempt(opts, deps, signal) {
  const { id, repoRoot, makerName, judgeName, dryRun } = opts;
  const startedAt = Date.now();
  const log = deps.log;
  const refuse = (reason) => ({
    outcome: 'refused', exitCode: 2,
    summary: `hone work — ${id}: REFUSED (no side effects)\n  ${reason}`,
  });

  // ---- 1. load + gate (fail-closed; refusals have NO side effects) ----
  let loaded;
  try { loaded = loadPacket(repoRoot, id); }
  catch (e) { return refuse(e.message); }
  const { packet, path: packetPath, rawText } = loaded;

  const schemaErrs = validatePacket(packet, { repoDir: repoRoot, warn: (m) => log(`  WARNING (validator): ${m}`) }); // repoDir enables the touchset-path + shared-DB lints
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
  // touchset entries normalized to git-root-relative — used by every comparison below
  const touchTop = packet.touchset.map((p) => normalizeTouchEntry(g, repoRoot, p));

  const dirty = dirtyEntries(g);
  if (dirty.length) {
    const inTouch = flatPaths(dirty).filter((p) => touchTop.includes(p));
    return refuse(`target git tree is dirty (${flatPaths(dirty).length} path(s), full git-root scope): ${flatPaths(dirty).slice(0, 10).join(', ')}` +
      (inTouch.length ? `\n  DIRTY TOUCHSET FILES: ${inTouch.join(', ')} — refusing: baseline would be unattributable` : ''));
  }

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
  const receiptSlices = [];      // parallel bounded output tails — judge context only, never the packet
  const receiptMeta = [];        // parallel {phase, pass} — drives the judge-evidence slice budget
  const makerMetas = [];
  const judgeMetas = [];
  let revisionCount = 0;
  let judgeResult = null;

  // judge-facing slices exclude the engine's own quality/ state (fix 1); receipts on
  // disk keep the RAW output — the filter shapes judge context, never the record
  const stripCtx = { qualityRel: g.topRel('quality'), candidateId: id };
  const writeReceipt = (phase, i, rung, res, verdict, executedCommand = undefined) => {
    const r = writeRungReceipt({ repoRoot, receiptsDirRel, id, phase, index: i, rung, res, verdict, stripCtx, executedCommand });
    receiptLines.push(r.line);
    receiptSlices.push(r.slice);
    receiptMeta.push(r.meta);
    return r.digest;
  };
  // portable rung execution: authored-worktree absolute paths are rewritten into THIS
  // tree (or the rung is refused fail-closed) — a rung must never measure another repo
  const execRung = makeRungExecutor({ gitRoot: g.gitRoot, repoRoot, prefix: g.prefix, log });

  let makerBriefCount = 0;
  /**
   * engine-iteration-4 fix 2 (run-2 finding: maker briefs were never persisted, so
   * retry-context claims were unverifiable on disk): per maker invocation, write
   * quality/receipts/<id>/maker-brief-<attempt>.digest.txt with the first ~4KB plus
   * the sha256 of the FULL brief — auditability without huge files. Written BEFORE
   * the maker runs so even a crashed/timed-out attempt leaves its brief on disk.
   */
  const persistBriefDigest = (briefText) => {
    persistMakerBriefDigest({ repoRoot, receiptsDirRel, id, attempt: ++makerBriefCount, briefText });
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

  /** the single terminal writer: packet outcome + claims + cost, every path, no exceptions
   * (shared spine: writeTerminal — wall_time_s is ALWAYS real engine wall; provider-never-ran
   * cost/tokens are a known 0, not an unknown null — dogfood packet 5). */
  const terminalize = ({ status, commit = null, skipReason = null, blockedOn = null, judgeVerdict = null, lesson = null, claims, headline }) =>
    writeTerminal({
      repoRoot, id, packet, packetPath, startedAt,
      makerName, judgeName, makerRan: makerMetas.length > 0, judgeRan: judgeMetas.length > 0,
      tokens: tokensOf(), revisionCount, judgeResult, receiptLines,
      status, commit, skipReason, blockedOn, judgeVerdict, lesson, claims, headline,
    });

  // arm the signal teardown NOW — from the in_progress write onward a kill would
  // otherwise strand maker residue + a stuck in_progress packet (fix 3).
  signal.arm = (sig) => {
    try { revertAll(g); } catch { /* best-effort — tree may be mid-write */ }
    terminalize({
      status: 'blocked',
      blockedOn: `terminated by signal (${sig}) — engine teardown, not a packet fact`,
      lesson: 'work process terminated by signal; changes (if any) reverted best-effort; reset status to pending to retry',
      claims: [
        { type: 'uncertainty', statement: `hone work ${id} terminated by ${sig} before a terminal gate decision; working tree reverted best-effort` },
        { type: 'remaining_work', statement: `packet ${id} blocked on signal termination; reset to pending to retry` },
      ],
      headline: `terminated by signal ${sig} — reverted (best-effort) + blocked`,
    });
  };

  // ---- 3. mark in_progress, then GREEN BASELINE ----
  packet.status = 'in_progress';
  writePacket(packetPath, packet);
  log(`hone work — ${id}: baseline (${packet.evidence_required.length} rung(s))`);

  const baselineRes = [];
  try {
    for (const rung of packet.evidence_required) {
      if (isCompareVsHead(rung.command)) {
        log(`  WARNING [${rung.rung}]: command matches a compare-vs-HEAD pattern (git diff --exit-code / check:generated) — pre-land evidence must be satisfiable in a dirty working tree; this rung is structurally unwinnable before commit (warning only; see README "Authoring evidence rungs")`);
      }
    }
    for (const [i, rung] of packet.evidence_required.entries()) {
      log(`  [baseline] ${rung.rung}: ${rung.command}`);
      const { res, verdict, executedCommand } = await execRung(rung, 'baseline');
      const digest = writeReceipt('baseline', i, rung, res, verdict, executedCommand);
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
    // L1 model selection: registry (models.json) + policy (routing.json) resolved once;
    // selectAgent is THE deterministic runtime chooser (two-strike escalation rides
    // revisionCount; quota state is an optional env input, honest-null otherwise).
    // Routing failure falls back to the provider's explicit defaults (routing is an
    // economics lever, not a safety gate — today's behavior is the safe floor).
    let routingCtx = null;
    try {
      const registry = loadRegistry();
      const policy = loadRouting(undefined, registry);
      routingCtx = { cls: resolveRoutingClass(packet, policy), registry, policy };
    } catch (e) { log(`  WARNING (routing): ${e.message} — provider default model/effort will be used`); }
    const makerCwd = makerName === 'codex' && touchsetLeavesRepoRoot(g, touchTop) ? g.gitRoot : repoRoot;
    const makerPacket = makerCwd === g.gitRoot
      ? { ...parseYaml(rawText), touchset: touchTop }
      : packet;
    const makerRawText = makerCwd === g.gitRoot ? stringifyYaml(makerPacket) : rawText;
    const makerCwdNote = makerCwd === g.gitRoot
      ? 'Path-coordinate note: Codex is running from the git root so its workspace-write sandbox covers the whole repository. The touchset in this brief has been rewritten to git-root-relative paths; follow the Binding rules touchset over any stale relative path prose in plan.instruction.'
      : null;
    const makerOpts = () => {
      const base = { cwd: makerCwd, timeoutMs: MAKER_TIMEOUT_MS };
      if (!routingCtx) return base;
      try {
        const quotaState = process.env.HONE_QUOTA_STATE ? JSON.parse(process.env.HONE_QUOTA_STATE) : null;
        const sel = selectAgent(routingCtx.cls, revisionCount, quotaState, routingCtx.registry, routingCtx.policy, { providerFilter: makerName });
        for (const n of sel.notes) log(`  routing note: ${n}`);
        log(`  maker selection (class=${routingCtx.cls}, strikes=${revisionCount}): ${sel.provider}:${sel.model}@${sel.effort}`);
        return { ...base, model: sel.model, effort: sel.effort };
      } catch (e) {
        log(`  WARNING (selectAgent): ${e.message} — provider default model/effort will be used`);
        return base;
      }
    };
    const brief = makerBrief(makerRawText, makerPacket, { cwdNote: makerCwdNote });
    log(`  maker: ${makerName} (timeout ${Math.round(MAKER_TIMEOUT_MS / 60000)}m)`);
    persistBriefDigest(brief);
    let makerRun;
    try {
      makerRun = await deps.maker(makerName, brief, makerOpts());
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
      const noDiffEvidence = [{ command: 'git status --porcelain=v1 -uall', output_digest: '(empty — no changes anywhere in the git root outside quality/)' }];
      const mv = parseMakerVerdict(makerRun.text);
      if (mv?.kind === 'validated-non-defect') {
        const why = headClip(mv.rationale, 240);
        return terminalize({
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
        return terminalize({
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
      return terminalize({
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
    if (violations.length) {
      revertAll(g);
      return terminalize({
        status: 'skipped',
        skipReason: `touchset-violation: maker modified ${violations.join(', ')} outside touchset [${touchTop.join(', ')}] (both git-root-relative); ALL changes reverted`,
        lesson: `maker (${makerName}) violated the touchset; brief forbids it explicitly — treat as provider reliability signal`,
        claims: [
          { type: 'verified_fact', statement: `maker (${makerName}) modified files outside the packet touchset: ${violations.join(', ')}; everything reverted, nothing landed`, evidence: [{ command: 'git status --porcelain=v1 -uall', output_digest: `changed=[${changed.join(', ')}] touchset=[${touchTop.join(', ')}]` }] },
          { type: 'remaining_work', statement: `packet ${id} unexecuted after touchset violation; reset to pending to retry` },
        ],
        headline: `touchset violation: ${violations.join(', ')} — reverted`,
      });
    }

    // ---- 6. deterministic oracle (≤1 maker revision cycle) ----
    const runOracle = async (phase) => {
      for (const [i, rung] of packet.evidence_required.entries()) {
        log(`  [${phase}] ${rung.rung}: ${rung.command}`);
        const { res, verdict, executedCommand } = await execRung(rung, 'post', baselineRes[i]);
        const digest = writeReceipt(phase, i, rung, res, verdict, executedCommand);
        if (!verdict.pass) return { green: false, rung, verdict, res, digest };
      }
      return { green: true };
    };

    const reverted = (failNote, claims, headline, lesson) => {
      revertAll(g);
      return terminalize({ status: 'reverted', lesson, claims, headline, judgeVerdict: failNote.judgeVerdict ?? null });
    };

    let oracle = await runOracle('post');
    if (!oracle.green) {
      revisionCount++;
      log(`  oracle RED at '${oracle.rung.rung}' — one maker revision cycle`);
      const failureNote = `deterministic oracle rung '${oracle.rung.rung}' FAILED: ${oracle.verdict.reason}\ncommand: ${oracle.rung.command}\nexpect: ${oracle.rung.expect}\noutput tail:\n${tailClip(oracle.res.output, 4000)}`;
      try {
        const revBrief = revisionBrief(brief, failureNote, g.git(['diff', '--', ...touchTop]));
        persistBriefDigest(revBrief);
        const rev = await deps.maker(makerName, revBrief, makerOpts());
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
            { type: 'verified_fact', statement: `revision maker call modified files outside touchset: ${violations.join(', ')}; everything reverted`, evidence: [{ command: 'git status --porcelain=v1 -uall', output_digest: `changed=[${changed.join(', ')}]` }] },
            { type: 'remaining_work', statement: `packet ${id} unexecuted after revision touchset violation` },
          ],
          headline: `touchset violation in revision — reverted`,
        });
      }
      oracle = await runOracle('post-r1');
      if (!oracle.green) {
        return reverted({}, [
          { type: 'verified_fact', statement: `evidence rung '${oracle.rung.rung}' still failing after 1 maker revision (${oracle.verdict.reason}); all changes reverted, nothing landed`, evidence: [{ command: oracle.rung.command, output_digest: oracle.digest }] },
          { type: 'remaining_work', statement: `packet ${id} reverted with a red oracle at '${oracle.rung.rung}'; needs a different approach or a better instruction` },
        ], `oracle red after revision: '${oracle.rung.rung}' ${oracle.verdict.reason}`, `transform failed its own evidence ladder at '${oracle.rung.rung}' — prior for ${packet.batch_key} down`);
      }
    }

    // ---- 7. independent judge (maker ≠ judge; ≤1 REVISE cycle) ----
    const buildDiff = () => buildWorkingDiff(g, touchTop);
    const evidenceText = () => buildCurrentJudgeEvidence(receiptLines.map((line, i) => ({ line, slice: receiptSlices[i], ...receiptMeta[i] })));
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
        const revBrief = revisionBrief(brief, `independent judge (${judgeName}) verdict REVISE: ${verdict.reasoning}`, buildDiff());
        persistBriefDigest(revBrief);
        const rev = await deps.maker(makerName, revBrief, makerOpts());
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
            { type: 'verified_fact', statement: `judge-revision maker call modified files outside touchset: ${violations.join(', ')}; everything reverted`, evidence: [{ command: 'git status --porcelain=v1 -uall', output_digest: `changed=[${changed.join(', ')}]` }] },
            { type: 'remaining_work', statement: `packet ${id} unexecuted after judge-revision touchset violation` },
          ],
          headline: 'touchset violation in judge-revision — reverted',
        });
      }
      oracle = await runOracle('post-r2');
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
    const commit = landCommit(g, {
      packet, id, touchTop, receiptsDirRel,
      pipelineLabel: `hone work: maker=${makerName} judge=${judgeName}`,
      confidence: verdict.confidence, revisionCount,
    });
    const claims = buildLandClaims({ packet, id, reasoning: verdict.reasoning, judgeProvider: judgeName, receiptLines, receiptsDirRel });
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

const ST_OUTSIDE = 'export const KINDS = ["owner", "client"];\n'; // out-of-subtree fixture content

/** with `subdir`, the fixture becomes a monorepo: --repo = <root>/<subdir>, plus a tracked
 * file OUTSIDE that subtree (packages/contract/index.ts) — the packet-9 shape. */
function stRepo(id, { branch = 'quality-sweep', packetOverrides = {}, subdir = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'hone-selftest-'));
  const run = (args) => {
    const r = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
    if (r.status !== 0) throw new Error(`selftest git ${args.join(' ')}: ${r.stderr}`);
  };
  run(['init', '-q']);
  run(['symbolic-ref', 'HEAD', `refs/heads/${branch}`]);
  run(['config', 'user.email', 'selftest@example.com']);
  run(['config', 'user.name', 'Self Test']);
  const repoRoot = subdir ? join(root, subdir) : root;
  mkdirSync(join(repoRoot, 'src'), { recursive: true });
  writeFileSync(join(repoRoot, 'src/util.js'), ST_ORIG);
  writeFileSync(join(repoRoot, 'test.js'), ST_TEST);
  writeFileSync(join(repoRoot, 'README.md'), '# selftest fixture\n');
  if (subdir) {
    mkdirSync(join(root, 'packages/contract'), { recursive: true });
    writeFileSync(join(root, 'packages/contract/index.ts'), ST_OUTSIDE);
  }
  run(['add', '-A']);
  run(['commit', '-q', '-m', 'init fixture']);
  const packet = stBasePacket(id, packetOverrides);
  assertValidPacket(packet, `selftest fixture ${id}`);
  mkdirSync(join(repoRoot, 'quality/packets'), { recursive: true });
  writeFileSync(join(repoRoot, 'quality/packets', `${id}.yaml`), stringifyYaml(packet));
  return root;
}

function stMockDeps(script, log) {
  let m = 0;
  let j = 0;
  const state = { makerCalls: 0, judgeCalls: 0, judgeArgs: [], makerPrompts: [], makerOpts: [] };
  return {
    state,
    deps: {
      // a scripted maker is a function(cwd) (may be async — e.g. the lock test's slow
      // maker), the string 'ERROR', or {run, text} when the scenario needs to control
      // the maker's reply text (HONE-VERDICT parsing).
      maker: async (name, prompt, { cwd }) => {
        state.makerCalls++;
        state.makerPrompts.push(prompt);
        state.makerOpts.push({ cwd });
        const entry = script.makers?.[m++];
        if (!entry) throw Object.assign(new Error('mock maker: no scripted call left'), { kind: 'mock-exhausted' });
        if (entry === 'ERROR') throw Object.assign(new Error('scripted maker failure'), { kind: 'timeout' });
        const fn = typeof entry === 'function' ? entry : entry.run;
        await fn(cwd);
        const text = typeof entry === 'function' ? 'mock maker done' : (entry.text ?? 'mock maker done');
        return { text, meta: { provider: name, model: 'mock', durationMs: 1, costUsd: 0.01, tokens: { input: 100, output: 50 } } };
      },
      judge: async (name) => ({
        name,
        judge: async (args) => {
          state.judgeCalls++;
          state.judgeArgs.push(args);
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
    return r.stdout.split('\n').filter((l) => l && !/(^|\/)quality\//.test(l.slice(3))).length === 0;
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
    const c0 = costs(root)[0];
    check('blocked cost: cost_usd 0 (not null) when no provider ran', c0.cost_usd === 0, JSON.stringify(c0));
    check('blocked cost: tokens 0 (not null) when no provider ran', c0.tokens_in === 0 && c0.tokens_out === 0);
    check('blocked cost: wall_time_s recorded', typeof c0.wall_time_s === 'number' && c0.wall_time_s >= 0);
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

  await scenario('maker no-diff, no HONE-VERDICT → generic skip (fail-safe fallback)', async (check) => {
    const root = stRepo(ID);
    const { deps } = stMockDeps({ makers: [() => {}] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('skipped(maker-no-diff)', r.outcome === 'skipped' && /maker-no-diff/.test(p.outcome.skip_reason ?? ''), r.summary);
    check('retry remaining_work claim kept (fallback behavior)', claims(root).some((c) => c.type === 'remaining_work' && /reset to pending/.test(c.statement)));
    check('ledgers written', claims(root).length >= 1 && costs(root).length === 1);
    check('cost reflects the maker call that DID run', costs(root)[0]?.cost_usd === 0.01 && costs(root)[0]?.tokens_in === 100);
  });

  await scenario('maker no-diff + HONE-VERDICT validated-non-defect → honest permanent close, NO retry claim', async (check) => {
    const root = stRepo(ID);
    const text = 'Checked the enum at the named site.\nHONE-VERDICT: validated-non-defect — union enum already includes the value; packet premise is a non-defect';
    const { deps } = stMockDeps({ makers: [{ run: () => {}, text }] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('skipped', r.outcome === 'skipped' && r.exitCode === 1, r.summary);
    check('skip_reason = validated-non-defect(rationale)', /^validated-non-defect\(union enum already includes/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? '');
    check('closing verified_fact carries the verdict + rationale', claims(root).some((c) => c.type === 'verified_fact' && /validated-non-defect/.test(c.statement) && /union enum already includes/.test(c.statement)), JSON.stringify(claims(root)));
    check('NO remaining_work retry claim (permanent close, not a retry)', !claims(root).some((c) => c.type === 'remaining_work'), JSON.stringify(claims(root).map((c) => c.type)));
    check('cost written', costs(root)[0]?.outcome === 'skipped');
  });

  await scenario('maker no-diff + HONE-VERDICT unactionable → retry claim carries the why', async (check) => {
    const root = stRepo(ID);
    const text = 'I could not do this.\nHONE-VERDICT: unactionable — plan.instruction names a function that does not exist in src/util.js';
    const { deps } = stMockDeps({ makers: [{ run: () => {}, text }] }, log);
    const r = await exec(root, deps);
    const p = packetOnDisk(root);
    check('skipped', r.outcome === 'skipped', r.summary);
    check('skip_reason = unactionable(why)', /^unactionable\(plan\.instruction names a function/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? '');
    check('remaining_work retry claim present with the why', claims(root).some((c) => c.type === 'remaining_work' && /unactionable/.test(c.statement) && /reset to pending/.test(c.statement)), JSON.stringify(claims(root)));
  });

  await scenario('HONE-VERDICT parsing: forms, separators, last-wins, unparseable → null', async (check) => {
    check('validated-non-defect em-dash', deepEqual(parseMakerVerdict('analysis…\nHONE-VERDICT: validated-non-defect — enum already contains mcp_package'), { kind: 'validated-non-defect', rationale: 'enum already contains mcp_package' }));
    check('unactionable double-hyphen', parseMakerVerdict('HONE-VERDICT: unactionable -- file does not exist')?.kind === 'unactionable');
    check('single-hyphen rationale captured', parseMakerVerdict('HONE-VERDICT: unactionable - file does not exist')?.rationale === 'file does not exist');
    check('colon separator ok', parseMakerVerdict('HONE-VERDICT: validated-non-defect: already correct')?.rationale === 'already correct');
    check('last match wins', parseMakerVerdict('HONE-VERDICT: unactionable — first take\nmore analysis\nHONE-VERDICT: validated-non-defect — actually correct')?.kind === 'validated-non-defect');
    check('no verdict line → null (generic fallback)', parseMakerVerdict('I made no changes because reasons.') === null);
    check('unknown verdict kind → null', parseMakerVerdict('HONE-VERDICT: wontfix — nah') === null);
    check('missing rationale → placeholder, still parsed', parseMakerVerdict('HONE-VERDICT: unactionable')?.rationale === '(no rationale given)');
    check('null-safe', parseMakerVerdict(null) === null && parseMakerVerdict(undefined) === null);
    const brief = makerBrief('yaml: here', stBasePacket(ID));
    check('maker brief instructs BOTH verdict lines', /HONE-VERDICT: validated-non-defect/.test(brief) && /HONE-VERDICT: unactionable/.test(brief));
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

  // ---- git-root scoping + touchset normalization (dogfood packets 8 + 9) ----
  const SUB = 'reference-impl';
  const subExec = (root, deps, extra = {}) => exec(root, deps, { repoRoot: join(root, SUB), ...extra });

  await scenario('git-root scope: out-of-subtree-ONLY edit → violation caught + fully reverted, NOT maker-no-diff', async (check) => {
    const root = stRepo(ID, { subdir: SUB });
    const editOutside = () => writeFileSync(join(root, 'packages/contract/index.ts'), ST_OUTSIDE.replace('"client"', '"client", "mcp_package"'));
    const { deps, state } = stMockDeps({ makers: [editOutside] }, log);
    const r = await subExec(root, deps);
    const p = packetOnDisk(join(root, SUB));
    check('skipped as touchset-violation, not maker-no-diff', r.outcome === 'skipped' && /touchset-violation/.test(p.outcome.skip_reason ?? '') && !/maker-no-diff/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? r.summary);
    check('violation names the out-of-subtree path', /packages\/contract\/index\.ts/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? '');
    check('out-of-subtree residue REVERTED byte-identical (the packet-9 false-claim gap)', read(root, 'packages/contract/index.ts') === ST_OUTSIDE);
    check('whole git root clean after revert', treeClean(root));
    check('judge never called', state.judgeCalls === 0);
  });

  await scenario('git-root scope: touchset edit + out-of-subtree edit → EVERYTHING reverted', async (check) => {
    const root = stRepo(ID, { subdir: SUB });
    const editBoth = (cwd) => {
      writeFileSync(join(cwd, 'src/util.js'), ST_GOOD);
      writeFileSync(join(root, 'packages/contract/index.ts'), 'export const KINDS = ["tampered"];\n');
    };
    const { deps } = stMockDeps({ makers: [editBoth] }, log);
    const r = await subExec(root, deps);
    check('skipped(touchset-violation)', r.outcome === 'skipped' && /touchset-violation/.test(packetOnDisk(join(root, SUB)).outcome.skip_reason ?? ''), r.summary);
    check('in-touchset file reverted', read(root, `${SUB}/src/util.js`) === ST_ORIG);
    check('out-of-subtree file reverted', read(root, 'packages/contract/index.ts') === ST_OUTSIDE);
    check('whole git root clean', treeClean(root));
  });

  await scenario('git-root scope: pre-existing dirt OUTSIDE the --repo subtree → preflight refuses', async (check) => {
    const root = stRepo(ID, { subdir: SUB });
    writeFileSync(join(root, 'packages/contract/index.ts'), ST_OUTSIDE + '// local mod\n');
    const r = await subExec(root, stMockDeps({}, log).deps);
    check('refused dirty (full git-root scope)', r.outcome === 'refused' && /dirty/.test(r.summary), r.summary);
    check('no ledgers', !existsSync(claimsPath(join(root, SUB))));
  });

  await scenario('touchset normalization: git-root-relative entry lands (packet-8 false-violation case)', async (check) => {
    const root = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: [`${SUB}/src/util.js`] } });
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean flattening', confidence: 0.9 }] }, log);
    const r = await subExec(root, deps);
    check('landed — identical path in two coordinate systems no longer a violation', r.outcome === 'landed', r.summary);
    check('edit committed', read(root, `${SUB}/src/util.js`) === ST_GOOD);
    check('tree clean after land', treeClean(root));
  });

  await scenario('touchset normalization: repo-relative entry still lands in a subtree repo', async (check) => {
    const root = stRepo(ID, { subdir: SUB }); // default touchset: ['src/util.js']
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean', confidence: 0.9 }] }, log);
    const r = await subExec(root, deps);
    check('landed', r.outcome === 'landed', r.summary);
    check('tree clean', treeClean(root));
  });

  await scenario('touchset normalization: legit out-of-subtree touchset entry is editable + landable', async (check) => {
    const root = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: ['src/util.js', 'packages/contract/index.ts'] } });
    const contractV2 = ST_OUTSIDE.replace('"client"', '"client", "mcp_package"');
    const editBoth = (cwd) => {
      writeFileSync(join(cwd, 'src/util.js'), ST_GOOD);
      writeFileSync(join(root, 'packages/contract/index.ts'), contractV2);
    };
    const { deps } = stMockDeps({ makers: [editBoth], judges: [{ verdict: 'PASS', reasoning: 'both files in contract', confidence: 0.9 }] }, log);
    const r = await subExec(root, deps);
    check('landed with cross-subtree touchset', r.outcome === 'landed', r.summary);
    check('out-of-subtree edit committed', read(root, 'packages/contract/index.ts') === contractV2 && treeClean(root));
  });

  await scenario('touchset normalization: ../ cross-package entry lands when diff path matches', async (check) => {
    const root = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: ['src/util.js', '../packages/contract/index.ts'] } });
    const contractV2 = ST_OUTSIDE.replace('"client"', '"client", "mcp_package"');
    const editBoth = (cwd) => {
      writeFileSync(join(cwd, 'src/util.js'), ST_GOOD);
      writeFileSync(join(root, 'packages/contract/index.ts'), contractV2);
    };
    const { deps } = stMockDeps({ makers: [editBoth], judges: [{ verdict: 'PASS', reasoning: '../ touchset normalized', confidence: 0.9 }] }, log);
    const r = await subExec(root, deps);
    check('landed with ../ cross-package touchset', r.outcome === 'landed', r.summary);
    check('cross-package edit committed', read(root, 'packages/contract/index.ts') === contractV2);
    check('tree clean', treeClean(root));
  });

  await scenario('touchset normalization: ../ cross-package entry still rejects unrelated writes', async (check) => {
    const root = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: ['src/util.js', '../packages/contract/index.ts'] } });
    const editAllowedAndStray = (cwd) => {
      writeFileSync(join(cwd, 'src/util.js'), ST_GOOD);
      writeFileSync(join(root, 'packages/contract/index.ts'), ST_OUTSIDE.replace('"client"', '"client", "mcp_package"'));
      writeFileSync(join(cwd, 'README.md'), '# selftest fixture\ntouched by maker\n');
    };
    const { deps, state } = stMockDeps({ makers: [editAllowedAndStray] }, log);
    const r = await subExec(root, deps);
    const p = packetOnDisk(join(root, SUB));
    check('skipped(touchset-violation)', r.outcome === 'skipped' && /touchset-violation/.test(p.outcome.skip_reason ?? ''), r.summary);
    check('violation names only the unrelated README path', new RegExp(`${SUB}/README\\.md`).test(p.outcome.skip_reason ?? '') && !/packages\/contract\/index\.ts outside/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? '');
    check('all maker residue reverted', read(root, `${SUB}/src/util.js`) === ST_ORIG && read(root, 'packages/contract/index.ts') === ST_OUTSIDE && treeClean(root));
    check('judge never called', state.judgeCalls === 0);
  });

  await scenario('codex maker cwd: cross-package touchset runs at git root; same-package stays at --repo', async (check) => {
    const crossRoot = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: ['src/util.js', '../packages/contract/index.ts'] } });
    const contractV2 = ST_OUTSIDE.replace('"client"', '"client", "mcp_package"');
    const editFromGitRoot = (cwd) => {
      writeFileSync(join(cwd, `${SUB}/src/util.js`), ST_GOOD);
      writeFileSync(join(cwd, 'packages/contract/index.ts'), contractV2);
    };
    const cross = stMockDeps({ makers: [editFromGitRoot], judges: [{ verdict: 'PASS', reasoning: 'codex root cwd ok', confidence: 0.9 }] }, log);
    const r1 = await subExec(crossRoot, cross.deps, { makerName: 'codex', judgeName: 'claude' });
    check('cross-package codex run landed', r1.outcome === 'landed', r1.summary);
    check('codex cwd switched to git root', cross.state.makerOpts[0]?.cwd === crossRoot, JSON.stringify(cross.state.makerOpts));
    check('brief touchset rewritten to git-root-relative paths', /Modify ONLY these files \(the touchset\): reference-impl\/src\/util\.js, packages\/contract\/index\.ts/.test(cross.state.makerPrompts[0] ?? ''), (cross.state.makerPrompts[0] ?? '').slice(0, 500));

    const sameRoot = stRepo(ID, { subdir: SUB });
    const same = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'same package', confidence: 0.9 }] }, log);
    const r2 = await subExec(sameRoot, same.deps, { makerName: 'codex', judgeName: 'claude' });
    check('same-package codex run landed', r2.outcome === 'landed', r2.summary);
    check('same-package codex cwd remains --repo', same.state.makerOpts[0]?.cwd === join(sameRoot, SUB), JSON.stringify(same.state.makerOpts));
  });

  await scenario('touchset normalization: real violation still caught with git-root-relative entries', async (check) => {
    const root = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: [`${SUB}/src/util.js`] } });
    const editUtilAndReadme = (cwd) => {
      writeFileSync(join(cwd, 'src/util.js'), ST_GOOD);
      writeFileSync(join(cwd, 'README.md'), '# selftest fixture\ntouched by maker\n');
    };
    const { deps, state } = stMockDeps({ makers: [editUtilAndReadme] }, log);
    const r = await subExec(root, deps);
    check('skipped(touchset-violation)', r.outcome === 'skipped' && /touchset-violation/.test(packetOnDisk(join(root, SUB)).outcome.skip_reason ?? ''), r.summary);
    check('violation names README, git-root-relative', new RegExp(`${SUB}/README\\.md`).test(packetOnDisk(join(root, SUB)).outcome.skip_reason ?? ''));
    check('both files reverted', read(root, `${SUB}/src/util.js`) === ST_ORIG && read(root, `${SUB}/README.md`) === '# selftest fixture\n');
    check('judge never called', state.judgeCalls === 0);
  });

  // ---- judge context: rung output slices (dogfood packet 1) ----
  await scenario('judge context: bounded rung output slices included alongside digests', async (check) => {
    const root = stRepo(ID);
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'saw the output', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    check('landed', r.outcome === 'landed', r.summary);
    const ev = state.judgeArgs[0]?.evidence ?? '';
    check('evidence includes REAL rung output (PASS 3/3), not just digests', ev.includes('PASS 3/3'), ev.slice(0, 400));
    check('evidence keeps the digest lines', /djb2=/.test(ev));
    check('evidence within the hard cap', ev.length <= 16384 + 128, `len=${ev.length}`);
  });

  await scenario('judge evidence builder: slice bounds + priority-preserving budget (never drop baseline lines / failing slices / final green)', async (check) => {
    const many = Array.from({ length: 100 }, (_, i) => `line-${i}`).join('\n');
    const s = outputSlice(many);
    check('keeps the LAST 40 lines', s.includes('line-99') && s.includes('line-60') && !s.includes('line-59\n'), s.slice(0, 80));
    check('omission marker for dropped lines', /omitted/.test(s));
    check('per-slice byte bound (tail wins)', outputSlice('x'.repeat(5000)).length <= 2048 + 32);
    const e = (line, slice, phase, pass) => ({ line, slice, phase, pass });
    const ev1 = buildJudgeEvidence([e('[post] t: cmd -> exit 0 PASS; djb2=abc', 'tail-output-here', 'post', true)]);
    check('slice attached under its digest line', ev1.includes('djb2=abc') && ev1.includes('tail-output-here'));
    check('entry without slice renders the line alone', buildJudgeEvidence([e('[post] t: digest-only', null, 'post', true)]) === '[post] t: digest-only');
    const current = currentJudgeEvidenceEntries([
      e('[baseline] t: base', 'BASE', 'baseline', true),
      e('[post] t: stale red', 'STALE-RED', 'post', false),
      e('[post-r1] t: retry green', 'RETRY-GREEN', 'post-r1', true),
    ]);
    check('current evidence entries keep baseline + latest post attempt only', current.map((x) => x.line).join('|') === '[baseline] t: base|[post-r1] t: retry green');
    // the run-4 hm-0003 shape: many rungs, big green outputs, one failing post run —
    // 8 baseline greens + 1 post FAIL + 8 post-r1 greens at ~2KB each (~34KB raw)
    const pad = (m) => `${m} ${'x'.repeat(2000 - m.length - 1)}`;
    const entries = [
      ...Array.from({ length: 8 }, (_, i) => e(`[baseline] rung${i}: cmd -> exit 0 PASS; digest-base${i}`, pad(`BASE${i}`), 'baseline', true)),
      e('[post] rung0: cmd -> exit 1 FAIL: boom; digest-postfail', pad('FAILCONTENT'), 'post', false),
      ...Array.from({ length: 8 }, (_, i) => e(`[post-r1] rung${i}: cmd -> exit 0 PASS; digest-postr1-${i}`, pad(`R1GREEN${i}`), 'post-r1', true)),
    ];
    const evBig = buildJudgeEvidence(entries);
    check('total hard-capped ~16KB', evBig.length <= 16384 + 128, `len=${evBig.length}`);
    check('EVERY digest line survives (baseline receipts at minimum as summaries)', entries.every((x) => evBig.includes(x.line.slice(0, 40))), evBig.slice(0, 200));
    check('failing run content kept', evBig.includes('FAILCONTENT'));
    check('final green run content kept', evBig.includes('R1GREEN7'));
    check('some green slices dropped with an explicit marker', /omitted — evidence budget/.test(evBig));
    check('post-phase greens dropped BEFORE baseline greens (non-final post-r1 slices all gone)', Array.from({ length: 7 }, (_, i) => `R1GREEN${i}`).every((m) => !evBig.includes(m)));
    check('at least one baseline slice retained under the freed budget', entries.slice(0, 8).some((_, i) => evBig.includes(`BASE${i} `)));
    check('no head-truncation marker (budget met by slice-dropping, not lossy truncation)', !/truncated from head/.test(evBig));
    // safety valve: must-keep content alone above the cap still hard-caps
    const pathological = buildJudgeEvidence([e('[post] r: FAIL; d', 'y'.repeat(3000), 'post', false)], 1024);
    check('pathological must-keep overflow → head-truncated to the cap (valve)', pathological.length <= 1024 + 128 && /truncated from head/.test(pathological));
  });

  await scenario('judge evidence e2e: two attempts → judge current evidence uses retry, not stale red attempt', async (check) => {
    const bigGreen = (n) => `node -e "console.log('PAD'.repeat(1500)); console.log('GREEN-RUNG-${n}')"`;
    const gate = `node -e "console.log('z'.repeat(3000)); const {clamp}=require('./src/util.js'); if(clamp(11,0,10)!==10){console.error('STALE-' + 'ATTEMPT-1-CLAMP-UPPER'); process.exit(1)} console.log('FINAL-GREEN-MARKER')"`;
    const root = stRepo(ID, {
      packetOverrides: {
        evidence_required: [
          ...Array.from({ length: 4 }, (_, i) => ({ rung: `pad-${i}`, command: bigGreen(i), expect: `prints GREEN-RUNG-${i}` })),
          { rung: 'direct-test', command: gate, expect: 'prints FINAL-GREEN-MARKER — clamp upper bound holds' },
        ],
      },
    });
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_BAD), stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'saw baseline and latest retry', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    check('landed after one oracle-red revision', r.outcome === 'landed' && costs(root)[0]?.revision_count === 1, r.summary);
    const ev = state.judgeArgs[0]?.evidence ?? '';
    check('ALL 5 baseline receipt lines reached the judge', (ev.match(/\[baseline\]/g) ?? []).length === 5, `found ${(ev.match(/\[baseline\]/g) ?? []).length}`);
    check('stale first-attempt red output excluded from current evidence', !ev.includes('STALE-ATTEMPT-1-CLAMP-UPPER') && !/\[post\] direct-test:/.test(ev), ev.slice(0, 500));
    check('retry green output reached the judge as current evidence', ev.includes('FINAL-GREEN-MARKER') && /\[post-r1\] direct-test:/.test(ev));
    check('evidence within the 16KB cap', ev.length <= 16384 + 128, `len=${ev.length}`);
    check('budget pressure was real (some slices omitted)', /omitted — evidence budget/.test(ev), `len=${ev.length}`);
  });

  // ---- engine quality/-state exclusion from judge scope evidence (engine-iteration-5 fix 1) ----
  await scenario('stripEngineQualityLines: run-5 diff-scope receipt shapes dropped, maker lines kept', async (check) => {
    const ctx = { qualityRel: 'reference-implementation/quality', candidateId: 'docs-q-0003' };
    const stat = [
      ' .../docs/generated/query-cookbook.md               |   2 +',
      ' reference-implementation/quality/AGENDA.json       | 622 ++--',
      ' .../quality/agendas/not-chosen.json                |  70 +--',
      ' .../baseline-1-fact-crosscheck.txt                 | 132 ++---',
      ' .../post-r2-2-diff-scope.txt                       |  64 +--',
      ' .../maker-brief-2.digest.txt                       |  12 +',
      ' .../docs-q-0003.yaml                               |  17 +-',
      ' .../packets/hm-awaited-order-oracle-0002.yaml      |  30 +-',
      ' .../claims.jsonl                                   |   6 +',
      ' 25 files changed, 1547 insertions(+), 1486 deletions(-)',
    ].join('\n');
    const { text, dropped } = stripEngineQualityLines(stat, ctx);
    check('maker file kept', text.includes('query-cookbook.md'));
    check('git-root-relative quality/ line dropped', !text.includes('AGENDA.json'));
    check('abbreviated .../quality/ line dropped', !text.includes('not-chosen.json'));
    check('fully-abbreviated receipt basenames dropped (no quality/ marker left on the line)', !/baseline-1-fact-crosscheck|post-r2-2-diff-scope|maker-brief-2/.test(text));
    check("candidate's own packet yaml dropped", !text.includes('docs-q-0003.yaml'));
    check("OTHER candidate's abbreviated packet yaml dropped (run-5 leak)", !text.includes('hm-awaited-order-oracle-0002.yaml'));
    check('abbreviated engine ledger basename dropped', !text.includes('claims.jsonl'));
    check('dropped count = 8', dropped === 8, `dropped=${dropped}`);
    check('note appended naming engine bookkeeping + count caveat', /engine bookkeeping/.test(text) && /8 line\(s\)/.test(text) && /COUNTS/.test(text));
    check('summary count line kept (honest — the note explains it)', text.includes('25 files changed'));
    check('maker path with quality/ mid-path NOT dropped', stripEngineQualityLines(' src/quality/util.js | 2 +', ctx).dropped === 0);
    check('porcelain quality/ line dropped (repoRoot == git root)', stripEngineQualityLines('?? quality/receipts/x/y.txt', { qualityRel: 'quality' }).dropped === 1);
    check('no engine lines → text byte-unchanged, no note', stripEngineQualityLines('all clean\nsrc/a.js | 2 +', ctx).text === 'all clean\nsrc/a.js | 2 +');
  });

  await scenario('e2e: engine quality/ writes invisible to judge scope evidence; raw receipt on disk; touchset gate unaffected (run-5 false-negative class)', async (check) => {
    const root = stRepo(ID, {
      packetOverrides: {
        evidence_required: [
          { rung: 'direct-test', command: 'node test.js', expect: 'exit 0' },
          { rung: 'diff-scope', command: 'git diff --stat', expect: 'ONLY src/util.js changed' },
        ],
      },
    });
    // make the engine's quality/ state TRACKED, like the real sweep: the in_progress
    // packet rewrite + receipt overwrites become tracked modifications that a packet's
    // own `git diff --stat` rung lists alongside the maker's work
    const stale = (p) => { mkdirSync(join(root, dirname(p)), { recursive: true }); writeFileSync(join(root, p), 'stale engine receipt\n'); };
    stale(`quality/receipts/${ID}/baseline-1-direct-test.txt`);
    stale(`quality/receipts/${ID}/baseline-2-diff-scope.txt`);
    stale(`quality/receipts/${ID}/post-1-direct-test.txt`);
    spawnSync('git', ['add', 'quality'], { cwd: root, encoding: 'utf8' });
    spawnSync('git', ['-c', 'user.email=selftest@example.com', '-c', 'user.name=Self Test', 'commit', '-q', '-m', 'track engine state'], { cwd: root, encoding: 'utf8' });
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'scope clean', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    check('landed — engine quality/ writes never trip the touchset gate', r.outcome === 'landed', r.summary);
    const ev = state.judgeArgs[0]?.evidence ?? '';
    check("judge evidence lists the maker's file in the diff-stat slice", /src\/util\.js\s[^\n]*\|/.test(ev), ev.slice(0, 400));
    check('no quality/ stat line reached the judge', !/quality\/[^\n]*\|/.test(ev));
    check('no abbreviated engine-receipt stat line reached the judge', !/direct-test\.txt[^\n]*\|/.test(ev) && !/diff-scope\.txt[^\n]*\|/.test(ev));
    check('omission note reached the judge', ev.includes('engine bookkeeping'));
    const rawReceipt = read(root, `quality/receipts/${ID}/post-2-diff-scope.txt`) ?? '';
    check('on-disk receipt keeps the RAW output (quality/ paths intact)', /quality\//.test(rawReceipt), rawReceipt.slice(0, 300));
  });

  // ---- remeasure-slice protection (engine-iteration-5 fix 3, run-5 hm-0003 clip) ----
  await scenario('judge evidence: remeasure-class slices shrink to an [engine]-head + 10-tail-line floor, never dropped', async (check) => {
    const e = (line, slice, phase, pass, rung) => ({ line, slice, phase, pass, rung });
    const pad = (m) => `${m} ${'x'.repeat(2000 - m.length - 1)}`;
    const remBody = Array.from({ length: 30 }, (_, i) => `flagged-${i} ${'y'.repeat(90)}`);
    const remSlice = ['[engine] remeasure summary (scalars…): {"file_excess":689}', ...remBody, 'TAIL-EXCESS-VALUE 689'].join('\n');
    const entries = [
      ...Array.from({ length: 6 }, (_, i) => e(`[baseline] pad${i}: cmd -> exit 0 PASS; dg-b${i}`, pad(`BASEG${i}`), 'baseline', true)),
      e('[post] file-excess-remeasure: cmd -> exit 0 PASS; dg-rem', remSlice, 'post', true, 'file-excess-remeasure'),
      ...Array.from({ length: 5 }, (_, i) => e(`[post] pad${i}: cmd -> exit 0 PASS; dg-p${i}`, pad(`POSTG${i}`), 'post', true, `pad${i}`)),
      e('[post] direct-test: cmd -> exit 0 PASS; dg-final', pad('FINALGREEN'), 'post', true, 'direct-test'),
    ];
    const ev = buildJudgeEvidence(entries, 4608);
    check('cap respected', ev.length <= 4608 + 128, `len=${ev.length}`);
    check('shrink sufficed — no pathological head truncation', !/truncated from head/.test(ev));
    check('remeasure slice NOT dropped under pressure that dropped all plain greens', !/dg-rem\n\s*\[rung output tail omitted/.test(ev) && Array.from({ length: 5 }, (_, i) => `POSTG${i}`).every((m) => !ev.includes(m)));
    check('decisive tail values present', ev.includes('TAIL-EXCESS-VALUE 689'));
    check('[engine] summary head survives the shrink', ev.includes('{"file_excess":689}'));
    check('shrunk marker present', /shrunk away — evidence budget/.test(ev));
    check('floor holds: last 10 body lines kept', ev.includes('flagged-21'), ev.slice(-600));
    check('lines above the floor gone', !ev.includes('flagged-20'));
    check('final green still kept in full', ev.includes('FINALGREEN'));
    // no budget pressure → remeasure slice intact, no shrink
    const evLoose = buildJudgeEvidence(entries, 64 * 1024);
    check('no pressure → remeasure slice intact', evLoose.includes('flagged-0') && !/shrunk away/.test(evLoose));
  });

  await scenario('e2e: remeasure rung decisive HEAD scalars reach the judge via the clip-proof summary under budget pressure', async (check) => {
    // the real hm-0003 shape: scope-fn prints its scalars (file_excess) at the HEAD of a
    // ~330-line pretty JSON; the tail-keeping 40-line slice alone loses them
    const collectorCmd = `node -e "const flagged=Array.from({length:80},(_,i)=>({fn:'f'+i,excess:4}));console.log(JSON.stringify({file:'x.js',found:true,fn_count:100,flagged_count:80,file_excess:689,flagged,red_scan:['bearer']},null,2))"`;
    const bigGreen = (n) => `node -e "console.log('PAD'.repeat(1500)); console.log('GREEN-RUNG-${n}')"`;
    const root = stRepo(ID, {
      packetOverrides: {
        evidence_required: [
          ...Array.from({ length: 4 }, (_, i) => ({ rung: `pad-${i}`, command: bigGreen(i), expect: `prints GREEN-RUNG-${i}` })),
          { rung: 'file-excess-remeasure', command: collectorCmd, expect: 'collector JSON emitted; judge verifies file_excess against the packet baseline' },
          { rung: 'direct-test', command: 'node test.js', expect: 'exit 0' },
        ],
      },
    });
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'saw the numbers', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    check('landed', r.outcome === 'landed', r.summary);
    const ev = state.judgeArgs[0]?.evidence ?? '';
    check('budget pressure was real (slices omitted)', /omitted — evidence budget/.test(ev), `len=${ev.length}`);
    check('clip-proof remeasure summary reached the judge', ev.includes('[engine] remeasure summary'), ev.slice(0, 300));
    check('decisive head scalar visible to the judge', ev.includes('"file_excess":689'));
    check('short arrays kept in the summary', ev.includes('"red_scan":["bearer"]'));
    check('huge array reduced to a count', ev.includes('"flagged_count":80') && !ev.includes(`"fn":"f79"`));
    check('evidence within the 16KB cap', ev.length <= 16384 + 128, `len=${ev.length}`);
  });

  // ---- compare-vs-HEAD rung warning (dogfood packet 9's check:generated trap) ----
  await scenario('compare-vs-HEAD rung → WARNING logged, behavior unchanged (structurally unwinnable → reverted)', async (check) => {
    const root = stRepo(ID, {
      packetOverrides: {
        evidence_required: [
          { rung: 'direct-test', command: 'node test.js', expect: 'exit 0' },
          { rung: 'generated-check', command: 'git diff --exit-code -- src/util.js', expect: 'exit 0' },
        ],
      },
    });
    const logs = [];
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD), stEditUtil(ST_GOOD)] }, (s) => logs.push(s));
    const r = await exec(root, deps);
    check('WARNING names the compare-vs-HEAD pattern', logs.some((l) => /WARNING/.test(l) && /compare-vs-HEAD/.test(l) && /generated-check/.test(l)), logs.filter((l) => /WARN/.test(l)).join(' | '));
    check('no WARNING for the honest rung', !logs.some((l) => /WARNING/.test(l) && /\[direct-test\]/.test(l)));
    check('behavior unchanged: rung red post-edit → reverted', r.outcome === 'reverted', r.summary);
    check('util.js restored', read(root, 'src/util.js') === ST_ORIG);
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

  // ---- validator lint: generate_evidence guard + touchset path (engine-iteration-3 fix 1) ----
  await scenario('validator lint: unguarded generate_evidence touchset reference REJECTED; hm- guard pattern passes', async (check) => {
    const ge = (evidence, touchset = ['test/new-oracle.test.js']) => stBasePacket(ID, {
      action: 'generate_evidence', proof_class: 'judgment_first', touchset, evidence_required: evidence,
    });
    const unguarded = ge([{ rung: 'direct-test', command: 'node --test test/new-oracle.test.js test/existing.test.js', expect: 'new file green; existing pass' }]);
    const errs = validatePacket(unguarded);
    check('unguarded rung rejected', errs.some((e) => /existence guard/.test(e)), errs.join(' | '));
    check('message cites the run-1 $0-block class + the guard pattern', errs.some((e) => /\$0/.test(e) && /if \[ -f/.test(e)));
    const guarded = ge([{ rung: 'direct-test', command: "sh -c 'if [ -f test/new-oracle.test.js ]; then node --test test/new-oracle.test.js test/existing.test.js; else node --test test/existing.test.js; fi'", expect: 'baseline (new file absent): existing green; post: new file green' }]);
    check('hm- guard pattern passes', validatePacket(guarded).length === 0, validatePacket(guarded).join(' | '));
    const fallback = ge([{ rung: 'direct-test', command: 'node --test test/new-oracle.test.js || echo oracle-not-yet-authored', expect: 'green or explicit fallback' }]);
    check('explicit || fallback passes', validatePacket(fallback).length === 0, validatePacket(fallback).join(' | '));
    const unrelated = ge([{ rung: 'typecheck', command: 'npx tsc --noEmit', expect: 'exit 0' }]);
    check('rung not referencing the touchset unaffected', validatePacket(unrelated).length === 0);
    const notGE = stBasePacket(ID, { evidence_required: [{ rung: 'direct-test', command: 'node --test src/util.js', expect: 'exit 0' }] });
    check('non-generate_evidence action not linted', validatePacket(notGE).length === 0, validatePacket(notGE).join(' | '));
    const crossCoord = ge(
      [{ rung: 'red-green-seeded-regression', command: 'cd /x && node --test test/connector-verdict-input.test.js', expect: 'red then green' }],
      ['reference-implementation/test/connector-verdict-input.test.js'],
    );
    check('basename match catches prefix-carrying touchset vs repo-relative command (rt-verdict shape)', validatePacket(crossCoord).some((e) => /existence guard/.test(e)));
  });

  await scenario('validator lint: duplicated repo-dir prefix touchset rejected with corrected path (--repo ctx)', async (check) => {
    const root = stRepo(ID, { subdir: SUB });
    const repoDir = join(root, SUB);
    const withTouch = (touchset) => stBasePacket(ID, { touchset });
    const dup = withTouch([`${SUB}/src/new-oracle.test.js`]); // exists nowhere + repo-basename prefix
    const errs = validatePacket(dup, { repoDir });
    check('duplicated prefix rejected', errs.some((e) => /duplicated repo-dir prefix/.test(e)), errs.join(' | '));
    check('corrected path suggested', errs.some((e) => /Corrected path: 'src\/new-oracle\.test\.js'/.test(e)));
    check('same packet WITHOUT ctx passes (best-effort: --repo unknown at bare validation)', validatePacket(dup).length === 0, validatePacket(dup).join(' | '));
    check('repo-relative existing entry passes', validatePacket(withTouch(['src/util.js']), { repoDir }).length === 0);
    check('git-root-relative EXISTING entry passes (legit cross-coordinate, packet-8 class)', validatePacket(withTouch([`${SUB}/src/util.js`]), { repoDir }).length === 0);
    check('to-be-created file in an existing dir passes', validatePacket(withTouch(['src/new-oracle.test.js']), { repoDir }).length === 0);
    check('path resolving nowhere rejected', validatePacket(withTouch(['no-such-dir/deep/file.js']), { repoDir }).some((e) => /neither --repo/.test(e)));
    check('absolute entry rejected', validatePacket(withTouch(['/abs/path.js']), { repoDir }).some((e) => /absolute/.test(e)));
    // the work gate threads --repo: a corrupted-touchset packet is REFUSED before any side effect
    const root2 = stRepo(ID, { subdir: SUB, packetOverrides: { touchset: [`${SUB}/src/new-oracle.test.js`] } });
    const r = await subExec(root2, stMockDeps({}, log).deps);
    check('work gate refuses the corrupted touchset with the corrected path', r.outcome === 'refused' && /duplicated repo-dir prefix/.test(r.summary), r.summary);
    check('refusal suggests the corrected path', /Corrected path: 'src\/new-oracle\.test\.js'/.test(r.summary));
  });

  // ---- per-packet lockfile (engine-iteration-3 fix 2: the batch-2a race) ----
  await scenario('work lock: second concurrent invocation refused (live lock); first unaffected; lock released', async (check) => {
    const root = stRepo(ID);
    let releaseMaker; const gate = new Promise((r) => { releaseMaker = r; });
    let makerStarted; const started = new Promise((r) => { makerStarted = r; });
    const slow = async (cwd) => { makerStarted(); await gate; writeFileSync(join(cwd, 'src/util.js'), ST_GOOD); };
    const { deps } = stMockDeps({ makers: [slow], judges: [{ verdict: 'PASS', reasoning: 'clean', confidence: 0.9 }] }, log);
    const p1 = exec(root, deps); // in flight — holds the lock
    await started;
    check('lock file exists while running', existsSync(join(root, 'quality/.locks', `${ID}.lock`)));
    const r2 = await exec(root, stMockDeps({}, log).deps);
    check('second concurrent invocation refused exit 2', r2.outcome === 'refused' && r2.exitCode === 2, r2.summary);
    check('refusal names the live lock holder pid', /LOCKED by a live/.test(r2.summary) && r2.summary.includes(String(process.pid)), r2.summary);
    releaseMaker();
    const r1 = await p1;
    check('first invocation unaffected — landed', r1.outcome === 'landed', r1.summary);
    check('lock released after completion', !existsSync(join(root, 'quality/.locks', `${ID}.lock`)));
  });

  await scenario('work lock: stale lock (dead pid / unparseable) broken with a logged note', async (check) => {
    const root = stRepo(ID);
    const lockPath = join(root, 'quality', '.locks', `${ID}.lock`);
    mkdirSync(dirname(lockPath), { recursive: true });
    const deadPid = spawnSync(process.execPath, ['-e', '']).pid; // exited → dead
    writeFileSync(lockPath, JSON.stringify({ pid: deadPid, started: '2026-01-01T00:00:00Z' }) + '\n');
    const logs = [];
    const { deps } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean', confidence: 0.9 }] }, (s) => logs.push(s));
    const r = await exec(root, deps);
    check('stale lock broken — run proceeds to landed', r.outcome === 'landed', r.summary);
    check('break logged with the dead pid', logs.some((l) => /STALE lock/.test(l) && l.includes(String(deadPid))), logs.join(' | '));
    check('lock released after completion', !existsSync(lockPath));
    writeFileSync(lockPath, 'not json at all\n'); // unparseable = stale too
    const logs2 = [];
    const r2 = await exec(root, stMockDeps({}, (s) => logs2.push(s)).deps);
    check('unparseable lock broken (then refused on terminal status, lock-independent)', r2.outcome === 'refused' && /terminal/.test(r2.summary), r2.summary);
    check('unparseable break logged', logs2.some((l) => /STALE lock/.test(l) && /unparseable/.test(l)), logs2.join(' | '));
    check('lock released after refusal', !existsSync(lockPath));
  });

  // ---- SIGTERM trap (engine-iteration-3 fix 3: the killed-work incident) ----
  await scenario('SIGTERM mid-maker → best-effort revert + blocked(terminated by signal) + ledger + lock released', async (check) => {
    const root = stRepo(ID);
    const dir = mkdtempSync(join(tmpdir(), 'hone-sigterm-'));
    const driver = join(dir, 'driver.mjs');
    writeFileSync(driver, [
      `import { executeWork } from ${JSON.stringify(import.meta.url)};`,
      "import { writeFileSync } from 'node:fs';",
      "import { join } from 'node:path';",
      'const deps = {',
      '  maker: async (name, prompt, { cwd }) => {',
      `    writeFileSync(join(cwd, 'src/util.js'), ${JSON.stringify(ST_GOOD)});`,
      "    process.stdout.write('MAKER-STARTED\\n');",
      '    await new Promise((r) => setTimeout(r, 60000)); // slow mode: hold mid-maker',
      "    return { text: 'mock maker done', meta: { provider: name, model: 'mock', durationMs: 1, costUsd: 0.01, tokens: { input: 1, output: 1 } } };",
      '  },',
      "  judge: async (name) => ({ judge: async () => ({ verdict: 'PASS', reasoning: 'n/a', confidence: 1, raw: { attempts: [] } }) }),",
      '  log: () => {},',
      '};',
      `const r = await executeWork({ id: ${JSON.stringify(ID)}, repoRoot: ${JSON.stringify(root)}, makerName: 'claude', judgeName: 'codex', dryRun: false }, deps);`,
      'process.exit(r.exitCode);',
    ].join('\n'));
    const child = spawn(process.execPath, [driver], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    await new Promise((resolveP, rejectP) => {
      const t = setTimeout(() => rejectP(new Error(`maker never started; output: ${out.slice(0, 400)}`)), 20000);
      child.stdout.on('data', () => { if (out.includes('MAKER-STARTED')) { clearTimeout(t); resolveP(); } });
      child.once('exit', (code) => { clearTimeout(t); rejectP(new Error(`child exited early (${code}); output: ${out.slice(0, 400)}`)); });
    });
    check('maker residue exists pre-signal (the incident precondition)', read(root, 'src/util.js') === ST_GOOD);
    child.kill('SIGTERM');
    const exitCode = await new Promise((resolveP, rejectP) => {
      const t = setTimeout(() => { child.kill('SIGKILL'); rejectP(new Error('child did not exit after SIGTERM')); }, 20000);
      child.removeAllListeners('exit');
      child.once('exit', (code) => { clearTimeout(t); resolveP(code); });
    });
    const p = packetOnDisk(root);
    check('child exited 1 (terminal non-landed)', exitCode === 1, `exit=${exitCode}; output: ${out.slice(0, 400)}`);
    check('maker residue reverted — util.js restored byte-identical', read(root, 'src/util.js') === ST_ORIG);
    check('whole tree clean', treeClean(root));
    check('packet blocked, NOT stranded in_progress', p.status === 'blocked', p.status);
    check('blocked_on names the signal', /terminated by signal \(SIGTERM\)/.test(p.outcome.blocked_on ?? ''), p.outcome.blocked_on ?? '');
    check('ledger cost line written outcome=blocked', costs(root).length === 1 && costs(root)[0].outcome === 'blocked', JSON.stringify(costs(root)));
    check('ledger claim names the termination', claims(root).some((c) => /terminated by SIGTERM/.test(c.statement)), JSON.stringify(claims(root).map((c) => c.statement)));
    check('lock released by the signal path', !existsSync(join(root, 'quality/.locks', `${ID}.lock`)));
    rmSync(dir, { recursive: true, force: true });
  });

  // ---- retry-context threading (engine-iteration-3 fix 4) ----
  await scenario('retry context: resets + prior outcome → maker brief carries the PRIOR ATTEMPT section (bounded)', async (check) => {
    const resets = [{ at: new Date().toISOString(), from_status: 'reverted', reason: 'retry with judge feedback' }];
    const outcome = {
      commit: null, skip_reason: null, blocked_on: null,
      judge_verdict: 'codex REVISE (confidence 0.6): name the guard intent explicitly in a comment',
      evidence_receipts: [], tokens_actual: 1234, lesson: 'transform failed its own evidence ladder',
    };
    const brief = makerBrief('yaml: here', stBasePacket(ID, { resets, outcome }));
    check('section present', brief.includes("== PRIOR ATTEMPT — the judge's exact demands =="), brief.slice(0, 200));
    check('judge_verdict threaded', brief.includes('name the guard intent explicitly'));
    check('lesson threaded', brief.includes('transform failed its own evidence ladder'));
    check('reset reason + from_status threaded', brief.includes('retry with judge feedback') && brief.includes('from reverted'));
    check('no resets → no section', !/PRIOR ATTEMPT/.test(makerBrief('yaml: here', stBasePacket(ID))));
    check('resets but empty prior outcome → no section', !/PRIOR ATTEMPT/.test(makerBrief('yaml: here', stBasePacket(ID, { resets }))));
    const huge = makerBrief('y', stBasePacket(ID, { resets, outcome: { ...outcome, judge_verdict: 'x'.repeat(5000) } }));
    const secLen = huge.indexOf('== WORK PACKET') - huge.indexOf('== PRIOR ATTEMPT');
    check('section hard-bounded ~2KB', secLen > 0 && secLen <= 2048 + 16, `len=${secLen}`);
    // end-to-end: a reopened packet's REAL maker prompt carries the section
    const root = stRepo(ID, { packetOverrides: { resets, outcome } });
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    check('reopened packet lands', r.outcome === 'landed', r.summary);
    check('maker PROMPT carries the section end-to-end', (state.makerPrompts[0] ?? '').includes('PRIOR ATTEMPT') && (state.makerPrompts[0] ?? '').includes('name the guard intent explicitly'));
  });

  // ---- brief-digest persistence (engine-iteration-4 fix 2) ----
  await scenario('maker brief digests: one per attempt on disk — first 4KB + sha256 of the full brief', async (check) => {
    const root = stRepo(ID);
    const { deps, state } = stMockDeps({ makers: [stEditUtil(ST_BAD), stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'clean', confidence: 0.9 }] }, log);
    const r = await exec(root, deps);
    check('landed after one revision', r.outcome === 'landed' && costs(root)[0]?.revision_count === 1, r.summary);
    const d1 = read(root, `quality/receipts/${ID}/maker-brief-1.digest.txt`);
    const d2 = read(root, `quality/receipts/${ID}/maker-brief-2.digest.txt`);
    check('digest file per maker attempt', d1 !== null && d2 !== null);
    check('no third attempt, no third file', read(root, `quality/receipts/${ID}/maker-brief-3.digest.txt`) === null);
    const sha = (s) => createHash('sha256').update(s, 'utf8').digest('hex');
    check('attempt 1 sha256 matches the FULL brief actually sent', d1?.includes(`sha256(full brief)=${sha(state.makerPrompts[0])}`), (d1 ?? '').split('\n')[1]);
    check('attempt 2 sha256 matches the revision brief actually sent', d2?.includes(`sha256(full brief)=${sha(state.makerPrompts[1])}`));
    check('attempt 1 head content is the brief head', d1?.includes(state.makerPrompts[0].slice(0, 200)));
    check('attempt 2 is the REVISION brief (retry context verifiable on disk)', d2?.includes('REVISION REQUIRED'));
    check('digest file bounded (~4KB head, not the whole brief)', (d1?.length ?? 0) < 4096 + 512, `len=${d1?.length}`);
  });

  // ---- rung-timeout process-group kill (engine-iteration-4 fix 3) ----
  await scenario('rung timeout: process-group SIGKILL — no grandchild survivors (run-2: node --test outlived spawnSync SIGKILL)', async (check) => {
    const dir = mkdtempSync(join(tmpdir(), 'hone-pgkill-'));
    const pidFile = join(dir, 'grandchild.pid');
    const cmd = `node -e "const {spawn}=require('child_process');const c=spawn('sleep',['300']);require('fs').writeFileSync('${pidFile}',String(c.pid));console.log('SPAWNED');setInterval(()=>{},1000)"`;
    const res = await runShellCmd(cmd, dir, 1500);
    check('timed out, fail-closed code null', res.timedOut === true && res.code === null, JSON.stringify({ code: res.code, timedOut: res.timedOut }));
    check('pre-kill output captured', /SPAWNED/.test(res.output), res.output.slice(0, 120));
    check('grandchild pid recorded before the kill', existsSync(pidFile));
    const gpid = Number(readFileSync(pidFile, 'utf8'));
    let alive = Number.isInteger(gpid) && gpid > 0;
    for (let i = 0; i < 40 && alive; i++) { alive = pidAlive(gpid); if (alive) await new Promise((rs) => setTimeout(rs, 50)); }
    check('grandchild DEAD after group kill (negative-pid SIGKILL)', !alive, `pid ${gpid} still alive after 2s`);
    check('normal completion unaffected', (await runShellCmd('echo fine', dir)).code === 0);
    rmSync(dir, { recursive: true, force: true });
  });

  // ---- failing_test_named expect_check (engine-iteration-4 fix 5, run-3 t1b-0012) ----
  await scenario('expect_check failing_test_named: the RIGHT test must fail, post-only', async (check) => {
    const mk = (code, stdout, output = stdout) => ({ code, timedOut: false, stdout, output, durationMs: 10 });
    const rg = (value) => ({ rung: 'red-then-green-kind-swap', expect: 'seeded run fails naming the telemetry test', expect_check: { type: 'failing_test_named', value } });
    const name = 'clamp respects upper bound';
    check('TAP not-ok line naming the test → pass', checkExpect(rg(name), mk(0, `not ok 1 - ${name}\n# fail 1`), 'post').pass);
    check('node spec reporter ✖ line → pass', checkExpect(rg('dispatched input records'), mk(0, '✖ dispatched input records wire.input.received then wire.input.dispatched (273ms)'), 'post').pass);
    check('jest ✕ line → pass', checkExpect(rg('renders header'), mk(0, '✕ renders header (12 ms)'), 'post').pass);
    check('mocha numbered failure → pass', checkExpect(rg('renders header'), mk(0, '  1) app renders header:\n     AssertionError'), 'post').pass);
    check('failure line on stderr still seen (combined output)', checkExpect(rg(name), mk(0, '', `not ok 1 - ${name}`), 'post').pass);
    check('WRONG failing test → RED (the t1b-0012 setup-409 class)', !checkExpect(rg(name), mk(0, 'not ok 1 - setup returns 409\nnot ok 2 - setup returns 409 again'), 'post').pass);
    check('RED reason names the trap', /WRONG test/.test(checkExpect(rg(name), mk(0, 'not ok 1 - setup returns 409'), 'post').reason));
    check('no failure at all → RED', !checkExpect(rg(name), mk(0, `✔ ${name}\nall green`), 'post').pass);
    check('passing ✔ line naming the test does NOT count as a failure', !checkExpect(rg(name), mk(0, `✔ ${name} (3ms)`), 'post').pass);
    check('NOT enforced at baseline (generate_evidence guards print the fallback there)', checkExpect(rg(name), mk(0, 'oracle-not-yet-authored'), 'baseline').pass);
    check('schema accepts the type', validatePacket(stBasePacket(ID, { evidence_required: [{ rung: 'x', command: 'true', expect: 'red', expect_check: { type: 'failing_test_named', value: 'n' } }] })).length === 0);
    check('schema rejects empty value', validatePacket(stBasePacket(ID, { evidence_required: [{ rung: 'x', command: 'true', expect: 'red', expect_check: { type: 'failing_test_named', value: '' } }] })).some((e) => /non-empty string required/.test(e)));
  });

  await scenario('failing_test_named e2e: wrong-test failure exits 0 → oracle RED → reverted; named failure → lands', async (check) => {
    const ec = { type: 'failing_test_named', value: 'clamp upper bound' };
    const wrong = stRepo(ID, {
      packetOverrides: {
        evidence_required: [{
          rung: 'kind-swap', command: `node -e "console.log('not ok 1 - setup returns 409')"`,
          expect: 'seeded mutation fails the clamp upper bound test', expect_check: ec,
        }],
      },
    });
    const { deps: dw, state: sw } = stMockDeps({ makers: [stEditUtil(ST_GOOD), stEditUtil(ST_GOOD)] }, log);
    const rw = await exec(wrong, dw);
    const pw = packetOnDisk(wrong);
    check('exit-0-with-wrong-failure no longer launders: reverted', rw.outcome === 'reverted' && rw.exitCode === 1, rw.summary);
    check('receipt names expect_check[failing_test_named]', pw.outcome.evidence_receipts.some((l) => /expect_check\[failing_test_named\]/.test(l)), JSON.stringify(pw.outcome.evidence_receipts.slice(-1)));
    check('judge never called (deterministic catch, not judgment)', sw.judgeCalls === 0);
    check('tree clean', treeClean(wrong));
    const right = stRepo(ID, {
      packetOverrides: {
        evidence_required: [{
          rung: 'kind-swap', command: `node -e "console.log('not ok 1 - clamp upper bound catches seeded mutation')"`,
          expect: 'seeded mutation fails the clamp upper bound test', expect_check: ec,
        }],
      },
    });
    const { deps: dr } = stMockDeps({ makers: [stEditUtil(ST_GOOD)], judges: [{ verdict: 'PASS', reasoning: 'named red observed', confidence: 0.9 }] }, log);
    const rr = await exec(right, dr);
    check('named failing test at post → landed (baseline unaffected: post-only goal)', rr.outcome === 'landed', rr.summary);
  });

  // ---- validator ctx rename + shared-DB lint (engine-iteration-4 fixes 4 + 6) ----
  await scenario('validator ctx: repoDir is the name; repoRoot accepted as deprecated alias with a warning', async (check) => {
    const root = stRepo(ID, { subdir: SUB });
    const repoDir = join(root, SUB);
    const dup = stBasePacket(ID, { touchset: [`${SUB}/src/new-oracle.test.js`] });
    check('repoDir ctx drives the path lint', validatePacket(dup, { repoDir }).some((e) => /duplicated repo-dir prefix/.test(e)));
    const warns = [];
    const errsAlias = validatePacket(dup, { repoRoot: repoDir, warn: (m) => warns.push(m) });
    check('repoRoot alias still drives the lint (accept both)', errsAlias.some((e) => /duplicated repo-dir prefix/.test(e)));
    check('alias use warns with the misuse rationale', warns.some((m) => /repoRoot is deprecated/.test(m) && /repoDir/.test(m)), warns.join(' | '));
    const warns2 = [];
    validatePacket(dup, { repoDir, warn: (m) => warns2.push(m) });
    check('repoDir use does NOT warn', warns2.length === 0, warns2.join(' | '));
    check('repoDir wins when both are passed (no warning)', (() => { const w = []; validatePacket(dup, { repoDir, repoRoot: '/nonexistent', warn: (m) => w.push(m) }); return w.length === 0; })());
  });

  await scenario('validator lint: node --test at the SHARED Postgres DB rejected; ephemeral-DB exemplar passes; missing --test-force-exit warns', async (check) => {
    const root = stRepo(ID);
    const withCmd = (command) => stBasePacket(ID, { evidence_required: [{ rung: 'direct-test', command, expect: 'green' }] });
    const shared = withCmd("cd /x && PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/pdpp node --test --test-force-exit test/foo.test.js");
    const errs = validatePacket(shared, { repoDir: root });
    check('shared-DB rung rejected', errs.some((e) => /SHARED Postgres DB/.test(e)), errs.join(' | '));
    check('rejection cites the spine-0003 per-file-ephemeral pattern + --test-force-exit', errs.some((e) => /spine-0003/.test(e) && /createdb \$db/.test(e) && /--test-force-exit/.test(e)));
    const exemplar = withCmd("sh -c 'db=pdpp_hone_t1b_x; dropdb --if-exists $db; createdb $db; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db node --test --test-force-exit test/foo.test.js; dropdb --if-exists $db'");
    const warnsEx = [];
    check('spine-0003 exemplar (ephemeral $db + force-exit) passes clean', validatePacket(exemplar, { repoDir: root, warn: (m) => warnsEx.push(m) }).length === 0 && warnsEx.length === 0, warnsEx.join(' | '));
    check('pdpp_hone-prefixed DB name is NOT the shared DB (\\b boundary)', validatePacket(withCmd('PDPP_TEST_POSTGRES_URL=postgres://u:p@h:5/pdpp_hone_x node --test --test-force-exit t.js'), { repoDir: root }).length === 0);
    const warns = [];
    const noForce = withCmd('db=x; createdb $db; PDPP_TEST_POSTGRES_URL=postgres://u:p@h:5/$db node --test test/foo.test.js');
    check('DB-backed node --test without --test-force-exit: no error…', validatePacket(noForce, { repoDir: root, warn: (m) => warns.push(m) }).length === 0);
    check('…but a warning citing the idle-hang (best-effort half)', warns.some((m) => /--test-force-exit/.test(m) && /idle-hang/.test(m)), warns.join(' | '));
    check('non-test shared-DB command (psql) not linted', validatePacket(withCmd('PDPP_TEST_POSTGRES_URL=postgres://u:p@h:5/pdpp psql -c "select 1"'), { repoDir: root }).length === 0);
    check('without ctx.repoDir the lint is off (best-effort by design)', validatePacket(shared).length === 0);
    // the work gate threads repoDir: a shared-DB packet is REFUSED before any side effect
    const root2 = stRepo(ID, { packetOverrides: shared });
    const before = read(root2, `quality/packets/${ID}.yaml`);
    const r = await exec(root2, stMockDeps({}, log).deps);
    check('work gate refuses the shared-DB rung packet', r.outcome === 'refused' && /SHARED Postgres DB/.test(r.summary), r.summary);
    check('refusal side-effect-free', read(root2, `quality/packets/${ID}.yaml`) === before && !existsSync(claimsPath(root2)));
  });

  // ---- restore-masks-rc lint (engine-iteration-5 fix 2, run-3/5 red-then-green class) ----
  await scenario('validator lint: mutate→test→restore without rc capture WARNs; canonical rc shape silent', async (check) => {
    const root = stRepo(ID);
    const withCmd = (command) => stBasePacket(ID, { evidence_required: [{ rung: 'red-then-green', command, expect: 'seeded red observed by exit code' }] });
    const masked = withCmd("cd /x && git diff --quiet -- server/auth.js && sed -i 's/a/b/' server/auth.js && node --test test/auth.test.js; git checkout -- server/auth.js");
    const warns = [];
    check('warn-only, never an error', validatePacket(masked, { repoDir: root, warn: (m) => warns.push(m) }).length === 0);
    check('WARN names the masked exit code', warns.some((m) => /ALWAYS the checkout's/.test(m) && /NEVER fail by exit code/.test(m)), warns.join(' | '));
    check('WARN carries the canonical rc-capture shape', warns.some((m) => m.includes('rc=0; <test cmd> || rc=1; git checkout -- <file>; exit $rc')));
    check('WARN suggests exit_code expect_check for seeded red', warns.some((m) => /expect_check \{type: exit_code, value: 1\}/.test(m)));
    const canonical = withCmd("cd /x && git diff --quiet -- server/auth.js && sed -i 's/a/b/' server/auth.js; rc=0; node --test test/auth.test.js || rc=1; git checkout -- server/auth.js; exit $rc");
    const w2 = [];
    validatePacket(canonical, { repoDir: root, warn: (m) => w2.push(m) });
    check('canonical rc-capture shape silent', w2.length === 0, w2.join(' | '));
    const statusVar = withCmd("node --test t.js; st=$?; git checkout -- f; exit $st");
    const w3 = [];
    validatePacket(statusVar, { repoDir: root, warn: (m) => w3.push(m) });
    check('$?-capture variant silent', w3.length === 0, w3.join(' | '));
    const guardedMasked = withCmd("cd /x && if [ -f test/o.test.js ]; then git diff --quiet -- s.js && sed -i 's/a/b/' s.js && node --test test/o.test.js; git checkout -- s.js; else echo oracle-not-yet-authored; fi");
    const w4 = [];
    validatePacket(guardedMasked, { repoDir: root, warn: (m) => w4.push(m) });
    check('guarded (if [ -f ]) masked shape still WARNs — the guard does not capture rc', w4.some((m) => /ALWAYS the checkout's/.test(m)), w4.join(' | '));
    const w5 = [];
    validatePacket(withCmd("sed -i 's/a/b/' f.js; git checkout -- f.js"), { repoDir: root, warn: (m) => w5.push(m) });
    check('restore without a test invocation not linted', w5.length === 0);
    const w6 = [];
    validatePacket(withCmd('node --test t.js && echo done'), { repoDir: root, warn: (m) => w6.push(m) });
    check('test without a restore not linted', w6.length === 0);
    const w7 = [];
    validatePacket(masked, { warn: (m) => w7.push(m) });
    check('without ctx.repoDir the lint is off (best-effort by design)', w7.length === 0, w7.join(' | '));
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
