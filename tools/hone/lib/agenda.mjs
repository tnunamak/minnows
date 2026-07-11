// agenda.mjs — `hone agenda`: the pressure-tested selection layer (AGENDA-DESIGN.md v2,
// commit 0524186). Judgment chooses, machinery proves: ONE strong-model call over compact
// sensor/ledger/doctrine digests produces the ranked work agenda; a deterministic citation
// verifier then re-derives every `sensor:` citation against quality/inventory/*.json and
// DEMOTES items whose evidence does not reproduce below every verified item. The agenda
// selects and sequences; it does NOT author packets and cannot choose proof tiers (the risk
// router runs on final packets as always).
//
//   hone agenda --repo PATH [--dry-run] [--challenge] [--doctrine PATH]
//
// Artifacts (all the agenda is allowed to write in the target repo):
//   quality/AGENDA.md + AGENDA.json            incumbent agenda (human + machine forms)
//   quality/agendas/agenda-<ts>.{md,json}      versioned history
//   quality/agendas/not-chosen.json            NOT-chosen persistence + aging counters
//   quality/selection-ledger.jsonl             predicted gain/cost/class per ranked item
//   quality/agendas/challenge-<ts>.{md,json}   blind challenger output (--challenge)
//   quality/agendas/challenge-<ts>-diff.md     rank-divergence summary vs the incumbent
//
// --challenge (design amendment 4): same flow, BLIND — the prior agenda, selection ledger,
// and not-chosen file are excluded from context; the OTHER provider family (codex) answers;
// output goes to quality/agendas/ only (a challenger challenges, it never certifies and never
// becomes the incumbent). v1 has no reconciliation logic — the diff artifact IS the deliverable.
//
// RE-RUN TRIGGERS (design §1 — fixed, versioned, mechanical; v1 invokes manually, the trigger
// list is documented here for the later wiring): batch completion · inventory-delta threshold ·
// any doctrine commit · campaign completion/stall · cost-overrun beyond band · aged NOT-chosen
// threshold (AGED_OMISSION_THRESHOLD). Re-rolling an agenda until an agent likes it is
// agenda-shopping and stays forbidden.
//
// The doctrine (budget direction + named targets) is a HUMAN-FIXED, read-only input: the
// profile carries `agenda.doctrine_path` (or pass --doctrine); the model receives its text
// verbatim and the report's threshold flags consume the profile's machine-readable projection
// (`agenda.named_targets`, `agenda.budget_bands`). The agenda call itself never edits it.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, isAbsolute, join, resolve } from 'node:path';
import { buildContext } from './profile.mjs';
import { CLAUDE_NO_MCP_ARGS, codexNoMcpArgs, extractFencedJson, noMcpEnv, requireGpt56, runCli } from '../providers/provider.mjs';
import { slug } from './util.mjs';
import { readPacketPool } from './report.mjs';
import { loadVerifiedInventorySnapshot } from './inventory-snapshot.mjs';
import { notChosenPath, selectionLedgerPath, readAgendaArtifacts } from './agenda-consume.mjs';

const AGENDA_TIMEOUT_MS = Number(process.env.HONE_AGENDA_TIMEOUT_MS ?? 20 * 60 * 1000);
const EVIDENCE_TYPES = ['sensor', 'corpus', 'b-inventory', 'test-gap', 'incident'];

// ---------------------------------------------------------------- context assembly
// Compact DIGESTS, never dumps: every section is byte-capped (head kept, tail clipped with a
// marker) so the whole context lands ~30-40KB. The model must cite only numbers it can see.

const CAPS = {
  'sensor:meta': 900,
  'sensor:tier-mass': 7000,
  'sensor:hotspots': 2600,
  'sensor:callback-smells': 3200,
  'sensor:test-signals': 2800,
  'packet-pool': 6000,
  'cost-actuals': 2600,
  'b-inventory': 5200,
  'ratification-queue': 3000,
  doctrine: 12800,
  'doctrine:named-targets': 2400,
  'prior-agenda': 3600,
  'selection-ledger': 1600,
  'not-chosen-aging': 1400,
};

const cap = (text, n) => {
  const t = String(text);
  return t.length <= n ? t : t.slice(0, n) + `\n…[${t.length - n} bytes clipped — digest cap]`;
};

const readJson = (p) => (existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : null);

function digestTierMass(inv) {
  const tm = readJson(join(inv, 'tier-mass.json'));
  if (!tm) return null;
  const L = [];
  L.push(`tier counts (fns): ${JSON.stringify(tm.universe_tier_count)}`);
  L.push(`tier mass (Σ excess-cc): ${JSON.stringify(tm.universe_tier_mass)}`);
  L.push('by subsystem (files · fns · mass):');
  for (const s of tm.by_subsystem ?? []) L.push(`  ${s.subsystem}: ${s.files} · ${s.fns} · ${s.mass}`);
  L.push('top files by attention (churn × mass) — file · mass · churn · fns · attention · tier-mass:');
  const files = [...(tm.by_file ?? [])].sort((a, b) => b.attention - a.attention).slice(0, 24);
  for (const f of files) {
    const tiers = Object.entries(f.tiers ?? {}).map(([t, m]) => `${t}:${m}`).join(',');
    L.push(`  ${f.file} · ${f.mass} · ${f.churn} · ${f.fns} · ${f.attention} · ${tiers}`);
  }
  L.push('top flagged functions — file::fn · line · cc · excess · churn · tier · attention:');
  for (const c of tm.top_candidates ?? []) {
    L.push(`  ${c.file}::${c.fn} · L${c.line} · cc=${c.cc} · excess=${c.excess} · churn=${c.churn} · ${c.tier} · ${c.attention}`);
  }
  return L.join('\n');
}

function digestHotspots(inv) {
  const h = readJson(join(inv, 'hotspots.json'));
  if (!h) return null;
  const L = ['top files by hotspot score — file · loc · churn · cog · coupling · score · nogo:'];
  for (const f of (h.files ?? []).slice(0, 25)) {
    L.push(`  ${f.file} · ${f.loc} · ${f.churn} · ${f.cog} · ${f.coupling} · ${f.score}${f.nogo ? ' · NOGO' : ''}`);
  }
  return L.join('\n');
}

function digestCallbacks(inv) {
  const cb = readJson(join(inv, 'callback-smells.json'));
  if (!cb) return null;
  const L = [];
  L.push(`callbacks by class: ${JSON.stringify(cb.by_class)} · mass by class: ${JSON.stringify(cb.mass_by_class)} · by kind: ${JSON.stringify(cb.by_kind)} · flagged for B: ${cb.b_flagged}`);
  L.push('top callback smells — file · parent_fn · kind · cc · excess · captured(mutable) · class · why:');
  const rows = [...(cb.callbacks ?? [])].sort((a, b) => b.excess - a.excess).slice(0, 15);
  for (const r of rows) {
    L.push(`  ${r.file} · ${r.parent_fn} · ${r.callback_kind} · cc=${r.cc} · excess=${r.excess} · ${(r.captured_vars || []).length}(${(r.captured_mutable_vars || []).length}) · ${r.recommended_class} · ${String(r.why ?? '').slice(0, 90)}`);
  }
  return L.join('\n');
}

function digestTestSignals(inv) {
  const ts = readJson(join(inv, 'test-signals.json'));
  if (!ts) return null;
  const L = [];
  L.push(`static skip markers: ${ts.skips?.total ?? 0} across ${(ts.skips?.files ?? []).length} test files (${ts.skips?.pattern ?? 'static count'})`);
  if ((ts.skips?.files ?? []).length) {
    L.push('top skip files — file · skips:');
    for (const f of ts.skips.files.slice(0, 10)) L.push(`  ${f.file} · ${f.skips}`);
  }
  const zb = ts.zero_by_name ?? {};
  L.push(`owned files whose exports have ZERO by-name test references: ${(zb.files ?? []).length}`);
  L.push('KNOWN-WEAK signal (by_name_only: true): dynamic call patterns make 0-by-name ≠ untested —');
  L.push('treat as a lead for evidence-generation, never as a coverage verdict.');
  if ((zb.files ?? []).length) {
    L.push('top zero-by-name files — file · exports (sample names):');
    for (const f of zb.files.slice(0, 12)) {
      L.push(`  ${f.file} · ${f.exports} (${(f.unreferenced ?? []).slice(0, 6).join(', ')})`);
    }
  }
  return L.join('\n');
}

/** the machine-readable doctrine projection (profile agenda.named_targets) — HUMAN-FIXED anchors. */
function digestNamedTargets(profileAgenda) {
  const targets = Array.isArray(profileAgenda?.named_targets) ? profileAgenda.named_targets : [];
  if (!targets.length) return null;
  const L = [
    'HUMAN-FIXED doctrine anchors (machine-readable projection from quality/hone.yaml — the agenda',
    'call never edits these). Every named target below MUST be either ranked as an item/campaign or',
    'listed in not_chosen with the reason; demoting or declining one is an ESCALATION → it must also',
    'appear in human_decisions_needed.',
  ];
  for (const t of targets) {
    L.push(`- ${t.id}${t.description ? `: ${t.description}` : ''}`);
    if (t.evidence_hint) L.push(`    evidence hint: ${t.evidence_hint}`);
  }
  return L.join('\n');
}

function digestPackets(repoRoot) {
  const pool = readPacketPool(repoRoot);
  if (!pool.packets.length) return 'packet pool: (empty)';
  const counts = {};
  for (const p of pool.packets) counts[p.status] = (counts[p.status] || 0) + 1;
  const L = [`packet pool: ${pool.packets.length} packets · ${JSON.stringify(counts)}`,
    'candidate_id · status · action×proof_class · gate · files · note:'];
  const order = { in_progress: 0, pending: 1, landed: 2, reverted: 3, skipped: 4, blocked: 5 };
  const packets = [...pool.packets].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || a.candidate_id.localeCompare(b.candidate_id));
  for (const p of packets) {
    const note = p.outcome?.skip_reason || p.outcome?.blocked_on || (p.outcome?.judge_verdict ? `judge: ${p.outcome.judge_verdict}` : '');
    L.push(`  ${p.candidate_id} · ${p.status} · ${p.action}×${p.proof_class} · ${p.execution_gate} · ${(p.files || []).join(',')}${note ? ` · ${String(note).slice(0, 90)}` : ''}`);
  }
  return L.join('\n');
}

function digestCost(repoRoot) {
  const path = join(repoRoot, 'quality', 'cost.jsonl');
  if (!existsSync(path)) return 'cost ledger: (empty)';
  const rows = readFileSync(path, 'utf8').split('\n').filter((l) => l.trim()).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  if (!rows.length) return 'cost ledger: (empty)';
  const usd = rows.filter((r) => Number.isFinite(r.cost_usd)).reduce((s, r) => s + r.cost_usd, 0);
  const landed = rows.filter((r) => r.outcome === 'landed').length;
  const L = [`cost actuals: ${rows.length} jobs · $${usd.toFixed(2)} total · ${landed} landed (${landed ? `$${(usd / landed).toFixed(2)}/landed` : 'n/a'})`,
    'job · outcome · $ · wall_s · revisions · judge:'];
  for (const r of rows.slice(-25)) {
    L.push(`  ${r.candidate_id} · ${r.outcome} · ${Number.isFinite(r.cost_usd) ? `$${r.cost_usd.toFixed(2)}` : 'n/a'} · ${r.wall_time_s} · ${r.revision_count} · ${r.judge_result ?? '-'}`);
  }
  return L.join('\n');
}

function findBInventory(repoRoot, gitRoot) {
  for (const dir of [join(repoRoot, 'docs', 'research'), join(gitRoot, 'docs', 'research')]) {
    if (!existsSync(dir)) continue;
    const f = readdirSync(dir).filter((x) => /^b-contract-inventory.*\.md$/.test(x)).sort().pop();
    if (f) return join(dir, f);
  }
  return null;
}

function digestBInventory(repoRoot, gitRoot) {
  const path = findBInventory(repoRoot, gitRoot);
  if (!path) return null;
  const text = readFileSync(path, 'utf8');
  const lines = text.split('\n');
  const L = [`source: ${basename(path)}`];
  // headers + the findings section in full; everything else is reachable by reference
  L.push(...lines.filter((l) => /^#{1,3} /.test(l)).map((l) => `  ${l}`));
  const findIdx = lines.findIndex((l) => /^## .*(surprises|findings)/i.test(l));
  if (findIdx !== -1) {
    let end = lines.length;
    for (let i = findIdx + 1; i < lines.length; i++) if (/^## /.test(lines[i])) { end = i; break; }
    L.push('--- highest-value B findings (verbatim section) ---');
    L.push(lines.slice(findIdx, end).join('\n'));
  }
  return L.join('\n');
}

function digestQueue(repoRoot, gitRoot) {
  for (const dir of [join(repoRoot, 'docs', 'research'), join(gitRoot, 'docs', 'research')]) {
    const p = join(dir, 'b-contract-ratification-queue.md');
    if (existsSync(p)) return readFileSync(p, 'utf8');
  }
  return null;
}

function digestPriorAgenda(repoRoot) {
  const { agenda } = readAgendaArtifacts(repoRoot);
  if (!agenda) return null;
  const L = [`prior agenda ${agenda.agenda_id} (${agenda.created}) — rank · id · class · verification · what:`];
  for (const it of agenda.items ?? []) {
    L.push(`  ${it.rank}. ${it.id} · ${it.workflow_class} · ${it.verification} · ${String(it.what ?? '').slice(0, 120)}`);
  }
  if (agenda.campaigns?.length) L.push(`campaigns: ${agenda.campaigns.map((c) => `${c.id}(${c.named_target})`).join(' · ')}`);
  for (const nc of agenda.not_chosen ?? []) L.push(`  NOT-chosen: ${nc.id} — ${String(nc.reason ?? '').slice(0, 110)}`);
  return L.join('\n');
}

function digestSelectionLedger(repoRoot) {
  const path = selectionLedgerPath(repoRoot);
  if (!existsSync(path)) return null;
  const rows = readFileSync(path, 'utf8').split('\n').filter((l) => l.trim()).slice(-30);
  return ['selection ledger (predicted, most recent last):',
    ...rows.map((l) => { try { const r = JSON.parse(l); return `  ${r.agenda_id} · #${r.rank} ${r.item_id} · ${r.predicted?.class} · est $${r.predicted?.est_cost_usd ?? '?'}`; } catch { return null; } }).filter(Boolean),
  ].join('\n');
}

function digestNotChosen(repoRoot) {
  const entries = readAgendaArtifacts(repoRoot).notChosen;
  const keys = Object.keys(entries);
  if (!keys.length) return null;
  return ['NOT-chosen aging (age_count = consecutive agendas not chosen; ≥3 triggers the run floor):',
    ...keys.map((k) => `  ${k} · age ${entries[k].age_count} · ${String(entries[k].reason_latest ?? '').slice(0, 110)}`),
  ].join('\n');
}

/** assemble every digest section; blind (challenge) excludes the incumbent's artifacts.
 * profileAgenda (quality/hone.yaml `agenda:`) is doctrine — human-fixed, so BLIND KEEPS IT. */
export function assembleAgendaContext({ repoRoot, gitRoot, doctrinePath, profileAgenda = null, blind = false }) {
  const inv = join(repoRoot, 'quality', 'inventory');
  const meta = readJson(join(inv, 'meta.json'));
  const raw = [
    ['sensor:meta', meta ? JSON.stringify(meta) : null],
    ['sensor:tier-mass', digestTierMass(inv)],
    ['sensor:hotspots', digestHotspots(inv)],
    ['sensor:callback-smells', digestCallbacks(inv)],
    ['sensor:test-signals', digestTestSignals(inv)],
    ['packet-pool', digestPackets(repoRoot)],
    ['cost-actuals', digestCost(repoRoot)],
    ['b-inventory', digestBInventory(repoRoot, gitRoot)],
    ['ratification-queue', digestQueue(repoRoot, gitRoot)],
    ['doctrine', doctrinePath && existsSync(doctrinePath) ? readFileSync(doctrinePath, 'utf8') : null],
    ['doctrine:named-targets', digestNamedTargets(profileAgenda)],
  ];
  if (!blind) {
    raw.push(['prior-agenda', digestPriorAgenda(repoRoot)]);
    raw.push(['selection-ledger', digestSelectionLedger(repoRoot)]);
    raw.push(['not-chosen-aging', digestNotChosen(repoRoot)]);
  }
  const sections = raw
    .filter(([, text]) => text != null)
    .map(([label, text]) => { const t = cap(text, CAPS[label] ?? 4000); return { label, text: t, bytes: Buffer.byteLength(t) }; });
  return { sections, totalBytes: sections.reduce((s, x) => s + x.bytes, 0) };
}

// ---------------------------------------------------------------- the prompt

const CITATION_GRAMMAR = `Sensor-citation grammar (machine-verified after you answer — cite ONLY numbers visible above):
  <file>:mass=<int>            Σ excess-cc of the file          (tier-mass by_file)
  <file>:churn=<int>           commits in the churn window      (tier-mass / hotspots)
  <file>:attention=<int>       churn × mass                     (tier-mass by_file)
  <file>:fns=<int>             flagged function count           (tier-mass by_file)
  <file>:loc=<int> | cog=<int> | coupling=<int> | score=<int>   (hotspots)
  <file>:cc[<fn>]=<int>        cognitive complexity of one fn   (tier-mass universe / top functions)
  <file>:excess[<fn>]=<int>    excess-cc of one fn
  <subsystem>:mass=<int> | fns=<int> | files=<int>              (tier-mass by_subsystem)
  <test-file>:skips=<int>      static skip markers in one test file          (test-signals)
  <file>:untested_exports=<int> exports with zero by-name test refs          (test-signals — weak, by_name_only)
A sensor citation that fails to reproduce marks the WHOLE item UNVERIFIED and demotes it below
every verified item in the consumable ranking. Other evidence types (corpus, b-inventory,
test-gap, incident) are recorded as-is and checkable by reference — cite the document/section/
row precisely.`;

const OUTPUT_CONTRACT = `End your reply with EXACTLY ONE fenced \`\`\`json block of this shape (no other fenced json):

\`\`\`json
{
  "items": [
    {
      "id": "kebab-case-stable-id (reuse the packet candidate_id when the item IS an existing packet)",
      "what": "<one sentence: the work>",
      "why_now": "<the argument, grounded in the evidence entries>",
      "evidence": [
        {"type": "sensor", "citation": "<grammar above, e.g. runtime/index.js:cc[handleMsg]=194>"},
        {"type": "corpus" | "b-inventory" | "test-gap" | "incident", "citation": "<doc §/row/observable>", "note": "<optional>"}
      ],
      "workflow_class": "B" | "A2" | "T1a" | "T1b" | "T2" | "T0" | "prevention" | "evidence-generation",
      "packet_ids": ["<candidate_id from the packet pool this item maps to>"],
      "campaign_id": "<id from campaigns[] when this item advances a campaign, else null>",
      "acceptance_criteria": ["<checkable criterion>"],
      "est_cost": {"usd": <number or null>, "basis": "<which cost-ledger actual this extrapolates>"},
      "predicted_gain": "<expected owner-attention / quality gain, one line>"
    }
  ],
  "campaigns": [
    {"id": "kebab-id", "named_target": "<the named target>", "why": "<one line>", "acceptance_criteria": ["<what DONE means for the campaign>"]}
  ],
  "not_chosen": [
    {"id": "kebab-id", "what": "<the candidate>", "reason": "<why not now>"}
  ],
  "deltas_from_prior": ["<what moved vs the prior agenda and why>"],
  "human_decisions_needed": ["<owner-level decision, stated as a decidable question>"]
}
\`\`\`

items are RANKED, most valuable first, 8-15 of them. Rules:
- why_now with TYPED evidence for every item — never bare judgment.
- Campaign entries carry a named target + acceptance criteria, NOT packet specs (packet
  authoring is a separate later step; the deterministic risk router assigns proof tiers, not you).
- Doctrine-named targets you decline or rank low MUST appear in not_chosen with the reason, and
  a doctrine-target demotion is an escalation → also list it in human_decisions_needed.
- Anything behavior-changing / public-contract / owner-level is a B campaign or a
  human_decisions_needed entry — never autonomous work.
- deltas_from_prior: ["(no prior agenda)"] when context contains none.`;

const ROLE = `You are the AGENDA-SETTER (the staff-engineer chooser) in a repo-quality engine.
Principle: intelligence at the front, mechanism at the back — you CHOOSE and SEQUENCE work;
deterministic machinery proves and executes it. You cannot touch the deterministic floor
(negative controls, in-flight campaigns, aged omissions) and you do not author packets.

The deterministic planner you replace had a measured streetlight bias: it ranked only what its
lint-adjacent sensors could see (small extractions) and missed the staff-engineer targets named
in the doctrine and corpus. Do not repeat that failure in either direction: the sensor digests
are first-class evidence (the mechanical instruments provide most of the valuable input), but
the agenda is a PORTFOLIO judgment over everything in context — public-contract (B) work,
campaigns on doctrine-named targets, evidence-generation that unlocks blocked work, prevention/
ratchets that stop the complexity distribution regenerating, and high-mass decomplection the
packet pool does not yet contain. Omission/starvation is the primary harm class: an agenda that
starves what the owner values most leaves the repo worse without landing a single bad diff.
Respect the doctrine's budget direction. Sequence by attention-weighted leverage.`;

const STRICT_SUFFIX = `

IMPORTANT: your previous reply could not be parsed or violated the output contract. This time
respond with ONLY the single fenced \`\`\`json block described above — no prose before or after.`;

export function buildAgendaPrompt(sections, { blind = false, strict = false } = {}) {
  const parts = [ROLE];
  if (blind) {
    parts.push('BLIND CHALLENGE MODE: you are the independent challenger. You have deliberately NOT been shown any prior agenda — derive your ranking from the evidence alone.');
  }
  for (const s of sections) parts.push(`== ${s.label.toUpperCase()} ==\n${s.text}`);
  parts.push(CITATION_GRAMMAR, OUTPUT_CONTRACT);
  let prompt = parts.join('\n\n');
  if (strict) prompt += STRICT_SUFFIX;
  return prompt;
}

// ---------------------------------------------------------------- model-output normalization

const isStr = (v) => typeof v === 'string';
const strArr = (v) => (Array.isArray(v) ? v.filter(isStr) : isStr(v) ? [v] : []);

/** normalize + validate the model's JSON → { doc, errors } (errors non-empty = contract violation). */
export function normalizeModelAgenda(parsed) {
  const errors = [];
  if (!parsed || typeof parsed !== 'object') return { doc: null, errors: ['reply is not a JSON object'] };
  const items = Array.isArray(parsed.items) ? parsed.items : [];
  if (!items.length) errors.push('items[]: non-empty array required');
  const normItems = items.map((it, i) => {
    const what = isStr(it?.what) ? it.what.trim() : '';
    if (!what) errors.push(`items[${i}].what: non-empty string required`);
    if (!isStr(it?.why_now) || !it.why_now.trim()) errors.push(`items[${i}].why_now: non-empty string required`);
    const evidence = (Array.isArray(it?.evidence) ? it.evidence : [])
      .filter((e) => e && typeof e === 'object' && EVIDENCE_TYPES.includes(e.type) && isStr(e.citation) && e.citation.trim())
      .map((e) => ({ type: e.type, citation: e.citation.trim(), ...(isStr(e.note) ? { note: e.note } : {}) }));
    if (!evidence.length) errors.push(`items[${i}].evidence: ≥1 typed citation required (types: ${EVIDENCE_TYPES.join('|')}) — never bare judgment`);
    if (!isStr(it?.workflow_class) || !it.workflow_class.trim()) errors.push(`items[${i}].workflow_class: required`);
    const acceptance = strArr(it?.acceptance_criteria);
    if (!acceptance.length) errors.push(`items[${i}].acceptance_criteria: ≥1 required`);
    let est = it?.est_cost;
    if (typeof est === 'number') est = { usd: est, basis: null };
    else if (isStr(est)) est = { usd: null, basis: est };
    else if (est && typeof est === 'object') est = { usd: Number.isFinite(est.usd) ? est.usd : null, basis: isStr(est.basis) ? est.basis : null };
    else { errors.push(`items[${i}].est_cost: required`); est = { usd: null, basis: null }; }
    return {
      id: isStr(it?.id) && it.id.trim() ? slug(it.id) : slug(what),
      what,
      why_now: isStr(it?.why_now) ? it.why_now : '',
      evidence,
      workflow_class: isStr(it?.workflow_class) ? it.workflow_class.trim() : 'unclassified',
      packet_ids: strArr(it?.packet_ids),
      campaign_id: isStr(it?.campaign_id) ? it.campaign_id : null,
      acceptance_criteria: acceptance,
      est_cost: est,
      predicted_gain: isStr(it?.predicted_gain) ? it.predicted_gain : null,
    };
  });
  const campaigns = (Array.isArray(parsed.campaigns) ? parsed.campaigns : []).map((c, i) => {
    if (!isStr(c?.named_target) || !c.named_target.trim()) errors.push(`campaigns[${i}].named_target: required`);
    if (!strArr(c?.acceptance_criteria).length) errors.push(`campaigns[${i}].acceptance_criteria: ≥1 required`);
    return {
      id: isStr(c?.id) && c.id.trim() ? slug(c.id) : slug(c?.named_target ?? `campaign-${i}`),
      named_target: isStr(c?.named_target) ? c.named_target : '',
      why: isStr(c?.why) ? c.why : '',
      acceptance_criteria: strArr(c?.acceptance_criteria),
    };
  });
  const notChosen = (Array.isArray(parsed.not_chosen) ? parsed.not_chosen : []).map((nc, i) => {
    if (!isStr(nc?.reason) || !nc.reason.trim()) errors.push(`not_chosen[${i}].reason: required`);
    return {
      id: isStr(nc?.id) && nc.id.trim() ? slug(nc.id) : slug(nc?.what ?? `not-chosen-${i}`),
      what: isStr(nc?.what) ? nc.what : '',
      reason: isStr(nc?.reason) ? nc.reason : '',
      ...(strArr(nc?.packet_ids).length ? { packet_ids: strArr(nc.packet_ids) } : {}),
    };
  });
  const doc = {
    items: normItems,
    campaigns,
    not_chosen: notChosen,
    deltas_from_prior: strArr(parsed.deltas_from_prior),
    human_decisions_needed: strArr(parsed.human_decisions_needed),
  };
  return { doc, errors };
}

// ---------------------------------------------------------------- citation verifier
// Deterministic, runs AFTER emit-time parsing (design amendment 2): re-derive every sensor
// citation against quality/inventory/*.json. Failures mark the item UNVERIFIED; unverified
// items CANNOT outrank verified items — the consumable ranking demotes them.

export function loadSensorIndex(repoRoot) {
  const inv = join(repoRoot, 'quality', 'inventory');
  const tm = readJson(join(inv, 'tier-mass.json')) ?? {};
  const hs = readJson(join(inv, 'hotspots.json')) ?? {};
  const ts = readJson(join(inv, 'test-signals.json')) ?? {};
  const byFile = new Map((tm.by_file ?? []).map((f) => [f.file, f]));
  const bySub = new Map((tm.by_subsystem ?? []).map((s) => [s.subsystem, s]));
  const hotspots = new Map((hs.files ?? []).map((f) => [f.file, f]));
  const skipsByFile = new Map((ts.skips?.files ?? []).map((f) => [f.file, f.skips]));
  const untestedByFile = new Map((ts.zero_by_name?.files ?? []).map((f) => [f.file, f.exports]));
  const fnRows = new Map();
  for (const u of tm.universe ?? []) {
    if (!fnRows.has(u.file)) fnRows.set(u.file, []);
    fnRows.get(u.file).push(u);
  }
  return { byFile, bySub, hotspots, fnRows, skipsByFile, untestedByFile };
}

const CITE_RE = /^(.+?):([a-z_]+)(?:\[(.+?)\])?=(-?\d+(?:\.\d+)?)$/;

/** verify one sensor citation → { ok, detail }. Unknown name/metric fails CLOSED. */
export function verifySensorCitation(citation, idx) {
  const m = CITE_RE.exec(String(citation).trim());
  if (!m) return { ok: false, detail: 'citation does not match the file:metric=value grammar' };
  const [, name, metric, fn, valueStr] = m;
  const value = Number(valueStr);
  const candidates = [];
  const file = idx.byFile.get(name);
  const hot = idx.hotspots.get(name);
  const sub = idx.bySub.get(name);
  if (fn) {
    if (!['cc', 'excess'].includes(metric)) return { ok: false, detail: `metric '${metric}[fn]' not in the grammar` };
    const rows = (idx.fnRows.get(name) ?? []).filter((r) => r.fn === fn);
    if (!rows.length) return { ok: false, detail: `no flagged function '${fn}' in ${name}` };
    for (const r of rows) candidates.push(r[metric]);
  } else {
    switch (metric) {
      case 'mass': if (file) candidates.push(file.mass); if (sub) candidates.push(sub.mass); break;
      case 'churn': if (file) candidates.push(file.churn); if (hot) candidates.push(hot.churn); break;
      case 'attention': if (file) candidates.push(file.attention); break;
      case 'fns': if (file) candidates.push(file.fns); if (sub) candidates.push(sub.fns); break;
      case 'files': if (sub) candidates.push(sub.files); break;
      case 'loc': case 'cog': case 'coupling': case 'score': if (hot) candidates.push(hot[metric]); break;
      case 'skips': if (idx.skipsByFile.has(name)) candidates.push(idx.skipsByFile.get(name)); break;
      case 'untested_exports': if (idx.untestedByFile.has(name)) candidates.push(idx.untestedByFile.get(name)); break;
      default: return { ok: false, detail: `metric '${metric}' not in the grammar` };
    }
    if (!candidates.length) return { ok: false, detail: `'${name}' not found in the inventory for metric '${metric}'` };
  }
  if (candidates.some((c) => Number(c) === value)) return { ok: true, detail: `reproduced: ${name}:${metric}${fn ? `[${fn}]` : ''}=${value}` };
  return { ok: false, detail: `does not reproduce — inventory says ${candidates.join(' or ')}, citation says ${value}` };
}

/** verify + DEMOTE: mutates items (evidence .verified, item .verification, .rank, .model_rank). */
export function verifyAndRank(items, idx) {
  const stats = { sensor_citations: 0, verified: 0, failed: 0, demoted_items: 0 };
  for (const it of items) {
    let bad = 0;
    for (const e of it.evidence) {
      if (e.type !== 'sensor') { e.verified = null; continue; }
      stats.sensor_citations++;
      const v = verifySensorCitation(e.citation, idx);
      e.verified = v.ok;
      e.verify_detail = v.detail;
      if (v.ok) stats.verified++; else { stats.failed++; bad++; }
    }
    it.verification = bad ? 'unverified' : 'verified';
  }
  items.forEach((it, i) => { it.model_rank = i + 1; });
  const ranked = [...items.filter((it) => it.verification === 'verified'), ...items.filter((it) => it.verification !== 'verified')];
  ranked.forEach((it, i) => { it.rank = i + 1; });
  stats.demoted_items = ranked.filter((it) => it.verification !== 'verified' && it.rank > it.model_rank).length;
  return { items: ranked, stats };
}

// ---------------------------------------------------------------- artifacts

export function renderAgendaMd(a, { agedEntries = {} } = {}) {
  const L = [];
  const w = (s = '') => L.push(s);
  w(`# hone agenda — ${a.agenda_id}${a.challenge ? ' (BLIND CHALLENGER — not the incumbent)' : ''}`);
  w();
  w(`Generated ${a.created} by ${a.provider}/${a.model} (one strong-model call; ${a.total_context_bytes} bytes of digest context).`);
  w(`Repo sha ${String(a.repo_sha).slice(0, 12)} · inventory sha ${String(a.inventory_sha ?? 'n/a').slice(0, 12)} · doctrine: ${a.doctrine_path ?? '(none supplied)'}`);
  w(`Citation verifier: ${a.verification.sensor_citations} sensor citation(s) · ${a.verification.verified} reproduced · ${a.verification.failed} FAILED.` +
    (a.verification.failed ? ` UNVERIFIED items are demoted below every verified item (the model's own order is kept as model_rank).` : ''));
  w();
  w('## Ranked items (consumable order — verified-first)');
  w();
  for (const it of a.items) {
    const demoted = it.rank !== it.model_rank ? ` (model ranked #${it.model_rank})` : '';
    w(`### ${it.rank}. ${it.what}${it.verification !== 'verified' ? ' — ⚠ UNVERIFIED' : ''}`);
    w(`- id: \`${it.id}\` · class: ${it.workflow_class}${it.campaign_id ? ` · campaign: ${it.campaign_id}` : ''}${demoted}`);
    w(`- why now: ${it.why_now}`);
    w('- evidence:');
    for (const e of it.evidence) {
      const mark = e.verified === true ? '✓' : e.verified === false ? `✗ FAILED (${e.verify_detail})` : 'by reference';
      w(`  - [${e.type} ${mark}] ${e.citation}${e.note ? ` — ${e.note}` : ''}`);
    }
    w('- acceptance criteria:');
    for (const c of it.acceptance_criteria) w(`  - ${c}`);
    w(`- est cost: ${it.est_cost.usd != null ? `$${it.est_cost.usd}` : 'n/a'}${it.est_cost.basis ? ` (${it.est_cost.basis})` : ''}${it.predicted_gain ? ` · predicted gain: ${it.predicted_gain}` : ''}`);
    if (it.packet_ids.length) w(`- packets: ${it.packet_ids.map((p) => `\`${p}\``).join(', ')}`);
    w();
  }
  w('## Campaigns (named target + acceptance criteria — NOT packet specs)');
  w();
  if (!a.campaigns.length) w('- (none)');
  for (const c of a.campaigns) {
    w(`- **${c.id}** → ${c.named_target}${c.why ? ` — ${c.why}` : ''}`);
    for (const ac of c.acceptance_criteria) w(`  - done when: ${ac}`);
  }
  w();
  w('## NOT chosen (persisted + aged in quality/agendas/not-chosen.json)');
  w();
  if (!a.not_chosen.length) w('- (none)');
  for (const nc of a.not_chosen) {
    const age = agedEntries[nc.id]?.age_count;
    w(`- \`${nc.id}\`${nc.what ? ` — ${nc.what}` : ''}: ${nc.reason}${age ? ` (age ${age})` : ''}`);
  }
  w();
  w('## Deltas from prior');
  w();
  for (const d of a.deltas_from_prior.length ? a.deltas_from_prior : ['(none stated)']) w(`- ${d}`);
  w();
  w('## Human decisions needed');
  w();
  for (const d of a.human_decisions_needed.length ? a.human_decisions_needed : ['(none)']) w(`- ${d}`);
  w();
  return L.join('\n');
}

/** merge this agenda's not_chosen into quality/agendas/not-chosen.json (aging counters). */
export function mergeNotChosen(repoRoot, agendaDoc) {
  const path = notChosenPath(repoRoot);
  const prior = existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : { version: 1, entries: {} };
  const entries = prior.entries ?? {};
  const ts = agendaDoc.created;
  const chosen = new Set(agendaDoc.items.flatMap((it) => [it.id, ...it.packet_ids]));
  for (const nc of agendaDoc.not_chosen) {
    const e = entries[nc.id];
    if (e) {
      e.age_count = (e.age_count ?? 0) + 1;
      e.last_seen = ts;
      e.reason_latest = nc.reason;
      if (nc.what) e.what = nc.what;
      if (nc.packet_ids) e.packet_ids = nc.packet_ids;
    } else {
      entries[nc.id] = { first_seen: ts, last_seen: ts, age_count: 1, reason_latest: nc.reason, what: nc.what, ...(nc.packet_ids ? { packet_ids: nc.packet_ids } : {}) };
    }
  }
  for (const key of Object.keys(entries)) if (chosen.has(key)) delete entries[key];
  mkdirSync(join(repoRoot, 'quality', 'agendas'), { recursive: true });
  writeFileSync(path, JSON.stringify({ version: 1, updated: ts, entries }, null, 2) + '\n');
  return entries;
}

/** one selection-ledger line per ranked item (design amendment 6 — meta-evaluate the chooser). */
export function appendSelectionLedger(repoRoot, agendaDoc) {
  const path = selectionLedgerPath(repoRoot);
  mkdirSync(join(repoRoot, 'quality'), { recursive: true });
  for (const it of agendaDoc.items) {
    appendFileSync(path, JSON.stringify({
      agenda_id: agendaDoc.agenda_id,
      agenda_ts: agendaDoc.created,
      item_id: it.id,
      rank: it.rank,
      model_rank: it.model_rank,
      verification: it.verification,
      predicted: { gain: it.predicted_gain, est_cost_usd: it.est_cost.usd, class: it.workflow_class },
      packet_ids: it.packet_ids,
      campaign_id: it.campaign_id,
    }) + '\n');
  }
}

// ---------------------------------------------------------------- challenge diff

const tokens = (s) => new Set(String(s).toLowerCase().split(/[^a-z0-9]+/).filter((t) => t.length >= 4));
function jaccard(a, b) {
  const ta = tokens(a), tb = tokens(b);
  if (!ta.size || !tb.size) return 0;
  let inter = 0;
  for (const t of ta) if (tb.has(t)) inter++;
  return inter / (ta.size + tb.size - inter);
}

/** rank-divergence summary between incumbent and challenger — v1 has NO reconciliation logic;
 * this diff artifact is the deliverable. */
export function diffAgendas(incumbent, challenger) {
  const L = [`# challenge diff — ${challenger.agenda_id} vs incumbent ${incumbent?.agenda_id ?? '(none)'}`, ''];
  if (!incumbent) {
    L.push('No incumbent quality/AGENDA.json — every challenger item is new.', '');
    for (const it of challenger.items) L.push(`- #${it.rank} ${it.id} — ${it.what}`);
    return L.join('\n') + '\n';
  }
  const pairItem = (it) => {
    const byId = incumbent.items.find((x) => x.id === it.id || x.packet_ids?.some((p) => it.packet_ids.includes(p)));
    if (byId) return byId;
    let best = null, bestScore = 0;
    for (const x of incumbent.items) {
      const s = jaccard(x.what, it.what);
      if (s > bestScore) { bestScore = s; best = x; }
    }
    return bestScore >= 0.5 ? best : null;
  };
  const paired = new Set();
  const rows = [];
  for (const it of challenger.items) {
    const inc = pairItem(it);
    if (inc) { paired.add(inc.id); rows.push({ it, inc }); }
    else rows.push({ it, inc: null });
  }
  const shared = rows.filter((r) => r.inc);
  const divergences = shared.map((r) => Math.abs(r.it.rank - r.inc.rank));
  L.push(`Shared items: ${shared.length} · only-in-challenger: ${rows.length - shared.length} · only-in-incumbent: ${incumbent.items.length - paired.size}`);
  if (shared.length) L.push(`Rank divergence over shared items: max ${Math.max(...divergences)} · mean ${(divergences.reduce((a, b) => a + b, 0) / shared.length).toFixed(1)}`);
  L.push('', '| challenger # | incumbent # | Δ | item |', '|---|---|---|---|');
  for (const r of rows) {
    L.push(`| ${r.it.rank} | ${r.inc ? r.inc.rank : '—'} | ${r.inc ? Math.abs(r.it.rank - r.inc.rank) : 'new'} | ${r.it.what.slice(0, 90)} |`);
  }
  const missed = incumbent.items.filter((x) => !paired.has(x.id));
  if (missed.length) {
    L.push('', 'Only in incumbent (challenger omitted — repeated omission is a reconciliation trigger):');
    for (const x of missed) L.push(`- incumbent #${x.rank} ${x.id} — ${x.what.slice(0, 100)}`);
  }
  return L.join('\n') + '\n';
}

// ---------------------------------------------------------------- provider execs
// The providers/ layer is judge/propose-shaped; the agenda needs ONE raw strong-tier call, so
// the two adapters live here, reusing runCli (process-group kill on timeout) and the same
// fresh-empty-cwd independence: the digests in the prompt are the ONLY case-specific context.

async function claudeExec(prompt, { timeoutMs = AGENDA_TIMEOUT_MS } = {}) {
  const model = process.env.HONE_AGENDA_CLAUDE_MODEL || 'opus'; // strong tier by default
  const cwd = mkdtempSync(join(tmpdir(), 'hone-agenda-'));
  const args = ['-p', '--model', model, '--output-format', 'json', '--no-session-persistence', ...CLAUDE_NO_MCP_ARGS];
  const { stdout, durationMs } = await runCli('claude', args, { input: prompt, timeoutMs, cwd, env: noMcpEnv() });
  let envelope;
  try { envelope = JSON.parse(stdout); }
  catch { throw Object.assign(new Error(`claude -p emitted non-JSON envelope: ${stdout.slice(0, 300)}`), { kind: 'bad-envelope' }); }
  if (envelope.is_error) throw Object.assign(new Error(`claude -p returned is_error: ${String(envelope.result).slice(0, 300)}`), { kind: 'provider-error' });
  return {
    text: envelope.result ?? '',
    meta: {
      provider: 'claude', model, durationMs,
      costUsd: envelope.total_cost_usd ?? null,
      tokens: envelope.usage ? { input: envelope.usage.input_tokens ?? null, output: envelope.usage.output_tokens ?? null } : null,
    },
  };
}

async function codexExec(prompt, { timeoutMs = AGENDA_TIMEOUT_MS } = {}) {
  const model = requireGpt56(process.env.HONE_AGENDA_CODEX_MODEL || process.env.HONE_CODEX_MODEL || 'gpt-5.6-sol');
  const dir = mkdtempSync(join(tmpdir(), 'hone-agenda-'));
  const outFile = join(dir, 'last-message.txt');
  const mcpArgs = await codexNoMcpArgs(dir);
  const args = ['exec', '--ephemeral', '--skip-git-repo-check', '-s', 'read-only', '--color', 'never', '-m', model, ...mcpArgs, '-o', outFile, '-'];
  const { stdout, stderr, durationMs } = await runCli('codex', args, { input: prompt, timeoutMs, cwd: dir });
  let text = '';
  try { text = readFileSync(outFile, 'utf8'); }
  catch { throw Object.assign(new Error(`codex exec produced no last-message file; stderr: ${stderr.slice(0, 300)}`), { kind: 'no-output' }); }
  const m = /tokens used[^\d]*([\d,]+)/i.exec(stdout + '\n' + stderr);
  return { text, meta: { provider: 'codex', model, durationMs, costUsd: null, tokens: { total: m ? Number(m[1].replaceAll(',', '')) : null } } };
}

function realDeps() {
  return {
    exec: async (provider, prompt, opts) => (provider === 'codex' ? codexExec : claudeExec)(prompt, opts),
    log: (s) => process.stderr.write(s + '\n'),
  };
}

// ---------------------------------------------------------------- the executor

export async function executeAgenda(opts, deps) {
  const { repoRoot, gitRoot, repoSha, doctrinePath, profileAgenda = null, challenge = false, dryRun = false } = opts;
  loadVerifiedInventorySnapshot(repoRoot, repoSha);
  const log = deps.log;
  const provider = challenge ? 'codex' : 'claude'; // challenger uses the OTHER family (amendment 4)

  const { sections, totalBytes } = assembleAgendaContext({ repoRoot, gitRoot, doctrinePath, profileAgenda, blind: challenge });
  if (!sections.some((s) => s.label === 'sensor:tier-mass')) {
    throw new Error(`no sensor inventory at ${join(repoRoot, 'quality/inventory')} — run \`hone inventory\` first (the agenda cites sensors; without them nothing is verifiable)`);
  }
  const prompt = buildAgendaPrompt(sections, { blind: challenge });

  const sizesLine = sections.map((s) => `${s.label}=${s.bytes}b`).join(' · ');
  if (dryRun) {
    return {
      outcome: 'dry-run', exitCode: 0, sections, prompt,
      summary: [
        `hone agenda — DRY RUN (no model call, no writes)${challenge ? ' — CHALLENGE (blind, codex)' : ''}`,
        `  context: ${sections.length} sections · ${totalBytes} bytes total`,
        ...sections.map((s) => `    ${s.label.padEnd(22)} ${String(s.bytes).padStart(6)}b`),
        `  prompt: ${Buffer.byteLength(prompt)} bytes → ${provider} (strong tier)`,
        `  --- prompt follows ---`,
        prompt,
      ].join('\n'),
    };
  }

  // one strong-model call; at most ONE retry (strict-output on parse/contract failure,
  // same prompt on transient provider failure) — then fail LOUD. Re-rolling further would
  // be agenda-shopping.
  let modelDoc = null, meta = null, contractErrors = [];
  for (let attempt = 0, strict = false; attempt < 2 && !modelDoc; attempt++) {
    log(`hone agenda — ${provider} call (attempt ${attempt + 1}${strict ? ', strict output' : ''}; prompt ${Buffer.byteLength(prompt)}b)`);
    let res;
    try { res = await deps.exec(provider, strict ? prompt + STRICT_SUFFIX : prompt, { timeoutMs: AGENDA_TIMEOUT_MS }); }
    catch (e) {
      contractErrors = [`provider ${provider} failed: ${e.kind ?? 'error'}: ${e.message}`];
      log(`  provider failure (${e.kind ?? 'error'}) — ${attempt === 0 ? 'one retry' : 'giving up'}`);
      continue;
    }
    meta = res.meta;
    const parsed = extractFencedJson(res.text);
    const { doc, errors } = normalizeModelAgenda(parsed);
    if (parsed && !errors.length) { modelDoc = doc; break; }
    contractErrors = parsed ? errors : ['no parseable fenced JSON block in the reply'];
    log(`  output-contract violation (${contractErrors.length} error(s)) — ${attempt === 0 ? 'one strict retry' : 'giving up'}`);
    strict = true;
  }
  if (!modelDoc) {
    throw new Error(`agenda call failed after retry — fail-loud, nothing written:\n  - ${contractErrors.join('\n  - ')}`);
  }

  // deterministic citation verification + demotion (amendment 2)
  const idx = loadSensorIndex(repoRoot);
  const { items, stats } = verifyAndRank(modelDoc.items, idx);

  const created = new Date().toISOString();
  const stamp = created.replace(/[:.]/g, '-');
  const inventoryMeta = readJson(join(repoRoot, 'quality', 'inventory', 'meta.json'));
  const agendaDoc = {
    version: 1,
    agenda_id: `${challenge ? 'challenge' : 'agenda'}-${stamp}`,
    created,
    trigger: 'manual', // automated triggers documented in the header, wired later
    challenge,
    provider,
    model: meta?.model ?? null,
    repo_sha: repoSha ?? null,
    inventory_sha: inventoryMeta?.repo_sha ?? null,
    doctrine_path: doctrinePath ?? null,
    context_bytes: Object.fromEntries(sections.map((s) => [s.label, s.bytes])),
    total_context_bytes: totalBytes,
    prompt_bytes: Buffer.byteLength(prompt),
    call: { duration_ms: meta?.durationMs ?? null, cost_usd: meta?.costUsd ?? null, tokens: meta?.tokens ?? null },
    verification: stats,
    items,
    campaigns: modelDoc.campaigns,
    not_chosen: modelDoc.not_chosen,
    deltas_from_prior: modelDoc.deltas_from_prior,
    human_decisions_needed: modelDoc.human_decisions_needed,
  };

  const qdir = join(repoRoot, 'quality');
  const histDir = join(qdir, 'agendas');
  mkdirSync(histDir, { recursive: true });
  const jsonText = JSON.stringify(agendaDoc, null, 2) + '\n';
  const written = [];

  if (challenge) {
    const md = renderAgendaMd(agendaDoc);
    const incumbent = readAgendaArtifacts(repoRoot).agenda;
    const diff = diffAgendas(incumbent, agendaDoc);
    writeFileSync(join(histDir, `${agendaDoc.agenda_id}.json`), jsonText);
    writeFileSync(join(histDir, `${agendaDoc.agenda_id}.md`), md);
    writeFileSync(join(histDir, `${agendaDoc.agenda_id}-diff.md`), diff);
    written.push(`quality/agendas/${agendaDoc.agenda_id}.json`, `quality/agendas/${agendaDoc.agenda_id}.md`, `quality/agendas/${agendaDoc.agenda_id}-diff.md`);
  } else {
    const agedEntries = mergeNotChosen(repoRoot, agendaDoc); // before render: md annotates ages
    const md = renderAgendaMd(agendaDoc, { agedEntries });
    writeFileSync(join(qdir, 'AGENDA.json'), jsonText);
    writeFileSync(join(qdir, 'AGENDA.md'), md);
    writeFileSync(join(histDir, `${agendaDoc.agenda_id}.json`), jsonText);
    writeFileSync(join(histDir, `${agendaDoc.agenda_id}.md`), md);
    appendSelectionLedger(repoRoot, agendaDoc);
    written.push('quality/AGENDA.json', 'quality/AGENDA.md', `quality/agendas/${agendaDoc.agenda_id}.{json,md}`, 'quality/agendas/not-chosen.json', 'quality/selection-ledger.jsonl');
  }

  const costLine = meta?.costUsd != null ? `$${meta.costUsd.toFixed(4)}` : (meta?.tokens?.total != null ? `${meta.tokens.total} tokens (no $ reported)` : 'cost unreported');
  return {
    outcome: challenge ? 'challenge' : 'agenda', exitCode: 0, agenda: agendaDoc,
    summary: [
      `hone agenda — ${agendaDoc.agenda_id}: ${items.length} ranked item(s), ${agendaDoc.campaigns.length} campaign(s), ${agendaDoc.not_chosen.length} NOT-chosen`,
      `  context: ${totalBytes}b (${sizesLine})`,
      `  citations: ${stats.sensor_citations} sensor · ${stats.verified} verified · ${stats.failed} FAILED${stats.failed ? ` → ${items.filter((i) => i.verification !== 'verified').length} item(s) demoted below all verified items` : ''}`,
      `  call: ${provider}/${agendaDoc.model} · ${Math.round((meta?.durationMs ?? 0) / 1000)}s · ${costLine}`,
      ...items.slice(0, 10).map((it) => `  ${String(it.rank).padStart(2)}. [${it.workflow_class}]${it.verification !== 'verified' ? ' ⚠UNVERIFIED' : ''} ${it.what.slice(0, 110)}`),
      `  wrote: ${written.join(' · ')}`,
    ].join('\n'),
  };
}

// ---------------------------------------------------------------- CLI entry

export async function runAgenda(flags) {
  const ctx = buildContext(flags.repo);
  const profAgenda = ctx.profile.agenda ?? {};
  let doctrinePath = flags.doctrine ? String(flags.doctrine) : (profAgenda.doctrine_path ?? null);
  if (doctrinePath && !isAbsolute(doctrinePath)) doctrinePath = resolve(ctx.repoRoot, doctrinePath);
  if (doctrinePath && !existsSync(doctrinePath)) {
    throw new Error(`doctrine not found: ${doctrinePath} (profile agenda.doctrine_path or --doctrine)`);
  }
  if (!doctrinePath) {
    process.stderr.write('WARN: no doctrine supplied (profile agenda.doctrine_path or --doctrine) — the agenda will run without the human-fixed budget + named targets, which weakens the whole design\n');
  }
  const res = await executeAgenda({
    repoRoot: ctx.repoRoot,
    gitRoot: ctx.git.gitRoot,
    repoSha: ctx.git.sha,
    doctrinePath,
    profileAgenda: profAgenda,
    challenge: !!flags.challenge,
    dryRun: !!flags['dry-run'],
  }, realDeps());
  process.stdout.write(res.summary + '\n');
  process.exitCode = res.exitCode;
  return res;
}
