// ledger.mjs — append-only claim/cost ledgers: <repo>/quality/claims.jsonl + cost.jsonl.
//
// Hand-mirrors schemas/claim.yaml and schemas/cost-entry.yaml (documentation-YAML, not
// machine-readable — this module IS the executable form; keep them in lockstep).
// Fail-CLOSED: a malformed line crashes BEFORE the append. The ledgers are the product
// (reports compile from claims, economics from cost) — one corrupt line poisons every
// future compile, so we refuse to write it.

import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export const CLAIM_TYPES = ['verified_fact', 'judged_design_claim', 'behavior_preserved',
  'behavior_changed', 'hypothesis', 'uncertainty', 'remaining_work'];
export const OUTCOMES = ['landed', 'reverted', 'skipped', 'blocked'];
export const JUDGE_RESULTS = ['PASS', 'REVISE', 'REJECT', 'PENDING'];
// stage-level token attribution (token-economics instrumentation): a cost entry MAY
// carry per-stage records — the efficiency levers are unoptimizable without them.
export const USAGE_STAGES = ['recon', 'edit', 'test', 'judge', 'plan'];
export const USAGE_ROLES = ['maker', 'judge', 'engine', 'planner'];
const EVIDENCE_REQUIRED_TYPES = ['verified_fact', 'behavior_preserved', 'behavior_changed'];

const isStr = (v) => typeof v === 'string';
const isNonEmptyStr = (v) => isStr(v) && v.trim().length > 0;
const isMap = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isIso = (v) => isStr(v) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(v);

export const claimsPath = (repoRoot) => join(repoRoot, 'quality', 'claims.jsonl');
export const costPath = (repoRoot) => join(repoRoot, 'quality', 'cost.jsonl');

/** @returns string[] — empty when valid (schema: schemas/claim.yaml). */
export function validateClaim(c) {
  const errs = [];
  const err = (m) => errs.push(m);
  if (!isMap(c)) return ['claim is not a map'];
  const KEYS = ['claim_id', 'created', 'candidate_id', 'type', 'statement', 'evidence', 'judge'];
  for (const k of Object.keys(c)) if (!KEYS.includes(k)) err(`unknown key: ${k}`);
  for (const k of KEYS) if (!(k in c)) err(`missing key: ${k}`);
  if (errs.length) return errs;

  if (!isNonEmptyStr(c.claim_id)) err('claim_id: non-empty string required');
  if (!isIso(c.created)) err('created: iso-timestamp required');
  if (!isNonEmptyStr(c.candidate_id)) err('candidate_id: non-empty string required');
  if (!CLAIM_TYPES.includes(c.type)) err(`type: must be one of [${CLAIM_TYPES.join(' | ')}], got ${JSON.stringify(c.type)}`);
  if (!isNonEmptyStr(c.statement)) err('statement: non-empty string required');

  if (!Array.isArray(c.evidence)) err('evidence: array required');
  else {
    for (const [i, e] of c.evidence.entries()) {
      if (!isMap(e) || Object.keys(e).sort().join(',') !== 'command,output_digest' ||
        !isNonEmptyStr(e.command) || !isNonEmptyStr(e.output_digest)) {
        err(`evidence[${i}]: {command, output_digest} (non-empty strings) required`);
      }
    }
    if (EVIDENCE_REQUIRED_TYPES.includes(c.type) && !c.evidence.length) {
      err(`type=${c.type} REQUIRES non-empty evidence (a claim without evidence is a hypothesis)`);
    }
  }

  if (c.judge !== null) {
    if (!isMap(c.judge) || Object.keys(c.judge).sort().join(',') !== 'provider,verdict' ||
      !isNonEmptyStr(c.judge.provider) || !isNonEmptyStr(c.judge.verdict)) {
      err('judge: null or {provider, verdict} (non-empty strings) required');
    }
  } else if (c.type === 'judged_design_claim') {
    err('type=judged_design_claim REQUIRES judge {provider, verdict}');
  }
  return errs;
}

/** one per-stage usage record inside a cost entry's optional `stages` array. */
export function validateStageEntry(s, i) {
  const errs = [];
  const err = (m) => errs.push(`stages[${i}]${m}`);
  if (!isMap(s)) return [`stages[${i}]: not a map`];
  const KEYS = ['role', 'provider', 'model', 'stage', 'tokens_in', 'tokens_out', 'tokens_total',
    'cache_read_tokens', 'cost_usd', 'wall_s', 'quota_pts'];
  for (const k of Object.keys(s)) if (!KEYS.includes(k)) err(`: unknown key '${k}'`);
  if (!USAGE_ROLES.includes(s.role)) err(`.role: one of [${USAGE_ROLES.join('|')}] required`);
  if (!isNonEmptyStr(s.provider)) err('.provider: non-empty string required');
  if (s.model != null && !isNonEmptyStr(s.model)) err('.model: non-empty string|null required');
  if (s.stage != null && !USAGE_STAGES.includes(s.stage)) err(`.stage: one of [${USAGE_STAGES.join('|')}]|null required (null = unattributed)`);
  for (const k of ['tokens_in', 'tokens_out', 'tokens_total', 'cache_read_tokens']) {
    if (s[k] != null && !Number.isInteger(s[k])) err(`.${k}: int|null required`);
  }
  for (const k of ['cost_usd', 'wall_s', 'quota_pts']) {
    if (s[k] != null && !(typeof s[k] === 'number' && Number.isFinite(s[k]))) err(`.${k}: number|null required`);
  }
  return errs;
}

/** @returns string[] — empty when valid (schema: schemas/cost-entry.yaml).
 * `stages` / `quota_pts` / `batch` are OPTIONAL additive fields (lane instrumentation +
 * batch amortization); the subprocess path never writes them — old entries stay valid. */
export function validateCostEntry(e) {
  const errs = [];
  const err = (m) => errs.push(m);
  if (!isMap(e)) return ['cost entry is not a map'];
  const KEYS = ['job_id', 'created', 'candidate_id', 'workflow', 'maker', 'judge', 'tokens_in',
    'tokens_out', 'cost_usd', 'wall_time_s', 'landed', 'revision_count', 'judge_result',
    'outcome', 'followup_created'];
  const OPTIONAL_KEYS = ['stages', 'quota_pts', 'batch'];
  for (const k of Object.keys(e)) if (!KEYS.includes(k) && !OPTIONAL_KEYS.includes(k)) err(`unknown key: ${k}`);
  for (const k of KEYS) if (!(k in e)) err(`missing key: ${k}`);
  if (errs.length) return errs;

  if ('quota_pts' in e && e.quota_pts !== null && !(typeof e.quota_pts === 'number' && Number.isFinite(e.quota_pts))) {
    err('quota_pts: number|null required (honest-null when the harness cannot meter — never fabricated)');
  }
  if ('stages' in e) {
    if (!Array.isArray(e.stages) || !e.stages.length) err('stages: non-empty array required when present');
    else for (const [i, s] of e.stages.entries()) errs.push(...validateStageEntry(s, i));
  }
  if ('batch' in e) {
    if (!isMap(e.batch) || !isNonEmptyStr(e.batch.batch_id) || !Number.isInteger(e.batch.size) || e.batch.size < 2 ||
      !isNonEmptyStr(e.batch.anchor) || Object.keys(e.batch).some((k) => !['batch_id', 'size', 'anchor'].includes(k))) {
      err('batch: {batch_id, size>=2, anchor} required when present (batch usage lives ONCE on the anchor member; non-anchor members carry null tokens so ledger sums stay honest)');
    }
  }
  if (errs.length) return errs;

  for (const k of ['job_id', 'created', 'candidate_id', 'workflow']) {
    if (!isNonEmptyStr(e[k])) err(`${k}: non-empty string required`);
  }
  if (!isIso(e.created)) err('created: iso-timestamp required');
  for (const k of ['maker', 'judge']) {
    if (!isMap(e[k]) || Object.keys(e[k]).sort().join(',') !== 'provider,tier' ||
      !isNonEmptyStr(e[k].provider) || !isNonEmptyStr(e[k].tier)) {
      err(`${k}: {provider, tier} (non-empty strings) required`);
    }
  }
  for (const k of ['tokens_in', 'tokens_out']) {
    if (e[k] !== null && !Number.isInteger(e[k])) err(`${k}: int|null required`);
  }
  if (e.cost_usd !== null && !(typeof e.cost_usd === 'number' && Number.isFinite(e.cost_usd))) err('cost_usd: number|null required');
  if (!(typeof e.wall_time_s === 'number' && Number.isFinite(e.wall_time_s) && e.wall_time_s >= 0)) err('wall_time_s: number >= 0 required');
  if (typeof e.landed !== 'boolean') err('landed: bool required');
  if (!Number.isInteger(e.revision_count) || e.revision_count < 0) err('revision_count: int >= 0 required');
  if (e.judge_result !== null && !JUDGE_RESULTS.includes(e.judge_result)) err(`judge_result: ${JUDGE_RESULTS.join('|')}|null required`);
  if (!OUTCOMES.includes(e.outcome)) err(`outcome: must be one of [${OUTCOMES.join(' | ')}], got ${JSON.stringify(e.outcome)}`);
  if (!Array.isArray(e.followup_created) || !e.followup_created.every(isNonEmptyStr)) err('followup_created: [candidate_id] required');
  return errs;
}

function appendValidated(path, obj, errs, what) {
  if (errs.length) {
    throw new Error(`MALFORMED ${what} — refusing to append to ${path}:\n  - ${errs.join('\n  - ')}\n${JSON.stringify(obj)}`);
  }
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, JSON.stringify(obj) + '\n');
}

export function appendClaim(repoRoot, claim) {
  appendValidated(claimsPath(repoRoot), claim, validateClaim(claim), 'CLAIM');
}

export function appendCostEntry(repoRoot, entry) {
  appendValidated(costPath(repoRoot), entry, validateCostEntry(entry), 'COST ENTRY');
}

/** parse a jsonl file; a corrupt line crashes loudly (the ledger is load-bearing). */
export function readJsonl(path) {
  if (!existsSync(path)) return [];
  return readFileSync(path, 'utf8').split('\n').filter((l) => l.trim()).map((l, i) => {
    try { return JSON.parse(l); }
    catch { throw new Error(`corrupt jsonl line ${i + 1} in ${path}: ${l.slice(0, 120)}`); }
  });
}

/** next 1-based sequence number for this candidate's claims (claim_id = clm-<candidate>-<n>). */
export function nextClaimSeq(repoRoot, candidateId) {
  return readJsonl(claimsPath(repoRoot)).filter((c) => c.candidate_id === candidateId).length + 1;
}

/** next 1-based attempt number for this candidate's jobs (job_id = job-<candidate>-<n>). */
export function nextJobAttempt(repoRoot, candidateId) {
  return readJsonl(costPath(repoRoot)).filter((c) => c.candidate_id === candidateId).length + 1;
}
