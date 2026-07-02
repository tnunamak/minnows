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
import { executeLaneEmit, executeLaneGate, executeLaneLand, executeLaneBatchGate, executeLaneBatchLand, parseUsageInput, parseVerdictInput, aggregateUsage } from './lane.mjs';

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

// second-order fixtures (batch scenarios): a sibling clamp2 in its own file with its own
// oracle, DISJOINT touchset — plus one rung command shared with packet 1 so the batch
// gate's union-dedupe is observable. Sorted, ID < ID2 (the batch anchor is ID).
const ID2 = 'lanetest-util2-t0-00000002';
const ST_ORIG2 = ST_ORIG.replaceAll('clamp', 'clamp2');
const ST_GOOD2 = ST_GOOD.replaceAll('clamp', 'clamp2');
const ST_BAD2 = ST_BAD.replaceAll('clamp', 'clamp2');
const ST_TEST2 = ST_TEST.replaceAll('clamp', 'clamp2').replaceAll('util.js', 'util2.js');

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

function fixtureRepo({ branch = 'quality-sweep', packetOverrides = {}, secondPacket = null } = {}) {
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
  mkdirSync(join(root, 'quality/packets'), { recursive: true });
  if (secondPacket) {
    writeFileSync(join(root, 'src/util2.js'), ST_ORIG2);
    writeFileSync(join(root, 'test2.js'), ST_TEST2);
    const p2 = basePacket({
      candidate_id: ID2,
      files: ['src/util2.js'],
      symbols: ['clamp2'],
      plan: { transform_class: 'certified-local-tidy', instruction: 'Flatten the nested else branches of clamp2 in src/util2.js into guard clauses. Behavior identical.' },
      touchset: ['src/util2.js'],
      // first rung SHARED with packet 1 (union dedupe observable), second its own oracle
      evidence_required: [
        { rung: 'direct-test', command: 'node test.js', expect: 'exit 0' },
        { rung: 'direct-test-2', command: 'node test2.js', expect: 'exit 0' },
      ],
      ...secondPacket,
    });
    assertValidPacket(p2, `lane fixture ${ID2}`);
    writeFileSync(join(root, 'quality/packets', `${ID2}.yaml`), stringifyYaml(p2));
  }
  run(['add', '-A']);
  run(['commit', '-q', '-m', 'init fixture']);
  const packet = basePacket(packetOverrides);
  assertValidPacket(packet, `lane fixture ${ID}`);
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
  const packetOnDisk = (root, id = ID) => parseYaml(read(root, `quality/packets/${id}.yaml`));
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

  await scenario('emit refuse: depends_on not landed (shared rule with the run scheduler — wf_67898fff)', async (check) => {
    const root = fixtureRepo({ packetOverrides: { depends_on: [ID2] }, secondPacket: {} }); // ID2 pending
    const r = await emit(root);
    check('unlanded dep refused, names the dep', r.exitCode === 2 && /depends_on not landed/.test(r.json.reason) && r.json.reason.includes(ID2), r.json.reason);
    check('refusal side-effect-free (packet pending, no lane state)', packetOnDisk(root).status === 'pending' && !existsSync(stateDir(root)));
    const root2 = fixtureRepo({ packetOverrides: { depends_on: [ID2] }, secondPacket: { status: 'in_progress' } });
    check('in_progress dep still unmet', (await emit(root2)).exitCode === 2);
    const root3 = fixtureRepo({ packetOverrides: { depends_on: ['ghost-packet-00000000'] } });
    const r3 = await emit(root3);
    check('MISSING dep packet counts as unlanded (fail-closed)', r3.exitCode === 2 && /ghost-packet-00000000/.test(r3.json.reason), r3.json.reason);
    const root4 = fixtureRepo({ packetOverrides: { depends_on: [ID2] }, secondPacket: { status: 'landed' } });
    const r4 = await emit(root4);
    check('landed dep → emit proceeds to green baseline', r4.exitCode === 0 && packetOnDisk(root4).status === 'in_progress', JSON.stringify(r4.json).slice(0, 200));
    check('dry-run also enforces the dep gate', (await emit(root, { dryRun: true })).exitCode === 2);
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
    const revBrief = readFileSync(r.json.revision_brief_path, 'utf8');
    check('revision brief ON DISK carries failure + diff + rules', /REVISION REQUIRED/.test(revBrief) && /FAILED/.test(revBrief) && /diff --git/.test(revBrief));
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
    const jc = JSON.parse(readFileSync(r.json.judge_context_path, 'utf8'));
    check('judge context ON DISK: evidence carries REAL rung output (PASS 3/3)', jc.evidence.includes('PASS 3/3'));
    check('judge context ON DISK: diff shows the actual edit + packet yaml present', /diff --git/.test(jc.diff) && /return lo;/.test(jc.diff) && /candidate_id/.test(jc.packet_yaml));
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

  // ---- stage-level usage accounting (instrumentation lever) ----
  const USAGE_STAGED = JSON.stringify([
    { role: 'maker', provider: 'claude', model: 'sonnet', stage: 'edit', tokens_in: 1000, tokens_out: 200, cache_read_tokens: 700, cost_usd: 0.05, wall_s: 60, quota_pts: 1 },
    { role: 'judge', provider: 'claude', model: 'opus', stage: 'judge', tokens_in: 500, tokens_out: 100, cache_read_tokens: 0, cost_usd: 0.04, wall_s: 30, quota_pts: 0.5 },
  ]);
  const landHappy = async (root, usageRaw) => {
    await emit(root);
    editUtil(root, ST_GOOD);
    await gate(root);
    return land(root, { verdictRaw: verdictJson('PASS', 'clean'), usageRaw });
  };

  await scenario('stage usage: per-stage attribution + cache + quota_pts land in the cost entry', async (check) => {
    const root = fixtureRepo();
    const r = await landHappy(root, USAGE_STAGED);
    check('landed', r.exitCode === 0, JSON.stringify(r.json).slice(0, 200));
    const c0 = costs(root)[0];
    check('stages array recorded with both stage records', Array.isArray(c0.stages) && c0.stages.length === 2 && c0.stages[0].stage === 'edit' && c0.stages[1].stage === 'judge', JSON.stringify(c0.stages));
    check('cache_read_tokens per stage (L4 measurement)', c0.stages[0].cache_read_tokens === 700 && c0.stages[1].cache_read_tokens === 0);
    check('wall_s per stage', c0.stages[0].wall_s === 60 && c0.stages[1].wall_s === 30);
    check('quota_pts aggregated at top level (owner currency)', c0.quota_pts === 1.5, String(c0.quota_pts));
    check('legacy totals still aggregated', c0.tokens_in === 1500 && c0.cost_usd === 0.09);
    check('packet outcome tokens_actual from usage', packetOnDisk(root).outcome.tokens_actual === 1800);
  });

  await scenario('stage usage back-compat: bare OBJECT = single unattributed stage; malformed stage refused', async (check) => {
    const bare = JSON.stringify({ role: 'maker', provider: 'claude', model: 'sonnet', tokens_in: 42 });
    const u = parseUsageInput(bare);
    check('bare object parses as one entry, stage null', u.errors.length === 0 && u.entries.length === 1 && u.entries[0].stage === null, u.errors.join(' | '));
    check('unknown stage value refused', parseUsageInput('[{"role":"maker","provider":"claude","stage":"vibes"}]').errors.some((e) => /stage/.test(e)));
    check('bad cache_read_tokens refused', parseUsageInput('[{"role":"maker","provider":"claude","cache_read_tokens":"many"}]').errors.some((e) => /cache_read_tokens/.test(e)));
    check('engine/planner roles accepted (test/plan stages have non-maker owners)', parseUsageInput('[{"role":"engine","provider":"hone","stage":"test"},{"role":"planner","provider":"claude","stage":"plan"}]').errors.length === 0);
    // end-to-end: a bare-object usage still lands and books it as one null-stage record
    const root = fixtureRepo();
    const bareBoth = JSON.stringify([{ role: 'maker', provider: 'claude', model: 'sonnet' }, { role: 'judge', provider: 'claude', model: 'opus' }]);
    const r = await landHappy(root, bareBoth);
    const c0 = costs(root)[0];
    check('array-without-stage lands; stages recorded with null stage + honest-null tokens', r.exitCode === 0 && c0.stages.length === 2 && c0.stages.every((s) => s.stage === null && s.tokens_in === null), JSON.stringify(c0.stages ?? null));
    check('quota_pts honest-null when unmetered', c0.quota_pts === null);
  });

  await scenario('report compiler tolerates the new cost fields (stages/quota_pts/batch)', async (check) => {
    const root = fixtureRepo();
    await landHappy(root, USAGE_STAGED);
    const honeBinPath = join(dirname(fileURLToPath(import.meta.url)), '..', 'hone');
    const r = spawnSync(process.execPath, [honeBinPath, 'report', '--repo', root], { encoding: 'utf8' });
    check('hone report exits 0 over staged books', r.status === 0, (r.stderr || r.stdout).slice(0, 300));
    check('no ledger errors surfaced', !/MALFORMED|corrupt|ledger error/i.test(r.stdout + r.stderr), (r.stdout + r.stderr).slice(0, 200));
  });

  // ---- L1 model-selection architecture: registry + policy + selectAgent ----
  await scenario('selectAgent: deterministic tiering, two-strike escalation, quota shift, calibration gate', async (check) => {
    const { loadRegistry, loadRouting, selectAgent, resolveRoutingClass, isBatchEligible } = await import('./routing.mjs');
    const reg = loadRegistry();
    const pol = loadRouting(undefined, reg);
    const seq = [0, 1, 2, 3, 4].map((n) => selectAgent('certified-mechanical', n, null, reg, pol).model);
    check('two-strike escalation walks the ladder (strikes 0,1→haiku; 2,3→sonnet; 4→opus)',
      seq.join(',') === 'claude-haiku-4-5,claude-haiku-4-5,claude-sonnet-5,claude-sonnet-5,claude-opus-4-8', seq.join(','));
    check('efforts come from the policy, exact ids from the registry', selectAgent('extraction', 0, null, reg, pol).model === 'claude-sonnet-5' && selectAgent('extraction', 0, null, reg, pol).effort === 'high');
    const q = selectAgent('async-order-oracle', 0, { pools: { 'openai-sub': 0.95 } }, reg, pol);
    check('quota pressure shifts to same-or-higher tier on the other pool, with a note', q.model === 'claude-opus-4-8' && q.notes.some((n) => /quota-pressure/.test(n) && /shifted/.test(n)), q.notes.join(' | '));
    const q2 = selectAgent('hard-ambiguous', 0, { pools: { 'claude-sub': 0.99 } }, reg, pol);
    check('no alternate pool → proceed WITH a ledger-visible note (honest, not silent)', q2.model === 'claude-opus-4-8' && q2.notes.some((n) => /no eligible alternate pool/.test(n)), q2.notes.join(' | '));
    const pf = selectAgent('async-order-oracle', 0, null, reg, pol, { providerFilter: 'claude' });
    check('providerFilter constrains to the CLI-chosen provider (hone work case)', pf.provider === 'claude' && pf.model === 'claude-opus-4-8');
    let threw = null;
    try { selectAgent('extraction', 0, null, reg, pol, { providerFilter: 'codex' }); } catch (e) { threw = e.message; }
    check('providerFilter with no candidates throws fail-closed', /no candidates for provider 'codex'/.test(threw ?? ''), threw);
    // calibration gate: an uncalibrated registry entry is routing-INELIGIBLE
    const regU = JSON.parse(JSON.stringify(reg));
    regU.models['haiku-4.5'].calibration = null;
    const su = selectAgent('certified-mechanical', 0, null, regU, pol);
    check('uncalibrated entry skipped with an explicit note (fail-closed gate)', su.model === 'claude-sonnet-5' && su.notes.some((n) => /NO calibration report/.test(n)), su.notes.join(' | '));
    check('allowUncalibrated override routes it (callers must ledger-note)', selectAgent('certified-mechanical', 0, null, regU, pol, { allowUncalibrated: true }).model === 'claude-haiku-4-5');
    const regAll = JSON.parse(JSON.stringify(reg));
    regAll.models['opus-4.8'].calibration = null;
    let threw2 = null;
    try { selectAgent('hard-ambiguous', 0, null, regAll, pol); } catch (e) { threw2 = e.message; }
    check('all candidates ineligible → throws (never a silent default)', /no eligible candidate/.test(threw2 ?? ''), threw2);
    check('class resolution: pin > action override > proof map', resolveRoutingClass({ routing_class: 'hard-ambiguous', action: 'preserve_refactor', proof_class: 'type_only' }, pol) === 'hard-ambiguous'
      && resolveRoutingClass({ action: 'generate_evidence', proof_class: 'judgment_first' }, pol) === 'async-order-oracle'
      && resolveRoutingClass({ action: 'preserve_refactor', proof_class: 'exact_move' }, pol) === 'certified-mechanical');
    check('batch eligibility: routine yes; high wrongness no; property_at_risk no', isBatchEligible(basePacket(), pol).eligible === true
      && isBatchEligible(basePacket({ risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'high', property_at_risk: null } }), pol).eligible === false
      && isBatchEligible(basePacket({ risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: 'bearer-token scope' } }), pol).eligible === false);
  });

  await scenario('validator: routing_class pins a CLASS, never a model', async (check) => {
    const { validatePacket } = await import('./validate-packet.mjs');
    check('valid class pin accepted', validatePacket(basePacket({ routing_class: 'certified-mechanical' })).length === 0);
    const errsModel = validatePacket(basePacket({ routing_class: 'opus' }));
    check('model-name pin rejected with the doctrine message', errsModel.some((e) => /never a specific model/.test(e) && /tier choice stays out of maker hands/.test(e)), errsModel.join(' | '));
    check('unknown class rejected', validatePacket(basePacket({ routing_class: 'bogus-class' })).some((e) => /routing_class/.test(e)));
    // a model-pinned packet cannot even be authored via writePacket; simulate a hand-edited
    // YAML on disk and confirm emit's schema gate refuses it before any side effect
    const root = fixtureRepo();
    writeFileSync(join(root, 'quality/packets', `${ID}.yaml`), stringifyYaml({ ...basePacket(), routing_class: 'gpt-5.5' }));
    const r = await emit(root);
    check('emit refuses a model-pinned packet before any side effect', r.exitCode === 2 && /never a specific model/.test(r.json.reason), r.json.reason);
  });

  await scenario('emit: routing block emitted (materialized candidates + batch eligibility)', async (check) => {
    const root = fixtureRepo();
    const r = await emit(root, { dryRun: true });
    check('routing.class resolved (certified_transform → extraction)', r.json.routing?.class === 'extraction', JSON.stringify(r.json.routing));
    check('candidates materialized from the registry (exact id + short alias + effort + pool)', r.json.routing.maker[0].model === 'claude-sonnet-5' && r.json.routing.maker[0].short === 'sonnet' && r.json.routing.maker[0].effort === 'high' && r.json.routing.maker[0].quota_pool === 'claude-sub');
    check('calibration flag carried per candidate', r.json.routing.maker.every((m) => m.calibrated === true));
    check('batch_eligible verdict included', r.json.routing.batch_eligible?.eligible === true);
  });

  await scenario('hone work: selectAgent drives maker model/effort with two-strike escalation', async (check) => {
    const { executeWork } = await import('./work.mjs');
    // proof_class exact_move → certified-mechanical: ladder haiku(med) → sonnet(high) → opus(high)
    const root = fixtureRepo({ packetOverrides: { proof_class: 'exact_move' } });
    const seen = [];
    let m = 0;
    const edits = [ST_BAD, ST_GOOD, ST_GOOD]; // oracle red once (strike 1), judge REVISE (strike 2), then PASS
    let j = 0;
    const judges = [{ verdict: 'REVISE', reasoning: 'name the guard intent', confidence: 0.6 }, { verdict: 'PASS', reasoning: 'revised acceptably', confidence: 0.85 }];
    const deps = {
      maker: async (name, prompt, opts) => {
        seen.push({ model: opts.model, effort: opts.effort });
        editUtil(root, edits[m++]);
        return { text: 'mock maker done', meta: { provider: name, model: opts.model, durationMs: 1, costUsd: 0.01, tokens: { input: 10, output: 5 } } };
      },
      judge: async (name) => ({ judge: async () => ({ ...judges[j++], raw: { provider: name, attempts: [] } }) }),
      log: () => {},
    };
    const r = await executeWork({ id: ID, repoRoot: root, makerName: 'claude', judgeName: 'codex', dryRun: false }, deps);
    check('landed after oracle-red + judge-REVISE cycles', r.outcome === 'landed', r.summary);
    check('maker models: haiku, haiku (strike 1), sonnet (two-strike escalation)',
      seen.map((s) => s.model).join(',') === 'claude-haiku-4-5,claude-haiku-4-5,claude-sonnet-5', JSON.stringify(seen));
    check('efforts routed alongside models (medium → medium → high)', seen.map((s) => s.effort).join(',') === 'medium,medium,high');
  });

  // ---- baseline cache (never pay for the same context twice at one HEAD) ----
  await scenario('emit baseline cache: identical rung at the same HEAD reused across batch-member emits', async (check) => {
    const root = fixtureRepo({ secondPacket: {} });
    const logs1 = [];
    await emit(root, { log: (s) => logs1.push(s) });
    check('first emit runs the rung fresh (no share note)', !logs1.some((l) => /shared/.test(l)), logs1.join(' | '));
    check('cache dir populated at this HEAD', existsSync(join(root, 'quality', '.lane', '.baseline-cache')));
    const logs2 = [];
    await executeLaneEmit({ id: ID2, repoRoot: root, makerProvider: 'claude', judgeProvider: 'claude', log: (s) => logs2.push(s) });
    check('second emit REUSES the shared rung result (engine-run, same HEAD, clean tree)', logs2.some((l) => /direct-test.*shared: engine-run result reused/.test(l)), logs2.join(' | '));
    check('non-shared rung still runs fresh', logs2.some((l) => /test2\.js/.test(l) && !/shared/.test(l)));
    check('both packets in_progress with their own baselines', packetOnDisk(root).status === 'in_progress' && packetOnDisk(root, ID2).status === 'in_progress');
  });

  // ---- batch verification mode (L2) ----
  const emit2 = (root) => executeLaneEmit({ id: ID2, repoRoot: root, makerProvider: 'claude', judgeProvider: 'claude' });
  const bgate = (root, ids) => executeLaneBatchGate({ ids, repoRoot: root });
  const bland = (root, ids, extra = {}) => executeLaneBatchLand({ ids, repoRoot: root, ...extra });

  await scenario('batch gate refusals: <2 ids / not emitted / risky class / touchset overlap — all fail-closed', async (check) => {
    const r1 = await bgate(fixtureRepo(), [ID]);
    check('single-id batch refused', r1.exitCode === 2 && /needs >= 2/.test(r1.json.reason), r1.json.reason);
    const root2 = fixtureRepo({ secondPacket: {} });
    await emit(root2); // ID2 NOT emitted
    const r2 = await bgate(root2, [ID, ID2]);
    check('unemitted member refuses the whole batch', r2.exitCode === 2 && new RegExp(`\\[${ID2}\\] status 'pending'`).test(r2.json.reason), r2.json.reason);
    const root3 = fixtureRepo({ secondPacket: { risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: 'oauth grant scope' } } });
    await emit(root3); await emit2(root3);
    const r3 = await bgate(root3, [ID, ID2]);
    check('RISKY member (named property_at_risk) refuses batching, names it', r3.exitCode === 2 && /risky-class/.test(r3.json.reason) && /property_at_risk/.test(r3.json.reason) && r3.json.reason.includes(ID2), r3.json.reason);
    check('risky refusal is side-effect-free (both members still in_progress)', packetOnDisk(root3).status === 'in_progress' && packetOnDisk(root3, ID2).status === 'in_progress');
    const root4 = fixtureRepo({ secondPacket: { touchset: ['src/util.js'], files: ['src/util.js'] } });
    await emit(root4); await emit2(root4);
    const r4 = await bgate(root4, [ID, ID2]);
    check('overlapping touchsets refused (per-order commits impossible)', r4.exitCode === 2 && /touchset overlap/.test(r4.json.reason), r4.json.reason);
  });

  await scenario('batch happy path: ONE suite-level run (union dedupe) → one judge → per-order commits + anchor accounting', async (check) => {
    const root = fixtureRepo({ secondPacket: {} });
    await emit(root); await emit2(root);
    editUtil(root, ST_GOOD);
    writeFileSync(join(root, 'src/util2.js'), ST_GOOD2);
    const g = await bgate(root, [ID, ID2]);
    check('batch gate green', g.exitCode === 0 && g.json.green === true, JSON.stringify(g.json).slice(0, 300));
    const batchReceipts = join(root, 'quality', 'receipts', g.json.batch_id);
    check('union DEDUPE: 3 evidence rungs across members, only 2 unique receipts (shared rung ran once)',
      existsSync(join(batchReceipts, 'post-1-direct-test.txt')) && existsSync(join(batchReceipts, 'post-2-direct-test-2.txt')) && !existsSync(join(batchReceipts, 'post-3-direct-test.txt')));
    const jc = JSON.parse(read(root, `quality/.lane/.batch/${g.json.batch_id}/judge-context.json`));
    check('batch judge context: both packet yamls + combined diff', Object.keys(jc.packet_yamls).sort().join(',') === `${ID},${ID2}` && /util\.js/.test(jc.diff) && /util2\.js/.test(jc.diff));
    const l = await bland(root, [ID, ID2], { verdictRaw: verdictJson('PASS', 'both flattenings clean, combined diff coherent'), usageRaw: USAGE_STAGED });
    check('batch land ok, 2 commits', l.exitCode === 0 && l.json.commits.length === 2, JSON.stringify(l.json).slice(0, 300));
    const p1 = packetOnDisk(root), p2 = packetOnDisk(root, ID2);
    const shas = spawnSync('git', ['log', '--format=%H|%s', '-3'], { cwd: root, encoding: 'utf8' }).stdout.trim().split('\n');
    check('each order is its OWN commit, in order (per-order revertability)', shas[0].includes(`[hone ${ID2}]`) && shas[1].includes(`[hone ${ID}]`) && p1.outcome.commit === shas[1].split('|')[0] && p2.outcome.commit === shas[0].split('|')[0]);
    check('commit trailer names the batch pipeline', /hone lane batch b-[0-9a-f]+ \(2 orders\)/.test(spawnSync('git', ['log', '-1', '--format=%B'], { cwd: root, encoding: 'utf8' }).stdout));
    const c1 = costs(root).find((c) => c.candidate_id === ID), c2 = costs(root).find((c) => c.candidate_id === ID2);
    check('ANCHOR accounting: usage ONCE on the anchor; non-anchor honest-null (sums stay honest)', c1.tokens_in === 1500 && c1.quota_pts === 1.5 && c2.tokens_in === null && !('quota_pts' in c2 ? c2.quota_pts !== null : false), JSON.stringify([c1.tokens_in, c2.tokens_in]));
    check('batch marker on BOTH entries {batch_id, size 2, anchor=first}', c1.batch?.size === 2 && c1.batch?.anchor === ID && c2.batch?.batch_id === c1.batch?.batch_id);
    check('stages recorded on the anchor only', Array.isArray(c1.stages) && !c2.stages);
    check('tree clean; lane + batch state cleaned', treeClean(root) && !existsSync(stateDir(root)) && !existsSync(join(root, `quality/.lane/.batch/${g.json.batch_id}`)));
  });

  await scenario('batch bisect: seeded offender isolated + reverted; green remainder lands', async (check) => {
    const root = fixtureRepo({ secondPacket: {} });
    await emit(root); await emit2(root);
    editUtil(root, ST_GOOD);                             // member 1: good
    writeFileSync(join(root, 'src/util2.js'), ST_BAD2);  // member 2: seeded offender (breaks test2)
    const g = await bgate(root, [ID, ID2]);
    check('gate green for the REMAINDER after bisect', g.exitCode === 0 && g.json.green === true && g.json.members.join(',') === ID, JSON.stringify(g.json).slice(0, 300));
    check('offender named', g.json.offenders.join(',') === ID2);
    const p2 = packetOnDisk(root, ID2);
    check('offender terminalized reverted with the ISOLATION claim (only-this-change evidence)', p2.status === 'reverted' && claims(root).some((c) => c.candidate_id === ID2 && c.type === 'verified_fact' && /with ONLY/.test(c.statement) && /direct-test-2/.test(c.statement)), JSON.stringify(claims(root).filter((c) => c.candidate_id === ID2).map((c) => c.statement)));
    check('offender change reverted byte-identical; survivor change preserved', read(root, 'src/util2.js') === ST_ORIG2 && read(root, 'src/util.js') === ST_GOOD);
    check('offender diff saved for forensics', /clamp2/.test(read(root, `quality/.lane/.batch/${g.json.batch_id}/offender-${ID2}.diff`) ?? ''));
    const l = await bland(root, [ID], { verdictRaw: verdictJson('PASS', 'survivor clean'), usageRaw: USAGE_STAGED });
    check('single-member remainder lands (no batch marker at size 1)', l.exitCode === 0 && packetOnDisk(root).status === 'landed' && !costs(root).find((c) => c.candidate_id === ID && c.landed)?.batch);
    check('tree clean after remainder land', treeClean(root));
  });

  await scenario('batch: no-diff member skipped, remainder proceeds; batch land refuses membership mismatch', async (check) => {
    const root = fixtureRepo({ secondPacket: {} });
    await emit(root); await emit2(root);
    editUtil(root, ST_GOOD); // only member 1 has a diff
    const g = await bgate(root, [ID, ID2]);
    check('gate green for the active member; no-diff member recorded', g.exitCode === 0 && g.json.members.join(',') === ID && g.json.results.some((r) => r.id === ID2 && /maker-no-diff/.test(r.reason)), JSON.stringify(g.json).slice(0, 300));
    check('no-diff member skipped honestly', /maker-no-diff \(batch/.test(packetOnDisk(root, ID2).outcome.skip_reason ?? ''));
    const wrong = await bland(root, [ID, ID2], { verdictRaw: verdictJson('PASS', 'x'), usageRaw: USAGE_STAGED });
    check('land with the WRONG membership refused (must land the exact green set)', wrong.exitCode === 2, wrong.json.reason);
    const l = await bland(root, [ID], { verdictRaw: verdictJson('PASS', 'clean'), usageRaw: USAGE_STAGED });
    check('exact green membership lands', l.exitCode === 0 && packetOnDisk(root).status === 'landed');
  });

  await scenario('batch land: judge REJECT reverts EVERY member (never land without PASS)', async (check) => {
    const root = fixtureRepo({ secondPacket: {} });
    await emit(root); await emit2(root);
    editUtil(root, ST_GOOD);
    writeFileSync(join(root, 'src/util2.js'), ST_GOOD2);
    await bgate(root, [ID, ID2]);
    const l = await bland(root, [ID, ID2], { verdictRaw: verdictJson('REJECT', 'combined diff hides a relocation'), usageRaw: USAGE_STAGED });
    check('reverted exit 1', l.exitCode === 1 && l.json.terminal === 'reverted');
    check('both members reverted, both files restored', packetOnDisk(root).status === 'reverted' && packetOnDisk(root, ID2).status === 'reverted' && read(root, 'src/util.js') === ST_ORIG && read(root, 'src/util2.js') === ST_ORIG2);
    check('shared verdict line names the batch', /\[batch b-[0-9a-f]+, 2 order\(s\)\]/.test(packetOnDisk(root).outcome.judge_verdict ?? ''), packetOnDisk(root).outcome.judge_verdict ?? '');
    check('no commits', headInfo(root).startsWith('init fixture'));
  });

  // ---- hone calibrate: the ledger-replay seam (v1 stub, real mechanics) ----
  await scenario('calibrate: replay seam over landed ground truth; report gates routing eligibility', async (check) => {
    const { executeCalibrate } = await import('./calibrate.mjs');
    const root = fixtureRepo();
    const r0 = executeCalibrate({ model: 'sonnet-5', replay: 1, repoRoot: root });
    check('no landed packets → refused (calibration needs ground truth)', r0.exitCode === 2 && /land something first/.test(r0.json.reason), r0.json.reason);
    await landHappy(root, USAGE_STAGED); // produce a real landed order + commit
    const r1 = executeCalibrate({ model: 'nope-9000', replay: 1, repoRoot: root });
    check('unknown model refused, names the registry', r1.exitCode === 2 && /not a models.json registry name/.test(r1.json.reason));
    check('replay < 1 refused', executeCalibrate({ model: 'sonnet-5', replay: 0, repoRoot: root }).exitCode === 2);
    const r = executeCalibrate({ model: 'sonnet-5', replay: 3, repoRoot: root });
    check('calibrate ok over the landed order', r.exitCode === 0 && r.json.replayable === 1 && r.json.of === 1, JSON.stringify(r.json));
    const report = JSON.parse(readFileSync(r.json.report_path, 'utf8'));
    check('report: replay set carries ground truth (commit+parent) + routing class', report.replay_set[0].candidate_id === ID && /^[0-9a-f]{40}$/.test(report.replay_set[0].parent) && report.replay_set[0].routing_class === 'extraction');
    check('report is HONEST about being a stub (not eligibility evidence)', report.status === 'stub-v1' && /NOT sufficient for routing eligibility/.test(report.honest_note));
    check('measured fields honest-null (no fabricated benchmarks)', Object.values(report.measured).every((v) => v === null));
    const wt = spawnSync('git', ['worktree', 'list'], { cwd: root, encoding: 'utf8' }).stdout.trim().split('\n');
    check('scratch worktrees cleaned up', wt.length === 1, wt.join(' | '));
  });

  // ---- workflows/hone-lane.js: the REAL script, mocked agents, real engine ----
  // The workflow body is executed exactly as the harness would (async function with
  // args/agent/phase/log): the mock agent plays a PERFECT dumb pipe (actually runs the
  // engine command), a scripted maker (applies edits), and a scripted judge (schema
  // verdicts). Everything below the agent boundary — emit/gate/land, receipts, ledgers,
  // commits — is the real engine on a real fixture repo.
  const honeDir = join(dirname(fileURLToPath(import.meta.url)), '..');
  const loadWorkflow = () => {
    const src = readFileSync(join(honeDir, 'workflows', 'hone-lane.js'), 'utf8').replace('export const meta', 'const meta');
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    return new AsyncFunction('args', 'agent', 'phase', 'log', src);
  };
  const mockAgents = (root, { makerEdits, judgeVerdicts }) => {
    const calls = [];
    let m = 0, j = 0;
    const agent = async (prompt, opts = {}) => {
      calls.push({ label: opts.label ?? null, model: opts.model ?? null, prompt });
      if (/Run exactly this ONE command/.test(prompt)) {
        const cm = prompt.match(/\n\n(cd [^\n]+)\n\n/);
        const r = spawnSync('/bin/bash', ['-c', cm ? cm[1] : 'false'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
        return r.stdout || 'EMPTY';
      }
      if (opts.schema) {
        const v = judgeVerdicts[j++];
        return v ?? { verdict: 'REVISE', reasoning: 'no scripted verdict left', confidence: 0 };
      }
      const edit = makerEdits[m++];
      if (typeof edit === 'function') edit(root);
      else if (typeof edit === 'string') writeFileSync(join(root, 'src/util.js'), edit);
      return 'Flattened clamp into guard clauses. Behavior preserved: same boundaries, same returns.';
    };
    return { agent, calls };
  };

  await scenario('workflow hone-lane.js e2e: happy path → landed via the real engine', async (check) => {
    const root = fixtureRepo();
    const { agent, calls } = mockAgents(root, { makerEdits: [ST_GOOD], judgeVerdicts: [{ verdict: 'PASS', reasoning: 'behavior preserved, genuine simplification', confidence: 0.9 }] });
    const res = await loadWorkflow()({ packets: [ID], repo: root, honeDir }, agent, () => {}, () => {});
    check('workflow tally: 1 landed', res?.tally?.landed === 1, JSON.stringify(res?.tally));
    const p = packetOnDisk(root);
    check('packet landed on disk', p.status === 'landed');
    const body = spawnSync('git', ['log', '-1', '--format=%B'], { cwd: root, encoding: 'utf8' }).stdout;
    check('commit names the lane substrate + model-qualified pair', /hone lane: maker=claude:sonnet judge=claude:opus/.test(body), body.slice(0, 200));
    check('model tiers used: haiku pipe, sonnet maker, opus judge', calls.some((c) => c.model === 'haiku') && calls.some((c) => c.model === 'sonnet' && /make:/.test(c.label)) && calls.some((c) => c.model === 'opus' && /judge:/.test(c.label)));
    check('judge read the engine-written context file, not agent relay', calls.find((c) => /judge:/.test(c.label ?? ''))?.prompt.includes('judge-context.json'));
    check('cost ledger written with explicit (null-token) usage', costs(root)[0]?.landed === true && costs(root)[0]?.tokens_in === null);
    check('tree clean after workflow', treeClean(root));
  });

  await scenario('workflow hone-lane.js e2e: oracle red → engine revision brief → fix → landed (revisions=1)', async (check) => {
    const root = fixtureRepo();
    const { agent, calls } = mockAgents(root, { makerEdits: [ST_BAD, ST_GOOD], judgeVerdicts: [{ verdict: 'PASS', reasoning: 'revised acceptably', confidence: 0.85 }] });
    const res = await loadWorkflow()({ packets: [ID], repo: root, honeDir }, agent, () => {}, () => {});
    check('landed after one oracle revision', res?.tally?.landed === 1 && costs(root)[0]?.revision_count === 1, JSON.stringify(res?.results));
    const revise = calls.find((c) => /revise:/.test(c.label ?? ''));
    check('revision maker pointed at the on-disk revision brief', revise != null && /revision-brief-1\.txt/.test(revise.prompt), revise?.prompt.slice(0, 200));
    check('post + post-r1 receipts on disk', ['post', 'post-r1'].every((ph) => existsSync(join(root, `quality/receipts/${ID}/${ph}-1-direct-test.txt`))));
  });

  await scenario('workflow hone-lane.js e2e: judge REJECT → reverted books, no commit', async (check) => {
    const root = fixtureRepo();
    const { agent } = mockAgents(root, { makerEdits: [ST_GOOD], judgeVerdicts: [{ verdict: 'REJECT', reasoning: 'relocation dressed as refactoring', confidence: 0.95 }] });
    const res = await loadWorkflow()({ packets: [ID], repo: root, honeDir }, agent, () => {}, () => {});
    check('workflow tally: 1 reverted', res?.tally?.reverted === 1, JSON.stringify(res?.tally));
    check('packet reverted, judge_verdict recorded', packetOnDisk(root).status === 'reverted' && /REJECT/.test(packetOnDisk(root).outcome.judge_verdict ?? ''));
    check('tree restored, no commit', read(root, 'src/util.js') === ST_ORIG && headInfo(root).startsWith('init fixture'));
  });

  await scenario('workflow hone-lane.js e2e: two-strike escalation walks the routed ladder (haiku→haiku→sonnet)', async (check) => {
    // certified-mechanical pin: ladder haiku(med) → sonnet(high) → opus(high). Two reds
    // then a fix: revision 1 stays haiku (strike 1), revision 2 escalates to sonnet (strike 2).
    const root = fixtureRepo({ packetOverrides: { routing_class: 'certified-mechanical' } });
    const { agent, calls } = mockAgents(root, { makerEdits: [ST_BAD, ST_BAD, ST_GOOD], judgeVerdicts: [{ verdict: 'PASS', reasoning: 'fixed after escalation', confidence: 0.8 }] });
    const res = await loadWorkflow()({ packets: [ID], repo: root, honeDir }, agent, () => {}, () => {});
    check('landed after two revisions', res?.tally?.landed === 1 && costs(root)[0]?.revision_count === 2, JSON.stringify(res?.results));
    const makerModels = calls.filter((c) => /^(make|revise):/.test(c.label ?? '')).map((c) => c.model);
    check('maker tier sequence haiku → haiku → sonnet (two-strike)', makerModels.join(',') === 'haiku,haiku,sonnet', makerModels.join(','));
    const c0 = costs(root)[0];
    check('landed usage records the ESCALATED maker model + stage attribution', c0.stages?.some((s) => s.role === 'maker' && s.model === 'sonnet' && s.stage === 'edit'), JSON.stringify(c0.stages));
    check('receipt phases post/post-r1/post-r2 on disk (engine ceiling exactly consumed)', ['post', 'post-r1', 'post-r2'].every((ph) => existsSync(join(root, `quality/receipts/${ID}/${ph}-1-direct-test.txt`))));
  });

  await scenario('workflow hone-lane.js e2e: batch arm — one maker pass, one judge, per-order commits', async (check) => {
    const root = fixtureRepo({ secondPacket: {} });
    const editBoth = (r) => { writeFileSync(join(r, 'src/util.js'), ST_GOOD); writeFileSync(join(r, 'src/util2.js'), ST_GOOD2); };
    const { agent, calls } = mockAgents(root, { makerEdits: [editBoth], judgeVerdicts: [{ verdict: 'PASS', reasoning: 'combined diff clean', confidence: 0.9 }] });
    const res = await loadWorkflow()({ packets: [ID, ID2], repo: root, honeDir, batch: true }, agent, () => {}, () => {});
    check('both members landed via the batch arm', res?.tally?.landed === 2, JSON.stringify(res?.tally));
    const makerCalls = calls.filter((c) => /make:batch/.test(c.label ?? ''));
    check('ONE persistent maker call carried BOTH briefs (L4 locality)', makerCalls.length === 1 && makerCalls[0].prompt.includes(`quality/.lane/${ID}/maker-brief.txt`) && makerCalls[0].prompt.includes(`quality/.lane/${ID2}/maker-brief.txt`), makerCalls.length);
    check('ONE judge call over the batch judge-context', calls.filter((c) => /judge:batch/.test(c.label ?? '')).length === 1);
    const shas = spawnSync('git', ['log', '--format=%s', '-3'], { cwd: root, encoding: 'utf8' }).stdout;
    check('per-order commits present', shas.includes(`[hone ${ID}]`) && shas.includes(`[hone ${ID2}]`));
    check('tree clean after batch workflow', treeClean(root));
  });

  await scenario('workflow hone-lane.js: refuses makerModel == judgeModel before any work', async (check) => {
    const root = fixtureRepo();
    const { agent, calls } = mockAgents(root, { makerEdits: [], judgeVerdicts: [] });
    const res = await loadWorkflow()({ packets: [ID], repo: root, honeDir, makerModel: 'sonnet', judgeModel: 'sonnet' }, agent, () => {}, () => {});
    check('refused with maker-eq-judge, zero agent calls', res?.error === 'maker-eq-judge' && calls.length === 0, JSON.stringify(res));
    check('packet untouched (still pending)', packetOnDisk(root).status === 'pending');
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
