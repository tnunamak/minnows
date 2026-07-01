// validate-packet.mjs — STRICT candidate-packet validator (schema v1.1).
//
// Hand-mirrors schemas/candidate-packet.yaml (the schema file is documentation-YAML, not
// machine-readable JSON Schema — this module IS the executable form; keep the two in lockstep).
// Strict by design: unknown keys reject, enums reject, missing execution_gate rejects
// (fail-closed — SPEC acceptance test #4: malformed packets crash loudly, they never land
// half-valid in the packet stream).

const ENUMS = {
  behavior_status: ['contract', 'likely_intended', 'provisional', 'accidental', 'unknown'],
  ownership: ['OWN', 'RENT', 'DELETE', 'FREEZE', 'QUARANTINE', 'GENERATED', 'TEMPORARY'],
  action: ['preserve_refactor', 'idealize_rewrite', 'surface_repair', 'generate_evidence',
    'delete', 'rent', 'freeze', 'quarantine', 'document', 'propose_contract_change'],
  proof_class: ['type_only', 'exact_move', 'certified_transform', 'pure_logic', 'effectful',
    'property_at_risk', 'judgment_first', 'liveness_roots', 'proposal'],
  execution_gate: ['autonomous', 'owner_ratify'],
  expected_quality_gain: ['low', 'medium', 'high'],
  owner_attention_reduction: ['low', 'medium', 'high'],
  product_impact: ['none', 'low', 'medium', 'high'],
  maker_tier: ['cheap', 'standard', 'strong'],
  judge_tier: ['standard', 'strong'],
  status: ['pending', 'in_progress', 'landed', 'reverted', 'skipped', 'blocked'],
};

const TOP_KEYS = [
  'candidate_id', 'created', 'repo_sha', 'subsystem', 'files', 'symbols', 'public_surface',
  'behavior_status', 'ownership', 'action', 'proof_class', 'execution_gate',
  'why_this_matters', 'plan', 'expected_quality_gain', 'owner_attention_reduction', 'product_impact',
  'risk', 'authoring_evidence', 'evidence_required', 'not_allowed',
  'maker_tier', 'judge_tier', 'maker_provider', 'judge_provider', 'batch_key', 'touchset',
  'estimates', 'depends_on', 'unlocks', 'status', 'outcome',
];

const isStr = (v) => typeof v === 'string';
const isNonEmptyStr = (v) => isStr(v) && v.trim().length > 0;
const isStrOrNull = (v) => v === null || isStr(v);
const isInt = (v) => Number.isInteger(v);
const isMap = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/** @returns string[] — empty when valid. */
export function validatePacket(p) {
  const errs = [];
  const err = (m) => errs.push(m);
  if (!isMap(p)) return ['packet is not a map'];

  for (const k of Object.keys(p)) if (!TOP_KEYS.includes(k)) err(`unknown top-level key: ${k}`);
  for (const k of TOP_KEYS) if (!(k in p)) err(`missing required key: ${k}`);
  if (errs.length) return errs; // shape is wrong — field checks below would just cascade

  if (!isNonEmptyStr(p.candidate_id)) err('candidate_id: non-empty string required');
  if (!isStr(p.created) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(p.created)) err(`created: iso-timestamp required, got ${JSON.stringify(p.created)}`);
  if (!isNonEmptyStr(p.repo_sha)) err('repo_sha: non-empty string required');
  if (!isNonEmptyStr(p.subsystem)) err('subsystem: non-empty string required');

  if (!Array.isArray(p.files) || !p.files.length || !p.files.every(isNonEmptyStr)) err('files: non-empty [string] required');
  if (!Array.isArray(p.symbols)) err('symbols: array required');
  else for (const [i, s] of p.symbols.entries()) {
    if (isNonEmptyStr(s)) continue;
    if (isMap(s)) {
      const keys = Object.keys(s).sort().join(',');
      if (keys !== 'anchor,file,line,parent_fn') { err(`symbols[${i}]: anchor map must have exactly {file, parent_fn, anchor, line}, got {${keys}}`); continue; }
      if (!isNonEmptyStr(s.file) || !isNonEmptyStr(s.parent_fn) || !isNonEmptyStr(s.anchor) || !isInt(s.line)) {
        err(`symbols[${i}]: anchor map needs string file/parent_fn/anchor + int line`);
      }
    } else err(`symbols[${i}]: string or {file, parent_fn, anchor, line} required`);
  }
  if (!Array.isArray(p.public_surface) || !p.public_surface.every(isNonEmptyStr)) err('public_surface: [string] required');

  for (const [k, allowed] of Object.entries(ENUMS)) {
    if (k in p && !allowed.includes(p[k])) err(`${k}: must be one of [${allowed.join(' | ')}], got ${JSON.stringify(p[k])}`);
  }

  if (!isNonEmptyStr(p.why_this_matters)) err('why_this_matters: non-empty string required');

  if (!isMap(p.plan)) err('plan: map {transform_class, instruction} required');
  else {
    for (const k of Object.keys(p.plan)) if (!['transform_class', 'instruction'].includes(k)) err(`plan: unknown key ${k}`);
    if (!isNonEmptyStr(p.plan.transform_class)) err('plan.transform_class: non-empty string required');
    if (!isNonEmptyStr(p.plan.instruction)) err('plan.instruction: non-empty string required');
  }

  if (!isMap(p.risk)) err('risk: map required');
  else {
    const RISK = {
      blast_radius: ['local', 'subsystem', 'cross-cutting'],
      reversibility: ['trivial', 'branch-revert', 'hard'],
      silent_wrongness_cost: ['low', 'medium', 'high'],
    };
    for (const k of Object.keys(p.risk)) if (!(k in RISK) && k !== 'property_at_risk') err(`risk: unknown key ${k}`);
    for (const [k, allowed] of Object.entries(RISK)) {
      if (!allowed.includes(p.risk[k])) err(`risk.${k}: must be one of [${allowed.join(' | ')}], got ${JSON.stringify(p.risk[k])}`);
    }
    if (!('property_at_risk' in p.risk) || !isStrOrNull(p.risk.property_at_risk)) err('risk.property_at_risk: string|null required');
  }

  if (!Array.isArray(p.authoring_evidence)) err('authoring_evidence: array required');
  else for (const [i, a] of p.authoring_evidence.entries()) {
    if (!isMap(a) || Object.keys(a).sort().join(',') !== 'detail,kind,result' ||
      !isNonEmptyStr(a.kind) || !isNonEmptyStr(a.detail) || !isNonEmptyStr(a.result)) {
      err(`authoring_evidence[${i}]: {kind, detail, result} (all non-empty strings) required`);
    }
  }

  if (!Array.isArray(p.evidence_required) || !p.evidence_required.length) err('evidence_required: non-empty array required');
  else for (const [i, e] of p.evidence_required.entries()) {
    if (!isMap(e) || Object.keys(e).sort().join(',') !== 'command,expect,rung' ||
      !isNonEmptyStr(e.rung) || !isNonEmptyStr(e.command) || !isNonEmptyStr(e.expect)) {
      err(`evidence_required[${i}]: {rung, command, expect} (all non-empty strings — LITERAL runnable command, not prose) required`);
    }
  }
  if (!Array.isArray(p.not_allowed) || !p.not_allowed.every(isNonEmptyStr)) err('not_allowed: [string] required');

  if (!isStrOrNull(p.maker_provider)) err('maker_provider: string|null required');
  if (!isStrOrNull(p.judge_provider)) err('judge_provider: string|null required');
  if (p.maker_provider !== null && p.judge_provider !== null && p.maker_provider === p.judge_provider) {
    err(`maker_provider and judge_provider MUST differ (maker ≠ judge is structural), both are ${JSON.stringify(p.maker_provider)}`);
  }
  if (!isNonEmptyStr(p.batch_key)) err('batch_key: non-empty string required');
  if (!Array.isArray(p.touchset) || !p.touchset.length || !p.touchset.every(isNonEmptyStr)) err('touchset: non-empty [string] required');

  if (!isMap(p.estimates)) err('estimates: map {tokens, evidence_cost} required');
  else {
    for (const k of Object.keys(p.estimates)) if (!['tokens', 'evidence_cost'].includes(k)) err(`estimates: unknown key ${k}`);
    if (!isInt(p.estimates.tokens)) err('estimates.tokens: int required');
    if (!['low', 'medium', 'high'].includes(p.estimates.evidence_cost)) err('estimates.evidence_cost: low|medium|high required');
  }
  if (!Array.isArray(p.depends_on) || !p.depends_on.every(isNonEmptyStr)) err('depends_on: [candidate_id] required');
  if (!Array.isArray(p.unlocks) || !p.unlocks.every(isNonEmptyStr)) err('unlocks: [candidate_id] required');

  if (!isMap(p.outcome)) err('outcome: map required');
  else {
    const OUT_KEYS = ['commit', 'skip_reason', 'blocked_on', 'judge_verdict', 'evidence_receipts', 'tokens_actual', 'lesson'];
    for (const k of Object.keys(p.outcome)) if (!OUT_KEYS.includes(k)) err(`outcome: unknown key ${k}`);
    for (const k of OUT_KEYS) if (!(k in p.outcome)) err(`outcome: missing key ${k}`);
    for (const k of ['commit', 'skip_reason', 'blocked_on', 'judge_verdict', 'lesson']) {
      if (k in p.outcome && !isStrOrNull(p.outcome[k])) err(`outcome.${k}: string|null required`);
    }
    if ('evidence_receipts' in p.outcome && (!Array.isArray(p.outcome.evidence_receipts) || !p.outcome.evidence_receipts.every(isNonEmptyStr))) {
      err('outcome.evidence_receipts: [string] required');
    }
    if ('tokens_actual' in p.outcome && p.outcome.tokens_actual !== null && !isInt(p.outcome.tokens_actual)) err('outcome.tokens_actual: int|null required');
    if (p.status === 'skipped' && !isNonEmptyStr(p.outcome.skip_reason)) err('status=skipped REQUIRES outcome.skip_reason');
    if (p.status === 'blocked' && !isNonEmptyStr(p.outcome.blocked_on)) err('status=blocked REQUIRES outcome.blocked_on');
  }

  return errs;
}

/** crash loudly on a malformed packet (SPEC acceptance test #4). */
export function assertValidPacket(p, context = '') {
  const errs = validatePacket(p);
  if (errs.length) {
    throw new Error(`MALFORMED PACKET${context ? ` (${context})` : ''} — refusing to emit:\n  - ${errs.join('\n  - ')}`);
  }
}
