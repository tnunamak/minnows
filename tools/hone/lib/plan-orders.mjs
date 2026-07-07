// plan-orders.mjs — model-authored candidate packets from fresh collector data.
//
// This is separate from deterministic `hone plan`: it asks Codex to author packets,
// then treats the strict packet validator plus deterministic authoring preflight as
// the authority. Invalid or malformed-baseline packets get at most two repair rounds;
// anything still invalid is discarded, never written.

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
import { makeRungExecutor } from './work.mjs';
import { buildRung, dbTestRung, mutantKillRung, seamPinRung } from './rung-builder.mjs';

const FREEFORM_TEMPLATE_PATH = join(HONE_ROOT, 'templates', 'author-orders.md');
const STRUCTURED_TEMPLATE_PATH = join(HONE_ROOT, 'templates', 'author-orders-structured.md');
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
    authoringMode: flags.freeform || flags['free-form'] || flags.legacy || flags['legacy-authoring'] ? 'freeform' : 'structured',
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
  const ctx = buildContext(repoRoot);
  const repoAbs = ctx.repoRoot;
  const measurements = await deps.collect({ repoRoot: repoAbs, max, targetDirs });
  const authoringMode = deps.authoringMode === 'freeform' ? 'freeform' : 'structured';
  const templatePath = authoringMode === 'freeform' ? FREEFORM_TEMPLATE_PATH : STRUCTURED_TEMPLATE_PATH;
  const template = readFileSync(templatePath, 'utf8');
  let prompt = renderAuthorPrompt(template, { repoRoot: repoAbs, max, classFilter, targetDirs, measurements });
  const attempts = [];
  let clean = [], invalid = [];
  let preflight = { passed: [], discarded: [] };
  const preflightHistory = [];
  for (let round = 0; round < 3; round++) {
    const reply = await deps.provider.complete(prompt, { timeoutMs: PLAN_ORDERS_TIMEOUT_MS });
    const parsed = parseOrdersReply(reply.text);
    const materialized = authoringMode === 'structured'
      ? materializeStructuredOrders(parsed.orders, measurements)
      : { orders: parsed.orders, invalid: [] };
    const result = validateOrders(materialized.orders, repoAbs);
    preflight = await preflightOrders(result.clean, repoAbs, { log: deps.log ?? (() => {}), gitRoot: ctx.git.gitRoot, prefix: ctx.git.prefix });
    preflightHistory.push({ round, passed: preflight.passed, discarded: preflight.discarded });
    attempts.push({
      round,
      parse_error: parsed.error,
      valid: preflight.clean.length,
      invalid: materialized.invalid.length + result.invalid.length,
      preflight_passed: preflight.passed.length,
      preflight_discarded: preflight.discarded.length,
    });
    clean = preflight.clean;
    invalid = [
      ...materialized.invalid,
      ...result.invalid,
      ...preflight.discarded,
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
  return { repoRoot: repoAbs, measurements, attempts, written, invalid, discarded, preflight, preflightHistory, templatePath, authoringMode };
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

const FORBIDDEN_STRUCTURED_RUNG_KEYS = ['rung', 'command', 'timeout_s', 'expect', 'expect_check'];

function materializeStructuredOrders(orders, measurements) {
  const clean = [];
  const invalid = [];
  for (const [index, order] of orders.entries()) {
    try {
      clean.push(materializeStructuredOrder(order, measurements));
    } catch (e) {
      invalid.push({
        index,
        candidate_id: order?.candidate_id ?? null,
        errors: [`structured authoring materialization: ${e.message}`],
        order,
      });
    }
  }
  return { orders: clean, invalid };
}

function materializeStructuredOrder(order, measurements) {
  if (!order || typeof order !== 'object' || Array.isArray(order)) throw new Error('order must be an object');
  if ('evidence_required' in order) throw new Error('evidence_required is forbidden in structured mode; use rungs[] semantics only');
  const candidateId = requiredStructuredString(order, 'candidate_id');
  const action = requiredStructuredString(order, 'action');
  const proofClass = requiredStructuredString(order, 'proof_class');
  const touchset = requiredStringArray(order.touchset, 'touchset');
  const why = requiredStructuredString(order, 'why');
  if (!Array.isArray(order.rungs) || order.rungs.length === 0) throw new Error('rungs: non-empty array required');
  const evidenceRequired = order.rungs.map((rung, i) => {
    for (const key of FORBIDDEN_STRUCTURED_RUNG_KEYS) {
      if (rung && typeof rung === 'object' && key in rung) {
        throw new Error(`rungs[${i}].${key}: command scaffolding fields are forbidden in structured mode`);
      }
    }
    try { return buildRung(rung); }
    catch (e) { throw new Error(`rungs[${i}]: ${e.message}`); }
  });
  const subsystem = subsystemFromTouchset(touchset);
  const packet = {
    candidate_id: candidateId,
    created: measurements.meta.generated_at,
    repo_sha: measurements.meta.repo_sha,
    subsystem,
    files: touchset,
    symbols: [],
    public_surface: [],
    behavior_status: 'likely_intended',
    ownership: 'OWN',
    action,
    proof_class: proofClass,
    execution_gate: 'autonomous',
    why_this_matters: why,
    plan: { transform_class: `structured-${action}`, instruction: why },
    expected_quality_gain: 'medium',
    owner_attention_reduction: 'medium',
    product_impact: 'none',
    risk: { blast_radius: 'local', reversibility: 'branch-revert', silent_wrongness_cost: 'low', property_at_risk: null },
    authoring_evidence: [{ kind: 'structured-authoring', detail: 'plan-orders structured JSON; rung commands materialized by lib/rung-builder.mjs', result: why }],
    evidence_required: evidenceRequired,
    not_allowed: ['behavior-change', 'new-dependency'],
    maker_tier: 'standard',
    judge_tier: 'standard',
    maker_provider: null,
    judge_provider: null,
    batch_key: `${action}×${proofClass}×${subsystem}`,
    touchset,
    estimates: { tokens: 1000, evidence_cost: evidenceRequired.length > 1 ? 'medium' : 'low' },
    depends_on: [],
    unlocks: [],
    status: 'pending',
    outcome: { commit: null, skip_reason: null, blocked_on: null, judge_verdict: null, evidence_receipts: [], tokens_actual: null, lesson: null },
  };
  if (order.routing_class != null && String(order.routing_class).trim()) packet.routing_class = String(order.routing_class);
  return packet;
}

function requiredStructuredString(order, key) {
  if (typeof order[key] !== 'string' || order[key].trim().length === 0) throw new Error(`${key}: non-empty string required`);
  return order[key];
}

function requiredStringArray(value, key) {
  if (!Array.isArray(value) || value.length === 0 || !value.every((v) => typeof v === 'string' && v.length > 0)) {
    throw new Error(`${key}: non-empty string array required`);
  }
  return value;
}

function subsystemFromTouchset(touchset) {
  const first = touchset[0] ?? '(root)';
  const parts = first.split('/').filter(Boolean);
  return parts.length <= 1 ? '(root)' : parts[0];
}

const NODE_TEST_RE = /(?:^|[\s;&|])node\s+[^;&|\n]*--test\b/;
const TEST_FILE_RE = /(?:^|[\s"'=])([A-Za-z0-9_./-]+\.test\.(?:mjs|cjs|js|jsx|ts|tsx))(?=$|[\s"';&|])/g;
const TEST_INVOCATION_RE = /\bnode\s+[^;&|\n]*--test\b|\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b|\bnpx\s+(?:vitest|jest|mocha|tap)\b/;
const CLEANUP_RE = /;\s*(?:git\s+checkout\b|dropdb\b|rm\s+(?:-[A-Za-z]+\s+)?)/;
const RC_CAPTURE_RE = /\brc=|\$\?/;
const EXIT_RC_RE = /\bexit\s+\$rc\b/;
const MUTANT_BRANCH_EXIT_RE = /\bif\s+\[\s+\$rc\s+-eq\s+0\s+\]\s*;\s*then\b[\s\S]*\bMUTANT KILLED\b[\s\S]*\bexit\s+0\b[\s\S]*\belse\b[\s\S]*\bMUTANT SURVIVED\b[\s\S]*\bexit\s+1\b/;
const OBSERVES_RC_RE = (cmd) => EXIT_RC_RE.test(cmd) || MUTANT_BRANCH_EXIT_RE.test(cmd);

async function preflightOrders(orders, repoRoot, { log = () => {}, gitRoot = repoRoot, prefix = '' } = {}) {
  const clean = [];
  const passed = [];
  const discarded = [];
  for (const [index, order] of orders.entries()) {
    const staticErrors = preflightStaticErrors(order, repoRoot);
    if (staticErrors.length) {
      discarded.push({ index, candidate_id: order?.candidate_id ?? null, errors: staticErrors, order });
      continue;
    }
    const baseline = await preflightBaseline(order, repoRoot, { log, gitRoot, prefix });
    if (baseline.errors.length) {
      discarded.push({ index, candidate_id: order?.candidate_id ?? null, errors: baseline.errors, order });
      continue;
    }
    clean.push(order);
    passed.push({ candidate_id: order.candidate_id, rungs: order.evidence_required.length });
  }
  return { clean, passed, discarded };
}

function preflightStaticErrors(order, repoRoot) {
  const errors = [];
  const warnings = [];
  validatePacket(order, { repoDir: repoRoot, warn: (m) => warnings.push(m) });
  for (const m of warnings) {
    if (/absolute path\(s\)/.test(m)) errors.push(`preflight rung hygiene: ${m}`);
  }
  for (const [i, rung] of (order.evidence_required ?? []).entries()) {
    if (!Number.isInteger(rung?.timeout_s) || rung.timeout_s <= 0) {
      errors.push(`preflight rung hygiene: evidence_required[${i}] (rung '${rung?.rung ?? '(unnamed)'}'): explicit timeout_s is required on every rung`);
    }
    const cmd = String(rung?.command ?? '');
    if (CLEANUP_RE.test(cmd) && TEST_INVOCATION_RE.test(cmd) && (!RC_CAPTURE_RE.test(cmd) || !OBSERVES_RC_RE(cmd))) {
      errors.push(`preflight rung hygiene: evidence_required[${i}] (rung '${rung?.rung ?? '(unnamed)'}'): cleanup after a graded command requires rc capture before cleanup and an rc-derived exit`);
    }
    if (NODE_TEST_RE.test(cmd)) {
      const db = nodeTestDbIsolationError(cmd);
      if (db) errors.push(`preflight DB isolation: evidence_required[${i}] (rung '${rung?.rung ?? '(unnamed)'}'): ${db}`);
    }
  }
  return errors;
}

function nodeTestDbIsolationError(cmd) {
  const hasForceExit = /--test-force-exit\b/.test(cmd);
  const hasDbName = /\bdb=pdpp_hone_[A-Za-z0-9_$-]*\b/.test(cmd) || /\/pdpp_hone_[A-Za-z0-9_$-]*\b/.test(cmd);
  const hasCreate = /\bcreatedb\b[^;&|\n]*(?:\$db|pdpp_hone_)/.test(cmd);
  const hasDrop = /\bdropdb\b[^;&|\n]*(?:\$db|pdpp_hone_)/.test(cmd);
  const hasUrl = /PDPP_TEST_POSTGRES_URL=/.test(cmd);
  const shared = /PDPP_TEST_POSTGRES_URL=postgres:\/\/[^ \t;&|]+\/pdpp\b/.test(cmd);
  const testFiles = [...cmd.matchAll(TEST_FILE_RE)].map((m) => m[1]);
  const multiFileSingleRun = testFiles.length > 1 && !/\bfor\s+\w+\s+in\b/.test(cmd) && !/\bxargs\b[^;&|\n]*\b-n\s*1\b/.test(cmd);
  if (shared) return 'node --test points at the shared PDPP_TEST_POSTGRES_URL database; use a per-file pdpp_hone_* database';
  if (!hasForceExit || !hasDbName || !hasCreate || !hasDrop || !hasUrl || !RC_CAPTURE_RE.test(cmd) || !OBSERVES_RC_RE(cmd)) {
    return 'node --test must use the per-file ephemeral-DB pattern: db=pdpp_hone_*; dropdb --if-exists $db; createdb $db; PDPP_TEST_POSTGRES_URL=.../$db node --test --test-force-exit <file>; rc capture; dropdb --if-exists $db; rc-derived exit';
  }
  if (multiFileSingleRun) {
    return `node --test lists multiple test files (${testFiles.join(', ')}) without a per-file DB loop`;
  }
  return null;
}

async function preflightBaseline(order, repoRoot, { log = () => {}, gitRoot = repoRoot, prefix = '' } = {}) {
  const errors = [];
  const execRung = makeRungExecutor({
    gitRoot,
    repoRoot,
    prefix,
    packetDefaultTimeoutS: order.rung_timeout_s ?? null,
    log,
  });
  for (const [i, rung] of order.evidence_required.entries()) {
    const { res, verdict } = await execRung(rung, 'baseline');
    if (!verdict.pass) {
      errors.push(`preflight baseline red: evidence_required[${i}] (rung '${rung.rung}') expected ${expectedSummary(rung)}; got ${verdict.reason} (exit ${res.code}, timeout=${res.timedOut})`);
      break;
    }
  }
  return { errors };
}

function expectedSummary(rung) {
  const ec = rung.expect_check;
  if (ec && typeof ec === 'object') return `expect_check ${ec.type} ${JSON.stringify(ec.value)}`;
  return JSON.stringify(rung.expect);
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
  w(`round | valid | invalid | preflight pass/discard | parse`);
  for (const a of res.attempts) {
    w(`${String(a.round).padStart(5)} | ${String(a.valid).padStart(5)} | ${String(a.invalid).padStart(7)} | ${String(a.preflight_passed ?? 0).padStart(6)}/${String(a.preflight_discarded ?? 0).padEnd(7)} | ${a.parse_error ? 'error' : 'ok'}`);
  }
  if (res.preflightHistory?.some((h) => h.passed.length || h.discarded.length)) {
    w(`preflight:`);
    for (const h of res.preflightHistory) {
      for (const x of h.passed) w(`  round ${h.round} PASS ${x.candidate_id}: ${x.rungs} baseline rung(s) green`);
      for (const x of h.discarded) w(`  round ${h.round} DISCARD ${x.candidate_id ?? '(unknown)'}: ${x.errors.join('; ')}`);
    }
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
    evidence_required: [{ rung: 'direct-test', command: 'node -e "process.exit(0)"', expect: 'exit 0', timeout_s: 5, expect_check: { type: 'exit_code', value: 0 } }],
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

function structuredReply(orders) {
  return reply(orders);
}

function structuredOrder(overrides = {}) {
  return {
    candidate_id: overrides.candidate_id ?? 'structured-src-a-0001',
    action: overrides.action ?? 'generate_evidence',
    proof_class: overrides.proof_class ?? 'effectful',
    routing_class: overrides.routing_class,
    touchset: overrides.touchset ?? ['src/a.js', 'test/a.test.js'],
    why: overrides.why ?? 'Fixture structured order pins behavior before reducing src/a.js maintenance risk.',
    rungs: overrides.rungs ?? [{
      kind: 'db-test',
      testFileNoExt: 'a',
      slug: 'structured-a',
      mode: 'coverage',
    }],
  };
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
    mkdirSync(join(root, 'test'), { recursive: true });
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
  const freeformDepsFor = (responses, prompts = []) => ({ ...depsFor(responses, prompts), authoringMode: 'freeform' });

  await scenario('template renders collector data and writes clean order', async (check) => {
    const root = mkRepo();
    const prompts = [];
    const res = await executePlanOrders({ repoRoot: root, max: 1, classFilter: 'both', targetDirs: ['src'] }, freeformDepsFor([reply([testPacket(root)])], prompts));
    check('prompt includes collector payload', prompts[0].includes('"top_candidates"') && prompts[0].includes('src/a.js') && prompts[0].includes('class_filter'), prompts[0].slice(0, 400));
    check('clean order written', res.written.length === 1 && existsSync(join(root, 'quality/packets/author-src-a-t0-0001.yaml')));
  });

  await scenario('invalid order triggers one repair round', async (check) => {
    const root = mkRepo();
    const invalid = testPacket(root);
    delete invalid.execution_gate;
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, freeformDepsFor([reply([invalid]), reply([testPacket(root, { candidate_id: 'author-repaired-0001' })])]));
    check('two provider calls', res.attempts.length === 2, JSON.stringify(res.attempts));
    check('repaired order written', res.written[0]?.candidate_id === 'author-repaired-0001');
  });

  await scenario('unrepairable order discarded after two repair rounds', async (check) => {
    const root = mkRepo();
    const invalid = testPacket(root, { candidate_id: 'author-bad-0001' });
    delete invalid.execution_gate;
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, freeformDepsFor([reply([invalid]), reply([invalid]), reply([invalid])]));
    check('three total rounds', res.attempts.length === 3, JSON.stringify(res.attempts));
    check('nothing written, discarded reason printed', res.written.length === 0 && res.discarded.some((d) => /execution_gate/.test(d.reason)), JSON.stringify(res.discarded));
  });

  await scenario('preflight discards red-at-baseline seam pin', async (check) => {
    const root = mkRepo();
    const bad = testPacket(root, {
      candidate_id: 'author-red-seam-0001',
      evidence_required: [{
        rung: 'seam-pin',
        command: 'node -e "process.exit(require(\'fs\').readFileSync(\'src/a.js\',\'utf8\').includes(\'function extractedHelper\') ? 0 : 7)"',
        expect: 'post-extraction helper exists',
        timeout_s: 5,
        expect_check: { type: 'exit_code', value: 0 },
      }],
    });
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, freeformDepsFor([reply([bad]), reply([bad]), reply([bad])]));
    check('red baseline order not written', res.written.length === 0 && !existsSync(join(root, 'quality/packets/author-red-seam-0001.yaml')));
    check('discard reason names seam rung', res.discarded.some((d) => d.candidate_id === 'author-red-seam-0001' && /seam-pin/.test(d.reason) && /preflight baseline red/.test(d.reason)), JSON.stringify(res.discarded));
  });

  await scenario('preflight statically discards non-isolated node --test without executing it', async (check) => {
    const root = mkRepo();
    mkdirSync(join(root, 'test'), { recursive: true });
    writeFileSync(join(root, 'test', 'db.test.js'), 'import test from "node:test"; test("x", () => {});\n');
    const bad = testPacket(root, {
      candidate_id: 'author-shared-db-0001',
      evidence_required: [{
        rung: 'db-test',
        command: 'node --test test/db.test.js; touch preflight-should-not-exist',
        expect: 'db test passes',
        timeout_s: 5,
        expect_check: { type: 'exit_code', value: 0 },
      }],
    });
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, freeformDepsFor([reply([bad]), reply([bad]), reply([bad])]));
    check('node --test rung discarded', res.written.length === 0 && res.discarded.some((d) => /preflight DB isolation/.test(d.reason)), JSON.stringify(res.discarded));
    check('static catch did not execute command tail', !existsSync(join(root, 'preflight-should-not-exist')));
  });

  await scenario('preflight passes well-formed order and preserves authored rung bytes', async (check) => {
    const root = mkRepo();
    const authoredCommand = 'printf "BASELINE_OK\\n"';
    const good = testPacket(root, {
      candidate_id: 'author-good-preflight-0001',
      evidence_required: [{
        rung: 'baseline-smoke',
        command: authoredCommand,
        expect: 'prints BASELINE_OK',
        timeout_s: 5,
        expect_check: { type: 'stdout_includes', value: 'BASELINE_OK' },
      }],
    });
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, freeformDepsFor([reply([good])]));
    const written = parseYaml(readFileSync(join(root, 'quality/packets/author-good-preflight-0001.yaml'), 'utf8'));
    check('well-formed order written', res.written[0]?.candidate_id === 'author-good-preflight-0001');
    check('written rung command is byte-identical to provider command', written.evidence_required[0].command === authoredCommand, written.evidence_required[0].command);
  });

  await scenario('preflight discard feeds repair round and corrected order is written', async (check) => {
    const root = mkRepo();
    const prompts = [];
    const bad = testPacket(root, {
      candidate_id: 'author-repair-preflight-0001',
      evidence_required: [{
        rung: 'seam-pin',
        command: 'node -e "process.exit(9)"',
        expect: 'exit 0',
        timeout_s: 5,
        expect_check: { type: 'exit_code', value: 0 },
      }],
    });
    const good = testPacket(root, {
      candidate_id: 'author-repair-preflight-0001',
      evidence_required: [{
        rung: 'seam-pin',
        command: 'node -e "process.exit(0)"',
        expect: 'exit 0',
        timeout_s: 5,
        expect_check: { type: 'exit_code', value: 0 },
      }],
    });
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, freeformDepsFor([reply([bad]), reply([good])], prompts));
    check('preflight failure caused repair provider call', res.attempts.length === 2 && /preflight baseline red/.test(prompts[1]), prompts[1]?.slice(-800));
    check('corrected repair order written', res.written[0]?.candidate_id === 'author-repair-preflight-0001');
  });

  await scenario('rung builder emits preflight-static clean gold shapes', async (check) => {
    const root = mkRepo();
    const built = [
      dbTestRung({ testFileNoExt: 'a', slug: 'gold-a', mode: 'extraction' }),
      dbTestRung({ testFileNoExt: 'a', slug: 'gold-a', mode: 'coverage' }),
      mutantKillRung({ testFileNoExt: 'a', sourceFile: 'src/a.js', slug: 'gold-a', mutationSed: 's/return 1/return 2/' }),
      seamPinRung({ sourceFile: 'src/a.js', preservedMarkers: ['function messy', 'return 1'], slug: 'gold-a' }),
    ];
    for (const rung of built) {
      const packet = testPacket(root, { candidate_id: `builder-${rung.rung}`, evidence_required: [rung] });
      const errs = preflightStaticErrors(packet, root);
      check(`${rung.rung} static preflight clean`, errs.length === 0, errs.join('; '));
    }
  });

  await scenario('db-test builder modes have correct baseline branching', async (check) => {
    const extraction = dbTestRung({ testFileNoExt: 'a', slug: 'mode-a', mode: 'extraction' });
    const coverage = dbTestRung({ testFileNoExt: 'a', slug: 'mode-a', mode: 'coverage' });
    check('extraction is unconditional', !extraction.command.includes('BASELINE: test not yet authored') && !extraction.command.includes('if [ ! -f'));
    check('coverage has baseline branch', coverage.command.includes('if [ ! -f test/$f.test.js ]; then echo "BASELINE: test not yet authored"; exit 0; fi'));
  });

  await scenario('rung builder output is byte-equivalent to gold template shape', async (check) => {
    const got = dbTestRung({ testFileNoExt: 'new-oracle', slug: 'byte_gold', mode: 'coverage' });
    const expected = {
      rung: 'direct-test',
      timeout_s: 600,
      command: 'cd "$REPO_ROOT" && sh -c \'set -e; f=new-oracle; if [ ! -f test/$f.test.js ]; then echo "BASELINE: test not yet authored"; exit 0; fi; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_byte_gold_$f; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 540 node --test --test-force-exit test/$f.test.js || rc=1; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; exit $rc\'',
      expect: 'BASELINE: prints BASELINE line exit 0 (test absent). POST: the authored test passes fully.',
      expect_check: { type: 'stdout_regex', value: 'BASELINE:|# fail 0' },
    };
    check('coverage db-test object byte-equivalent', deepEqual(got, expected), JSON.stringify(got, null, 2));
  });

  await scenario('structured authoring materializes three builder-backed packets', async (check) => {
    const root = mkRepo();
    const prompts = [];
    const orders = [
      structuredOrder({ candidate_id: 'structured-db-0001', rungs: [{ kind: 'db-test', testFileNoExt: 'new-a', slug: 'structured-db', mode: 'coverage' }], touchset: ['src/a.js', 'test/new-a.test.js'] }),
      structuredOrder({ candidate_id: 'structured-mut-0002', rungs: [{ kind: 'mutant-kill', testFileNoExt: 'new-b', sourceFile: 'src/a.js', slug: 'structured-mut', mutationSed: 's/return 1/return 2/' }], touchset: ['src/a.js', 'test/new-b.test.js'] }),
      structuredOrder({ candidate_id: 'structured-seam-0003', action: 'preserve_refactor', proof_class: 'pure_logic', rungs: [{ kind: 'seam-pin', sourceFile: 'src/a.js', preservedMarkers: ['function messy', 'return 1'], slug: 'structured-seam' }], touchset: ['src/a.js'] }),
    ];
    const res = await executePlanOrders({ repoRoot: root, max: 3, classFilter: 'both', targetDirs: ['src'] }, depsFor([structuredReply(orders)], prompts));
    check('structured template is default', res.authoringMode === 'structured' && res.templatePath.endsWith('author-orders-structured.md'));
    check('prompt forbids command strings', prompts[0].includes('Do NOT author shell commands') && prompts[0].includes('lib/rung-builder.mjs'));
    check('three packets written', res.written.length === 3, JSON.stringify(res.discarded));
    for (const id of orders.map((o) => o.candidate_id)) {
      const written = parseYaml(readFileSync(join(root, 'quality/packets', `${id}.yaml`), 'utf8'));
      check(`${id} has builder command`, written.evidence_required.length === 1 && /cd "\$REPO_ROOT"/.test(written.evidence_required[0].command), JSON.stringify(written.evidence_required));
      check(`${id} preflight passed`, res.preflight.passed.some((p) => p.candidate_id === id), JSON.stringify(res.preflight));
    }
  });

  await scenario('structured command fields are rejected before packet validation', async (check) => {
    const root = mkRepo();
    const bad = structuredOrder({
      candidate_id: 'structured-command-forbidden-0001',
      rungs: [{ kind: 'seam-pin', sourceFile: 'src/a.js', preservedMarkers: ['function messy'], slug: 'bad', command: 'echo model-authored' }],
      touchset: ['src/a.js'],
    });
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, depsFor([structuredReply([bad]), structuredReply([bad]), structuredReply([bad])]));
    check('nothing written', res.written.length === 0);
    check('discard reason forbids command field', res.discarded.some((d) => /command scaffolding fields are forbidden/.test(d.reason)), JSON.stringify(res.discarded));
  });

  await scenario('structured semantic bad source is discarded by baseline preflight', async (check) => {
    const root = mkRepo();
    const bad = structuredOrder({
      candidate_id: 'structured-missing-source-0001',
      action: 'preserve_refactor',
      proof_class: 'pure_logic',
      rungs: [{ kind: 'seam-pin', sourceFile: 'src/missing.js', preservedMarkers: ['anything'], slug: 'missing-src' }],
      touchset: ['src/a.js'],
    });
    const res = await executePlanOrders({ repoRoot: root, max: 1 }, depsFor([structuredReply([bad]), structuredReply([bad]), structuredReply([bad])]));
    check('not a materialization error', !res.discarded.some((d) => /structured authoring materialization/.test(d.reason)), JSON.stringify(res.discarded));
    check('discarded by preflight baseline red', res.discarded.some((d) => d.candidate_id === 'structured-missing-source-0001' && /preflight baseline red/.test(d.reason)), JSON.stringify(res.discarded));
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
