// report.mjs — `hone report`: compile <repo>/quality/{claims.jsonl,cost.jsonl} + packet
// outcomes into a markdown report.
//
// PURE COMPILATION, no LLM calls, deterministic: identical ledgers compile to identical
// bytes (the output filename embeds an input digest; nothing wall-clock-dependent is
// emitted). Reports are never hand-written prose (SPEC non-negotiable #5).
//
// THE HONESTY GATE (SPEC acceptance test #2): the compiler refuses to emit
// done/complete/clean/first-class/solved wording for a candidate unless the claim
// carrying it is of an evidence-backed type WITH the evidence actually attached
// (verified_fact / behavior_preserved / behavior_changed + non-empty evidence, or
// judged_design_claim + a named judge verdict). Unbacked overclaim statements render
// as `[UNVERIFIED: <statement>]`. Per schemas/claim.yaml: "a claim without evidence
// is a hypothesis, and the compiler treats it as one" — evidence-requiring types that
// arrive without evidence are downgraded to hypothesis (and thus gated + surfaced as
// open questions), not trusted.
//
// FAIL-CLOSED on inputs: unparseable ledger lines / packet YAML are compiled into a
// "Ledger errors" section and warned to stderr — never skipped silently.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { buildContext, loadProfile } from './profile.mjs';
import { parseYaml } from './yaml.mjs';
import { djb2 } from './util.mjs';
import {
  readAgendaArtifacts, packetPriority, doctrineClassOf, normalizeDoctrineClass,
  agendaRankMap, divergenceFlags,
} from './agenda-consume.mjs';

export const CLAIM_TYPES = ['verified_fact', 'judged_design_claim', 'behavior_preserved',
  'behavior_changed', 'hypothesis', 'uncertainty', 'remaining_work'];
const EVIDENCE_TYPES = new Set(['verified_fact', 'behavior_preserved', 'behavior_changed']);
const COST_OUTCOMES = ['landed', 'reverted', 'skipped', 'blocked'];
const JUDGE_RESULTS = ['PASS', 'REVISE', 'REJECT'];
const PACKET_STATUS_ORDER = ['landed', 'reverted', 'skipped', 'blocked', 'in_progress', 'pending'];

// Overclaim wording the compiler refuses to pass through unbacked. Word-boundary +
// common inflections; deliberately a bit over-broad (fail-closed: over-matching only
// forces UNVERIFIED rendering on unbacked claims, never hides backed ones).
const OVERCLAIM_RE = /\b(?:done|complete[ds]?|completely|clean(?:ed|ly|s)?|first[- ]class|solved?|solves|solving)\b/i;

const isStr = (v) => typeof v === 'string';
const nonEmpty = (v) => isStr(v) && v.trim().length > 0;
const isMapObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

// ---------------------------------------------------------------- honesty gate

function hasValidEvidence(c) {
  return Array.isArray(c.evidence) && c.evidence.length > 0 &&
    c.evidence.every((e) => isMapObj(e) && nonEmpty(e.command) && nonEmpty(e.output_digest));
}

function hasValidJudge(c) {
  return isMapObj(c.judge) && nonEmpty(c.judge.provider) && nonEmpty(c.judge.verdict);
}

/** does this claim's type+evidence actually back an assertion? (the only path past the gate) */
export function claimIsBacked(c) {
  if (EVIDENCE_TYPES.has(c.type)) return hasValidEvidence(c);
  if (c.type === 'judged_design_claim') return hasValidJudge(c);
  return false; // hypothesis / uncertainty / remaining_work never back a done-claim
}

/** the type the compiler treats the claim as (schema: claim without evidence = hypothesis). */
export function effectiveClaimType(c) {
  if (EVIDENCE_TYPES.has(c.type) && !hasValidEvidence(c)) return 'hypothesis';
  if (c.type === 'judged_design_claim' && !hasValidJudge(c)) return 'hypothesis';
  return c.type;
}

/** render a claim statement through the honesty gate. */
export function gateStatement(c) {
  if (OVERCLAIM_RE.test(c.statement) && !claimIsBacked(c)) return `[UNVERIFIED: ${c.statement}]`;
  return c.statement;
}

// ---------------------------------------------------------------- ledger readers

function readJsonl(path, rel, validate) {
  const rows = [], errors = [];
  if (!existsSync(path)) return { rows, errors, raw: '' };
  const raw = readFileSync(path, 'utf8');
  raw.split('\n').forEach((line, i) => {
    if (!line.trim()) return;
    let obj;
    try { obj = JSON.parse(line); } catch (e) {
      errors.push(`${rel}:${i + 1} — unparseable JSON (${e.message}) — ${JSON.stringify(line.slice(0, 120))}`);
      return;
    }
    const errs = validate(obj);
    if (errs.length) errors.push(`${rel}:${i + 1} — ${errs.join('; ')} — ${JSON.stringify(line.slice(0, 120))}`);
    else rows.push(obj);
  });
  return { rows, errors, raw };
}

function validateClaim(c) {
  const errs = [];
  if (!isMapObj(c)) return ['claim line is not an object'];
  if (!nonEmpty(c.claim_id)) errs.push('claim_id: non-empty string required');
  if (!nonEmpty(c.candidate_id)) errs.push('candidate_id: non-empty string required');
  if (!CLAIM_TYPES.includes(c.type)) errs.push(`type: must be one of [${CLAIM_TYPES.join('|')}], got ${JSON.stringify(c.type)}`);
  if (!nonEmpty(c.statement)) errs.push('statement: non-empty string required');
  if (!nonEmpty(c.created)) errs.push('created: iso-timestamp required');
  return errs;
}

function validateCostEntry(c) {
  const errs = [];
  if (!isMapObj(c)) return ['cost line is not an object'];
  if (!nonEmpty(c.job_id)) errs.push('job_id: non-empty string required');
  if (!nonEmpty(c.candidate_id)) errs.push('candidate_id: non-empty string required');
  if (!COST_OUTCOMES.includes(c.outcome)) errs.push(`outcome: must be one of [${COST_OUTCOMES.join('|')}], got ${JSON.stringify(c.outcome)}`);
  if (!Number.isFinite(c.wall_time_s)) errs.push('wall_time_s: finite number required');
  if (!Number.isInteger(c.revision_count)) errs.push('revision_count: int required');
  if (c.cost_usd !== null && c.cost_usd !== undefined && !Number.isFinite(c.cost_usd)) errs.push('cost_usd: number|null required');
  for (const k of ['tokens_in', 'tokens_out']) {
    if (c[k] !== null && c[k] !== undefined && !Number.isInteger(c[k])) errs.push(`${k}: int|null required`);
  }
  if (c.judge_result !== null && c.judge_result !== undefined && !JUDGE_RESULTS.includes(c.judge_result)) {
    errs.push(`judge_result: PASS|REVISE|REJECT|null required, got ${JSON.stringify(c.judge_result)}`);
  }
  return errs;
}

/**
 * read the packet pool. Files that parse but carry no candidate_id/status are foreign
 * YAML and ignored (same tolerance as plan.mjs); files that FAIL to parse are errors.
 * Shared with run.mjs (the lane scheduler reads the same pool).
 */
export function readPacketPool(repoRoot) {
  const dir = join(repoRoot, 'quality', 'packets');
  const packets = [], errors = [], fileById = new Map();
  if (!existsSync(dir)) return { packets, errors, fileById };
  for (const f of readdirSync(dir).sort()) {
    if (!f.endsWith('.yaml') && !f.endsWith('.yml')) continue;
    const rel = `quality/packets/${f}`;
    let doc;
    try { doc = parseYaml(readFileSync(join(dir, f), 'utf8')); } catch (e) {
      errors.push(`${rel} — unparseable packet YAML (${e.message})`);
      continue;
    }
    if (!isMapObj(doc) || !nonEmpty(doc.candidate_id) || !nonEmpty(doc.status)) continue; // foreign yaml
    packets.push(doc);
    fileById.set(doc.candidate_id, join(dir, f));
  }
  return { packets, errors, fileById };
}

// ---------------------------------------------------------------- compilation

export function compileReport(repoRoot) {
  const qdir = join(repoRoot, 'quality');
  const claims = readJsonl(join(qdir, 'claims.jsonl'), 'quality/claims.jsonl', validateClaim);
  const cost = readJsonl(join(qdir, 'cost.jsonl'), 'quality/cost.jsonl', validateCostEntry);
  const pool = readPacketPool(repoRoot);
  const agendaArts = readAgendaArtifacts(repoRoot);
  const profileAgenda = loadProfile(repoRoot).profile.agenda ?? {};
  const ledgerErrors = [...claims.errors, ...cost.errors, ...pool.errors, ...agendaArts.errors];

  const packetsCanonical = JSON.stringify(
    [...pool.packets].sort((a, b) => a.candidate_id.localeCompare(b.candidate_id)));
  const digest = djb2([claims.raw, cost.raw, packetsCanonical, agendaArts.raws.agenda,
    agendaArts.raws.notChosen, agendaArts.raws.batches, agendaArts.raws.ledger,
    JSON.stringify(profileAgenda)].join("\\u0000"));

  // ---- group by candidate (union of packet ids, claim ids, cost ids) ----
  const byCand = new Map();
  const cand = (id) => {
    if (!byCand.has(id)) byCand.set(id, { id, packet: null, claims: [], jobs: [] });
    return byCand.get(id);
  };
  for (const p of pool.packets) cand(p.candidate_id).packet = p;
  for (const c of claims.rows) cand(c.candidate_id).claims.push(c);
  for (const j of cost.rows) cand(j.candidate_id).jobs.push(j);
  const candIds = [...byCand.keys()].sort();

  // ---- aggregates ----
  const statusCounts = {};
  for (const p of pool.packets) statusCounts[p.status] = (statusCounts[p.status] || 0) + 1;
  const skipReasons = countBy(pool.packets.filter((p) => p.status === 'skipped'),
    (p) => p.outcome?.skip_reason || '(skip_reason missing — schema violation)');
  const blockedOn = countBy(pool.packets.filter((p) => p.status === 'blocked'),
    (p) => p.outcome?.blocked_on || '(blocked_on missing — schema violation)');

  const typeCounts = {};
  let downgraded = 0;
  for (const c of claims.rows) {
    const eff = effectiveClaimType(c);
    if (eff !== c.type) downgraded++;
    typeCounts[eff] = (typeCounts[eff] || 0) + 1;
  }

  const jobs = cost.rows;
  const withCost = jobs.filter((j) => Number.isFinite(j.cost_usd));
  const totalUsd = withCost.reduce((s, j) => s + j.cost_usd, 0);
  const totalWall = jobs.reduce((s, j) => s + j.wall_time_s, 0);
  const tokensIn = jobs.filter((j) => Number.isInteger(j.tokens_in)).reduce((s, j) => s + j.tokens_in, 0);
  const tokensOut = jobs.filter((j) => Number.isInteger(j.tokens_out)).reduce((s, j) => s + j.tokens_out, 0);
  const totalRevisions = jobs.reduce((s, j) => s + j.revision_count, 0);
  const landedCount = statusCounts.landed || 0;
  const judgeDist = countBy(jobs, (j) => j.judge_result ?? '(none)');

  // ---- render ----
  const L = [];
  const w = (s = '') => L.push(s);
  w(`# hone report — ${basename(repoRoot)}`);
  w();
  w('Compiled from `quality/claims.jsonl` + `quality/cost.jsonl` + `quality/packets/` — never hand-written');
  w('(SPEC non-negotiable #5: agents overclaim; reports compile from the claim ledger).');
  w(`Inputs: ${claims.rows.length} claims · ${jobs.length} cost entries · ${pool.packets.length} packets · input digest \`${digest}\` (identical ledgers compile to identical bytes).`);
  if (ledgerErrors.length) w(`**${ledgerErrors.length} malformed ledger input(s)** — see "Ledger errors" below.`);
  w();

  // fail-loud divergence thresholds (AGENDA-DESIGN.md amendment 3): a FLAG requiring owner
  // acknowledgment, never a halt — computed from batch records vs the doctrine projection.
  const flags = divergenceFlags(agendaArts.batches, profileAgenda);
  if (flags.length) {
    w('## ⚠ DIVERGENCE — OWNER ACK REQUIRED');
    w();
    w('Threshold-crossing divergence, computed from `quality/agendas/batches.jsonl` against the');
    w('doctrine projection in `quality/hone.yaml` (`agenda.named_targets` / `agenda.budget_bands`).');
    w('This is a flag, not a re-weighting opportunity and not a halt:');
    w();
    for (const f of flags) w(`- ⚠ ${f}`);
    w();
  }

  w('## Outcomes');
  w();
  const statusKeys = [...PACKET_STATUS_ORDER.filter((s) => statusCounts[s]),
    ...Object.keys(statusCounts).filter((s) => !PACKET_STATUS_ORDER.includes(s)).sort()];
  if (!statusKeys.length) w('- (no packets on disk)');
  for (const s of statusKeys) w(`- ${s}: ${statusCounts[s]}`);
  w();
  w('Skip reasons (negative results are first-class knowledge):');
  w();
  writeCounts(w, skipReasons);
  if (blockedOn.length) {
    w('Blocked on:');
    w();
    writeCounts(w, blockedOn);
  }

  w('## Cost');
  w();
  if (!jobs.length) { w('- (no cost entries)'); w(); }
  else {
    w(`- total: $${totalUsd.toFixed(2)} across ${jobs.length} job(s) (${withCost.length} with cost data) · ${totalWall.toFixed(1)}s wall`);
    w(`- per landed packet: ${landedCount ? `$${(totalUsd / landedCount).toFixed(2)}` : 'n/a'} (${landedCount} landed)`);
    w(`- tokens: ${tokensIn} in / ${tokensOut} out (providers that reported them)`);
    w(`- revisions: ${totalRevisions} across ${jobs.length} job(s) (${(totalRevisions / jobs.length).toFixed(2)}/job)`);
    w(`- judge results: ${judgeDist.map(([k, n]) => `${k} ${n}`).join(' · ')}`);
    w();
  }

  // agenda & chooser sections — rendered ONLY when agenda artifacts exist (repos without an
  // agenda compile exactly as before, minus digest widening).
  if (agendaArts.agenda || agendaArts.ledger.length || Object.keys(agendaArts.notChosen).length) {
    w('## Agenda & chooser (computed from AGENDA.json + ledgers — never model-asserted)');
    w();
    if (agendaArts.agenda) {
      const a = agendaArts.agenda;
      w(`- incumbent: ${a.agenda_id} (${a.created}) · ${(a.items ?? []).length} item(s) · ${a.verification?.verified ?? 0}/${a.verification?.sensor_citations ?? 0} sensor citation(s) reproduced${a.verification?.failed ? ` · ${a.verification.failed} FAILED (items demoted)` : ''}`);
      w(`- budget composition (computed): ${budgetComposition(a, jobs, pool)}`);
      w(`- ${formulaVsAgenda(a, pool)}`);
    }
    const ncKeys = Object.keys(agendaArts.notChosen).sort();
    if (ncKeys.length) {
      w('- NOT-chosen aging (age counts consecutive agendas not chosen; ≥3 triggers the run floor):');
      for (const k of ncKeys) {
        const e = agendaArts.notChosen[k];
        w(`  - \`${k}\` · age ${e.age_count} · ${e.reason_latest ?? '(no reason recorded)'}`);
      }
    }
    w();
    if (agendaArts.ledger.length) {
      w('### Chooser calibration (selection ledger — predicted vs realized, per class)');
      w();
      for (const line of chooserCalibration(agendaArts.ledger, pool, jobs)) w(line);
      w();
    }
  }

  w('## Claims by type');
  w();
  if (!claims.rows.length) w('- (no claims)');
  for (const t of CLAIM_TYPES) if (typeCounts[t]) w(`- ${t}: ${typeCounts[t]}`);
  if (downgraded) w(`- (${downgraded} claim(s) arrived as evidence-requiring types WITHOUT valid evidence — downgraded to hypothesis per schema)`);
  w();

  w('## Open questions (hypotheses & uncertainties — never buried)');
  w();
  const open = [];
  for (const id of candIds) {
    for (const c of byCand.get(id).claims) {
      const eff = effectiveClaimType(c);
      if (eff === 'hypothesis' || eff === 'uncertainty') {
        const note = eff !== c.type ? ` (was ${c.type} — no evidence)` : '';
        open.push(`- \`${id}\` [${eff}${note}] ${gateStatement(c)}`);
      }
    }
  }
  if (!open.length) w('- (none)'); else for (const l of open) w(l);
  w();

  w('## Remaining work');
  w();
  const remaining = [];
  for (const id of candIds) {
    for (const c of byCand.get(id).claims) {
      if (effectiveClaimType(c) === 'remaining_work') remaining.push(`- \`${id}\` ${gateStatement(c)}`);
    }
  }
  if (!remaining.length) w('- (none)'); else for (const l of remaining) w(l);
  w();

  w('## Candidates');
  w();
  if (!candIds.length) { w('(nothing in the ledgers or packet stream yet)'); w(); }
  for (const id of candIds) renderCandidate(w, byCand.get(id));

  w('## Ledger errors (malformed input — reported, never silently skipped)');
  w();
  if (!ledgerErrors.length) w('- (none)'); else for (const e of ledgerErrors) w(`- ${e}`);
  w();

  return {
    text: L.join('\n'),
    digest,
    ledgerErrors,
    counts: { claims: claims.rows.length, jobs: jobs.length, packets: pool.packets.length, statusCounts },
  };
}

// ---------------------------------------------------------------- agenda & chooser (computed)

/** predicted (AGENDA.json est_cost by doctrine class) vs realized (cost ledger joined via
 * packets) — the budget-composition line is COMPUTED here, never asserted by the model. */
function budgetComposition(agenda, costRows, pool) {
  const byId = new Map(pool.packets.map((p) => [p.candidate_id, p]));
  const predicted = {};
  let predTotal = 0;
  for (const it of agenda.items ?? []) {
    const cls = normalizeDoctrineClass(it.workflow_class);
    const usd = Number.isFinite(it.est_cost?.usd) ? it.est_cost.usd : 0;
    predicted[cls] = (predicted[cls] || 0) + usd;
    predTotal += usd;
  }
  const realized = {};
  let realTotal = 0;
  for (const r of costRows) {
    const p = byId.get(r.candidate_id);
    const cls = p ? doctrineClassOf(p, agenda) : 'other';
    const usd = Number.isFinite(r.cost_usd) ? r.cost_usd : 0;
    realized[cls] = (realized[cls] || 0) + usd;
    realTotal += usd;
  }
  const fmt = (o, total) => Object.entries(o).sort((a, b) => a[0].localeCompare(b[0]))
    .map(([k, v]) => `${k} $${v.toFixed(2)}${total > 0 ? ` (${((v / total) * 100).toFixed(0)}%)` : ''}`).join(' · ') || '(none)';
  return `predicted — ${fmt(predicted, predTotal)} | realized — ${fmt(realized, realTotal)}`;
}

/** the free streetlight-bias sensor (design amendment 7): how far the agenda's judgment
 * diverges from the deterministic churn×complexity formula over agenda-ranked pending packets. */
function formulaVsAgenda(agenda, pool) {
  const pending = pool.packets.filter((p) => p.status === 'pending');
  const rank = agendaRankMap(agenda);
  const ranked = pending.filter((p) => rank.has(p.candidate_id));
  if (!ranked.length) return `formula-rank vs agenda-rank (streetlight-bias sensor): no pending packet is agenda-ranked (${pending.length} pending)`;
  const formulaOrder = [...ranked].sort((a, b) => packetPriority(b) - packetPriority(a) || a.candidate_id.localeCompare(b.candidate_id));
  const agendaOrder = [...ranked].sort((a, b) => rank.get(a.candidate_id) - rank.get(b.candidate_id) || a.candidate_id.localeCompare(b.candidate_id));
  const posF = new Map(formulaOrder.map((p, i) => [p.candidate_id, i]));
  let maxD = 0, maxNote = null;
  agendaOrder.forEach((p, i) => {
    const d = Math.abs(posF.get(p.candidate_id) - i);
    if (d > maxD) { maxD = d; maxNote = `${p.candidate_id}: formula #${posF.get(p.candidate_id) + 1} → agenda #${i + 1}`; }
  });
  const k = Math.min(10, ranked.length);
  const topF = new Set(formulaOrder.slice(0, k).map((p) => p.candidate_id));
  const overlap = agendaOrder.slice(0, k).filter((p) => topF.has(p.candidate_id)).length;
  return `formula-rank vs agenda-rank (streetlight-bias sensor): top-${k} overlap ${overlap}/${k} · max displacement ${maxD}${maxNote ? ` (${maxNote})` : ''} · ${pending.length - ranked.length} pending packet(s) unranked by the agenda`;
}

/** join the selection ledger (predicted gain/cost/class) against realized packet outcomes +
 * cost actuals — scores the CHOOSER, not the code (design amendment 6). */
function chooserCalibration(ledger, pool, costRows) {
  const byId = new Map(pool.packets.map((p) => [p.candidate_id, p]));
  const usdByCand = new Map();
  for (const r of costRows) usdByCand.set(r.candidate_id, (usdByCand.get(r.candidate_id) || 0) + (Number.isFinite(r.cost_usd) ? r.cost_usd : 0));
  const byClass = new Map();
  for (const row of ledger) {
    const cls = row.predicted?.class ?? '(unclassified)';
    if (!byClass.has(cls)) byClass.set(cls, { items: 0, est: 0, estKnown: 0, statuses: {}, usd: 0, linked: 0 });
    const g = byClass.get(cls);
    g.items++;
    if (Number.isFinite(row.predicted?.est_cost_usd)) { g.est += row.predicted.est_cost_usd; g.estKnown++; }
    let linked = false;
    for (const c of new Set([...(Array.isArray(row.packet_ids) ? row.packet_ids : []), row.item_id])) {
      const p = byId.get(c);
      if (!p) continue;
      linked = true;
      g.statuses[p.status] = (g.statuses[p.status] || 0) + 1;
      g.usd += usdByCand.get(c) || 0;
    }
    if (linked) g.linked++;
  }
  return [...byClass.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([cls, g]) => {
    const st = Object.entries(g.statuses).sort((a, b) => a[0].localeCompare(b[0])).map(([k, v]) => `${k} ${v}`).join(', ') || 'no packets yet';
    return `- ${cls}: predicted ${g.items} item(s), est $${g.est.toFixed(2)}${g.estKnown < g.items ? ` (${g.items - g.estKnown} without $)` : ''} → realized: ${st}${g.usd ? ` · spent $${g.usd.toFixed(2)}` : ''}${g.linked < g.items ? ` · ${g.items - g.linked} item(s) not packet-linked` : ''}`;
  });
}

function countBy(arr, keyFn) {
  const m = new Map();
  for (const x of arr) { const k = keyFn(x); m.set(k, (m.get(k) || 0) + 1); }
  return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function writeCounts(w, entries) {
  if (!entries.length) w('- (none)'); else for (const [k, n] of entries) w(`- ${n}× ${k}`);
  w('');
}

function renderCandidate(w, { id, packet, claims, jobs }) {
  w(`### ${id} — ${packet ? packet.status : '(claims/cost reference this candidate; no packet on disk)'}`);
  w();
  if (packet) {
    w(`- ${packet.action} × ${packet.proof_class} · subsystem \`${packet.subsystem}\` · files: ${(packet.files || []).join(', ')}`);
    w(`- gate: ${packet.execution_gate} · touchset: ${(packet.touchset || []).join(', ')}`);
    const o = packet.outcome || {};
    const parts = [];
    if (o.commit) parts.push(`commit \`${o.commit}\``);
    if (o.skip_reason) parts.push(`skip_reason: ${o.skip_reason}`);
    if (o.blocked_on) parts.push(`blocked_on: ${o.blocked_on}`);
    if (Number.isInteger(o.tokens_actual)) parts.push(`tokens_actual: ${o.tokens_actual}`);
    if (parts.length) w(`- outcome: ${parts.join(' · ')}`);
    if (o.judge_verdict) w(`- judge verdict (verbatim gist): "${o.judge_verdict}"`);
    if (Array.isArray(o.evidence_receipts) && o.evidence_receipts.length) {
      w('- evidence receipts:');
      for (const r of o.evidence_receipts) w(`  - ${r}`);
    }
    if (o.lesson) w(`- lesson: ${o.lesson}`);
  }
  if (claims.length) {
    w(`- claims (${claims.length}):`);
    for (const c of claims) {
      const eff = effectiveClaimType(c);
      const label = eff === c.type ? eff : `${eff} (was ${c.type} — no evidence)`;
      w(`  - [${label}] ${gateStatement(c)}`);
      if (EVIDENCE_TYPES.has(c.type) && hasValidEvidence(c)) {
        for (const e of c.evidence) w(`    - evidence: \`${e.command}\` → digest \`${e.output_digest}\``);
      }
      if (c.type === 'judged_design_claim' && hasValidJudge(c)) {
        w(`    - judge ${c.judge.provider}: "${c.judge.verdict}"`);
      }
    }
  } else w('- claims: (none — nothing asserted for this candidate)');
  if (jobs.length) {
    const usd = jobs.filter((j) => Number.isFinite(j.cost_usd)).reduce((s, j) => s + j.cost_usd, 0);
    const wall = jobs.reduce((s, j) => s + j.wall_time_s, 0);
    const rev = jobs.reduce((s, j) => s + j.revision_count, 0);
    const jr = jobs.map((j) => j.judge_result ?? 'none').join(',');
    w(`- cost: ${jobs.length} job(s) · $${usd.toFixed(2)} · ${wall.toFixed(1)}s wall · ${rev} revision(s) · judge: ${jr}`);
  }
  w();
}

// ---------------------------------------------------------------- CLI entry

export async function runReport(flags) {
  const ctx = buildContext(flags.repo);
  const { text, digest, ledgerErrors, counts } = compileReport(ctx.repoRoot);
  const outDir = resolve(ctx.repoRoot, flags.out ? String(flags.out) : join('quality', 'reports'));
  mkdirSync(outDir, { recursive: true });
  const outPath = join(outDir, `report-${digest}.md`);
  writeFileSync(outPath, text);
  for (const e of ledgerErrors) process.stderr.write(`WARN malformed ledger input: ${e}\n`);
  process.stdout.write(`hone report — compiled ${counts.claims} claims · ${counts.jobs} cost entries · ${counts.packets} packets → ${outPath}\n`);
  process.stdout.write(`outcomes: ${JSON.stringify(counts.statusCounts)}${ledgerErrors.length ? ` · MALFORMED INPUTS: ${ledgerErrors.length}` : ''}\n`);
  return outPath;
}
