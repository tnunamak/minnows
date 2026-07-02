#!/usr/bin/env node
// test-lane.mjs — offline self-tests for lane.mjs (`hone lane emit|gate|land`, the
// Workflow-substrate spine). Real git fixture repos + real shell evidence commands,
// NO maker/judge processes at all — the lane CLI is engine-only by design, so the
// tests play the orchestrator: call emit, edit the tree like a maker would, call
// gate, hand-craft verdict/usage JSON like the workflow would, call land.
//
// Matrix (mirrors the work.mjs self-test grammar so the two substrates stay honest):
//   emit    refusals (owner_ratify, terminal, dirty, main, pin, unknown provider — no
//           side effects) · dry-run · red baseline → blocked terminal · green → state
//   gate    refusals (no emit, no state, already-green) · maker-no-diff (HONE-VERDICT
//           permanent close / unactionable / generic) · touchset violation → revert+skip ·
//           red below ceiling → tree preserved + revision brief · red at ceiling →
//           reverted terminal · green → tree-bound receipt + judge evidence
//   land    refusals (no green receipt, tree changed after gate, maker==judge identity,
//           malformed/missing verdict+usage) · REJECT → reverted · PASS → landed (commit
//           author/subject, ledgers, explicit usage into cost) · PASS after revision ·
//           abort → skipped · foreign commit → blocked, no auto-revert
//
// Run: node lib/test-lane.mjs   (or: hone lane --self-test). Exit 0 iff all green.

import { existsSync, mkdtempSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parseYaml, stringifyYaml } from './yaml.mjs';
import { assertValidPacket } from './validate-packet.mjs';
import { readJsonl, claimsPath, costPath } from './ledger.mjs';
import { AUTHOR_EMAIL } from './work.mjs';
import { executeLaneEmit, executeLaneGate, executeLaneLand, parseUsageInput, parseVerdictInput, aggregateUsage } from './lane.mjs';

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
const ST_BAD = ST_GOOD.replace('if (n > hi) return hi;', 'if (n > hi) return lo;');
const ST_TEST = `const { clamp } = require('./src/util.js');
const ok = clamp(5, 0, 10) === 5 && clamp(-1, 0, 10) === 0 && clamp(11, 0, 10) === 10;
if (!ok) { console.error('FAIL clamp'); process.exit(1); }
console.log('PASS 3/3');
`;

const ID = 'lanetest-util-t0-00000001';

function basePacket(overrides = {}) {
  return {
    candidate_id: ID,
    created: new Date().toISOString(),
    repo_sha: 'lanetest0000',
    subsystem: 'src',
    files: ['src/util.js'],
    symbols: ['clamp'],
    public_surface: [],
    behavior_status: 'likely_intended',
    ownership: 'OWN',
    action: 'preserve_refactor',
    proof_class: 'certified_transform',
    execution_gate: 'autonomous',
    why_this_matters: 'lane self-test fixture: nested-else clamp flattening',
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

function fixtureRepo({ branch = 'quality-sweep', packetOverrides = {} } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'hone-lanetest-'));
  const run = (args) => {
    const r = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
    if (r.status !== 0) throw new Error(`fixture git ${args.join(' ')}: ${r.stderr}`);
  };
  run(['init', '-q']);
  run(['symbolic-ref', 'HEAD', `refs/heads/${branch}`]);
  run(['config', 'user.email', 'lanetest@example.com']);
  run(['config', 'user.name', 'Lane Test']);
  mkdirSync(join(root, 'src'), { recursive: true });
  writeFileSync(join(root, 'src/util.js'), ST_ORIG);
  writeFileSync(join(root, 'test.js'), ST_TEST);
  writeFileSync(join(root, 'README.md'), '# lane fixture\n');
  run(['add', '-A']);
  run(['commit', '-q', '-m', 'init fixture']);
  const packet = basePacket(packetOverrides);
  assertValidPacket(packet, `lane fixture ${ID}`);
  mkdirSync(join(root, 'quality/packets'), { recursive: true });
  writeFileSync(join(root, 'quality/packets', `${ID}.yaml`), stringifyYaml(packet));
  return root;
}

const USAGE_OK = JSON.stringify([
  { role: 'maker', provider: 'claude', model: 'sonnet', tokens_in: 1000, tokens_out: 200, cost_usd: 0.05 },
  { role: 'judge', provider: 'claude', model: 'opus', tokens_in: 500, tokens_out: 100, cost_usd: 0.04 },
]);
const verdictJson = (verdict, reasoning, model = 'opus') =>
  JSON.stringify({ verdict, reasoning, confidence: 0.9, judge: { provider: 'claude', model } });

export async function laneSelfTest({ verbose = false } = {}) {
  const results = [];
  const w = (s) => process.stdout.write(s + '\n');
  const read = (root, p) => (existsSync(join(root, p)) ? readFileSync(join(root, p), 'utf8') : null);
  const packetOnDisk = (root) => parseYaml(read(root, `quality/packets/${ID}.yaml`));
  const claims = (root) => readJsonl(claimsPath(root));
  const costs = (root) => readJsonl(costPath(root));
  const stateDir = (root) => join(root, 'quality', '.lane', ID);
  const treeClean = (root) => {
    const r = spawnSync('git', ['status', '--porcelain=v1', '-uall'], { cwd: root, encoding: 'utf8' });
    return r.stdout.split('\n').filter((l) => l && !/(^|\/)quality\//.test(l.slice(3))).length === 0;
  };
  const headInfo = (root) => spawnSync('git', ['log', '-1', '--format=%s|%ae|%H'], { cwd: root, encoding: 'utf8' }).stdout.trim();
  const editUtil = (root, content) => writeFileSync(join(root, 'src/util.js'), content);
  const emit = (root, extra = {}) => executeLaneEmit({ id: ID, repoRoot: root, makerProvider: 'claude', judgeProvider: 'claude', ...extra });
  const gate = (root, extra = {}) => executeLaneGate({ id: ID, repoRoot: root, ...extra });
  const land = (root, extra = {}) => executeLaneLand({ id: ID, repoRoot: root, ...extra });

  async function scenario(name, fn) {
    const checks = [];
    const check = (label, cond, detail = '') => checks.push({ label, ok: !!cond, detail });
    try { await fn(check); }
    catch (e) { checks.push({ label: 'no-unexpected-throw', ok: false, detail: e.message }); }
    results.push({ name, checks });
  }

  // ---- emit: gate refusals (no side effects) ----
  await scenario('emit refuse: owner_ratify / terminal status / pin mismatch / unknown provider', async (check) => {
    const root = fixtureRepo({ packetOverrides: { execution_gate: 'owner_ratify' } });
    const before = read(root, `quality/packets/${ID}.yaml`);
    const r = await emit(root);
    check('owner_ratify refused exit 2', r.exitCode === 2 && r.json.refused, r.json.reason);
    check('packet unchanged, no ledgers', read(root, `quality/packets/${ID}.yaml`) === before && !existsSync(claimsPath(root)));
    const root2 = fixtureRepo({ packetOverrides: { status: 'landed' } });
    const r2 = await emit(root2);
    check('terminal status refused', r2.exitCode === 2 && /re-litigate/.test(r2.json.reason), r2.json.reason);
    const root3 = fixtureRepo({ packetOverrides: { maker_provider: 'codex' } });
    const r3 = await emit(root3);
    check('maker pin mismatch refused, routes to hone work', r3.exitCode === 2 && /pins maker_provider/.test(r3.json.reason) && /hone work/.test(r3.json.reason), r3.json.reason);
    const r4 = await emit(fixtureRepo(), { makerProvider: 'gpt' });
    check('unknown provider refused', r4.exitCode === 2 && /unknown maker provider/.test(r4.json.reason));
  });

  await scenario('emit refuse: dirty tree / default branch (no side effects)', async (check) => {
    const root = fixtureRepo();
    writeFileSync(join(root, 'src/util.js'), ST_ORIG + '// local edit\n');
    const r = await emit(root);
    check('dirty touchset refused', r.exitCode === 2 && /dirty/.test(r.json.reason) && /DIRTY TOUCHSET/.test(r.json.reason), r.json.reason);
    check('no state dir created', !existsSync(stateDir(root)));
    const root2 = fixtureRepo({ branch: 'main' });
    const r2 = await emit(root2);
    check('main branch refused', r2.exitCode === 2 && /'main'/.test(r2.json.reason), r2.json.reason);
  });

  await scenario('emit dry-run: brief emitted, zero side effects', async (check) => {
    const root = fixtureRepo();
    const before = read(root, `quality/packets/${ID}.yaml`);
    const r = await emit(root, { dryRun: true });
    check('exit 0 dry_run', r.exitCode === 0 && r.json.dry_run === true);
    check('brief carries binding rules + both HONE-VERDICT forms', /MAKER in a repo-quality engine/.test(r.json.brief) && /HONE-VERDICT: validated-non-defect/.test(r.json.brief) && /HONE-VERDICT: unactionable/.test(r.json.brief));
    check('touchset normalized to git-root-relative', JSON.stringify(r.json.touchset_toplevel) === '["src/util.js"]');
    check('packet byte-unchanged, no state, no ledgers', read(root, `quality/packets/${ID}.yaml`) === before && !existsSync(stateDir(root)) && !existsSync(claimsPath(root)));
  });

  // ---- emit: terminal + green paths ----
  await scenario('emit: red baseline → blocked terminal (books written, no provider cost)', async (check) => {
    const root = fixtureRepo({ packetOverrides: { evidence_required: [{ rung: 'direct-test', command: 'node -e "process.exit(3)"', expect: 'exit 0' }] } });
    const r = await emit(root);
    const p = packetOnDisk(root);
    check('exit 1 blocked', r.exitCode === 1 && r.json.terminal === 'blocked', JSON.stringify(r.json));
    check('packet blocked + blocked_on names red baseline', p.status === 'blocked' && /red baseline/.test(p.outcome.blocked_on ?? ''));
    check('receipt recorded with FAIL', p.outcome.evidence_receipts.length === 1 && /FAIL/.test(p.outcome.evidence_receipts[0]));
    check('claims verified_fact + remaining_work', claims(root).map((c) => c.type).join(',') === 'verified_fact,remaining_work');
    const c0 = costs(root)[0];
    check('cost: known 0 when no provider ran, outcome blocked', c0?.cost_usd === 0 && c0?.tokens_in === 0 && c0?.outcome === 'blocked');
    check('lane state cleaned up', !existsSync(stateDir(root)));
  });

  await scenario('emit: green baseline → in_progress + persisted lane state + brief digest', async (check) => {
    const root = fixtureRepo();
    const r = await emit(root);
    check('exit 0 ok', r.exitCode === 0 && r.json.ok === true, JSON.stringify(r.json).slice(0, 200));
    check('packet in_progress on disk', packetOnDisk(root).status === 'in_progress');
    check('state.json + baseline.json persisted', existsSync(join(stateDir(root), 'state.json')) && existsSync(join(stateDir(root), 'baseline.json')));
    const st = JSON.parse(read(root, `quality/.lane/${ID}/state.json`));
    check('state records head_sha + providers + normalized touchset', /^[0-9a-f]{40}$/.test(st.head_sha) && st.maker_provider === 'claude' && st.touchset_toplevel[0] === 'src/util.js');
    check('baseline receipt file on disk', existsSync(join(root, `quality/receipts/${ID}/baseline-1-direct-test.txt`)));
    check('maker brief digest persisted (attempt 1)', /sha256\(full brief\)=/.test(read(root, `quality/receipts/${ID}/maker-brief-1.digest.txt`) ?? ''));
    check('JSON has brief + packet_yaml + baseline lines + next step', r.json.brief.length > 200 && /candidate_id/.test(r.json.packet_yaml) && r.json.baseline.length === 1 && /hone lane gate/.test(r.json.next));
    check('re-emit refused (in_progress is not pending)', (await emit(root)).exitCode === 2);
  });

  // ---- gate: refusals ----
  await scenario('gate refuse: pending packet / in_progress without lane state', async (check) => {
    const root = fixtureRepo();
    const r = await gate(root);
    check('pending refused (emit first)', r.exitCode === 2 && /in_progress/.test(r.json.reason), r.json.reason);
    const root2 = fixtureRepo({ packetOverrides: { status: 'in_progress' } });
    const r2 = await gate(root2);
    check('in_progress without state refused, names manual resolution', r2.exitCode === 2 && /no lane state/.test(r2.json.reason), r2.json.reason);
  });

  // ---- gate: maker-no-diff (HONE-VERDICT semantics preserved) ----
  await scenario('gate: no-diff + HONE-VERDICT validated-non-defect → honest permanent close', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    const summary = 'Checked the code.\nHONE-VERDICT: validated-non-defect — clamp already uses guard clauses';
    const r = await gate(root, { makerSummary: summary });
    const p = packetOnDisk(root);
    check('exit 1 skipped', r.exitCode === 1 && r.json.terminal === 'skipped', JSON.stringify(r.json).slice(0, 300));
    check('skip_reason = validated-non-defect(rationale)', /^validated-non-defect\(clamp already uses/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? '');
    check('NO remaining_work retry claim (permanent close)', !claims(root).some((c) => c.type === 'remaining_work'));
    check('state cleaned', !existsSync(stateDir(root)));
    const root2 = fixtureRepo();
    await emit(root2);
    const r2 = await gate(root2, { makerSummary: 'no verdict line here' });
    check('no-diff without verdict → maker-no-diff + retry claim', /maker-no-diff/.test(packetOnDisk(root2).outcome.skip_reason ?? '') && claims(root2).some((c) => c.type === 'remaining_work'), r2.json.terminal);
  });

  // ---- gate: touchset violation ----
  await scenario('gate: touchset violation → EVERYTHING reverted + skipped', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    writeFileSync(join(root, 'README.md'), '# lane fixture\ntouched by maker\n');
    const r = await gate(root);
    const p = packetOnDisk(root);
    check('skipped(touchset-violation) names README', r.json.terminal === 'skipped' && /touchset-violation/.test(p.outcome.skip_reason ?? '') && /README\.md/.test(p.outcome.skip_reason ?? ''));
    check('both files reverted byte-identical', read(root, 'src/util.js') === ST_ORIG && read(root, 'README.md') === '# lane fixture\n');
    check('tree clean, state cleaned', treeClean(root) && !existsSync(stateDir(root)));
  });

  // ---- gate: red below ceiling → tree preserved; red at ceiling → reverted ----
  await scenario('gate: rung red below ceiling → exit 1, tree PRESERVED, engine-built revision brief', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_BAD);
    const r = await gate(root);
    check('exit 1, not terminal', r.exitCode === 1 && !r.json.terminal && r.json.green === false, JSON.stringify(r.json).slice(0, 200));
    check('red names the rung + attempts left', r.json.red.rung === 'direct-test' && r.json.attempts_used === 1 && r.json.attempts_left === 2);
    check('tree preserved for revision (maker diff still applied)', read(root, 'src/util.js') === ST_BAD);
    check('packet still in_progress (no terminal write)', packetOnDisk(root).status === 'in_progress' && !existsSync(claimsPath(root)));
    check('revision brief carries failure + diff + rules', /REVISION REQUIRED/.test(r.json.revision_brief) && /FAILED/.test(r.json.revision_brief) && /diff --git/.test(r.json.revision_brief));
    check('post receipt on disk (phase post)', existsSync(join(root, `quality/receipts/${ID}/post-1-direct-test.txt`)));
    check('revision brief digest persisted (attempt 2)', /REVISION REQUIRED/.test(read(root, `quality/receipts/${ID}/maker-brief-2.digest.txt`) ?? ''));
  });

  await scenario('gate: red at the attempt ceiling → reverted terminal, tree restored', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_BAD);
    await gate(root); // attempt 1: red, preserved
    await gate(root); // attempt 2: red, preserved
    const r3 = await gate(root); // attempt 3 = ceiling: red → revert + terminal
    const p = packetOnDisk(root);
    check('third red → reverted terminal', r3.exitCode === 1 && r3.json.terminal === 'reverted', JSON.stringify(r3.json).slice(0, 300));
    check('tree restored byte-identical', read(root, 'src/util.js') === ST_ORIG && treeClean(root));
    check('cost revision_count=2, judge never ran (judge_result null)', costs(root)[0]?.revision_count === 2 && costs(root)[0]?.judge_result === null);
    check('receipt phases post, post-r1, post-r2 all on disk', ['post', 'post-r1', 'post-r2'].every((ph) => existsSync(join(root, `quality/receipts/${ID}/${ph}-1-direct-test.txt`))));
    check('claims verified_fact cites the failing rung', claims(root).some((c) => c.type === 'verified_fact' && /still failing/.test(c.statement)));
    check('state cleaned', !existsSync(stateDir(root)));
  });

  // ---- gate: green ----
  await scenario('gate: full green → tree-bound receipt + judge-facing evidence + diff', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    const r = await gate(root);
    check('exit 0 green', r.exitCode === 0 && r.json.green === true, JSON.stringify(r.json).slice(0, 200));
    check('tree_hash present', /^[0-9a-f]{8}$/.test(r.json.tree_hash));
    check('receipts include baseline AND post lines with digests', r.json.receipts.some((l) => l.startsWith('[baseline]')) && r.json.receipts.some((l) => l.startsWith('[post]')) && r.json.receipts.every((l) => /djb2=/.test(l)));
    check('evidence carries REAL rung output (PASS 3/3)', r.json.evidence.includes('PASS 3/3'));
    check('diff shows the actual edit', /guard|return lo;/.test(r.json.diff) && /diff --git/.test(r.json.diff));
    check('re-gate refused while green', (await gate(root)).exitCode === 2);
    check('packet still in_progress until land', packetOnDisk(root).status === 'in_progress');
  });

  // ---- land: refusals (fail-closed) ----
  await scenario('land refuse: no green gate receipt / missing verdict / missing usage', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    const r = await land(root, { verdictRaw: verdictJson('PASS', 'looks right'), usageRaw: USAGE_OK });
    check('no green gate receipt → refused (agent claims never trusted)', r.exitCode === 2 && /no green gate receipt/.test(r.json.reason), r.json.reason);
    await gate(root);
    const r2 = await land(root, { usageRaw: USAGE_OK });
    check('missing verdict refused', r2.exitCode === 2 && /judge-verdict/.test(r2.json.reason));
    const r3 = await land(root, { verdictRaw: verdictJson('PASS', 'ok') });
    check('missing usage refused (explicit accounting required)', r3.exitCode === 2 && /--usage/.test(r3.json.reason));
    check('packet still in_progress after refusals (retryable)', packetOnDisk(root).status === 'in_progress');
  });

  await scenario('land refuse: tree changed after the green gate', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    await gate(root);
    editUtil(root, ST_GOOD + '// drive-by tweak after gate\n');
    const r = await land(root, { verdictRaw: verdictJson('PASS', 'ok'), usageRaw: USAGE_OK });
    check('tree-hash mismatch → refused, demands re-gate', r.exitCode === 2 && /tree state changed/.test(r.json.reason) && /re-run hone lane gate/.test(r.json.reason), r.json.reason);
    check('no commit created', headInfo(root).startsWith('init fixture'));
  });

  await scenario('land refuse: maker == judge identity / malformed inputs', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    await gate(root);
    const sameIdentity = JSON.stringify([
      { role: 'maker', provider: 'claude', model: 'sonnet' },
      { role: 'judge', provider: 'claude', model: 'sonnet' },
    ]);
    const r = await land(root, { verdictRaw: verdictJson('PASS', 'ok', 'sonnet'), usageRaw: sameIdentity });
    check('same provider:model identity refused (non-negotiable #1)', r.exitCode === 2 && /maker == judge identity/.test(r.json.reason), r.json.reason);
    const r2 = await land(root, { verdictRaw: '{"verdict":"MAYBE","reasoning":"x","judge":{"provider":"claude"}}', usageRaw: USAGE_OK });
    check('unknown verdict value refused', r2.exitCode === 2 && /malformed --judge-verdict/.test(r2.json.reason));
    const r3 = await land(root, { verdictRaw: verdictJson('PASS', 'ok'), usageRaw: '[{"role":"maker","provider":"claude","tokens_in":"lots"}]' });
    check('malformed usage refused', r3.exitCode === 2 && /malformed --usage/.test(r3.json.reason));
    const r4 = await land(root, { verdictRaw: verdictJson('PASS', 'ok'), usageRaw: JSON.stringify([{ role: 'maker', provider: 'claude' }]) });
    check('usage without a judge entry refused', r4.exitCode === 2 && /at least one maker entry and one judge entry/.test(r4.json.reason));
    check('still landable after refusals: PASS lands', (await land(root, { verdictRaw: verdictJson('PASS', 'clean flattening'), usageRaw: USAGE_OK })).exitCode === 0);
  });

  // ---- land: terminal paths ----
  await scenario('land: judge REJECT → reverted (never land without PASS)', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    await gate(root);
    const r = await land(root, { verdictRaw: verdictJson('REJECT', 'relocation dressed as refactoring'), usageRaw: USAGE_OK });
    const p = packetOnDisk(root);
    check('reverted exit 1', r.exitCode === 1 && r.json.terminal === 'reverted');
    check('util.js restored byte-identical', read(root, 'src/util.js') === ST_ORIG && treeClean(root));
    check('judge_verdict records provider:model + reasoning', /claude:opus REJECT/.test(p.outcome.judge_verdict ?? '') && /relocation/.test(p.outcome.judge_verdict ?? ''));
    check('judged_design_claim carries model-qualified judge identity', claims(root).some((c) => c.type === 'judged_design_claim' && c.judge?.verdict === 'REJECT' && c.judge?.provider === 'claude:opus'));
    check('cost judge_result=REJECT, landed=false, usage recorded', costs(root)[0]?.judge_result === 'REJECT' && costs(root)[0]?.landed === false && costs(root)[0]?.tokens_in === 1500);
    check('no commit created', headInfo(root).startsWith('init fixture'));
  });

  await scenario('land: judge PASS → landed (commit discipline + explicit usage into the ledger)', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    await gate(root);
    const r = await land(root, { verdictRaw: verdictJson('PASS', 'behavior preserved, real simplification'), usageRaw: USAGE_OK });
    const p = packetOnDisk(root);
    const head = headInfo(root); // subject|author-email|sha
    check('landed exit 0 with commit', r.exitCode === 0 && r.json.terminal === 'landed' && typeof r.json.commit === 'string');
    check('commit subject references candidate id', head.includes(`[hone ${ID}]`), head);
    check('author email tnunamak@gmail.com', head.split('|')[1] === AUTHOR_EMAIL, head);
    check('packet landed + commit sha matches HEAD', p.status === 'landed' && p.outcome.commit === head.split('|')[2]);
    check('model-qualified identities recorded on packet (schema must-differ rule holds)', p.maker_provider === 'claude:sonnet' && p.judge_provider === 'claude:opus', `${p.maker_provider}/${p.judge_provider}`);
    check('outcome receipts include baseline + post', p.outcome.evidence_receipts.length === 2);
    check('behavior_preserved + judged_design_claim written', ['behavior_preserved', 'judged_design_claim'].every((t) => claims(root).some((c) => c.type === t)));
    check('behavior_preserved evidence digests present', claims(root).find((c) => c.type === 'behavior_preserved')?.evidence.every((e) => /djb2=/.test(e.output_digest)));
    const c0 = costs(root)[0];
    check('cost: usage summed exactly (1500 in / 300 out / $0.09)', c0?.tokens_in === 1500 && c0?.tokens_out === 300 && c0?.cost_usd === 0.09, JSON.stringify(c0));
    check('cost landed=true revision_count=0 judge_result=PASS', c0?.landed === true && c0?.revision_count === 0 && c0?.judge_result === 'PASS');
    check('tokens_actual on packet from usage', p.outcome.tokens_actual === 1800);
    check('tree clean, state cleaned', treeClean(root) && !existsSync(stateDir(root)));
  });

  await scenario('land: PASS after one gate revision → revision_count=1', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_BAD);
    const g1 = await gate(root);
    check('first gate red', g1.exitCode === 1 && g1.json.green === false);
    editUtil(root, ST_GOOD);
    const g2 = await gate(root, { revisionNote: 'fixed the upper-bound return per the red rung' });
    check('second gate green (phase post-r1)', g2.exitCode === 0 && existsSync(join(root, `quality/receipts/${ID}/post-r1-1-direct-test.txt`)));
    check('revision note receipt persisted', /fixed the upper-bound/.test(read(root, `quality/receipts/${ID}/revision-note-2.txt`) ?? ''));
    const r = await land(root, { verdictRaw: verdictJson('PASS', 'revised acceptably'), usageRaw: USAGE_OK });
    const p = packetOnDisk(root);
    check('landed', r.exitCode === 0 && p.status === 'landed');
    check('cost revision_count=1, lesson notes the revision', costs(root)[0]?.revision_count === 1 && /revision/.test(p.outcome.lesson ?? ''));
  });

  await scenario('land: --abort → reverted tree + skipped(lane-abort) with honest claims', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    const r0 = await land(root, { abort: true });
    check('abort without reason refused', r0.exitCode === 2 && /--reason/.test(r0.json.reason));
    const r = await land(root, { abort: true, abortReason: 'maker agent errored mid-run' });
    const p = packetOnDisk(root);
    check('skipped(lane-abort) exit 1', r.exitCode === 1 && /^lane-abort\(maker agent errored/.test(p.outcome.skip_reason ?? ''), p.outcome.skip_reason ?? '');
    check('tree reverted', read(root, 'src/util.js') === ST_ORIG && treeClean(root));
    check('uncertainty claim, no fabricated evidence', claims(root).some((c) => c.type === 'uncertainty'));
    check('state cleaned', !existsSync(stateDir(root)));
  });

  // ---- foreign-commit guard (in-harness makers HAVE Bash — the new risk class) ----
  await scenario('foreign commit between emit and gate → blocked, tree NOT auto-reverted', async (check) => {
    const root = fixtureRepo();
    await emit(root);
    editUtil(root, ST_GOOD);
    spawnSync('git', ['add', 'src/util.js'], { cwd: root });
    spawnSync('git', ['-c', 'user.email=rogue@example.com', '-c', 'user.name=Rogue Maker', 'commit', '-q', '-m', 'rogue maker commit'], { cwd: root });
    const r = await gate(root);
    const p = packetOnDisk(root);
    check('blocked terminal, manual cleanup flagged', r.exitCode === 1 && r.json.terminal === 'blocked' && r.json.manual_cleanup_required === true, JSON.stringify(r.json).slice(0, 300));
    check('blocked_on names foreign-commit', /foreign-commit/.test(p.outcome.blocked_on ?? ''));
    check('rogue commit left in place (no auto-revert against foreign HEAD)', headInfo(root).startsWith('rogue maker commit'));
    check('claims record uncertainty + remaining_work', ['uncertainty', 'remaining_work'].every((t) => claims(root).some((c) => c.type === t)));
  });

  // ---- input parsers (unit) ----
  await scenario('parsers: usage/verdict fail-closed shapes + aggregation semantics', async (check) => {
    check('usage: valid parses', parseUsageInput(USAGE_OK).errors.length === 0);
    check('usage: unknown key refused', parseUsageInput('[{"role":"maker","provider":"claude","note":"hi"}]').errors.some((e) => /unknown key/.test(e)));
    check('usage: bad role refused', parseUsageInput('[{"role":"checker","provider":"claude"}]').errors.some((e) => /role/.test(e)));
    check('usage: empty array refused', parseUsageInput('[]').errors.length > 0);
    check('usage: non-JSON refused', parseUsageInput('not json').errors.length > 0);
    const agg = aggregateUsage(parseUsageInput(USAGE_OK).entries);
    check('aggregate sums in/out/usd, derives total', agg.inTok === 1500 && agg.outTok === 300 && agg.total === 1800 && agg.usd === 0.09, JSON.stringify(agg));
    const nulls = aggregateUsage(parseUsageInput('[{"role":"maker","provider":"claude"},{"role":"judge","provider":"claude","model":"opus"}]').entries);
    check('all-null usage stays null (honest unknown, never fabricated 0)', nulls.inTok === null && nulls.total === null && nulls.usd === null);
    check('verdict: valid parses', parseVerdictInput(verdictJson('PASS', 'ok')).errors.length === 0);
    check('verdict: missing reasoning refused', parseVerdictInput('{"verdict":"PASS","judge":{"provider":"claude"}}').errors.some((e) => /reasoning/.test(e)));
    check('verdict: confidence out of range refused', parseVerdictInput('{"verdict":"PASS","reasoning":"x","confidence":1.5,"judge":{"provider":"claude"}}').errors.some((e) => /confidence/.test(e)));
    check('verdict: missing judge refused', parseVerdictInput('{"verdict":"PASS","reasoning":"x"}').errors.some((e) => /judge/.test(e)));
    check('verdict: unknown key refused', parseVerdictInput('{"verdict":"PASS","reasoning":"x","judge":{"provider":"claude"},"extra":1}').errors.some((e) => /unknown key/.test(e)));
  });

  // ---- CLI wiring end-to-end (dispatcher + b64 inputs) ----
  await scenario('CLI: hone lane through the dispatcher with b64 inputs', async (check) => {
    const honeBin = join(dirname(fileURLToPath(import.meta.url)), '..', 'hone');
    const root = fixtureRepo();
    const cli = (args) => spawnSync(process.execPath, [honeBin, 'lane', ...args, '--repo', root], { encoding: 'utf8' });
    const dry = cli(['emit', '--packet', ID, '--dry-run']);
    check('dispatcher: emit --dry-run exit 0, JSON on stdout', dry.status === 0 && JSON.parse(dry.stdout).dry_run === true, dry.stderr.slice(0, 200));
    const em = cli(['emit', '--packet', ID]);
    check('dispatcher: emit exit 0', em.status === 0, em.stderr.slice(0, 200));
    const b64 = Buffer.from('HONE-VERDICT: validated-non-defect — already guard clauses').toString('base64');
    const gt = cli(['gate', '--packet', ID, '--maker-summary-b64', b64]);
    check('dispatcher: gate with b64 maker summary → skipped(validated-non-defect)', gt.status === 1 && /validated-non-defect/.test(packetOnDisk(root).outcome.skip_reason ?? ''), gt.stdout.slice(0, 300));
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
  w(`\nhone lane --self-test: ${pass} checks passed, ${fail} failed, ${results.length} scenarios (no LLM calls)`);
  return fail === 0 ? 0 : 1;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  process.exitCode = await laneSelfTest({ verbose: process.argv.includes('--verbose') });
}
