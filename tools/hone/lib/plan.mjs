// plan.mjs — `hone plan`: inventory → classified, ranked candidate packets (schema v1.1).
//
// v1 classification is DETERMINISTIC-HEURISTIC: tier routing comes from the ported router v1
// (already applied at inventory time), behavior_status defaults by path pattern from the
// profile, and ranking uses the SPEC formula with mapped heuristic scores. Judgment-dependent
// fields carry an explicit `[v1 deterministic heuristic …]` label with numeric confidence —
// metrics NOMINATE work here; semantic validation (wave 2) decides it. Packet enrichment via
// LLM providers is wave 2.
//
// Never re-read what inventory already knows; never overwrite a terminal packet (SPEC #9 and
// the packets-are-memory rule). Every packet is schema-validated AND YAML round-trip-verified
// before it is written — a malformed packet crashes the plan (acceptance test #4).
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildContext, HONE_ROOT } from './profile.mjs';
import { parseYaml, stringifyYaml } from './yaml.mjs';
import { assertValidPacket } from './validate-packet.mjs';
import { deepEqual, djb2, escRe, globToRegExp, slug, subsystemOf } from './util.mjs';

// ---- tier → execution contract (fixed deterministic table; provenance: SPEC evidence ladder) ----
const TIER_EXEC = {
  'T0': { proof_class: 'certified_transform', transform_class: 'certified-local-tidy', maker_tier: 'cheap', tokens: 60000, evidence_cost: 'low', confidence: 0.8, short: 't0' },
  'T1-extractable-callback': { proof_class: 'exact_move', transform_class: 'hoist-to-named-module-fn', maker_tier: 'cheap', tokens: 60000, evidence_cost: 'low', confidence: 0.8, short: 't1a' },
  'T1b-explicit-context': { proof_class: 'pure_logic', transform_class: 'explicit-context-extraction', maker_tier: 'standard', tokens: 150000, evidence_cost: 'medium', confidence: 0.6, short: 't1b' },
  'T1-seam': { proof_class: 'pure_logic', transform_class: 'concept-seam-split', maker_tier: 'standard', tokens: 200000, evidence_cost: 'medium', confidence: 0.6, short: 'seam' },
  'T2-async-order': { proof_class: 'effectful', transform_class: 'async-order-preserving-decomplect', maker_tier: 'strong', tokens: 300000, evidence_cost: 'medium', confidence: 0.4, short: 't2async' },
  'T2-capturing-mutable': { proof_class: 'effectful', transform_class: 'shared-cell-isolation', maker_tier: 'strong', tokens: 300000, evidence_cost: 'medium', confidence: 0.4, short: 't2mut' },
  'T2-property': { proof_class: 'property_at_risk', transform_class: 'guard-preserving-decomplect', maker_tier: 'strong', tokens: 350000, evidence_cost: 'high', confidence: 0.35, short: 't2prop' },
  'DELETE': { proof_class: 'liveness_roots', transform_class: 'dead-code-deletion', maker_tier: 'standard', tokens: 120000, evidence_cost: 'high', confidence: 0.3, short: 'del' },
};
const LMH = { low: 1, medium: 2, high: 3 };
const PROD = { none: 1, low: 2, medium: 3, high: 4 };
const MAKER_COST = { cheap: 1, standard: 2, strong: 3 };
const OWNER_RATIFY_ACTIONS = new Set(['delete', 'rent', 'freeze', 'quarantine', 'propose_contract_change']);

/**
 * SPEC ranking formula with continuous numerator proxies: enum scores saturate (mass 21 and
 * mass 800 both map to 'high'), which degenerates the plan to only-cheap-tiers. log2 keeps the
 * formula's shape while letting big-mass/high-churn seam and T2 work compete with cheap T0 wins.
 * The packet still carries the honest enums; this is only the ordering.
 */
function priorityOf(g) {
  const gainNum = Math.log2(1 + g.mass);
  const attnNum = Math.log2(1 + g.churn);
  const p = (gainNum * attnNum * PROD[g.impact] * g.exec.confidence) /
    (LMH[g.swc] * LMH[g.exec.evidence_cost] * MAKER_COST[g.exec.maker_tier] * 1 /* reversibility: branch-revert */);
  return Math.round(p * 1000) / 1000;
}

export async function runPlan(flags) {
  const ctx = buildContext(flags.repo);
  const topN = Number(flags.top ?? 20);
  const invDir = join(ctx.repoRoot, 'quality', 'inventory');
  for (const f of ['meta.json', 'tier-mass.json', 'hotspots.json']) {
    if (!existsSync(join(invDir, f))) throw new Error(`missing quality/inventory/${f} — run \`hone inventory --repo ${ctx.repoRoot}\` first`);
  }
  const meta = JSON.parse(readFileSync(join(invDir, 'meta.json'), 'utf8'));
  const tierMass = JSON.parse(readFileSync(join(invDir, 'tier-mass.json'), 'utf8'));
  const hotspots = JSON.parse(readFileSync(join(invDir, 'hotspots.json'), 'utf8'));
  if (meta.repo_sha !== ctx.git.sha) {
    process.stderr.write(`WARN: inventory repo_sha ${meta.repo_sha.slice(0, 12)} != HEAD ${ctx.git.sha.slice(0, 12)} — packets stamp the INVENTORY sha; re-run inventory for fresh routing\n`);
  }

  const cls = ctx.profile.classification || {};
  const publicGlobs = cls.public_surface_globs || [];
  const skipGlobs = [...(cls.generated_globs || []), ...(cls.freeze_globs || [])];
  const nogoRe = ctx.profile.markers?.nogo_path_pattern ? new RegExp(ctx.profile.markers.nogo_path_pattern, 'i') : null;
  const hotspotByFile = new Map(hotspots.files.map((h) => [h.file, h]));
  const churnOf = (f) => tierMass.by_file.find((x) => x.file === f)?.churn ?? hotspotByFile.get(f)?.churn ?? 0;

  // ---- 1. group the flagged universe by (file × tier) — the packet grain ----
  let skippedGenerated = 0;
  const groups = new Map();
  for (const u of tierMass.universe) {
    if (skipGlobs.length && skipGlobs.some((g) => globToRegExp(g).test(u.file))) { skippedGenerated++; continue; }
    const key = `${u.file}::${u.tier}`;
    if (!groups.has(key)) groups.set(key, { file: u.file, tier: u.tier, rows: [] });
    groups.get(key).rows.push(u);
  }

  // ---- 2. score each group (SPEC ranking formula, heuristic mapped scores) ----
  const scored = [...groups.values()].map((g) => {
    g.rows.sort((a, b) => b.cc - a.cc || a.line - b.line);
    const mass = g.rows.reduce((s, r) => s + r.excess, 0);
    const churn = churnOf(g.file);
    const coupling = hotspotByFile.get(g.file)?.coupling ?? 0;
    const sec = [...new Set(g.rows.flatMap((r) => r.sec || []))];
    const storage = [...new Set(g.rows.flatMap((r) => r.storage || []))];
    const pub = [...new Set(g.rows.flatMap((r) => r.public || []))];
    const publicHeavy = g.rows.some((r) => (r.public || []).length >= 2);
    const contractGlob = publicGlobs.find((gl) => globToRegExp(gl).test(g.file)) || null;
    const nogo = nogoRe ? nogoRe.test(g.file) : false;

    const exec = TIER_EXEC[g.tier];
    const gain = mass >= 20 ? 'high' : mass >= 8 ? 'medium' : 'low';
    const attn = churn >= 20 ? 'high' : churn >= 5 ? 'medium' : 'low';
    const impact = (publicHeavy || contractGlob) ? 'medium' : 'none';
    const swc = (sec.length || g.tier === 'T2-property' || g.tier === 'DELETE') ? 'high'
      : (storage.length || g.tier.startsWith('T2') || contractGlob || publicHeavy) ? 'medium' : 'low';
    const out = { ...g, mass, churn, coupling, sec, storage, pub, publicHeavy, contractGlob, nogo, exec, gain, attn, impact, swc, attention: churn * mass };
    out.priority = priorityOf(out);
    return out;
  }).sort((a, b) => b.priority - a.priority || b.attention - a.attention || a.file.localeCompare(b.file) || a.tier.localeCompare(b.tier));

  // ---- 3. DELETE upgrade on the top pool: hard 0-caller evidence for small named groups ----
  // (bounded grep cost; falsify's callerCount, ported: -w word match across owned dirs, minus the def)
  const dirsAbs = (meta.owned_dirs || []).map((d) => `'${join(ctx.repoRoot, d)}'`).join(' ');
  const callerCount = (fn) => {
    if (!/^[A-Za-z_$][\w$]*$/.test(fn)) return null;
    const n = ctx.sh(`grep -rncw '${fn}' ${dirsAbs} 2>/dev/null | awk -F: '{s+=$2} END{print s+0}'`).trim();
    return Number.isFinite(Number(n)) ? Math.max(0, Number(n) - 1) : null;
  };
  const pool = scored.slice(0, topN * 2);
  for (const g of pool) {
    if (g.tier !== 'T0' && g.tier !== 'T1-seam') continue;
    const named = g.rows.filter((r) => !r.is_anon && !r.is_callback);
    if (!named.length || named.length > 3 || named.length !== g.rows.length) continue;
    const counts = named.map((r) => ({ fn: r.fn, callers: callerCount(r.fn) }));
    if (counts.every((c) => c.callers === 0)) {
      g.tier = 'DELETE';
      g.exec = TIER_EXEC.DELETE;
      g.swc = 'high';
      g.deleteEvidence = counts;
      g.priority = priorityOf(g);
    }
  }
  pool.sort((a, b) => b.priority - a.priority || b.attention - a.attention || a.file.localeCompare(b.file) || a.tier.localeCompare(b.tier));
  const chosen = pool.slice(0, topN);

  // ---- 4. build + validate + write packets ----
  const outDir = join(ctx.repoRoot, 'quality', 'packets');
  mkdirSync(outDir, { recursive: true });
  const existingStatus = new Map(); // candidate_id → status (terminal packets are never overwritten)
  for (const f of (existsSync(outDir) ? readdirSync(outDir) : [])) {
    if (!f.endsWith('.yaml')) continue;
    try {
      const p = parseYaml(readFileSync(join(outDir, f), 'utf8'));
      if (p?.candidate_id && p?.status) existingStatus.set(p.candidate_id, p.status);
    } catch { /* foreign yaml — leave it alone */ }
  }

  const emitted = [], preserved = [];
  for (const g of chosen) {
    const packet = buildPacket(g, ctx, meta);
    assertValidPacket(packet, packet.candidate_id);
    const yaml = stringifyYaml(packet);
    const back = parseYaml(yaml);
    if (!deepEqual(packet, back)) {
      throw new Error(`YAML round-trip mismatch for ${packet.candidate_id} — refusing to write a packet that would not read back identically`);
    }
    const prior = existingStatus.get(packet.candidate_id);
    if (prior && prior !== 'pending') { preserved.push({ id: packet.candidate_id, status: prior }); continue; }
    writeFileSync(join(outDir, `${packet.candidate_id}.yaml`), yaml);
    emitted.push({ ...g, id: packet.candidate_id, action: packet.action, gate: packet.execution_gate });
  }

  // ---- 5. summary (stdout — the plan IS the product) ----
  const w = (s) => process.stdout.write(s + '\n');
  w(`hone plan — ${emitted.length} packets → quality/packets/ (repo_sha ${meta.repo_sha.slice(0, 12)}, from ${groups.size} candidate groups${skippedGenerated ? `, ${skippedGenerated} generated/frozen fns excluded` : ''})`);
  if (preserved.length) w(`preserved terminal packets (never overwritten): ${preserved.map((p) => `${p.id}[${p.status}]`).join(', ')}`);
  const byTier = {}, byAction = {};
  for (const e of emitted) { byTier[e.tier] = (byTier[e.tier] || 0) + 1; byAction[e.action] = (byAction[e.action] || 0) + 1; }
  w(`by tier:   ${JSON.stringify(byTier)}`);
  w(`by action: ${JSON.stringify(byAction)}  gates: ${JSON.stringify(emitted.reduce((o, e) => ((o[e.gate] = (o[e.gate] || 0) + 1), o), {}))}`);
  w(`priority | tier                   | mass | churn | gate       | candidate`);
  w('-'.repeat(100));
  for (const e of emitted) {
    w(`${String(e.priority).padStart(8)} | ${e.tier.padEnd(22)} | ${String(e.mass).padStart(4)} | ${String(e.churn).padStart(5)} | ${e.gate.padEnd(10)} | ${e.id}`);
  }
}

// ---------------------------------------------------------------- packet assembly

function buildPacket(g, ctx, meta) {
  const exec = g.exec;
  const action = g.tier === 'DELETE' ? 'delete' : 'preserve_refactor';
  const subsystem = subsystemOf(g.file);
  const base = g.file.split('/').pop().replace(/\.(js|mjs|cjs|ts|tsx|jsx)$/, '');
  const rows = g.rows.slice(0, 20);
  const symbols = rows.map((r) => r.is_anon
    ? { file: g.file, parent_fn: r.enclosing_fn || '<module>', anchor: r.fn, line: r.line }
    : r.fn);
  const namedSyms = [...new Set(rows.filter((r) => !r.is_anon).map((r) => r.fn))];
  const behavior = g.contractGlob ? 'contract' : (ctx.profile.classification?.behavior_status_default || 'likely_intended');
  const publicSurface = g.pub.length ? g.pub.slice(0, 10)
    : (g.contractGlob ? [`file matches public-surface glob '${g.contractGlob}' (v1 path heuristic — enumerate actual routes/tools in wave 2)`] : []);
  const gate = (OWNER_RATIFY_ACTIONS.has(action) || g.nogo) ? 'owner_ratify' : 'autonomous';

  const top = g.rows[0];
  const why = `[v1 deterministic heuristic, confidence ${exec.confidence} — metric-nominated; semantic validation pending (metrics nominate, never decide)] ` +
    `${top.why} Top fn '${typeof top.fn === 'string' ? top.fn : top.fn}' cc=${top.cc}. ` +
    `File attention: churn ${g.churn} × excess-cc mass ${g.mass} = ${g.attention}; ${g.rows.length} flagged fn(s) in this tier.` +
    (g.nogo ? ' PATH MATCHES NO-GO PATTERN (essential security complexity) — owner_ratify gate applied.' : '');

  const notAllowed = action === 'delete'
    ? ['new-dependency', 'auto-land-without-owner-ratification']
    : ['behavior-change', 'new-dependency', 'relocation-without-decomplecting',
      ...((behavior === 'contract' || publicSurface.length) ? ['public-shape-change'] : [])];

  // ---- authoring evidence (what plan actually looked at) ----
  const authoring = [
    {
      kind: 'inventory',
      detail: `quality/inventory/tier-mass.json universe rows for ${g.file}, tier ${g.tier} (repo_sha ${meta.repo_sha})`,
      result: `${g.rows.length} flagged fn(s), Σ excess-cc ${g.mass}, max cc ${top.cc}, router v1 tier ${g.tier}`,
    },
    {
      kind: 'churn',
      detail: `git log --since='${meta.churn_window}' -- ${g.file}`,
      result: `${g.churn} commits in window; coupling(fan-in proxy)=${g.coupling}`,
    },
  ];
  const captures = [...new Set(rows.flatMap((r) => r.captured_vars || []))];
  const capturesMut = [...new Set(rows.flatMap((r) => r.captured_mutable_vars || []))];
  const moduleRefs = [...new Set(rows.flatMap((r) => r.module_refs || []))];
  if (rows.some((r) => r.is_callback)) {
    authoring.push({
      kind: 'ast-scope',
      detail: 'true free-variable analysis (TS compiler API; function-scope captures only, v1.1 module-scope fix)',
      result: `captures=[${captures.slice(0, 8).join(',')}] mutable=[${capturesMut.join(',')}] module_refs=[${moduleRefs.slice(0, 8).join(',')}]`,
    });
  }
  if (g.deleteEvidence) {
    for (const c of g.deleteEvidence) {
      authoring.push({
        kind: 'liveness-sweep',
        detail: `grep -rncw '${c.fn}' ${(meta.owned_dirs || []).join(' ')} (definition occurrence subtracted)`,
        result: `callers=0 — definition-only; grep-based, NOT a liveness proof (dynamic dispatch/exports unchecked → owner_ratify)`,
      });
    }
  }

  const now = new Date().toISOString();
  return {
    candidate_id: `${slug(subsystem)}-${slug(base)}-${exec.short}-${djb2(`${g.file}|${g.tier}|${rows.map((r) => r.line).join(',')}`)}`,
    created: now,
    repo_sha: meta.repo_sha,
    subsystem,
    files: [g.file],
    symbols,
    public_surface: publicSurface,
    behavior_status: behavior,
    ownership: g.tier === 'DELETE' ? 'DELETE' : 'OWN',
    action,
    proof_class: exec.proof_class,
    execution_gate: gate,
    why_this_matters: why,
    plan: {
      transform_class: exec.transform_class,
      instruction: instructionFor(g, namedSyms, captures, capturesMut),
    },
    expected_quality_gain: g.gain,
    owner_attention_reduction: g.attn,
    product_impact: g.impact,
    // ranking PRIOR persisted for `run` ordering (recalibrated by cost actuals; never a quality claim)
    priority: { score: g.priority, computed: now, inputs: { mass: g.mass, churn: g.churn } },
    risk: {
      blast_radius: g.coupling < 5 ? 'local' : g.coupling < 15 ? 'subsystem' : 'cross-cutting',
      reversibility: 'branch-revert',
      silent_wrongness_cost: g.swc,
      property_at_risk: g.sec.length
        ? `security-marker-guarded logic present [${g.sec.slice(0, 4).join(', ')}] — maker must NAME the invariant before work (v1 heuristic, unvalidated)`
        : null,
    },
    authoring_evidence: authoring,
    evidence_required: evidenceFor(g, namedSyms, ctx, meta),
    not_allowed: notAllowed,
    maker_tier: exec.maker_tier,
    judge_tier: g.swc === 'high' ? 'strong' : 'standard',
    maker_provider: null,
    judge_provider: null,
    batch_key: `${action}×${exec.proof_class}×${subsystem}`,
    touchset: [g.file],
    estimates: { tokens: exec.tokens, evidence_cost: exec.evidence_cost },
    depends_on: [],
    unlocks: [],
    status: 'pending',
    outcome: {
      commit: null, skip_reason: null, blocked_on: null, judge_verdict: null,
      evidence_receipts: [], tokens_actual: null, lesson: null,
    },
  };
}

function instructionFor(g, namedSyms, captures, capturesMut) {
  const anchors = g.rows.slice(0, 6).map((r) => (r.is_anon ? `${r.fn}@L${r.line}(in ${r.enclosing_fn || '<module>'})` : r.fn)).join(', ');
  const more = g.rows.length > 6 ? ` (+${g.rows.length - 6} more — see quality/inventory/tier-mass.json)` : '';
  switch (g.tier) {
    case 'T1-extractable-callback':
      return `Hoist the anonymous callback(s) ${anchors}${more} in ${g.file} to named top-level functions (zero function-scope captures — exact move). Name each for the concept it implements; body must be identical under whitespace normalization; call sites reference the new names.`;
    case 'T1b-explicit-context':
      return `Extract the capturing callback(s) ${anchors}${more} in ${g.file} into named functions taking an explicit context parameter carrying [${captures.slice(0, 8).join(', ')}]; call sites build and pass the context. Hidden state → explicit; no behavior change.`;
    case 'T0':
      return `Apply certified local simplifications to ${anchors}${more} in ${g.file} (guard-clause flattening, branch de-nesting, redundant-condition removal) — mechanical transforms with checkable side conditions only; no seam moves, no renames of exported symbols.`;
    case 'T1-seam':
      return `Decomplect ${anchors}${more} in ${g.file} (max cc ${g.rows[0].cc}) by splitting at a REAL concept seam: name the sub-concepts, keep the public signature stable. Reject relocation — moving the blob behind a new name with green tests is not decomplecting.`;
    case 'T2-async-order':
      return `Decomplect ${anchors}${more} in ${g.file} WITHOUT reordering awaited effects: document the awaited-effect order first, restructure around it, and show the order is unchanged (trace or focused integration evidence).`;
    case 'T2-capturing-mutable':
      return `Make the shared mutable captures [${capturesMut.join(', ')}] of ${anchors}${more} in ${g.file} explicit (pass a state object or return updates) BEFORE any extraction; mutation semantics must be proven unchanged.`;
    case 'T2-property':
      return `NAME the property at risk first (security markers present: [${g.sec.slice(0, 4).join(', ')}]), then decomplect ${anchors}${more} in ${g.file} ONLY around the guard logic — every security-marker line byte-identical in the diff; the evidence must cover THAT invariant.`;
    case 'DELETE':
      return `Verify liveness roots for ${namedSyms.join(', ')} in ${g.file} (entrypoints, routes, specs, dynamic dispatch, config, re-exports — grep found 0 callers but grep is not a liveness proof), then delete the dead function(s) and any now-unused imports. Owner ratifies before landing.`;
    default:
      return `Decomplect ${anchors}${more} in ${g.file} per tier ${g.tier}.`;
  }
}

function evidenceFor(g, namedSyms, ctx, meta) {
  const ev = [];
  const repo = ctx.repoRoot;
  const typecheck = ctx.profile.commands?.typecheck;
  const test = ctx.profile.commands?.test;
  if (typecheck) ev.push({ rung: 'typecheck', command: `cd ${repo} && ${typecheck}`, expect: 'exit 0' });
  if (test) ev.push({ rung: 'direct-test', command: `cd ${repo} && ${test}`, expect: 'all tests pass; 0 fail; no new skips' });
  if (g.tier === 'T1-extractable-callback') {
    ev.push({
      rung: 'whitespace-normalized-body-move',
      command: `cd ${repo} && git diff -w -- ${g.file}`,
      expect: 'hoisted bodies appear verbatim under -w (indent-only change at the move site); no logic edits',
    });
  }
  if (g.tier === 'T2-property') {
    const pat = g.sec.slice(0, 6).map(escRe).join('|');
    ev.push({
      rung: 'guard-byte-identity',
      command: `cd ${repo} && git diff -U0 -- ${g.file} | grep -inE '(${pat})' || echo GUARDS-UNTOUCHED`,
      expect: 'prints GUARDS-UNTOUCHED — no security-marker line added/removed/modified by the diff',
    });
  }
  if (g.tier === 'DELETE' && namedSyms.length) {
    ev.push({
      rung: 'liveness-sweep',
      command: `cd ${repo} && grep -rnw '${namedSyms[0]}' ${(meta.owned_dirs || []).join(' ')} | grep -v '${g.file}'`,
      expect: 'no references outside the defining file before deletion; repo-wide grep empty after',
    });
  }
  if (namedSyms.length && g.tier !== 'DELETE') {
    ev.push({
      rung: 'complexity-remeasure',
      command: `node ${HONE_ROOT}/collectors/scope-fn.mjs --repo ${repo} --target '${g.file}::${namedSyms[0]}'`,
      expect: `found=true and cognitive_before < ${g.rows.find((r) => r.fn === namedSyms[0])?.cc ?? g.rows[0].cc} (packet baseline); red_scan unchanged`,
    });
  }
  if (!ev.length) {
    // profile configured no oracle commands and no tier rung applies — the packet is honest about it.
    ev.push({
      rung: 'no-oracle-configured',
      command: `echo 'BLOCKED: configure commands.test / commands.typecheck in ${repo}/quality/hone.yaml'`,
      expect: 'work MUST mark this packet blocked(missing-oracle) — no evidence, no preservation claim',
    });
  }
  return ev;
}
