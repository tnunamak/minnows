#!/usr/bin/env node
// test-relative-paths.mjs — offline self-tests for the artifact-boundary rule:
// every path hone writes into a COMMITTED quality artifact is relative to the git root
// (or the $HONE_ROOT placeholder for the engine's own install dir); runtime context keeps
// absolute paths for reads and subprocess cwd. Zero LLM calls, zero real biome/typescript:
// the fixture repo carries a stub biome command and a stub `typescript` module, so the REAL
// runInventory + runPlan run end-to-end against a real git checkout.
//
// Matrix (each in a repo-root checkout AND a nested-subdirectory checkout):
//   1. inventory writers  — meta.json + the four sensor files hold no absolute path
//   2. plan               — packets carry no absolute path; rung commands are `cd "$REPO_ROOT" && …`
//                           engine collector path is $HONE_ROOT
//   3. claims             — buildLandClaims over those packets inherit the relative commands
//   4. round-trip         — artifacts made in checkout A are consumed from checkout B (A deleted)
//   5. portableRungCommand — foreign-absolute mapping still works; relative form is identity
//   6. legacy rung cwd    — a relative `cd <name>` rung keeps running from the repo root, even
//                           when <name> equals the repo's git-root prefix
//   7. receipts           — writeRungReceipt output (command, executed-command, rung output) holds
//                           no absolute checkout/engine path
//
// Run: node lib/test-relative-paths.mjs   Exit 0 iff every check passes.

import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { execSync } from 'node:child_process';
import { HONE_ROOT, buildContext } from './profile.mjs';
import { runInventory } from './inventory.mjs';
import { runPlan } from './plan.mjs';
import { loadVerifiedInventorySnapshot } from './inventory-snapshot.mjs';
import { loadPacket } from './packet-io.mjs';
import { buildLandClaims, makeRungExecutor, portableRungCommand, writeRungReceipt } from './work.mjs';

const checks = [];
const ok = (name, cond, detail = '') => {
  checks.push({ name, pass: !!cond });
  if (!cond) process.stderr.write(`FAIL ${name}${detail ? ` — ${detail}` : ''}\n`);
};

const BIOME_STUB = `process.stdout.write(JSON.stringify({ diagnostics: [{
  message: 'Excessive complexity of 9 detected (max: 1).',
  location: { path: { file: 'src/a.js' }, start: { line: 1, column: 0 } },
}] }));
`;
const A_SRC = `export function pick(x) {
  if (x > 1) { if (x > 2) { return 'a'; } else { return 'b'; } }
  for (const y of [1, 2]) { if (y > x) { return 'c'; } }
  return 'd';
}
`;
const PROFILE = `version: 1
commands:
  biome: node biome-stub.mjs
  typecheck: test -f src/a.js
  test: node -e "process.exit(0)"
analysis:
  owned_dirs: [src]
  cog_threshold: 5
`;

/** every temp root, registered the moment it exists so an exception cannot leak it. */
const tempRoots = [];
const track = (dir) => { tempRoots.push(dir); return dir; };

/** a real git checkout; `nested` puts the hone repo in a subdirectory of the git root. */
function makeCheckout(label, nested) {
  const gitRoot = track(mkdtempSync(join(tmpdir(), `hone-relpaths-${label}-`)));
  const repoRoot = nested ? join(gitRoot, 'reference-implementation') : gitRoot;
  mkdirSync(join(repoRoot, 'src'), { recursive: true });
  mkdirSync(join(repoRoot, 'quality'), { recursive: true });
  mkdirSync(join(repoRoot, 'node_modules', 'typescript'), { recursive: true });
  writeFileSync(join(repoRoot, 'node_modules', 'typescript', 'index.js'), 'module.exports = {};\n');
  writeFileSync(join(repoRoot, 'biome-stub.mjs'), BIOME_STUB);
  writeFileSync(join(repoRoot, 'src', 'a.js'), A_SRC);
  writeFileSync(join(repoRoot, 'quality', 'hone.yaml'), PROFILE);
  execSync('git init -q && git add -A -f && git -c user.email=t@t -c user.name=t commit -q -m fixture', { cwd: gitRoot });
  return { gitRoot, repoRoot, prefix: nested ? 'reference-implementation' : '' };
}

function readTree(dir) {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) out.push(...readTree(p));
    else out.push({ file: p, text: readFileSync(p, 'utf8') });
  }
  return out;
}

/** every distinct machine-specific string a committed artifact must not carry. */
const leakNeedles = (co) => [...new Set([co.gitRoot, co.repoRoot, realpathSync(co.gitRoot), realpathSync(co.repoRoot), HONE_ROOT, homedir()])];
const leaks = (files, co) => files.flatMap((f) => leakNeedles(co).filter((n) => f.text.includes(n)).map((n) => `${f.file.slice(co.gitRoot.length)}: ${n}`));

const quiet = async (fn) => {
  const w = process.stderr.write;
  process.stderr.write = () => true;
  try { return await fn(); } finally { process.stderr.write = w; }
};

try {
for (const nested of [false, true]) {
  const tag = nested ? 'nested' : 'root';
  const co = makeCheckout(tag, nested);

  await quiet(async () => { await runInventory({ repo: co.repoRoot }); await runPlan({ repo: co.repoRoot }); });
  const invFiles = readTree(join(co.repoRoot, 'quality', 'inventory'));
  const pktFiles = readTree(join(co.repoRoot, 'quality', 'packets'));
  ok(`${tag}: fixture produced inventory + packets`, invFiles.length === 5 && pktFiles.length >= 1, `${invFiles.length} inventory, ${pktFiles.length} packets`);

  // ---- 1. inventory writers
  const meta = JSON.parse(readFileSync(join(co.repoRoot, 'quality', 'inventory', 'meta.json'), 'utf8'));
  ok(`${tag}: inventory files carry no absolute checkout/engine/home path`, !leaks(invFiles, co).length, leaks(invFiles, co).join(' | '));
  ok(`${tag}: meta.repo_root is the git-root-relative prefix`, meta.repo_root === (co.prefix || '.'), String(meta.repo_root));
  ok(`${tag}: meta.git_root is "."`, meta.git_root === '.', String(meta.git_root));
  ok(`${tag}: meta.profile_source is repo-relative`, String(meta.profile_source) === join(co.prefix, 'quality', 'hone.yaml'), String(meta.profile_source));
  const sensor = JSON.parse(readFileSync(join(co.repoRoot, 'quality', 'inventory', 'hotspots.json'), 'utf8'));
  ok(`${tag}: sensor generated_from.repo_root is relative`, sensor.generated_from.repo_root === (co.prefix || '.'), String(sensor.generated_from.repo_root));

  // ---- 2. packets
  ok(`${tag}: packets carry no absolute checkout/engine/home path`, !leaks(pktFiles, co).length, leaks(pktFiles, co).join(' | '));
  const packetId = pktFiles[0].file.split('/').pop().replace(/\.yaml$/, '');
  const { packet } = loadPacket(co.repoRoot, packetId);
  const rungs = packet.evidence_required;
  const typecheck = rungs.find((r) => r.rung === 'typecheck');
  const expectTypecheck = 'cd "$REPO_ROOT" && test -f src/a.js';
  ok(`${tag}: typecheck rung is "${expectTypecheck}"`, typecheck?.command === expectTypecheck, typecheck?.command);
  const scopeFn = rungs.find((r) => /scope-fn\.mjs/.test(r.command));
  if (scopeFn) {
    ok(`${tag}: scope-fn rung uses $HONE_ROOT and a relative --repo`, /^cd "\$REPO_ROOT" && node \$HONE_ROOT\/collectors\/scope-fn\.mjs --repo \. /.test(scopeFn.command), scopeFn.command);
  }

  // ---- 3. claims
  const claims = buildLandClaims({ packet, id: packetId, reasoning: 'fixture', judgeProvider: 'fixture', receiptLines: [], receiptsDirRel: 'quality/receipts/x' });
  const claimText = JSON.stringify(claims);
  ok(`${tag}: claims carry no absolute path and echo the relative rung command`, !leakNeedles(co).some((n) => claimText.includes(n)) && claimText.includes(JSON.stringify(expectTypecheck).slice(1, -1)), claimText.slice(0, 200));

  // ---- 4. round-trip: consume from a different checkout location, original gone
  const dest = track(mkdtempSync(join(tmpdir(), `hone-relpaths-moved-${tag}-`)));
  cpSync(co.gitRoot, dest, { recursive: true });
  const moved = { gitRoot: dest, repoRoot: co.prefix ? join(dest, co.prefix) : dest, prefix: co.prefix };
  rmSync(co.gitRoot, { recursive: true, force: true });
  const ctxB = buildContext(moved.repoRoot);
  let snap = null, snapErr = '';
  try { snap = loadVerifiedInventorySnapshot(moved.repoRoot, ctxB.git.sha); } catch (e) { snapErr = e.message; }
  ok(`${tag}: round-trip — inventory snapshot verifies from the new location`, snap && snap.meta.repo_sha === ctxB.git.sha, snapErr);
  const movedPacket = loadPacket(moved.repoRoot, packetId).packet;
  const exec = makeRungExecutor({ gitRoot: moved.gitRoot, repoRoot: moved.repoRoot, prefix: moved.prefix });
  const r = await exec(movedPacket.evidence_required.find((x) => x.rung === 'typecheck'), 'baseline');
  ok(`${tag}: round-trip — typecheck rung runs green from the new location`, r.verdict.pass && r.executedCommand === expectTypecheck, `${r.verdict.reason} exit=${r.res.code} ${r.res.output}`);
  const bad = await exec({ rung: 'missing', command: 'cd "$REPO_ROOT" && test -f src/nope.js', expect: 'exit 0', expect_check: { type: 'exit_code', value: 0 } }, 'baseline');
  ok(`${tag}: round-trip — a failing relative rung is still red (executor is not vacuous)`, !bad.verdict.pass);
}

// ---------------------------------------------------------------- 5. portableRungCommand
{
  const co = makeCheckout('portable', true);
  const pc = { gitRoot: co.gitRoot, repoRoot: co.repoRoot, prefix: co.prefix };
  const foreign = portableRungCommand('cd /some/other/checkout/reference-implementation && node -e "0"', pc);
  ok('portable: foreign-absolute leading cd still maps by suffix into the current tree', foreign.rewritten && foreign.command.startsWith(`cd "${co.repoRoot}" &&`) && !foreign.refused, foreign.command);
  const eng = portableRungCommand('node /old/install/tools/hone/collectors/scope-fn.mjs --repo /some/other/checkout/reference-implementation --cog 5', pc);
  ok('portable: foreign engine path + --repo still map to the running engine / current repo',
    eng.command === `node ${HONE_ROOT}/collectors/scope-fn.mjs --repo ${co.repoRoot} --cog 5`, eng.command);
  const rel = portableRungCommand('cd "$REPO_ROOT" && node $HONE_ROOT/collectors/scope-fn.mjs --repo . --cog 5', pc);
  ok('portable: new relative form (cd "$REPO_ROOT", $HONE_ROOT, --repo .) is identity', !rel.rewritten && !rel.refused, rel.command);
}

// ---------------------------------------------------------------- 6. legacy rung cwd
{
  const co = makeCheckout('legacy', true);
  // a directory under the repo root that shares the git-root prefix's name: `cd <prefix>` from the
  // repo root lands HERE, while from the git root it would land on the repo root itself.
  mkdirSync(join(co.repoRoot, co.prefix), { recursive: true });
  writeFileSync(join(co.repoRoot, co.prefix, 'marker.txt'), 'x\n');
  const exec = makeRungExecutor({ gitRoot: co.gitRoot, repoRoot: co.repoRoot, prefix: co.prefix });
  const legacy = await exec({ rung: 'legacy-rel', command: `cd ${co.prefix} && test -f marker.txt`, expect: 'exit 0', expect_check: { type: 'exit_code', value: 0 } }, 'baseline');
  ok('legacy: a relative `cd <prefix-named dir>` rung still runs from the repo root', legacy.verdict.pass, `exit=${legacy.res.code} ${legacy.res.output}`);
  const fresh = await exec({ rung: 'fresh', command: 'cd "$REPO_ROOT" && test -f src/a.js', expect: 'exit 0', expect_check: { type: 'exit_code', value: 0 } }, 'baseline');
  ok('legacy: the `cd "$REPO_ROOT"` form runs the repo root in a nested checkout', fresh.verdict.pass, `exit=${fresh.res.code} ${fresh.res.output}`);
}

// ---------------------------------------------------------------- 7. receipts
{
  const co = makeCheckout('receipt', true);
  const exec = makeRungExecutor({ gitRoot: co.gitRoot, repoRoot: co.repoRoot, prefix: co.prefix });
  const stripCtx = { qualityRel: join(co.prefix, 'quality'), candidateId: 'x' };
  const rungs = [
    { rung: 'where', command: 'pwd; echo "$HONE_ROOT $GIT_ROOT"', expect: 'exit 0', expect_check: { type: 'exit_code', value: 0 } },
    // an old packet that names a foreign absolute checkout: the executor rewrites it to the current absolute tree
    { rung: 'old-packet', command: 'cd /some/other/checkout/reference-implementation && pwd', expect: 'exit 0', expect_check: { type: 'exit_code', value: 0 } },
  ];
  const files = [];
  for (const [index, rung] of rungs.entries()) {
    const { res, verdict, executedCommand } = await exec(rung, 'baseline');
    const r = writeRungReceipt({ repoRoot: co.repoRoot, gitRoot: co.gitRoot, receiptsDirRel: 'quality/receipts/x', id: 'x', phase: 'baseline', index, rung, res, verdict, stripCtx, executedCommand });
    files.push({ file: `receipt-${rung.rung}`, text: readFileSync(join(co.repoRoot, 'quality/receipts/x', `baseline-${index + 1}-${rung.rung}.txt`), 'utf8') });
    files.push({ file: `receipt-line-${rung.rung}`, text: r.line + r.digest });
  }
  ok('receipts: files and ledger lines carry no absolute checkout/engine/home path', !leaks(files, co).length, leaks(files, co).join(' | '));
  ok('receipts: output keeps a neutral placeholder for the checkout', /\$REPO_ROOT/.test(files[0].text), files[0].text);
}
} finally {
  for (const dir of tempRoots) rmSync(dir, { recursive: true, force: true });
}

const failed = checks.filter((c) => !c.pass);
process.stdout.write(`\ntest-relative-paths: ${checks.length - failed.length}/${checks.length} checks passed\n`);
for (const c of checks) process.stdout.write(`  ${c.pass ? 'ok  ' : 'FAIL'} ${c.name}\n`);
process.exit(failed.length ? 1 : 0);
