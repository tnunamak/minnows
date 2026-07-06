// plan-orders.mjs — model-authored candidate packets from fresh collector data.
//
// This is separate from deterministic `hone plan`: it asks Codex to author packets,
// then treats the strict packet validator as the authority. Invalid packets get at
// most two repair rounds; anything still invalid is discarded, never written.

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildContext, HONE_ROOT } from './profile.mjs';
import { buildInventoryContext } from './inventory.mjs';
import { collectTierMass } from '../collectors/tier-mass.mjs';
import { collectCallbackSmells } from '../collectors/callback-smells.mjs';
import { collectHotspots } from '../collectors/hotspots.mjs';
import { collectTestSignals } from '../collectors/test-signals.mjs';
import { parseYaml, stringifyYaml } from './yaml.mjs';
import { validatePacket } from './validate-packet.mjs';
import { deepEqual } from './util.mjs';
import { extractFencedJson } from '../providers/provider.mjs';

const TEMPLATE_PATH = join(HONE_ROOT, 'templates', 'author-orders.md');
const TERMINAL = new Set(['landed', 'reverted', 'skipped', 'blocked']);

export async function runPlanOrders(flags) {
  if (flags['self-test']) {
    process.exitCode = await planOrdersSelfTest();
    return;
  }
  const repoRoot = flags.repo || '.';
  const max = Number(flags.max ?? 10);
  const classFilter = String(flags.class ?? 'both');
  const targetDirs = typeof flags['target-dirs'] === 'string'
    ? flags['target-dirs'].split(',').map((s) => s.trim()).filter(Boolean)
    : [];
  const provider = (await import('../providers/codex.mjs')).default;
  const res = await executePlanOrders({ repoRoot, max, classFilter, targetDirs }, {
    provider,
    collect: collectFreshMeasurements,
  });
  printSummary(res);
  process.exitCode = res.invalid.length || res.discarded.length ? 1 : 0;
}

async function collectFreshMeasurements({ repoRoot, max, targetDirs }) {
  const ctx = buildContext(repoRoot);
  if (targetDirs.length) {
    ctx.profile.analysis = { ...(ctx.profile.analysis ?? {}), owned_dirs: targetDirs };
  }
  const inv = await buildInventoryContext(ctx, { top: Math.max(max, 20) });
  const tierMass = collectTierMass(inv);
  inv.universe = tierMass.universe;
  const callbackSmells = collectCallbackSmells(inv);
  const hotspots = collectHotspots(inv);
  const testSignals = collectTestSignals(inv);

  const outDir = join(ctx.repoRoot, 'quality', 'inventory');
  mkdirSync(outDir, { recursive: true });
  const write = (name, obj) => writeFileSync(join(outDir, name), JSON.stringify(obj, null, 2) + '\n');
  write('tier-mass.json', tierMass);
  write('callback-smells.json', callbackSmells);
  write('hotspots.json', hotspots);
  write('test-signals.json', testSignals);
  write('meta.json', {
    repo_sha: ctx.git.sha,
    repo_root: ctx.repoRoot,
    git_root: ctx.git.gitRoot,
    profile_source: ctx.profileSource,
    owned_dirs: inv.ownedDirs,
    cog_threshold: inv.cog,
    seam_cc: inv.seamCc,
    churn_window: inv.window,
    generated_at: new Date().toISOString(),
    counts: {
      flagged_fns: tierMass.universe.length,
      flagged_files: tierMass.by_file.length,
      callbacks: callbackSmells.callbacks.length,
      hotspot_files: hotspots.files.length,
      test_files: testSignals.generated_from.test_files,
      static_skips: testSignals.skips.total,
      zero_by_name_files: testSignals.zero_by_name.files.length,
    },
  });
  return {
    meta: JSON.parse(readFileSync(join(outDir, 'meta.json'), 'utf8')),
    tierMass,
    callbackSmells,
    hotspots,
    testSignals,
  };
}

const PLAN_ORDERS_TIMEOUT_MS = Number(process.env.HONE_PLAN_TIMEOUT_MS ?? 20 * 60 * 1000);

export async function executePlanOrders({ repoRoot, max = 10, classFilter = 'both', targetDirs = [] }, deps) {
  const repoAbs = buildContext(repoRoot).repoRoot;
  const measurements = await deps.collect({ repoRoot: repoAbs, max, targetDirs });
  const template = readFileSync(TEMPLATE_PATH, 'utf8');
  let prompt = renderAuthorPrompt(template, { repoRoot: repoAbs, max, classFilter, targetDirs, measurements });
  const attempts = [];
  let clean = [], invalid = [];
  for (let round = 0; round < 3; round++) {
    const reply = await deps.provider.complete(prompt, { timeoutMs: PLAN_ORDERS_TIMEOUT_MS });
    const parsed = parseOrdersReply(reply.text);
    const result = validateOrders(parsed.orders, repoAbs);
    attempts.push({ round, parse_error: parsed.error, valid: result.clean.length, invalid: result.invalid.length });
    clean = result.clean;
    invalid = [
      ...result.invalid,
      ...(parsed.error ? [{ index: null, candidate_id: null, errors: [parsed.error], order: null }] : []),
    ];
    if (!invalid.length) break;
    if (round === 2) break;
    prompt = repairPrompt({ priorPrompt: prompt, invalid, round: round + 1 });
  }

  const written = [];
  const discarded = [];
  const outDir = join(repoAbs, 'quality', 'packets');
  mkdirSync(outDir, { recursive: true });
  const terminal = existingTerminalStatuses(outDir);
  for (const order of clean.slice(0, max)) {
    const prior = terminal.get(order.candidate_id);
    if (prior) {
      discarded.push({ candidate_id: order.candidate_id, reason: `terminal packet already exists (${prior})` });
      continue;
    }
    const yaml = stringifyYaml(order);
    const back = parseYaml(yaml);
    if (!deepEqual(order, back)) {
      discarded.push({ candidate_id: order.candidate_id, reason: 'YAML round-trip mismatch' });
      continue;
    }
    const path = join(outDir, `${order.candidate_id}.yaml`);
    writeFileSync(path, yaml);
    written.push({ candidate_id: order.candidate_id, path, action: order.action, proof_class: order.proof_class });
  }
  for (const bad of invalid) {
    discarded.push({ candidate_id: bad.candidate_id ?? '(parse)', reason: bad.errors.join('; ') });
  }
  return { repoRoot: repoAbs, measurements, attempts, written, invalid, discarded, templatePath: TEMPLATE_PATH };
}

function renderAuthorPrompt(template, { repoRoot, max, classFilter, targetDirs, measurements }) {
  const payload = {
    repo_root: repoRoot,
    target_dirs: targetDirs,
    max_orders: max,
    class_filter: classFilter,
    generated_at: measurements.meta.generated_at,
    inventory_meta: measurements.meta,
    complexity: {
      tier_mass: measurements.tierMass.universe_tier_mass,
      top_candidates: measurements.tierMass.top_candidates?.slice(0, Math.max(max * 4, 20)) ?? [],
      top_files: measurements.tierMass.by_file?.slice(0, Math.max(max * 3, 15)) ?? [],
      callbacks: measurements.callbackSmells.callbacks?.slice(0, Math.max(max * 3, 15)) ?? [],
      hotspots: measurements.hotspots.files?.slice(0, Math.max(max * 3, 15)) ?? [],
    },
    coverage: {
      skips: measurements.testSignals.skips,
      zero_by_name: measurements.testSignals.zero_by_name,
      test_files: measurements.testSignals.generated_from?.test_files ?? null,
    },
  };
  return template
    .replaceAll('{{REPO_ROOT}}', repoRoot)
    .replaceAll('{{MAX_ORDERS}}', String(max))
    .replaceAll('{{CLASS_FILTER}}', classFilter)
    .replaceAll('{{TARGET_DIRS}}', targetDirs.length ? targetDirs.join(',') : '(profile/default)')
    .replaceAll('{{COLLECTOR_DATA_JSON}}', JSON.stringify(payload, null, 2));
}

function parseOrdersReply(text) {
  const parsed = extractFencedJson(text);
  if (!parsed) return { orders: [], error: 'provider reply contained no parseable JSON object/array' };
  const orders = Array.isArray(parsed) ? parsed : parsed.orders;
  if (!Array.isArray(orders)) return { orders: [], error: 'provider JSON must be an array or {orders:[...]}' };
  return { orders, error: null };
}

function validateOrders(orders, repoRoot) {
  const clean = [], invalid = [];
  for (const [index, order] of orders.entries()) {
    const errs = validatePacket(order, { repoDir: repoRoot, warn: () => {} });
    if (errs.length) invalid.push({ index, candidate_id: order?.candidate_id ?? null, errors: errs, order });
    else clean.push(order);
  }
  return { clean, invalid };
}

function repairPrompt({ priorPrompt, invalid, round }) {
  return [
    priorPrompt,
    '',
    `== VALIDATOR REPAIR ROUND ${round} ==`,
    'The previous JSON included invalid orders. Return the full corrected JSON object as { "orders": [...] }.',
    'Discard any order you cannot repair without guessing. Validator errors:',
    JSON.stringify(invalid.map((x) => ({ index: x.index, candidate_id: x.candidate_id, errors: x.errors, order: x.order })), null, 2),
  ].join('\n');
}

function existingTerminalStatuses(outDir) {
  const out = new Map();
  if (!existsSync(outDir)) return out;
  for (const f of readdirSync(outDir)) {
    if (!f.endsWith('.yaml')) continue;
    try {
      const p = parseYaml(readFileSync(join(outDir, f), 'utf8'));
      if (p?.candidate_id && TERMINAL.has(p.status)) out.set(p.candidate_id, p.status);
    } catch { /* ignore foreign yaml */ }
  }
  return out;
}

function printSummary(res) {
  const w = (s) => process.stdout.write(s + '\n');
  w(`hone plan-orders — ${res.written.length} clean order(s) written to quality/packets/`);
  w(`template: ${res.templatePath}`);
  w(`round | valid | invalid | parse`);
  for (const a of res.attempts) {
    w(`${String(a.round).padStart(5)} | ${String(a.valid).padStart(5)} | ${String(a.invalid).padStart(7)} | ${a.parse_error ? 'error' : 'ok'}`);
  }
  if (res.written.length) {
    w(`candidate_id                         | action              | proof_class`);
    for (const x of res.written) w(`${x.candidate_id.padEnd(36)} | ${x.action.padEnd(19)} | ${x.proof_class}`);
  }
  if (res.discarded.length) {
    w(`discarded:`);
    for (const x of res.discarded) w(`  ${x.candidate_id}: ${x.reason}`);
  }
}

function fixtureMeasurements(repoRoot) {
  return {
    meta: {
      repo_sha: 'fixture-sha',
      repo_root: repoRoot,
      git_root: repoRoot,
      owned_dirs: ['src'],
      cog_threshold: 5,
      seam_cc: 12,
      churn_window: 'fixture',
      generated_at: '2026-07-06T00:00:00.000Z',
      counts: { flagged_fns: 1, flagged_files: 1, callbacks: 0, hotspot_files: 1, test_files: 1, static_skips: 0, zero_by_name_files: 1 },
    },
    tierMass: {
      universe_tier_mass: { T0: 4 },
      top_candidates: [{ file: 'src/a.js', fn: 'messy', line: 1, cc: 9, excess: 4, churn: 3, tier: 'T0' }],
      by_file: [{ file: 'src/a.js', churn: 3, fns: 1, mass: 4, tiers: { T0: 4 } }],
    },
    callbackSmells: { callbacks: [] },
    hotspots: { files: [{ file: 'src/a.js', loc: 20, churn: 3, cog: 1, coupling: 0, score: 6, nogo: false }] },
    testSignals: { skips: { total: 0, files: [] }, zero_by_name: { files: ['src/a.js'] }, generated_from: { test_files: 1 } },
  };
}

function testPacket(repoRoot, overrides = {}) {
  return {
    candidate_id: overrides.candidate_id ?? 'author-src-a-t0-0001',
    created: '2026-07-06T00:00:00.000Z',
    repo_sha: 'fixture-sha',
    subsystem: 'src',
    files: ['src/a.js'],
    symbols: ['messy'],
    public_surface: [],
    behavior_status: 'likely_intended',
    ownership: 'OWN',
    action: 'preserve_refactor',
    proof_class: 'pure_logic',
    execution_gate: 'autonomous',
    why_this_matters: 'fixture packet from collector data',
    plan: { transform_class: 'concept-seam-split', instruction: 'Decomplect messy without behavior change.' },
    expected_quality_gain: 'low',
    owner_attention_reduction: 'low',
    product_impact: 'none',
    risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: null },
    authoring_evidence: [{ kind: 'inventory', detail: 'fixture collectors', result: 'src/a.js cc=9' }],
    evidence_required: [{ rung: 'direct-test', command: 'node -e "process.exit(0)"', expect: 'exit 0', expect_check: { type: 'exit_code', value: 0 } }],
    not_allowed: ['behavior-change', 'new-dependency'],
    maker_tier: 'standard',
    judge_tier: 'standard',
    maker_provider: null,
    judge_provider: null,
    batch_key: 'preserve_refactor×pure_logic×src',
    touchset: ['src/a.js'],
    estimates: { tokens: 1000, evidence_cost: 'low' },
    depends_on: [],
    unlocks: [],
    status: 'pending',
    outcome: { commit: null, skip_reason: null, blocked_on: null, judge_verdict: null, evidence_receipts: [], tokens_actual: null, lesson: null },
    ...overrides,
  };
}

function reply(orders) {
  return `\`\`\`json\n${JSON.stringify({ orders }, null, 2)}\n\`\`\``;
}

export async function planOrdersSelfTest() {
  const results = [];
  const w = (s) => process.stdout.write(s + '\n');
  async function scenario(name, fn) {
    const checks = [];
    const check = (label, cond, detail = '') => checks.push({ label, ok: !!cond, detail });
    try { await fn(check); } catch (e) { checks.push({ label: 'no-unexpected-throw', ok: false, detail: e.message }); }
    results.push({ name, checks });
  }
  const mkRepo = () => {
    const root = mkdtempSync(join(tmpdir(), 'hone-author-'));
    mkdirSync(join(root, 'src'), { recursive: true });
    mkdirSync(join(root, 'quality', 'packets'), { recursive: true });
    writeFileSync(join(root, 'src', 'a.js'), 'function messy(){ return 1; }\n');
    return root;
  };
  const depsFor = (responses, prompts = []) => ({
    collect: async ({ repoRoot }) => fixtureMeasurements(repoRoot),
    provider: {
      complete: async (prompt) => {
        prompts.push(prompt);
        const next = responses.shift();
        return { text: typeof next === 'function' ? next(prompt) : next, meta: { provider: 'mock' } };
      },
    },
  });

  await scenario('template renders collector data and writes clean order', async (check) => {
    const root = mkRepo();
    const prompts = [];
    const res = await executePlanOrders({ repoRoot: root, max: 1, classFilter: 'both', targetDirs: ['src'] }, depsFor([reply([testPacket(root)])], prompts));
    check('prompt includes collector payload', prompts[0].includes('"top_candidates"') && prompts[0].includes('src/a.js') && prompts[0].includes('class_filter'), prompts[0].slice(0, 400));
    check('clean order written', res.written.length === 1 && existsSync(join(root, 'quality/packets/author-src-a-t0-0001.yaml')));
  });

  await scenario('invalid order triggers one repair round', async (check) => {
    const root = mkRepo();
    const invalid = testPacket(root);
    delete invalid.execution_gate;
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, depsFor([reply([invalid]), reply([testPacket(root, { candidate_id: 'author-repaired-0001' })])]));
    check('two provider calls', res.attempts.length === 2, JSON.stringify(res.attempts));
    check('repaired order written', res.written[0]?.candidate_id === 'author-repaired-0001');
  });

  await scenario('unrepairable order discarded after two repair rounds', async (check) => {
    const root = mkRepo();
    const invalid = testPacket(root, { candidate_id: 'author-bad-0001' });
    delete invalid.execution_gate;
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, depsFor([reply([invalid]), reply([invalid]), reply([invalid])]));
    check('three total rounds', res.attempts.length === 3, JSON.stringify(res.attempts));
    check('nothing written, discarded reason printed', res.written.length === 0 && res.discarded.some((d) => /execution_gate/.test(d.reason)), JSON.stringify(res.discarded));
  });

  let passed = 0, failed = 0;
  for (const s of results) {
    for (const c of s.checks) {
      if (c.ok) { passed++; w(` ok  ${s.name}: ${c.label}`); }
      else { failed++; w(`FAIL ${s.name}: ${c.label}${c.detail ? `\n     ${c.detail}` : ''}`); }
    }
  }
  w(`\nhone plan-orders --self-test: ${passed} checks passed, ${failed} failed, ${results.length} scenarios (mocked provider, no LLM calls)`);
  return failed ? 1 : 0;
}
