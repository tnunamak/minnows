// routing.mjs — L1 model-selection architecture: DATA (models.json registry) is split
// from POLICY (routing.json class→candidate table) is split from MECHANISM (selectAgent,
// the ONE deterministic runtime chooser). No LLM ever chooses a model at call time; a
// packet may pin `routing_class`, never a model (validator-enforced).
//
// Fail-CLOSED: malformed registry/policy throws at load; a registry entry without
// calibration provenance is INELIGIBLE for routing until `hone calibrate` produces a
// report (override only via selectAgent's allowUncalibrated, which callers must
// ledger-note). Quota state is an optional INPUT — honest-null when unavailable.

import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROUTING_CLASSES = ['certified-mechanical', 'extraction', 'async-order-oracle', 'hard-ambiguous'];
const PROVIDERS = ['claude', 'codex'];
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
const WRONGNESS_ORDER = ['low', 'medium', 'high'];

const HONE_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_POLICY_PATH = join(HONE_ROOT, 'routing.json');
const DEFAULT_REGISTRY_PATH = join(HONE_ROOT, 'models.json');

/** load + validate models.json (the registry is load-bearing for every agent invocation). */
export function loadRegistry(path = DEFAULT_REGISTRY_PATH) {
  const r = JSON.parse(readFileSync(path, 'utf8'));
  const bad = (m) => { throw new Error(`models.json invalid (${path}): ${m}`); };
  if (!r.models || typeof r.models !== 'object' || !Object.keys(r.models).length) bad('models map required');
  for (const [name, m] of Object.entries(r.models)) {
    const at = `models.${name}`;
    if (!PROVIDERS.includes(m.provider)) bad(`${at}.provider: one of [${PROVIDERS.join('|')}] required`);
    for (const k of ['id', 'short', 'lineage']) if (typeof m[k] !== 'string' || !m[k].trim()) bad(`${at}.${k}: non-empty string required`);
    if (!m.pricing || ['in', 'out', 'cache_read'].some((k) => !(typeof m.pricing[k] === 'number' && Number.isFinite(m.pricing[k])))) {
      bad(`${at}.pricing: {in, out, cache_read} numbers required`);
    }
    if (typeof m.quota_pool !== 'string' || !m.quota_pool.trim()) bad(`${at}.quota_pool: non-empty string required`);
    if (!Array.isArray(m.efforts) || !m.efforts.length || m.efforts.some((e) => !EFFORTS.includes(e))) {
      bad(`${at}.efforts: non-empty subset of [${EFFORTS.join('|')}] required`);
    }
    if (!Number.isInteger(m.tier_rank) || m.tier_rank < 1) bad(`${at}.tier_rank: int >= 1 required`);
    if (!['active', 'deprecated', 'experimental'].includes(m.status)) bad(`${at}.status: active|deprecated|experimental required`);
    if (m.calibration !== null && (typeof m.calibration !== 'object' || Array.isArray(m.calibration) ||
      typeof m.calibration.source !== 'string' || typeof m.calibration.date !== 'string')) {
      bad(`${at}.calibration: null (uncalibrated => routing-ineligible) or {type, source, date, note?} required`);
    }
  }
  return r;
}

/** load + validate routing.json policy (references must resolve against the registry). */
export function loadRouting(path = DEFAULT_POLICY_PATH, registry = null) {
  const r = JSON.parse(readFileSync(path, 'utf8'));
  const bad = (m) => { throw new Error(`routing.json invalid (${path}): ${m}`); };
  if (!r.classes || typeof r.classes !== 'object') bad('classes map required');
  const names = Object.keys(r.classes).sort();
  if (names.join(',') !== [...ROUTING_CLASSES].sort().join(',')) {
    bad(`classes must be exactly [${ROUTING_CLASSES.join(', ')}], got [${names.join(', ')}]`);
  }
  const reg = registry ?? loadRegistry();
  for (const [name, cls] of Object.entries(r.classes)) {
    if (!Array.isArray(cls.maker) || !cls.maker.length) bad(`classes.${name}.maker: non-empty ordered candidate list required`);
    for (const [i, c] of cls.maker.entries()) {
      const entry = reg.models[c.model];
      if (!entry) bad(`classes.${name}.maker[${i}].model: '${c.model}' is not a models.json registry name (known: ${Object.keys(reg.models).join(', ')})`);
      if (!entry.efforts.includes(c.effort)) bad(`classes.${name}.maker[${i}].effort: '${c.effort}' unsupported by ${c.model} (supported: ${entry.efforts.join(', ')})`);
    }
  }
  if (!ROUTING_CLASSES.includes(r.default_class)) bad('default_class must be a known class');
  for (const [k, v] of Object.entries(r.proof_class_map ?? {})) {
    if (!ROUTING_CLASSES.includes(v)) bad(`proof_class_map.${k} -> unknown class '${v}'`);
  }
  for (const [k, v] of Object.entries(r.action_overrides ?? {})) {
    if (!ROUTING_CLASSES.includes(v)) bad(`action_overrides.${k} -> unknown class '${v}'`);
  }
  if (!r.escalation || !Number.isInteger(r.escalation.strikes_per_step) || r.escalation.strikes_per_step < 1) {
    bad('escalation: {rule, strikes_per_step >= 1} required');
  }
  if (!r.quota || !(typeof r.quota.pressure_threshold === 'number' && r.quota.pressure_threshold > 0 && r.quota.pressure_threshold <= 1)) {
    bad('quota: {pressure_threshold in (0,1]} required');
  }
  const b = r.batch ?? {};
  if (!Array.isArray(b.eligible_actions) || !Array.isArray(b.eligible_proof_classes) ||
    !WRONGNESS_ORDER.includes(b.max_silent_wrongness) || !Array.isArray(b.eligible_behavior_status)) {
    bad('batch: {eligible_actions[], eligible_proof_classes[], max_silent_wrongness, eligible_behavior_status[]} required');
  }
  return r;
}

/** the deterministic class for a packet: explicit pin > action override > proof-class map > default. */
export function resolveRoutingClass(packet, policy) {
  if (packet.routing_class != null) return packet.routing_class; // validator guarantees membership
  return policy.action_overrides?.[packet.action]
    ?? policy.proof_class_map?.[packet.proof_class]
    ?? policy.default_class;
}

/** one candidate materialized with its registry facts (what consumers/ledgers see). */
function materialize(name, effort, registry) {
  const e = registry.models[name];
  return {
    name, effort,
    provider: e.provider, model: e.id, short: e.short,
    lineage: e.lineage, tier_rank: e.tier_rank, quota_pool: e.quota_pool,
    status: e.status, calibrated: e.calibration !== null,
  };
}

/** full routing for a packet: {class, maker: [materialized ordered candidates], judge_constraint, escalation}. */
export function resolveRouting(packet, policy, registry = null) {
  const reg = registry ?? loadRegistry();
  const cls = resolveRoutingClass(packet, policy);
  const entry = policy.classes[cls];
  if (!entry) throw new Error(`routing: unknown class '${cls}' for ${packet.candidate_id} (known: ${ROUTING_CLASSES.join(', ')})`);
  return {
    class: cls,
    maker: entry.maker.map((c) => materialize(c.model, c.effort, reg)),
    judge_constraint: policy.judge_constraint,
    escalation: policy.escalation,
  };
}

/**
 * THE runtime model selection — one exported deterministic function; nothing with a
 * context window chooses a model at call time.
 *
 *   selectAgent(cls, attemptNo, quotaState, registry, policy, opts?)
 *     -> {provider, model (exact id), short, name, effort, tier_rank, quota_pool, notes[]}
 *
 * attemptNo = 0-based strike count (failed gates + judge REVISEs); two-strike escalation
 * (policy.escalation.strikes_per_step) walks the ordered candidate list, clamped at the end.
 * Eligibility (fail-closed): registry entry exists, status active, calibration present
 * (override: opts.allowUncalibrated — callers MUST ledger-note the override).
 * quotaState (optional, honest-null): {pools: {<pool>: utilization 0..1}}; when the chosen
 * candidate's pool exceeds policy.quota.pressure_threshold, prefer an eligible SAME-OR-
 * HIGHER tier_rank candidate from a different pool; otherwise proceed with a note.
 * opts.providerFilter constrains candidates to one provider (the `hone work --maker` case).
 */
export function selectAgent(cls, attemptNo, quotaState, registry, policy, opts = {}) {
  const clsEntry = policy.classes[cls];
  if (!clsEntry) throw new Error(`selectAgent: unknown class '${cls}' (known: ${ROUTING_CLASSES.join(', ')})`);
  const notes = [];
  let candidates = clsEntry.maker.map((c) => ({ ...materialize(c.model, c.effort, registry), _cal: registry.models[c.model].calibration !== null }));
  if (opts.providerFilter) {
    candidates = candidates.filter((c) => c.provider === opts.providerFilter);
    if (!candidates.length) throw new Error(`selectAgent: class '${cls}' has no candidates for provider '${opts.providerFilter}' (fail-closed; route via a provider the policy names)`);
  }
  const eligible = candidates.filter((c) => {
    if (c.status !== 'active') { notes.push(`skipped ${c.name}: status=${c.status}`); return false; }
    if (!c._cal && !opts.allowUncalibrated) { notes.push(`skipped ${c.name}: NO calibration report — routing-ineligible (run hone calibrate --model ${c.name}; override=allowUncalibrated requires a ledger note)`); return false; }
    return true;
  });
  if (!eligible.length) {
    throw new Error(`selectAgent: no eligible candidate for class '${cls}' (${notes.join('; ') || 'empty candidate list'}) — fail-closed`);
  }
  const step = policy.escalation?.strikes_per_step ?? 2;
  const idx = Math.min(Math.floor(Math.max(0, attemptNo) / step), eligible.length - 1);
  let chosen = eligible[idx];
  const threshold = policy.quota?.pressure_threshold ?? 0.9;
  const util = quotaState?.pools?.[chosen.quota_pool];
  if (typeof util === 'number' && util > threshold) {
    const alt = eligible.find((c) =>
      c.quota_pool !== chosen.quota_pool &&
      c.tier_rank >= chosen.tier_rank &&
      !(typeof quotaState?.pools?.[c.quota_pool] === 'number' && quotaState.pools[c.quota_pool] > threshold));
    if (alt) {
      notes.push(`quota-pressure: pool '${chosen.quota_pool}' at ${util} > ${threshold} — shifted ${chosen.name} -> ${alt.name} (same-or-higher tier, pool '${alt.quota_pool}')`);
      chosen = alt;
    } else {
      notes.push(`quota-pressure: pool '${chosen.quota_pool}' at ${util} > ${threshold} — no eligible alternate pool at tier_rank >= ${chosen.tier_rank}; proceeding on the pressured pool`);
    }
  }
  const { _cal, ...result } = chosen;
  return { ...result, notes };
}

/**
 * L2 batch eligibility — the RISKY-class refusal is deterministic and data-driven:
 * auth/storage/behavior-visible work stays per-order. Returns {eligible, reason}.
 */
export function isBatchEligible(packet, policy) {
  const b = policy.batch;
  const no = (reason) => ({ eligible: false, reason });
  if (packet.execution_gate !== 'autonomous') return no(`execution_gate '${packet.execution_gate}' is not autonomous`);
  if (!b.eligible_actions.includes(packet.action)) return no(`action '${packet.action}' is not batch-eligible (allowed: ${b.eligible_actions.join(', ')})`);
  if (!b.eligible_proof_classes.includes(packet.proof_class)) return no(`proof_class '${packet.proof_class}' is not batch-eligible (allowed: ${b.eligible_proof_classes.join(', ')})`);
  if (!b.eligible_behavior_status.includes(packet.behavior_status)) return no(`behavior_status '${packet.behavior_status}' is behavior-visible — per-order only`);
  const wrongness = WRONGNESS_ORDER.indexOf(packet.risk?.silent_wrongness_cost);
  if (wrongness === -1 || wrongness > WRONGNESS_ORDER.indexOf(b.max_silent_wrongness)) {
    return no(`silent_wrongness_cost '${packet.risk?.silent_wrongness_cost}' exceeds batch ceiling '${b.max_silent_wrongness}'`);
  }
  if (packet.risk?.property_at_risk != null) return no(`named property_at_risk ('${packet.risk.property_at_risk}') — auth/storage/security stays per-order`);
  return { eligible: true, reason: null };
}
