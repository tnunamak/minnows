#!/usr/bin/env node
// test-report-run.mjs — offline self-tests for report.mjs + run.mjs.
//
// No LLM calls, no network, no real `hone work` (that is another lane's deliverable):
// the loop is exercised against test-report-run-stub.mjs through the exact CLI contract
// (`<cmd> <id> --repo ... --maker ... --judge ...`, exit 0 = landed, details in ledgers).
//
// Matrix:
//   1. packetsConflict unit cases (shared file / subsystem overlap / fail-safe on missing)
//   2. honesty-gate units (red: unbacked overclaims wrap as UNVERIFIED; green: backed pass)
//   3. report compilation from seeded ledgers — gate red-then-green in compiled text,
//      open questions, skip-reason distribution, malformed-ledger errors, determinism
//   4. lane-conflict serialization — timestamp proof (shared-touchset packets never
//      overlap; disjoint ones do overlap across 2 lanes) + report compiled at the end
//   5. stop condition — 2 consecutive infrastructure crashes stop the lane/run
//   6. honest terminals (reverted/skipped/blocked) do NOT stop the lane
//   7. gating — owner_ratify / missing gate / unmet depends_on are never executed
//   8. refusals — --budget unimplemented, maker==judge
//   9. ordering — a persisted priority.score outranks the enum-derived fallback
//
// Exit 0 iff every check passes.
import { execSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stringifyYaml, parseYaml } from './yaml.mjs';
import { assertValidPacket } from './validate-packet.mjs';
import { compileReport, runReport, gateStatement, effectiveClaimType } from './report.mjs';
import { runLoop, packetsConflict, packetPriority } from './run.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const STUB = join(HERE, 'test-report-run-stub.mjs');
const WORK_CMD = `${process.execPath} ${STUB}`;

const checks = [];
const ok = (name, cond, detail = '') => {
  checks.push({ name, pass: !!cond });
  if (!cond) process.stderr.write(`FAIL ${name}${detail ? ` — ${detail}` : ''}\n`);
};
const occurrences = (text, needle) => text.split(needle).length - 1;

// ---------------------------------------------------------------- fixtures

function makePacket(id, { subsystem, file, gate = 'autonomous', status = 'pending', dependsOn = [], outcome = {} }) {
  return {
    candidate_id: id,
    created: '2026-07-01T00:00:00.000Z',
    repo_sha: 'fixture0000',
    subsystem,
    files: [file],
    symbols: ['fixtureFn'],
    public_surface: [],
    behavior_status: 'likely_intended',
    ownership: 'OWN',
    action: 'preserve_refactor',
    proof_class: 'pure_logic',
    execution_gate: gate,
    why_this_matters: 'seeded fixture for the report/run self-test',
    plan: { transform_class: 'concept-seam-split', instruction: 'seeded fixture instruction' },
    expected_quality_gain: 'medium',
    owner_attention_reduction: 'medium',
    product_impact: 'none',
    risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: null },
    authoring_evidence: [{ kind: 'fixture', detail: 'seeded by test-report-run.mjs', result: 'n/a' }],
    evidence_required: [{ rung: 'direct-test', command: 'node --test', expect: 'all pass' }],
    not_allowed: ['behavior-change'],
    maker_tier: 'standard',
    judge_tier: 'standard',
    maker_provider: null,
    judge_provider: null,
    batch_key: `preserve_refactor×pure_logic×${subsystem}`,
    touchset: [file],
    estimates: { tokens: 1000, evidence_cost: 'low' },
    depends_on: dependsOn,
    unlocks: [],
    status,
    outcome: {
      commit: null, skip_reason: null, blocked_on: null, judge_verdict: null,
      evidence_receipts: [], tokens_actual: null, lesson: null, ...outcome,
    },
  };
}

function newRepo(name) {
  const root = mkdtempSync(join(tmpdir(), `hone-rr-${name}-`));
  mkdirSync(join(root, 'quality', 'packets'), { recursive: true });
  // real repos are git repos; an initial commit keeps gitFacts() quiet and honest
  execSync('git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m fixture', { cwd: root });
  return root;
}

function writePacket(root, packet, { validate = true } = {}) {
  if (validate) assertValidPacket(packet, packet.candidate_id);
  writeFileSync(join(root, 'quality', 'packets', `${packet.candidate_id}.yaml`), stringifyYaml(packet));
}

function writeStubPlan(root, plan) {
  const p = join(root, 'stub-plan.json');
  writeFileSync(p, JSON.stringify(plan));
  process.env.HONE_STUB_PLAN = p;
}

function readTrace(root) {
  const p = join(root, 'quality', 'stub-trace.jsonl');
  if (!existsSync(p)) return [];
  return readFileSync(p, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

const packetStatus = (root, id) =>
  parseYaml(readFileSync(join(root, 'quality', 'packets', `${id}.yaml`), 'utf8')).status;
const overlaps = (x, y) => x.start_ms < y.end_ms && y.start_ms < x.end_ms;

// ---------------------------------------------------------------- 1. conflict units

{
  const A = { candidate_id: 'a', subsystem: 'srva', touchset: ['srva/a.mjs'] };
  const B = { candidate_id: 'b', subsystem: 'srva', touchset: ['srva/a.mjs'] };
  const C = { candidate_id: 'c', subsystem: 'srvc', touchset: ['srvc/c.mjs'] };
  const D = { candidate_id: 'd', subsystem: 'srvd', touchset: ['srva/deep/x.mjs'] }; // reaches into A's subsystem
  const E = { candidate_id: 'e', subsystem: 'srve', touchset: [] };                  // fail-safe
  ok('conflict: shared file serializes', packetsConflict(A, B) === true);
  ok('conflict: disjoint subsystems run parallel', packetsConflict(A, C) === false);
  ok('conflict: file under other subsystem serializes', packetsConflict(A, D) === true && packetsConflict(D, A) === true);
  ok('conflict: empty touchset serializes with everything (fail-safe)', packetsConflict(E, C) === true);
  ok('conflict: missing touchset serializes (fail-safe)', packetsConflict({ candidate_id: 'f', subsystem: 'x' }, C) === true);
}

// ---------------------------------------------------------------- 2. honesty-gate units

{
  const red1 = { type: 'hypothesis', statement: 'error handling is clean now', evidence: [], judge: null };
  const red2 = { type: 'verified_fact', statement: 'the migration is done', evidence: [], judge: null };
  const red3 = { type: 'judged_design_claim', statement: 'the API is first-class', evidence: [], judge: null };
  const green1 = { type: 'verified_fact', statement: 'the refactor is complete: cc 31 -> 9', evidence: [{ command: 'node scope-fn.mjs', output_digest: 'aabbccdd' }], judge: null };
  const green2 = { type: 'judged_design_claim', statement: 'the seam split is first-class', evidence: [], judge: { provider: 'codex', verdict: 'PASS — genuine decomplect' } };
  const neutral = { type: 'hypothesis', statement: 'the retry path may be dead', evidence: [], judge: null };
  ok('gate red: unbacked hypothesis overclaim wraps', gateStatement(red1) === '[UNVERIFIED: error handling is clean now]');
  ok('gate red: verified_fact WITHOUT evidence wraps', gateStatement(red2) === '[UNVERIFIED: the migration is done]');
  ok('gate red: judged claim WITHOUT judge wraps', gateStatement(red3) === '[UNVERIFIED: the API is first-class]');
  ok('gate green: evidenced verified_fact passes verbatim', gateStatement(green1) === green1.statement);
  ok('gate green: judged claim with named judge passes verbatim', gateStatement(green2) === green2.statement);
  ok('gate: non-overclaim statements untouched', gateStatement(neutral) === neutral.statement);
  ok('downgrade: evidence-less verified_fact treated as hypothesis', effectiveClaimType(red2) === 'hypothesis');
  ok('downgrade: judged claim without judge treated as hypothesis', effectiveClaimType(red3) === 'hypothesis');
  ok('no downgrade when evidence attached', effectiveClaimType(green1) === 'verified_fact');
}

// ---------------------------------------------------------------- 3. report compilation

{
  const root = newRepo('report');
  writePacket(root, makePacket('rr-ra', {
    subsystem: 'srva', file: 'srva/a.mjs', status: 'landed',
    outcome: { commit: 'abc1234', judge_verdict: 'PASS — real seam', evidence_receipts: ['node --test → 12/12 pass'] },
  }));
  writePacket(root, makePacket('rr-rb', {
    subsystem: 'srvb', file: 'srvb/b.mjs', status: 'skipped',
    outcome: { skip_reason: 'T1c: capture crosses txn boundary' },
  }));
  const claims = [
    { claim_id: 'clm-1', created: '2026-07-01T01:00:00Z', candidate_id: 'rr-ra', type: 'verified_fact', statement: 'the records aggregate refactor is complete: cc 31 -> 9', evidence: [{ command: 'node collectors/scope-fn.mjs', output_digest: 'aabbccdd' }], judge: null },
    { claim_id: 'clm-2', created: '2026-07-01T01:01:00Z', candidate_id: 'rr-rb', type: 'hypothesis', statement: 'error handling is clean now', evidence: [], judge: null },
    { claim_id: 'clm-3', created: '2026-07-01T01:02:00Z', candidate_id: 'rr-ra', type: 'verified_fact', statement: 'the migration is done', evidence: [], judge: null },
    { claim_id: 'clm-4', created: '2026-07-01T01:03:00Z', candidate_id: 'rr-ra', type: 'uncertainty', statement: 'unclear whether the retry path is exercised in prod', evidence: [], judge: null },
    { claim_id: 'clm-5', created: '2026-07-01T01:04:00Z', candidate_id: 'rr-rb', type: 'remaining_work', statement: 'wire the new helper into the second call site', evidence: [], judge: null },
    { claim_id: 'clm-6', created: '2026-07-01T01:05:00Z', candidate_id: 'rr-ra', type: 'judged_design_claim', statement: 'the seam split is first-class', evidence: [], judge: { provider: 'codex', verdict: 'PASS — genuine decomplect, not relocation' } },
  ];
  const claimLines = [...claims.map((c) => JSON.stringify(c)),
    '{not json at all',
    JSON.stringify({ claim_id: 'clm-x', created: '2026-07-01T01:06:00Z', candidate_id: 'rr-ra', type: 'victory_lap', statement: 'x' })];
  writeFileSync(join(root, 'quality', 'claims.jsonl'), claimLines.join('\n') + '\n');
  const cost = [
    { job_id: 'job-rr-ra-1', created: '2026-07-01T01:10:00Z', candidate_id: 'rr-ra', workflow: 'preserve_refactor', maker: { provider: 'claude', tier: 'standard' }, judge: { provider: 'codex', tier: 'standard' }, tokens_in: 120000, tokens_out: 8000, cost_usd: 0.42, wall_time_s: 132.5, landed: true, revision_count: 1, judge_result: 'PASS', outcome: 'landed', followup_created: [] },
    { job_id: 'job-rr-rb-1', created: '2026-07-01T01:11:00Z', candidate_id: 'rr-rb', workflow: 'preserve_refactor', maker: { provider: 'claude', tier: 'standard' }, judge: { provider: 'codex', tier: 'standard' }, tokens_in: null, tokens_out: null, cost_usd: null, wall_time_s: 20.1, landed: false, revision_count: 0, judge_result: null, outcome: 'skipped', followup_created: [] },
  ];
  writeFileSync(join(root, 'quality', 'cost.jsonl'),
    cost.map((c) => JSON.stringify(c)).join('\n') + '\n' + JSON.stringify({ job_id: 'job-bad' }) + '\n');

  const r1 = compileReport(root);
  const r2 = compileReport(root);
  const t = r1.text;
  ok('report: deterministic — identical ledgers compile to identical bytes', r1.text === r2.text && r1.digest === r2.digest);
  ok('report red: unbacked "clean" claim rendered UNVERIFIED', t.includes('[UNVERIFIED: error handling is clean now]'));
  ok('report red: every occurrence of the unbacked statement is wrapped',
    occurrences(t, 'error handling is clean now') === occurrences(t, '[UNVERIFIED: error handling is clean now]'));
  ok('report red: evidence-less "done" verified_fact rendered UNVERIFIED', t.includes('[UNVERIFIED: the migration is done]'));
  ok('report green: evidenced "complete" claim rendered verbatim',
    t.includes('the records aggregate refactor is complete: cc 31 -> 9') &&
    !t.includes('[UNVERIFIED: the records aggregate refactor is complete'));
  ok('report green: judged "first-class" claim rendered verbatim with named judge',
    t.includes('the seam split is first-class') && !t.includes('[UNVERIFIED: the seam split is first-class') &&
    t.includes('judge codex: "PASS — genuine decomplect, not relocation"'));
  ok('report: open questions surface hypotheses + uncertainties',
    t.includes('## Open questions') && t.includes('unclear whether the retry path is exercised in prod'));
  ok('report: remaining work surfaced', t.includes('wire the new helper into the second call site'));
  ok('report: skip-reason distribution present', t.includes('1× T1c: capture crosses txn boundary'));
  ok('report: judge verdict verbatim gist from packet outcome', t.includes('judge verdict (verbatim gist): "PASS — real seam"'));
  ok('report: cost actuals compiled', t.includes('$0.42') && t.includes('per landed packet: $0.42 (1 landed)'));
  ok('report: malformed ledger lines reported, not skipped silently',
    r1.ledgerErrors.length === 3 && t.includes('unparseable JSON') && t.includes('victory_lap') && t.includes('job-bad'));

  const outPath = await runReport({ repo: root });
  const outPath2 = await runReport({ repo: root });
  ok('report: runReport writes digest-named file, stable across runs',
    outPath === outPath2 && existsSync(outPath) && readFileSync(outPath, 'utf8') === t);
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 4. lane-conflict serialization (timestamp proof)

{
  const root = newRepo('lanes');
  writePacket(root, makePacket('rr-la', { subsystem: 'srva', file: 'srva/a.mjs' }));
  writePacket(root, makePacket('rr-lb', { subsystem: 'srva', file: 'srva/a.mjs' })); // same touchset file as rr-la
  writePacket(root, makePacket('rr-lc', { subsystem: 'srvc', file: 'srvc/c.mjs' })); // disjoint
  writeStubPlan(root, {
    'rr-la': { result: 'landed', sleep_ms: 500 },
    'rr-lb': { result: 'landed', sleep_ms: 500 },
    'rr-lc': { result: 'landed', sleep_ms: 500 },
  });
  const summary = await runLoop({ repo: root, n: '3', lanes: '2', 'work-cmd': WORK_CMD });
  const trace = Object.fromEntries(readTrace(root).map((x) => [x.candidate_id, x]));
  ok('lanes: all three packets executed and landed',
    summary.landed === 3 && ['rr-la', 'rr-lb', 'rr-lc'].every((id) => packetStatus(root, id) === 'landed'));
  ok('lanes: shared-touchset packets NEVER overlap (timestamps)',
    trace['rr-la'] && trace['rr-lb'] && !overlaps(trace['rr-la'], trace['rr-lb']),
    JSON.stringify({ a: trace['rr-la'], b: trace['rr-lb'] }));
  ok('lanes: disjoint packet DID run concurrently (timestamps)',
    trace['rr-lc'] && (overlaps(trace['rr-lc'], trace['rr-la']) || overlaps(trace['rr-lc'], trace['rr-lb'])),
    JSON.stringify(trace));
  ok('lanes: run finishes by compiling the report',
    summary.reportPath && existsSync(summary.reportPath) &&
    readdirSync(join(root, 'quality', 'reports')).length === 1);
  const reportText = readFileSync(summary.reportPath, 'utf8');
  ok('lanes: compiled report reflects the stub ledgers', reportText.includes('- landed: 3'));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 5. stop condition: 2 consecutive infra failures

{
  const root = newRepo('stop');
  writePacket(root, makePacket('rr-ka', { subsystem: 'srva', file: 'srva/a.mjs' }));
  writePacket(root, makePacket('rr-kb', { subsystem: 'srvb', file: 'srvb/b.mjs' }));
  writePacket(root, makePacket('rr-kc', { subsystem: 'srvc', file: 'srvc/c.mjs' }));
  writeStubPlan(root, {
    'rr-ka': { result: 'crash', sleep_ms: 50 },
    'rr-kb': { result: 'crash', sleep_ms: 50 },
    'rr-kc': { result: 'landed', sleep_ms: 50 },
  });
  const summary = await runLoop({ repo: root, n: '3', lanes: '1', 'work-cmd': WORK_CMD });
  ok('stop: 2 consecutive crashes stop the lane and the run',
    summary.infra === 2 && summary.unexecuted === 1 && summary.landed === 0);
  ok('stop: the queued packet after the stop is untouched',
    packetStatus(root, 'rr-kc') === 'pending' && !readTrace(root).some((x) => x.candidate_id === 'rr-kc'));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 6. honest terminals do NOT stop the lane

{
  const root = newRepo('honest');
  writePacket(root, makePacket('rr-ha', { subsystem: 'srva', file: 'srva/a.mjs' }));
  writePacket(root, makePacket('rr-hb', { subsystem: 'srvb', file: 'srvb/b.mjs' }));
  writePacket(root, makePacket('rr-hc', { subsystem: 'srvc', file: 'srvc/c.mjs' }));
  writePacket(root, makePacket('rr-hd', { subsystem: 'srvd', file: 'srvd/d.mjs' }));
  writeStubPlan(root, {
    'rr-ha': { result: 'reverted', sleep_ms: 50 },
    'rr-hb': { result: 'skipped', sleep_ms: 50 },
    'rr-hc': { result: 'blocked', sleep_ms: 50 },
    'rr-hd': { result: 'landed', sleep_ms: 50 },
  });
  const summary = await runLoop({ repo: root, n: '4', lanes: '1', 'work-cmd': WORK_CMD });
  ok('honest: reverted/skipped/blocked are normal outcomes, loop continues to the end',
    summary.infra === 0 && summary.unexecuted === 0 &&
    summary.reverted === 1 && summary.skipped === 1 && summary.blocked === 1 && summary.landed === 1);
  ok('honest: terminal statuses written through the work contract',
    packetStatus(root, 'rr-ha') === 'reverted' && packetStatus(root, 'rr-hb') === 'skipped' &&
    packetStatus(root, 'rr-hc') === 'blocked' && packetStatus(root, 'rr-hd') === 'landed');
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 7. gating: never execute what run must refuse

{
  const root = newRepo('gate');
  writePacket(root, makePacket('rr-ga', { subsystem: 'srva', file: 'srva/a.mjs', gate: 'owner_ratify' }));
  const noGate = makePacket('rr-gb', { subsystem: 'srvb', file: 'srvb/b.mjs' });
  delete noGate.execution_gate; // ungated packet — schema-invalid on purpose
  writePacket(root, noGate, { validate: false });
  writePacket(root, makePacket('rr-gc', { subsystem: 'srvc', file: 'srvc/c.mjs', dependsOn: ['rr-ga'] }));
  writePacket(root, makePacket('rr-gd', { subsystem: 'srvd', file: 'srvd/d.mjs' }));
  writeStubPlan(root, { 'rr-gd': { result: 'landed', sleep_ms: 50 } });
  const summary = await runLoop({ repo: root, n: '10', lanes: '2', 'work-cmd': WORK_CMD });
  ok('gating: owner_ratify / ungated / unmet-dependency packets are refused',
    summary.selected === 1 && summary.landed === 1);
  ok('gating: refused packets untouched on disk',
    packetStatus(root, 'rr-ga') === 'pending' && packetStatus(root, 'rr-gb') === 'pending' &&
    packetStatus(root, 'rr-gc') === 'pending' && packetStatus(root, 'rr-gd') === 'landed');
  ok('gating: only the autonomous packet ever ran', readTrace(root).map((x) => x.candidate_id).join(',') === 'rr-gd');
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 8. refusals

{
  const root = newRepo('refuse');
  let budgetErr = null, mjErr = null;
  try { await runLoop({ repo: root, budget: '5' }); } catch (e) { budgetErr = e.message; }
  try { await runLoop({ repo: root, maker: 'claude', judge: 'claude' }); } catch (e) { mjErr = e.message; }
  ok('refusal: --budget is not implemented in v1', /--budget is not implemented/.test(budgetErr || ''));
  ok('refusal: maker==judge rejected structurally', /maker and judge MUST differ/.test(mjErr || ''));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 9. persisted priority beats enum fallback

{
  const root = newRepo('prio');
  // rr-pa: WORST enums (fallback rank 1*1*1/(1*1*2) = 0.5) but carries a persisted plan prior.
  const withPrior = makePacket('rr-pa', { subsystem: 'srva', file: 'srva/a.mjs' });
  withPrior.expected_quality_gain = 'low';
  withPrior.owner_attention_reduction = 'low';
  withPrior.priority = { score: 9.9, computed: '2026-07-01T00:00:00.000Z', inputs: { mass: 40, churn: 12 } };
  writePacket(root, withPrior); // validate:true — proves the validator accepts the optional block
  // rr-pb: better enums (fallback rank 2*2*1/(1*1*2) = 2), NO persisted priority (hand-authored shape).
  writePacket(root, makePacket('rr-pb', { subsystem: 'srvb', file: 'srvb/b.mjs' }));
  ok('priority: persisted score preferred, enum fallback when absent',
    packetPriority(withPrior) === 9.9 && packetPriority(makePacket('rr-pb', { subsystem: 'srvb', file: 'srvb/b.mjs' })) === 2);
  writeStubPlan(root, {
    'rr-pa': { result: 'landed', sleep_ms: 50 },
    'rr-pb': { result: 'landed', sleep_ms: 50 },
  });
  const summary = await runLoop({ repo: root, n: '2', lanes: '1', 'work-cmd': WORK_CMD });
  const order = readTrace(root).sort((a, b) => a.start_ms - b.start_ms).map((x) => x.candidate_id).join(',');
  ok('priority: persisted-prior packet executes BEFORE the higher-enum fallback packet',
    summary.landed === 2 && order === 'rr-pa,rr-pb', `execution order: ${order}`);
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- verdict

const failed = checks.filter((c) => !c.pass);
process.stdout.write(`\ntest-report-run: ${checks.length - failed.length}/${checks.length} checks passed\n`);
for (const c of checks) process.stdout.write(`  ${c.pass ? 'ok  ' : 'FAIL'} ${c.name}\n`);
process.exit(failed.length ? 1 : 0);
