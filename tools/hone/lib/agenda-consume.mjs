// agenda-consume.mjs — the CONSUMPTION side of the agenda (AGENDA-DESIGN.md v2): everything
// `run` and `report` need to read agenda artifacts and apply the deterministic machinery that
// sits OUTSIDE the chooser's authority. No LLM calls live here, ever.
//
// Artifacts read (all under <repo>/quality/):
//   AGENDA.json                    the incumbent agenda (machine form; verified-first ranking)
//   agendas/not-chosen.json        NOT-chosen persistence + aging counters
//   agendas/batches.jsonl          one line per completed `hone run` batch (spend by class/target)
//   selection-ledger.jsonl         one line per ranked agenda item (predicted gain/cost/class)
//
// The deterministic floor (design amendment 5 — the agenda CANNOT displace it):
//   (a) negative-control / seeded-trap packets are always eligible and scheduled ≥1×/batch
//   (b) packets of an IN-FLIGHT campaign (any sibling landed/in_progress via the depends_on
//       graph) take precedence over everything else — campaigns finish before agenda thrash
//       can abandon them
//   (c) aged NOT-chosen items (age_count ≥ AGED_OMISSION_THRESHOLD) with a pending packet get
//       ≥1 packet/batch
//
// Amendment 7: the churn×complexity formula survives as the default order within a class and
// as the challenger baseline — packetPriority lives HERE (run re-exports it) so report can
// render the formula-rank vs agenda-rank diff without a run↔report import cycle.
import { existsSync, readFileSync, appendFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { djb2 } from './util.mjs';

export const AGED_OMISSION_THRESHOLD = 3; // agendas an item must sit NOT-chosen before floor (c)

const LMH = { low: 1, medium: 2, high: 3 };
const PROD = { none: 1, low: 2, medium: 3, high: 4 };
const MAKER_COST = { cheap: 1, standard: 2, strong: 3 };

/**
 * rank for ordering: prefer the persisted plan-time prior (packet.priority.score — schema
 * v1.1 optional block; recalibrated by cost actuals, never a quality claim). Packets without
 * it (hand-authored) fall back to the coarse enum-derived rank below. The two scales differ
 * (the persisted log2-based prior usually exceeds the enum ratio), which is accepted: the
 * richer prior deliberately wins, and a hand-authored packet can carry its own priority block.
 */
export function packetPriority(p) {
  const persisted = p.priority?.score;
  if (typeof persisted === 'number' && Number.isFinite(persisted)) return persisted;
  const num = (LMH[p.expected_quality_gain] || 1) * (LMH[p.owner_attention_reduction] || 1) * (PROD[p.product_impact] || 1);
  const den = (LMH[p.risk?.silent_wrongness_cost] || 3) * (LMH[p.estimates?.evidence_cost] || 3) * (MAKER_COST[p.maker_tier] || 3);
  return num / den; // unknown enums price as worst-case (fail-closed ranking)
}

// ---------------------------------------------------------------- artifact readers
// All fail-SOFT: a missing artifact is null/empty (agenda machinery is optional); a MALFORMED
// artifact is surfaced in .errors so report can render it — never silently treated as absent.

export const agendaJsonPath = (repoRoot) => join(repoRoot, 'quality', 'AGENDA.json');
export const notChosenPath = (repoRoot) => join(repoRoot, 'quality', 'agendas', 'not-chosen.json');
export const batchesPath = (repoRoot) => join(repoRoot, 'quality', 'agendas', 'batches.jsonl');
export const selectionLedgerPath = (repoRoot) => join(repoRoot, 'quality', 'selection-ledger.jsonl');

function readJsonSoft(path, errors, what) {
  if (!existsSync(path)) return { doc: null, raw: '' };
  const raw = readFileSync(path, 'utf8');
  try { return { doc: JSON.parse(raw), raw }; }
  catch (e) { errors.push(`${what} — unparseable JSON (${e.message})`); return { doc: null, raw }; }
}

function readJsonlSoft(path, errors, what) {
  if (!existsSync(path)) return { rows: [], raw: '' };
  const raw = readFileSync(path, 'utf8');
  const rows = [];
  raw.split('\n').forEach((line, i) => {
    if (!line.trim()) return;
    try { rows.push(JSON.parse(line)); }
    catch (e) { errors.push(`${what}:${i + 1} — unparseable JSON (${e.message})`); }
  });
  return { rows, raw };
}

/** every agenda artifact in one read: { agenda, notChosen, batches, ledger, raws, errors }. */
export function readAgendaArtifacts(repoRoot) {
  const errors = [];
  const a = readJsonSoft(agendaJsonPath(repoRoot), errors, 'quality/AGENDA.json');
  const nc = readJsonSoft(notChosenPath(repoRoot), errors, 'quality/agendas/not-chosen.json');
  const b = readJsonlSoft(batchesPath(repoRoot), errors, 'quality/agendas/batches.jsonl');
  const l = readJsonlSoft(selectionLedgerPath(repoRoot), errors, 'quality/selection-ledger.jsonl');
  const agenda = a.doc && Array.isArray(a.doc.items) ? a.doc : null;
  if (a.doc && !agenda) errors.push('quality/AGENDA.json — parses but has no items[] array; ignored (fallback ordering)');
  return {
    agenda,
    notChosen: nc.doc?.entries && typeof nc.doc.entries === 'object' ? nc.doc.entries : {},
    batches: b.rows,
    ledger: l.rows,
    raws: { agenda: a.raw, notChosen: nc.raw, batches: b.raw, ledger: l.raw },
    errors,
  };
}

/** NOT-chosen entry keys aged to the floor threshold (design floor (c)). */
export function agedNotChosenIds(notChosenEntries, threshold = AGED_OMISSION_THRESHOLD) {
  const out = new Set();
  for (const [key, e] of Object.entries(notChosenEntries || {})) {
    if (Number.isInteger(e?.age_count) && e.age_count >= threshold) {
      out.add(key);
      for (const pid of Array.isArray(e.packet_ids) ? e.packet_ids : []) out.add(pid);
    }
  }
  return out;
}

// ---------------------------------------------------------------- agenda ranking

/**
 * candidate_id → consumable agenda rank. AGENDA.json items are already verified-first
 * (the citation verifier demoted UNVERIFIED items at emit time); `rank` is that consumable
 * order. An item reaches packets through packet_ids or through an item id that IS a
 * candidate_id.
 */
export function agendaRankMap(agenda) {
  const map = new Map();
  const put = (k, r) => { if (k && !map.has(k)) map.set(k, r); };
  for (const item of agenda?.items ?? []) {
    const r = Number.isInteger(item.rank) ? item.rank : Infinity;
    put(item.id, r);
    for (const pid of Array.isArray(item.packet_ids) ? item.packet_ids : []) put(pid, r);
  }
  return map;
}

/** floor (a): negative-control / seeded-trap calibration packets — outside chooser authority. */
export function isCalibrationPacket(p) {
  const hay = `${p.candidate_id ?? ''} ${p.batch_key ?? ''}`;
  return /negctl|seeded-trap/i.test(hay);
}

/**
 * floor (b) input: candidate_id → 'inflight' | 'new' | 'none' over the depends_on graph
 * (undirected components across the WHOLE pool). A component is in-flight when any member is
 * landed/in_progress; a multi-packet component with no such member is a new campaign; a
 * singleton is not a campaign at all.
 */
export function campaignStates(packets) {
  const byId = new Map(packets.map((p) => [p.candidate_id, p]));
  const adj = new Map(packets.map((p) => [p.candidate_id, new Set()]));
  for (const p of packets) {
    for (const d of Array.isArray(p.depends_on) ? p.depends_on : []) {
      if (!byId.has(d)) continue;
      adj.get(p.candidate_id).add(d);
      adj.get(d).add(p.candidate_id);
    }
  }
  const state = new Map();
  const seen = new Set();
  for (const p of packets) {
    if (seen.has(p.candidate_id)) continue;
    const comp = [];
    const stack = [p.candidate_id];
    while (stack.length) {
      const id = stack.pop();
      if (seen.has(id)) continue;
      seen.add(id);
      comp.push(id);
      for (const nb of adj.get(id) ?? []) stack.push(nb);
    }
    const inflight = comp.some((id) => ['landed', 'in_progress'].includes(byId.get(id)?.status));
    const s = inflight ? 'inflight' : comp.length > 1 ? 'new' : 'none';
    for (const id of comp) state.set(id, s);
  }
  return state;
}

/**
 * selection order when an agenda governs: in-flight campaign packets first (floor (b) —
 * finish what is started), then agenda rank (verified-first by construction), then the
 * deterministic formula (amendment 7: default order within a class / for agenda-unranked
 * packets), then id for stability.
 */
export function orderExecutableByAgenda(executable, poolPackets, agenda) {
  const rank = agendaRankMap(agenda);
  const camp = campaignStates(poolPackets);
  return [...executable].sort((a, b) => {
    const ia = camp.get(a.candidate_id) === 'inflight' ? 0 : 1;
    const ib = camp.get(b.candidate_id) === 'inflight' ? 0 : 1;
    if (ia !== ib) return ia - ib;
    const ra = rank.get(a.candidate_id) ?? Infinity;
    const rb = rank.get(b.candidate_id) ?? Infinity;
    if (ra !== rb) return ra - rb;
    return packetPriority(b) - packetPriority(a) || a.candidate_id.localeCompare(b.candidate_id);
  });
}

/**
 * floors (a) + (c) applied to the SELECTED queue (size ≤ n): guarantee ≥1 pending calibration
 * packet and ≥1 aged-omission packet per batch when such packets are executable. Guarantees
 * insert by REPLACING the lowest-ranked non-guaranteed slot when the queue is full (the agenda
 * cannot veto the floor; the floor evicts the agenda's tail, never its head).
 */
export function applyAgendaFloor(queue, executable, { n, agedIds = new Set() } = {}) {
  const notes = [];
  const out = [...queue];
  const protectedIds = new Set();
  const guarantee = (label, predicate) => {
    if (out.some(predicate)) { protectedIds.add(out.find(predicate).candidate_id); return; }
    const candidate = executable.find((p) => predicate(p) && !out.includes(p));
    if (!candidate) return;
    if (out.length < (n ?? out.length + 1)) {
      out.push(candidate);
      notes.push(`${label}: scheduled ${candidate.candidate_id} (appended — floor guarantee)`);
    } else {
      // evict the last slot that is not itself floor-protected
      for (let i = out.length - 1; i >= 0; i--) {
        if (protectedIds.has(out[i].candidate_id)) continue;
        notes.push(`${label}: scheduled ${candidate.candidate_id} (evicted ${out[i].candidate_id} — the agenda cannot displace the floor)`);
        out[i] = candidate;
        break;
      }
    }
    protectedIds.add(candidate.candidate_id);
  };
  guarantee('negative-control floor', (p) => isCalibrationPacket(p));
  guarantee('aged-omission floor', (p) => agedIds.has(p.candidate_id));
  return { queue: out, notes };
}

// ---------------------------------------------------------------- doctrine classes + batch records

/** normalize a workflow class to the doctrine budget classes (B · A2 · T1 · T0). */
export function normalizeDoctrineClass(wc) {
  const s = String(wc ?? '').toUpperCase();
  if (s === 'B') return 'B';
  if (s === 'A2' || s.startsWith('T2')) return 'A2';
  if (s.startsWith('T1')) return 'T1';
  if (s === 'T0' || s === 'PREVENTION') return 'T0';
  return 'other'; // evidence-generation etc. — real classes, just not doctrine-banded
}

/** doctrine class of a packet: agenda linkage wins; otherwise deterministic proof_class map. */
export function doctrineClassOf(packet, agenda = null) {
  if (agenda) {
    for (const item of agenda.items ?? []) {
      const ids = [item.id, ...(Array.isArray(item.packet_ids) ? item.packet_ids : [])];
      if (ids.includes(packet.candidate_id)) return normalizeDoctrineClass(item.workflow_class);
    }
  }
  if (packet.action === 'propose_contract_change') return 'B';
  switch (packet.proof_class) {
    case 'certified_transform': return 'T0';
    case 'exact_move':
    case 'pure_logic': return 'T1';
    case 'effectful':
    case 'property_at_risk': return 'A2';
    case 'liveness_roots': return 'DELETE';
    default: return 'other';
  }
}

/** named-target attribution: profile agenda.named_targets [{id, keywords: [...]}] matched
 * case-insensitively against the packet's identifying text + any linked campaign target. */
export function targetHits(packet, namedTargets, agenda = null) {
  let hay = `${packet.candidate_id} ${(packet.files || []).join(' ')} ${packet.subsystem ?? ''} ${packet.batch_key ?? ''} ${packet.why_this_matters ?? ''}`;
  if (agenda) {
    for (const item of agenda.items ?? []) {
      const ids = [item.id, ...(Array.isArray(item.packet_ids) ? item.packet_ids : [])];
      if (!ids.includes(packet.candidate_id)) continue;
      hay += ` ${item.what ?? ''} ${item.campaign_id ?? ''}`;
      const camp = (agenda.campaigns ?? []).find((c) => c.id === item.campaign_id);
      if (camp) hay += ` ${camp.named_target ?? ''}`;
    }
  }
  const low = hay.toLowerCase();
  return (namedTargets || [])
    .filter((t) => (t.keywords || []).some((k) => low.includes(String(k).toLowerCase())))
    .map((t) => t.id);
}

/**
 * one batch record per completed `hone run` under an agenda: the input the report's
 * fail-loud thresholds consume (target starved N batches / class allocation outside band).
 * `costRows` are ONLY the cost.jsonl rows this batch appended.
 */
export function appendBatchRecord(repoRoot, { agenda, profileAgenda, poolPackets, executed, costRows }) {
  const byId = new Map(poolPackets.map((p) => [p.candidate_id, p]));
  const spendByClass = {};
  const spendByTarget = {};
  const jobsByTarget = {};
  for (const t of profileAgenda?.named_targets || []) { spendByTarget[t.id] = 0; jobsByTarget[t.id] = 0; }
  let totalUsd = 0;
  for (const row of costRows) {
    const usd = Number.isFinite(row.cost_usd) ? row.cost_usd : 0;
    totalUsd += usd;
    const packet = byId.get(row.candidate_id);
    const cls = packet ? doctrineClassOf(packet, agenda) : 'other';
    spendByClass[cls] = Math.round(((spendByClass[cls] || 0) + usd) * 10000) / 10000;
    if (packet) {
      for (const tid of targetHits(packet, profileAgenda?.named_targets, agenda)) {
        spendByTarget[tid] = Math.round((spendByTarget[tid] + usd) * 10000) / 10000;
        jobsByTarget[tid] += 1;
      }
    }
  }
  const record = {
    batch_id: `batch-${djb2(JSON.stringify(executed) + Date.now())}`,
    created: new Date().toISOString(),
    agenda_id: agenda?.agenda_id ?? null,
    executed: executed.map((e) => ({ id: e.id, status: e.status ?? null, kind: e.kind })),
    jobs: costRows.map((r) => r.job_id),
    spend_usd: Math.round(totalUsd * 10000) / 10000,
    spend_by_class: spendByClass,
    spend_by_target: spendByTarget,
    jobs_by_target: jobsByTarget,
  };
  const path = batchesPath(repoRoot);
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, JSON.stringify(record) + '\n');
  return record;
}

/**
 * fail-loud divergence flags (design amendment 3): computed from the last `window` batch
 * records against the profile's doctrine projection (quality/hone.yaml `agenda:` section).
 * A flag is a FLAG requiring owner acknowledgment, never a halt. Fail-open when there are
 * fewer than `window` batches or no cost data — divergence cannot be asserted without data.
 */
export function divergenceFlags(batches, profileAgenda, { window = 3 } = {}) {
  const flags = [];
  if (!Array.isArray(batches) || batches.length < window) return flags;
  const recent = batches.slice(-window);
  for (const t of profileAgenda?.named_targets || []) {
    const starved = recent.every((b) => !(b.jobs_by_target?.[t.id] > 0) && !(b.spend_by_target?.[t.id] > 0));
    if (starved) {
      flags.push(`doctrine-named target '${t.id}' has ZERO realized spend for ${window} consecutive batches (${recent.map((b) => b.batch_id).join(', ')})`);
    }
  }
  const bands = profileAgenda?.budget_bands || {};
  for (const [cls, band] of Object.entries(bands)) {
    if (!Array.isArray(band) || band.length !== 2) continue;
    const outOfBand = recent.every((b) => {
      const total = Number.isFinite(b.spend_usd) ? b.spend_usd : 0;
      if (total <= 0) return false; // no cost data → cannot assert divergence (fail-open)
      const share = ((b.spend_by_class?.[cls] || 0) / total) * 100;
      return share < band[0] || share > band[1];
    });
    if (outOfBand) {
      const shares = recent.map((b) => {
        const total = Number.isFinite(b.spend_usd) && b.spend_usd > 0 ? b.spend_usd : null;
        return total === null ? 'n/a' : `${(((b.spend_by_class?.[cls] || 0) / total) * 100).toFixed(0)}%`;
      });
      flags.push(`class '${cls}' allocation outside the doctrine band ${band[0]}-${band[1]}% for ${window} consecutive batches (realized: ${shares.join(', ')})`);
    }
  }
  return flags;
}
