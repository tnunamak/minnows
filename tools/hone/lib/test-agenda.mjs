#!/usr/bin/env node
// test-agenda.mjs — offline self-tests for agenda.mjs + agenda-consume.mjs + the agenda
// consumption in run.mjs/report.mjs. Zero LLM calls, zero network: the strong-model call is a
// scripted mock exec; `hone run` is exercised through test-report-run-stub.mjs via the same
// CLI contract the report/run suite uses.
//
// Matrix:
//   1. dry-run — context assembled + prompt printed, NO provider call, NO writes
//   2. emit + citation verifier — sensor citation that fails to reproduce marks the item
//      UNVERIFIED and demotes it below every verified item (json re-rank + md note);
//      artifacts written (AGENDA.{md,json}, history, not-chosen, selection ledger)
//   3. output-contract retry — garbage first reply retried strict once, then fail-loud
//   4. NOT-chosen aging across agendas — age_count increments; chosen items drop out
//   5. --challenge blindness — incumbent agenda/ledger/not-chosen NEVER in the prompt; codex
//      family; challenge + diff artifacts only (incumbent untouched, no ledger append)
//   6. deterministic floor units — negctl always scheduled, in-flight campaign precedence,
//      aged-omission minimum, agenda-rank ordering
//   7. run integration — AGENDA.json governs order (in-flight first), negctl executed, batch
//      record appended; no AGENDA.json → ordering/fallback behavior unchanged
//   8. threshold flags — named-target starvation + out-of-band class allocation for 3
//      consecutive batches flag ⚠ DIVERGENCE in the report; green inputs do not
//   9. chooser calibration + computed budget line + formula-vs-agenda diff in the report
//
// Exit 0 iff every check passes.
import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stringifyYaml } from './yaml.mjs';
import { assertValidPacket } from './validate-packet.mjs';
import {
  assembleAgendaContext, buildAgendaPrompt, normalizeModelAgenda, verifySensorCitation,
  loadSensorIndex, verifyAndRank, executeAgenda, diffAgendas,
} from './agenda.mjs';
import {
  packetPriority, isCalibrationPacket, campaignStates, orderExecutableByAgenda,
  applyAgendaFloor, agedNotChosenIds, divergenceFlags, readAgendaArtifacts,
} from './agenda-consume.mjs';
import { compileReport } from './report.mjs';
import { runLoop } from './run.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const WORK_CMD = `${process.execPath} ${join(HERE, 'test-report-run-stub.mjs')}`;

const checks = [];
const ok = (name, cond, detail = '') => {
  checks.push({ name, pass: !!cond });
  if (!cond) process.stderr.write(`FAIL ${name}${detail ? ` — ${detail}` : ''}\n`);
};

// ---------------------------------------------------------------- fixtures

function seedInventory(root) {
  const inv = join(root, 'quality', 'inventory');
  mkdirSync(inv, { recursive: true });
  writeFileSync(join(inv, 'meta.json'), JSON.stringify({
    repo_sha: 'fixtureinventorysha0000000000000000000000', owned_dirs: ['srva'], cog_threshold: 5,
    counts: { flagged_fns: 2, flagged_files: 1, total_excess_mass: 30 },
  }));
  writeFileSync(join(inv, 'tier-mass.json'), JSON.stringify({
    generated_from: { repo_sha: 'fixture' },
    universe_tier_count: { T0: 1, 'T1-seam': 1 },
    universe_tier_mass: { T0: 5, 'T1-seam': 25 },
    by_subsystem: [{ subsystem: 'srva', files: 1, fns: 2, mass: 30, tiers: { T0: 5 } }],
    by_file: [{ file: 'srva/a.mjs', churn: 12, fns: 2, mass: 30, tiers: { T0: 5, 'T1-seam': 25 }, attention: 360 }],
    top_candidates: [{ file: 'srva/a.mjs', fn: 'bigFn', line: 10, cc: 40, excess: 35, churn: 12, attention: 420, tier: 'T1-seam', is_callback: false, dominant_file_tier: 'T1-seam' }],
    universe: [
      { file: 'srva/a.mjs', line: 10, cc: 40, excess: 35, fn: 'bigFn', is_anon: false, is_callback: false, tier: 'T1-seam' },
      { file: 'srva/a.mjs', line: 90, cc: 7, excess: 2, fn: 'smallFn', is_anon: false, is_callback: false, tier: 'T0' },
    ],
  }));
  writeFileSync(join(inv, 'hotspots.json'), JSON.stringify({
    generated_from: {}, files: [{ file: 'srva/a.mjs', loc: 120, churn: 12, cog: 40, coupling: 3, score: 99, nogo: false }],
  }));
  writeFileSync(join(inv, 'callback-smells.json'), JSON.stringify({
    generated_from: {}, by_class: { T1b: 1 }, mass_by_class: { T1b: 9 }, by_kind: { iterator: 1 }, b_flagged: 0,
    callbacks: [{ file: 'srva/a.mjs', parent_fn: 'bigFn', callback_anchor: '.map', callback_kind: 'iterator', cc: 9, excess: 4, captured_vars: ['x'], captured_mutable_vars: [], recommended_class: 'T1b', why: 'captures x' }],
  }));
  writeFileSync(join(inv, 'test-signals.json'), JSON.stringify({
    generated_from: { repo_sha: 'fixture', test_files: 2 },
    skips: { total: 4, pattern: 'static', files: [{ file: 'test/slow.test.mjs', skips: 3 }, { file: 'test/old.test.mjs', skips: 1 }] },
    zero_by_name: { by_name_only: true, note: 'weak', files: [{ file: 'srva/dark.mjs', exports: 5, unreferenced: ['darkOne', 'darkTwo'], by_name_only: true }] },
  }));
}

function seedDoctrine(root) {
  const p = join(root, 'DOCTRINE-fixture.md');
  writeFileSync(p, ['# fixture doctrine', '', 'Budget direction: B 40-50% · A2 30-40% · T1 10-15% · T0 5-10%.',
    'Named target: storage unification (the dual-backend braid).', 'Named target: prevention ratchet.'].join('\n'));
  return p;
}

function newAgendaRepo(name, { profileAgenda = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), `hone-ag-${name}-`));
  execSync('git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m fixture', { cwd: root });
  mkdirSync(join(root, 'quality', 'packets'), { recursive: true });
  seedInventory(root);
  const doctrine = seedDoctrine(root);
  const profile = { version: 1, agenda: { doctrine_path: doctrine, ...(profileAgenda ?? {}) } };
  writeFileSync(join(root, 'quality', 'hone.yaml'), stringifyYaml(profile));
  return { root, doctrine };
}

function makePacket(id, { subsystem = 'srva', file = 'srva/a.mjs', status = 'pending', dependsOn = [], proofClass = 'pure_logic', priorityScore = null, outcome = {} } = {}) {
  const p = {
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
    proof_class: proofClass,
    execution_gate: 'autonomous',
    why_this_matters: 'seeded fixture for the agenda self-test',
    plan: { transform_class: 'concept-seam-split', instruction: 'seeded fixture instruction' },
    expected_quality_gain: 'medium',
    owner_attention_reduction: 'medium',
    product_impact: 'none',
    risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: null },
    authoring_evidence: [{ kind: 'fixture', detail: 'seeded', result: 'n/a' }],
    evidence_required: [{ rung: 'direct-test', command: 'node --test', expect: 'all pass' }],
    not_allowed: ['behavior-change'],
    maker_tier: 'standard',
    judge_tier: 'standard',
    maker_provider: null,
    judge_provider: null,
    batch_key: `preserve_refactor×${proofClass}×${subsystem}`,
    touchset: [file],
    estimates: { tokens: 1000, evidence_cost: 'low' },
    depends_on: dependsOn,
    unlocks: [],
    status,
    outcome: { commit: null, skip_reason: null, blocked_on: null, judge_verdict: null, evidence_receipts: [], tokens_actual: null, lesson: null, ...outcome },
  };
  if (priorityScore != null) p.priority = { score: priorityScore, computed: '2026-07-01T00:00:00.000Z', inputs: { mass: 1, churn: 1 } };
  return p;
}

function writePacket(root, packet) {
  assertValidPacket(packet, packet.candidate_id);
  writeFileSync(join(root, 'quality', 'packets', `${packet.candidate_id}.yaml`), stringifyYaml(packet));
}

const fence = (obj) => 'Analysis…\n```json\n' + JSON.stringify(obj) + '\n```\n';
const mockMeta = (provider) => ({ provider, model: 'mock-strong', durationMs: 5, costUsd: 0.5, tokens: { input: 100, output: 50 } });

/** scripted exec: replies[] consumed in order; records every prompt + provider. */
function mockDeps(replies) {
  const state = { calls: [] };
  return {
    state,
    deps: {
      exec: async (provider, prompt) => {
        state.calls.push({ provider, prompt });
        const r = replies[state.calls.length - 1] ?? replies[replies.length - 1];
        if (r === 'ERROR') throw Object.assign(new Error('scripted provider failure'), { kind: 'timeout' });
        return { text: r, meta: mockMeta(provider) };
      },
      log: () => {},
    },
  };
}

const MODEL_AGENDA = {
  items: [
    { id: 'good-sensor', what: 'Decomplect bigFn in srva/a.mjs', why_now: 'top attention hairball', evidence: [{ type: 'sensor', citation: 'srva/a.mjs:cc[bigFn]=40' }, { type: 'sensor', citation: 'srva:mass=30' }], workflow_class: 'A2', packet_ids: ['rr-aa'], acceptance_criteria: ['cc strictly down'], est_cost: { usd: 5, basis: 'ledger actuals $0.5/landed' }, predicted_gain: 'high' },
    { id: 'bad-sensor', what: 'Refactor the phantom mass', why_now: 'claims a mass the sensors never measured', evidence: [{ type: 'sensor', citation: 'srva/a.mjs:mass=999' }], workflow_class: 'T1b', acceptance_criteria: ['n/a'], est_cost: { usd: 3, basis: 'actuals' } },
    { id: 'storage-unification-step', what: 'Storage unification campaign step (B proposal)', why_now: 'doctrine-named target', evidence: [{ type: 'corpus', citation: 'DOCTRINE fixture — named target: storage unification' }], workflow_class: 'B', campaign_id: 'storage-unification', acceptance_criteria: ['proposal packet ratified by owner'], est_cost: { usd: 8, basis: 'actuals' } },
  ],
  campaigns: [{ id: 'storage-unification', named_target: 'storage unification', why: 'dual-backend braid', acceptance_criteria: ['one storage seam, backends behind it'] }],
  not_chosen: [{ id: 'low-mass-tidy', what: 'small T0 tidy', reason: 'below the leverage bar this batch' }],
  deltas_from_prior: ['(no prior agenda)'],
  human_decisions_needed: ['ratify the storage unification proposal'],
};

// ---------------------------------------------------------------- 1. dry-run

{
  const { root, doctrine } = newAgendaRepo('dry');
  writePacket(root, makePacket('rr-aa'));
  const { state, deps } = mockDeps([fence(MODEL_AGENDA)]);
  const r = await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine, dryRun: true }, deps);
  ok('dry-run: no provider call', r.outcome === 'dry-run' && state.calls.length === 0);
  ok('dry-run: prints per-section byte sizes + prompt', /sensor:tier-mass/.test(r.summary) && /--- prompt follows ---/.test(r.summary));
  ok('dry-run: prompt carries doctrine + sensors + contract', /fixture doctrine/.test(r.prompt) && /cc=40/.test(r.prompt) && /est_cost/.test(r.prompt));
  ok('dry-run: nothing written', !existsSync(join(root, 'quality', 'AGENDA.json')) && !existsSync(join(root, 'quality', 'agendas')));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 2. emit + citation verifier

{
  const { root, doctrine } = newAgendaRepo('emit');
  writePacket(root, makePacket('rr-aa'));
  const { state, deps } = mockDeps([fence(MODEL_AGENDA)]);
  const r = await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine }, deps);
  ok('emit: incumbent agenda uses the claude family', state.calls[0].provider === 'claude');
  const aj = JSON.parse(readFileSync(join(root, 'quality', 'AGENDA.json'), 'utf8'));
  const md = readFileSync(join(root, 'quality', 'AGENDA.md'), 'utf8');
  ok('emit: AGENDA.json + AGENDA.md written', r.exitCode === 0 && md.length > 0);
  ok('emit: history file appended to quality/agendas/', readdirSync(join(root, 'quality', 'agendas')).some((f) => /^agenda-.*\.json$/.test(f)));
  ok('verifier: good sensor citations reproduce', aj.items.find((i) => i.id === 'good-sensor')?.verification === 'verified' &&
    aj.verification.sensor_citations === 3 && aj.verification.verified === 2 && aj.verification.failed === 1, JSON.stringify(aj.verification));
  const bad = aj.items.find((i) => i.id === 'bad-sensor');
  ok('verifier: unreproducible citation marks the item UNVERIFIED', bad?.verification === 'unverified' &&
    bad.evidence[0].verified === false && /does not reproduce/.test(bad.evidence[0].verify_detail ?? ''), JSON.stringify(bad));
  ok('verifier: UNVERIFIED item DEMOTED below all verified items (model #2 → consumable #3)',
    bad?.model_rank === 2 && bad?.rank === 3 &&
    aj.items.map((i) => `${i.rank}:${i.id}`).join(',') === '1:good-sensor,2:storage-unification-step,3:bad-sensor', JSON.stringify(aj.items.map((i) => [i.rank, i.id])));
  ok('verifier: demotion noted in AGENDA.md', /⚠ UNVERIFIED/.test(md) && /model ranked #2/.test(md) && /FAILED/.test(md));
  ok('emit: campaign carries named target + acceptance criteria, no packet specs', /storage unification/.test(md) && /done when:/.test(md));
  const nc = JSON.parse(readFileSync(join(root, 'quality', 'agendas', 'not-chosen.json'), 'utf8'));
  ok('emit: not-chosen persisted with age 1', nc.entries['low-mass-tidy']?.age_count === 1 && nc.entries['low-mass-tidy'].reason_latest.includes('leverage'));
  const ledger = readFileSync(join(root, 'quality', 'selection-ledger.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  ok('emit: selection ledger — one line per ranked item with predicted {gain, cost, class}', ledger.length === 3 &&
    ledger.every((l) => l.agenda_id === aj.agenda_id && Number.isInteger(l.rank) && 'class' in l.predicted && 'est_cost_usd' in l.predicted));
  ok('emit: ledger rank order is the verified-first consumable order', ledger.find((l) => l.item_id === 'bad-sensor')?.rank === 3);
  ok('emit: context bounded and sizes recorded', aj.total_context_bytes > 500 && aj.total_context_bytes < 45000 && aj.context_bytes['doctrine'] > 0);
  ok('emit: call cost recorded', aj.call.cost_usd === 0.5);

  // ---- 4. aging across a second + third agenda (same repo) ----
  const secondNotChosen = { ...MODEL_AGENDA, deltas_from_prior: ['kept shape'] };
  await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine }, mockDeps([fence(secondNotChosen)]).deps);
  const nc2 = JSON.parse(readFileSync(join(root, 'quality', 'agendas', 'not-chosen.json'), 'utf8'));
  ok('aging: age_count increments across agendas', nc2.entries['low-mass-tidy']?.age_count === 2, JSON.stringify(nc2));
  const third = { ...MODEL_AGENDA, items: [...MODEL_AGENDA.items, { id: 'low-mass-tidy', what: 'small T0 tidy — now chosen', why_now: 'aged in', evidence: [{ type: 'sensor', citation: 'srva/a.mjs:cc[smallFn]=7' }], workflow_class: 'T0', acceptance_criteria: ['x'], est_cost: { usd: 1, basis: 'actuals' } }], not_chosen: [] };
  await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine }, mockDeps([fence(third)]).deps);
  const nc3 = JSON.parse(readFileSync(join(root, 'quality', 'agendas', 'not-chosen.json'), 'utf8'));
  ok('aging: chosen item leaves the not-chosen file', !('low-mass-tidy' in nc3.entries), JSON.stringify(nc3));

  // ---- 5. challenge blindness (incumbent + ledger + not-chosen now exist) ----
  writeFileSync(join(root, 'quality', 'agendas', 'not-chosen.json'), JSON.stringify({ version: 1, entries: { 'ZZMARKER-not-chosen': { age_count: 2, reason_latest: 'ZZMARKER-reason' } } }));
  const incumbentBefore = readFileSync(join(root, 'quality', 'AGENDA.json'), 'utf8');
  const ledgerBefore = readFileSync(join(root, 'quality', 'selection-ledger.jsonl'), 'utf8');
  const challengerReply = { ...MODEL_AGENDA, items: [MODEL_AGENDA.items[0], { id: 'challenger-only', what: 'a thing only the challenger sees', why_now: 'independent take', evidence: [{ type: 'sensor', citation: 'srva/a.mjs:churn=12' }], workflow_class: 'T1a', acceptance_criteria: ['x'], est_cost: { usd: 2, basis: 'actuals' } }] };
  const { state: chState, deps: chDeps } = mockDeps([fence(challengerReply)]);
  const cr = await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine, challenge: true }, chDeps);
  ok('challenge: uses the OTHER provider family (codex)', chState.calls[0].provider === 'codex');
  const chPrompt = chState.calls[0].prompt;
  ok('challenge: BLIND — no prior-agenda/ledger/not-chosen section in the prompt',
    !/PRIOR-AGENDA/i.test(chPrompt) && !/SELECTION-LEDGER/i.test(chPrompt) && !/NOT-CHOSEN/i.test(chPrompt));
  ok('challenge: BLIND — incumbent content absent from the prompt', !chPrompt.includes('storage-unification-step') && !chPrompt.includes('ZZMARKER'));
  ok('challenge: blind-mode instruction present', /BLIND CHALLENGE MODE/.test(chPrompt));
  const agFiles = readdirSync(join(root, 'quality', 'agendas'));
  ok('challenge: emits challenge-<ts>.json + diff summary', agFiles.some((f) => /^challenge-.*\.json$/.test(f)) && agFiles.some((f) => /-diff\.md$/.test(f)), agFiles.join(','));
  ok('challenge: incumbent AGENDA.json untouched, no ledger append, not-chosen untouched',
    readFileSync(join(root, 'quality', 'AGENDA.json'), 'utf8') === incumbentBefore &&
    readFileSync(join(root, 'quality', 'selection-ledger.jsonl'), 'utf8') === ledgerBefore &&
    readFileSync(join(root, 'quality', 'agendas', 'not-chosen.json'), 'utf8').includes('ZZMARKER'));
  const diffMd = readFileSync(join(root, 'quality', 'agendas', agFiles.find((f) => /-diff\.md$/.test(f))), 'utf8');
  ok('challenge diff: shared ranks + only-in-one lists', /Shared items: 1/.test(diffMd) && /only-in-challenger: 1/.test(diffMd) && /Only in incumbent/.test(diffMd), diffMd.slice(0, 300));
  ok('challenge diff: rank divergence table rendered', /\| challenger # \| incumbent # \|/.test(diffMd));
  ok('challenge outcome', cr.outcome === 'challenge' && cr.agenda.challenge === true);
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 3. output-contract retry + fail-loud

{
  const { root, doctrine } = newAgendaRepo('retry');
  const { state, deps } = mockDeps(['no json here at all', fence(MODEL_AGENDA)]);
  const r = await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine }, deps);
  ok('retry: unparseable first reply → ONE strict retry succeeds', r.exitCode === 0 && state.calls.length === 2 && /ONLY the single fenced/.test(state.calls[1].prompt));
  rmSync(root, { recursive: true, force: true });
}
{
  const { root, doctrine } = newAgendaRepo('fail');
  const { deps } = mockDeps(['garbage', 'still garbage']);
  let err = null;
  try { await executeAgenda({ repoRoot: root, gitRoot: root, repoSha: 'sha', doctrinePath: doctrine }, deps); } catch (e) { err = e.message; }
  ok('fail-loud: two contract violations → throws, nothing written', /fail-loud/.test(err || '') && !existsSync(join(root, 'quality', 'AGENDA.json')), err ?? '(no error)');
  ok('contract: items without typed evidence rejected by the normalizer',
    normalizeModelAgenda({ items: [{ id: 'x', what: 'w', why_now: 'y', evidence: [], workflow_class: 'T0', acceptance_criteria: ['a'], est_cost: { usd: 1 } }] }).errors.some((e) => /never bare judgment/.test(e)));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- verifier micro-units

{
  const { root } = newAgendaRepo('verif');
  const idx = loadSensorIndex(root);
  ok('citation: file metric reproduces', verifySensorCitation('srva/a.mjs:mass=30', idx).ok);
  ok('citation: subsystem metric reproduces', verifySensorCitation('srva:mass=30', idx).ok);
  ok('citation: hotspot metric reproduces', verifySensorCitation('srva/a.mjs:score=99', idx).ok);
  ok('citation: per-fn cc reproduces', verifySensorCitation('srva/a.mjs:cc[bigFn]=40', idx).ok);
  ok('citation: wrong value fails', !verifySensorCitation('srva/a.mjs:mass=31', idx).ok);
  ok('citation: unknown file fails closed', !verifySensorCitation('nope.mjs:mass=30', idx).ok);
  ok('citation: unknown metric fails closed', !verifySensorCitation('srva/a.mjs:vibes=30', idx).ok);
  ok('citation: test-signals skips metric reproduces', verifySensorCitation('test/slow.test.mjs:skips=3', idx).ok);
  ok('citation: test-signals skips wrong value fails', !verifySensorCitation('test/slow.test.mjs:skips=4', idx).ok);
  ok('citation: untested_exports metric reproduces', verifySensorCitation('srva/dark.mjs:untested_exports=5', idx).ok);
  ok('citation: untested_exports unknown file fails closed', !verifySensorCitation('srva/a.mjs:untested_exports=5', idx).ok);
  ok('citation: malformed grammar fails closed', !verifySensorCitation('just a claim', idx).ok);
  const items = normalizeModelAgenda(MODEL_AGENDA).doc.items;
  const { items: ranked, stats } = verifyAndRank(items, idx);
  ok('verifyAndRank: stable within partitions', ranked.map((i) => i.id).join(',') === 'good-sensor,storage-unification-step,bad-sensor' && stats.failed === 1);
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 6. deterministic floor units

{
  const camp1 = makePacket('camp-a', { status: 'landed' });
  const camp2 = makePacket('camp-b', { dependsOn: ['camp-a'] });
  const newC1 = makePacket('newcamp-a');
  const newC2 = makePacket('newcamp-b', { dependsOn: ['newcamp-a'] });
  const solo = makePacket('solo-top');
  const negctl = makePacket('df-negctl-fixture-1');
  const aged = makePacket('aged-x');
  const pool = [camp1, camp2, newC1, newC2, solo, negctl, aged];
  const agenda = { agenda_id: 'agenda-t', items: [
    { id: 'i1', rank: 1, packet_ids: ['solo-top'], workflow_class: 'T1b' },
    { id: 'i2', rank: 2, packet_ids: ['camp-b'], workflow_class: 'A2' },
    { id: 'i3', rank: 3, packet_ids: ['newcamp-a'], workflow_class: 'T1a' },
  ] };
  ok('floor: calibration packet detected by id', isCalibrationPacket(negctl) && !isCalibrationPacket(solo));
  const states = campaignStates(pool);
  ok('floor: depends_on graph classifies in-flight vs new campaigns',
    states.get('camp-b') === 'inflight' && states.get('newcamp-a') === 'new' && states.get('solo-top') === 'none');
  const executable = [camp2, newC1, newC2, solo, negctl, aged];
  const ordered = orderExecutableByAgenda(executable, pool, agenda);
  ok('order: in-flight campaign FIRST, then agenda rank, then formula for unranked',
    ordered.map((p) => p.candidate_id).slice(0, 3).join(',') === 'camp-b,solo-top,newcamp-a', ordered.map((p) => p.candidate_id).join(','));
  const q1 = applyAgendaFloor(ordered.slice(0, 3), ordered, { n: 3, agedIds: new Set(['aged-x']) });
  ok('floor: negctl forced into a FULL queue by evicting the tail',
    q1.queue.some((p) => p.candidate_id === 'df-negctl-fixture-1') && q1.notes.some((s) => /negative-control/.test(s)), JSON.stringify(q1.notes));
  ok('floor: aged-omission packet also guaranteed', q1.queue.some((p) => p.candidate_id === 'aged-x') && q1.notes.some((s) => /aged-omission/.test(s)));
  ok('floor: in-flight head never evicted', q1.queue[0].candidate_id === 'camp-b');
  const q2 = applyAgendaFloor(ordered.slice(0, 2), ordered, { n: 6, agedIds: new Set() });
  ok('floor: appends (no eviction) when the queue is not full', q2.queue.length === 3 && q2.queue[2].candidate_id === 'df-negctl-fixture-1');
  const q3 = applyAgendaFloor([negctl, aged], [negctl, aged], { n: 2, agedIds: new Set(['aged-x']) });
  ok('floor: already-satisfied guarantees are no-ops', q3.queue.length === 2 && q3.notes.length === 0);
  ok('aged ids: threshold ≥3 respected', agedNotChosenIds({ a: { age_count: 3 }, b: { age_count: 2 }, c: { age_count: 5, packet_ids: ['pc'] } }).size === 3);
}

// ---------------------------------------------------------------- 7. run integration (stub work)

{
  const { root } = newAgendaRepo('run');
  writePacket(root, makePacket('camp-a', { status: 'landed', subsystem: 'srvz', file: 'srvz/z.mjs' }));
  writePacket(root, makePacket('camp-b', { dependsOn: ['camp-a'], subsystem: 'srvb', file: 'srvb/b.mjs' }));
  writePacket(root, makePacket('solo-top', { subsystem: 'srva', file: 'srva/a.mjs', priorityScore: 9.9 }));
  writePacket(root, makePacket('df-negctl-run-1', { subsystem: 'srvc', file: 'srvc/c.mjs' }));
  const agenda = {
    version: 1, agenda_id: 'agenda-fixture', created: '2026-07-02T00:00:00.000Z',
    verification: { sensor_citations: 1, verified: 1, failed: 0 },
    items: [
      { id: 'i1', rank: 1, model_rank: 1, verification: 'verified', what: 'solo', workflow_class: 'T1b', packet_ids: ['solo-top'], est_cost: { usd: 5 } },
      { id: 'i2', rank: 2, model_rank: 2, verification: 'verified', what: 'campaign step', workflow_class: 'A2', packet_ids: ['camp-b'], est_cost: { usd: 7 } },
    ],
    campaigns: [], not_chosen: [], deltas_from_prior: [], human_decisions_needed: [],
  };
  writeFileSync(join(root, 'quality', 'AGENDA.json'), JSON.stringify(agenda));
  const stubPlan = join(root, 'stub-plan.json');
  writeFileSync(stubPlan, JSON.stringify({
    'camp-b': { result: 'landed', sleep_ms: 30 }, 'solo-top': { result: 'landed', sleep_ms: 30 }, 'df-negctl-run-1': { result: 'landed', sleep_ms: 30 },
  }));
  process.env.HONE_STUB_PLAN = stubPlan;
  const summary = await runLoop({ repo: root, n: '10', lanes: '1', 'work-cmd': WORK_CMD });
  const trace = readFileSync(join(root, 'quality', 'stub-trace.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  const order = trace.sort((a, b) => a.start_ms - b.start_ms).map((x) => x.candidate_id).join(',');
  ok('run: in-flight campaign precedes the agenda #1 despite its 9.9 formula prior',
    order.startsWith('camp-b,solo-top'), `order: ${order}`);
  ok('run: negctl scheduled in the batch', trace.some((x) => x.candidate_id === 'df-negctl-run-1'));
  ok('run: all landed', summary.landed === 3, JSON.stringify(summary.executed));
  const batches = readFileSync(join(root, 'quality', 'agendas', 'batches.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  ok('run: batch record appended with agenda id + spend by DOCTRINE class (T1b normalizes to T1)', batches.length === 1 && batches[0].agenda_id === 'agenda-fixture' &&
    batches[0].jobs.length === 3 && batches[0].spend_by_class.A2 > 0 && batches[0].spend_by_class.T1 > 0, JSON.stringify(batches[0]));
  rmSync(root, { recursive: true, force: true });
}
{
  // fallback: NO AGENDA.json → ordering by formula prior, no batch record (behavior unchanged)
  const { root } = newAgendaRepo('fallback');
  writePacket(root, makePacket('rr-low', { subsystem: 'srva', file: 'srva/a.mjs' }));
  writePacket(root, makePacket('rr-high', { subsystem: 'srvb', file: 'srvb/b.mjs', priorityScore: 9.9 }));
  const stubPlan = join(root, 'stub-plan.json');
  writeFileSync(stubPlan, JSON.stringify({ 'rr-low': { result: 'landed', sleep_ms: 20 }, 'rr-high': { result: 'landed', sleep_ms: 20 } }));
  process.env.HONE_STUB_PLAN = stubPlan;
  await runLoop({ repo: root, n: '2', lanes: '1', 'work-cmd': WORK_CMD });
  const order = readFileSync(join(root, 'quality', 'stub-trace.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l))
    .sort((a, b) => a.start_ms - b.start_ms).map((x) => x.candidate_id).join(',');
  ok('fallback: no AGENDA.json → formula-prior order unchanged', order === 'rr-high,rr-low', order);
  ok('fallback: no batch record without an agenda', !existsSync(join(root, 'quality', 'agendas', 'batches.jsonl')));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 8. threshold flags (red / green)

{
  const profileAgenda = { named_targets: [{ id: 'storage-unification', keywords: ['storage'] }], budget_bands: { B: [40, 50] } };
  const starvedBatch = (i) => ({ batch_id: `b${i}`, created: 'x', spend_usd: 10, spend_by_class: { T1: 10 }, spend_by_target: { 'storage-unification': 0 }, jobs_by_target: { 'storage-unification': 0 } });
  const fedBatch = (i) => ({ batch_id: `b${i}`, created: 'x', spend_usd: 10, spend_by_class: { B: 4.5, T1: 5.5 }, spend_by_target: { 'storage-unification': 4.5 }, jobs_by_target: { 'storage-unification': 1 } });
  const red = divergenceFlags([starvedBatch(1), starvedBatch(2), starvedBatch(3)], profileAgenda);
  ok('flags red: starved named target + out-of-band class both flagged after 3 batches',
    red.length === 2 && red.some((f) => /storage-unification.*ZERO realized spend/.test(f)) && red.some((f) => /class 'B' allocation outside/.test(f)), JSON.stringify(red));
  ok('flags green: fed target + in-band class → no flags', divergenceFlags([fedBatch(1), fedBatch(2), fedBatch(3)], profileAgenda).length === 0);
  ok('flags: fewer than 3 batches cannot flag (fail-open)', divergenceFlags([starvedBatch(1), starvedBatch(2)], profileAgenda).length === 0);
  ok('flags: a single fed batch inside the window resets both', divergenceFlags([starvedBatch(1), fedBatch(2), starvedBatch(3)], profileAgenda).length === 0);

  // report renders the DIVERGENCE section prominently (red) and omits it (green)
  const { root } = newAgendaRepo('flags', { profileAgenda });
  mkdirSync(join(root, 'quality', 'agendas'), { recursive: true });
  for (const b of [starvedBatch(1), starvedBatch(2), starvedBatch(3)]) appendFileSync(join(root, 'quality', 'agendas', 'batches.jsonl'), JSON.stringify(b) + '\n');
  const redText = compileReport(root).text;
  ok('report red: ⚠ DIVERGENCE — OWNER ACK REQUIRED section rendered', redText.includes('## ⚠ DIVERGENCE — OWNER ACK REQUIRED') && /storage-unification/.test(redText));
  writeFileSync(join(root, 'quality', 'agendas', 'batches.jsonl'), [fedBatch(1), fedBatch(2), fedBatch(3)].map((b) => JSON.stringify(b)).join('\n') + '\n');
  const greenText = compileReport(root).text;
  ok('report green: no divergence section', !greenText.includes('⚠ DIVERGENCE'));
  ok('report: deterministic with agenda inputs', compileReport(root).text === greenText);
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 9. calibration + budget line + streetlight diff

{
  const { root } = newAgendaRepo('calib');
  writePacket(root, makePacket('rr-aa', { status: 'landed', proofClass: 'pure_logic', outcome: { commit: 'abc' } }));
  writePacket(root, makePacket('rr-bb', { subsystem: 'srvb', file: 'srvb/b.mjs', priorityScore: 9.9 }));
  writePacket(root, makePacket('rr-cc', { subsystem: 'srvc', file: 'srvc/c.mjs', priorityScore: 0.1 }));
  const agenda = {
    version: 1, agenda_id: 'agenda-calib', created: '2026-07-02T00:00:00.000Z',
    verification: { sensor_citations: 2, verified: 2, failed: 0 },
    items: [
      { id: 'i1', rank: 1, model_rank: 1, verification: 'verified', what: 'landed one', workflow_class: 'T1b', packet_ids: ['rr-aa'], est_cost: { usd: 4 } },
      { id: 'i2', rank: 2, model_rank: 2, verification: 'verified', what: 'low-formula pick', workflow_class: 'B', packet_ids: ['rr-cc'], est_cost: { usd: 6 } },
      { id: 'i3', rank: 3, model_rank: 3, verification: 'verified', what: 'high-formula pick', workflow_class: 'A2', packet_ids: ['rr-bb'], est_cost: { usd: 2 } },
    ],
    campaigns: [], not_chosen: [], deltas_from_prior: [], human_decisions_needed: [],
  };
  writeFileSync(join(root, 'quality', 'AGENDA.json'), JSON.stringify(agenda));
  for (const it of agenda.items) {
    appendFileSync(join(root, 'quality', 'selection-ledger.jsonl'), JSON.stringify({
      agenda_id: agenda.agenda_id, agenda_ts: agenda.created, item_id: it.id, rank: it.rank, model_rank: it.model_rank,
      verification: it.verification, predicted: { gain: 'g', est_cost_usd: it.est_cost.usd, class: it.workflow_class },
      packet_ids: it.packet_ids, campaign_id: null,
    }) + '\n');
  }
  writeFileSync(join(root, 'quality', 'cost.jsonl'), JSON.stringify({
    job_id: 'job-rr-aa-1', created: '2026-07-02T01:00:00Z', candidate_id: 'rr-aa', workflow: 'preserve_refactor',
    maker: { provider: 'claude', tier: 'standard' }, judge: { provider: 'codex', tier: 'standard' },
    tokens_in: 100, tokens_out: 10, cost_usd: 3.5, wall_time_s: 60, landed: true, revision_count: 0,
    judge_result: 'PASS', outcome: 'landed', followup_created: [],
  }) + '\n');
  const t = compileReport(root).text;
  ok('report: budget-composition line computed from AGENDA.json + cost ledger',
    /budget composition \(computed\): predicted — A2 \$2\.00 \(17%\) · B \$6\.00 \(50%\) · T1 \$4\.00 \(33%\) \| realized — T1 \$3\.50 \(100%\)/.test(t), t.match(/budget composition.*$/m)?.[0]);
  ok('report: chooser calibration joins predicted vs realized per class',
    t.includes('### Chooser calibration') && /- T1b: predicted 1 item\(s\), est \$4\.00 → realized: landed 1 · spent \$3\.50/.test(t), t.match(/- T1b:.*$/m)?.[0]);
  ok('report: formula-rank vs agenda-rank diff line rendered (streetlight-bias sensor)',
    /formula-rank vs agenda-rank \(streetlight-bias sensor\): top-2 overlap/.test(t) && /max displacement 1/.test(t), t.match(/formula-rank.*$/m)?.[0]);
  ok('report: incumbent line + verification stats surfaced', /incumbent: agenda-calib/.test(t) && /2\/2 sensor citation\(s\) reproduced/.test(t));
  // not-chosen surfaced by report
  mkdirSync(join(root, 'quality', 'agendas'), { recursive: true });
  writeFileSync(join(root, 'quality', 'agendas', 'not-chosen.json'), JSON.stringify({ version: 1, entries: { 'old-idea': { first_seen: 'x', last_seen: 'y', age_count: 4, reason_latest: 'still below bar' } } }));
  const t2 = compileReport(root).text;
  ok('report: NOT-chosen aging surfaced', /NOT-chosen aging/.test(t2) && /`old-idea` · age 4 · still below bar/.test(t2));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- context assembly + prompt units

{
  const { root, doctrine } = newAgendaRepo('ctx');
  writePacket(root, makePacket('rr-aa'));
  const { sections, totalBytes } = assembleAgendaContext({ repoRoot: root, gitRoot: root, doctrinePath: doctrine, blind: false });
  const labels = sections.map((s) => s.label);
  ok('context: sensor + pool + cost + doctrine sections assembled',
    ['sensor:meta', 'sensor:tier-mass', 'sensor:hotspots', 'sensor:callback-smells', 'sensor:test-signals', 'packet-pool', 'doctrine'].every((l) => labels.includes(l)), labels.join(','));
  ok('context: bounded total', totalBytes < 45000, String(totalBytes));
  const tsSection = sections.find((s) => s.label === 'sensor:test-signals');
  ok('context: test-signals digest carries skip ranking + the weak-signal label',
    /test\/slow\.test\.mjs · 3/.test(tsSection?.text ?? '') && /by_name_only/.test(tsSection?.text ?? '') && /srva\/dark\.mjs · 5/.test(tsSection?.text ?? ''), tsSection?.text?.slice(0, 200));

  // named_targets from the profile projection: FIRST-CLASS doctrine anchors, present even BLIND
  const profileAgenda = {
    named_targets: [
      { id: 'storage-unification', description: 'one storage seam', evidence_hint: 'hotspots srva/a.mjs', keywords: ['storage'] },
      { id: 'skipped-test-audit', description: 'audit every skip' },
    ],
    budget_bands: { B: [40, 50] },
  };
  const withTargets = assembleAgendaContext({ repoRoot: root, gitRoot: root, doctrinePath: doctrine, profileAgenda, blind: false });
  const ntSection = withTargets.sections.find((s) => s.label === 'doctrine:named-targets');
  ok('context: named-targets section rendered from the profile projection',
    !!ntSection && /storage-unification: one storage seam/.test(ntSection.text) && /evidence hint: hotspots/.test(ntSection.text) && /skipped-test-audit/.test(ntSection.text), ntSection?.text?.slice(0, 200));
  ok('context: named-targets section states the demotion-is-escalation rule',
    /ESCALATION/.test(ntSection?.text ?? '') && /human_decisions_needed/.test(ntSection?.text ?? ''));
  const blindTargets = assembleAgendaContext({ repoRoot: root, gitRoot: root, doctrinePath: doctrine, profileAgenda, blind: true });
  ok('context: named-targets survive BLIND mode (doctrine, not incumbent state)',
    blindTargets.sections.some((s) => s.label === 'doctrine:named-targets'));
  ok('context: no named-targets section without a projection',
    !sections.some((s) => s.label === 'doctrine:named-targets'));
  const ntPrompt = buildAgendaPrompt(withTargets.sections, { blind: false });
  ok('prompt: named targets land in the model prompt as a doctrine section',
    /== DOCTRINE:NAMED-TARGETS ==/.test(ntPrompt) && /storage-unification/.test(ntPrompt));
  writeFileSync(join(root, 'quality', 'AGENDA.json'), JSON.stringify({ version: 1, agenda_id: 'a-1', created: 'x', items: [{ id: 'zz', rank: 1, what: 'w', workflow_class: 'T0', packet_ids: [] }], not_chosen: [], campaigns: [] }));
  const blind = assembleAgendaContext({ repoRoot: root, gitRoot: root, doctrinePath: doctrine, blind: true });
  const open = assembleAgendaContext({ repoRoot: root, gitRoot: root, doctrinePath: doctrine, blind: false });
  ok('context: blind excludes the prior agenda; open includes it',
    !blind.sections.some((s) => s.label === 'prior-agenda') && open.sections.some((s) => s.label === 'prior-agenda'));
  const prompt = buildAgendaPrompt(open.sections, { blind: false });
  ok('prompt: citation grammar + output contract + doctrine-demotion rule present',
    /Sensor-citation grammar/.test(prompt) && /not_chosen/.test(prompt) && /demotes it below/.test(prompt) && /doctrine-named targets you decline/i.test(prompt));
  ok('diff: no-incumbent case handled', /every challenger item is new/i.test(diffAgendas(null, { agenda_id: 'c-1', items: [{ id: 'a', rank: 1, what: 'w', packet_ids: [] }] })));
  ok('formula prior still exported for compat', typeof packetPriority(makePacket('rr-zz')) === 'number');
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- verdict

const failed = checks.filter((c) => !c.pass);
process.stdout.write(`\ntest-agenda: ${checks.length - failed.length}/${checks.length} checks passed\n`);
for (const c of checks) process.stdout.write(`  ${c.pass ? 'ok  ' : 'FAIL'} ${c.name}\n`);
process.exit(failed.length ? 1 : 0);
