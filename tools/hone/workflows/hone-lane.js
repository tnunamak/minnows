export const meta = {
  name: 'hone-lane',
  description: 'Workflow-substrate execution lane for hone packets: routed in-harness makers (L1 tier ladder, two-strike escalation) + different-model fresh-context judge ride the Claude Code Workflow tool, while the hone CLI (lane emit/gate/land) keeps EVERY receipt, gate, and ledger write. Supports per-change and batch verification (L2: one gate + one judge amortized over N routine orders, auto-bisect on red). Books identical to hone work; measured baseline: fable-workflow-baseline-2026-07-02.',
  whenToUse: 'When executing proven-trustworthy bulk-class hone packets from a repo with quality/packets/. Pass args={packets:[ids], repo, honeDir, batch?: true, makerModel?, judgeModel?}. makerModel pins a matrix arm (overrides the routing ladder); batch:true runs ONE combined gate+judge over all packets (risky classes are refused by the engine). Cross-provider pairs (codex pins) still route through `hone work`.',
  phases: [
    { title: 'Emit' },
    { title: 'Make' },
    { title: 'Gate' },
    { title: 'Judge', model: 'opus' },
    { title: 'Land' },
  ],
};

// ---------------------------------------------------------------------------
// Control inversion (design memo: tools/hone/SUBSTRATE.md). The Workflow tool only
// exists inside a Claude Code session, so hone cannot call it; instead THIS script
// consumes packets and drives agents, and the ENGINE-run rungs are the only receipts:
//   hone lane emit   -> gates + in_progress + GREEN BASELINE + maker brief + L1 routing
//   maker agent      -> applies the brief in the repo (tier from the routing ladder,
//                       two-strike escalation); MUST NOT run git write ops
//   hone lane gate   -> authoritative: re-runs every rung vs the current tree, touchset
//                       gate, receipts; green receipt bound to head_sha+diff hash
//   judge agent      -> different MODEL, fresh context; reads the engine-written
//                       judge-context.json (packet + evidence + diff); schema verdict
//   hone lane land   -> refuses without a matching green gate receipt; records verdict
//                       + explicit STAGE-ATTRIBUTED usage into the ledgers; commits/reverts
// Batch arm (args.batch): emit each member (the engine's baseline cache dedupes
// identical rungs at one HEAD), ONE persistent maker agent applies all briefs (L4
// context locality), then `gate --batch` (union touchset, one suite-level run,
// auto-bisect isolates offenders) and `land --batch` (one judge; each order lands as
// its OWN commit — per-order revertability preserved).
// Agent claims are never trusted: a lying pipe/maker/judge cannot forge a land —
// land itself is the engine and re-verifies tree state + receipts on disk.
//
// Prior art: .claude/workflows/refactor-loop.js (34 zero-regression lands overnight).
// Workflow scripts cannot import/require, so engine commands run through a MINIMAL
// haiku pipe agent that executes one command and echoes its JSON stdout verbatim (a
// dumb pipe, no judgment — the values stay the ENGINE's). If the harness ever exposes
// a native shell primitive, the pipe collapses to it with no design change.
// ---------------------------------------------------------------------------

const REPO = args && args.repo;
const HONE_DIR = args && args.honeDir;
const PACKETS = (args && args.packets) || [];
const BATCH = !!(args && args.batch);
const MAKER_OVERRIDE = (args && args.makerModel) || null; // matrix arm: pin the maker tier
const JUDGE_MODEL = (args && args.judgeModel) || 'opus';
if (!REPO || !HONE_DIR || !PACKETS.length) {
  log('hone-lane: args {packets:[ids], repo, honeDir} are required. Nothing to do.');
  return { error: 'missing-args', results: [] };
}
if (MAKER_OVERRIDE && MAKER_OVERRIDE === JUDGE_MODEL) {
  // structural maker != judge, enforced before any work; hone lane land re-refuses.
  log(`hone-lane: makerModel == judgeModel ('${MAKER_OVERRIDE}') — refusing (non-negotiable #1).`);
  return { error: 'maker-eq-judge', results: [] };
}
const HONE = `${HONE_DIR}/hone`;

// pure-JS UTF-8 base64 (no Buffer/btoa guaranteed in the workflow runtime); b64 args are
// shell-safe by construction, so engine flags never need shell quoting of free text.
function b64(s) {
  const bytes = [];
  for (let i = 0; i < s.length; i++) {
    let c = s.codePointAt(i);
    if (c > 0xffff) i++;
    if (c < 0x80) bytes.push(c);
    else if (c < 0x800) bytes.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else bytes.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i], b = bytes[i + 1], c = bytes[i + 2];
    out += A[a >> 2] + A[((a & 3) << 4) | ((b ?? 0) >> 4)]
      + (b == null ? '=' : A[((b & 15) << 2) | ((c ?? 0) >> 6)])
      + (c == null ? '=' : A[c & 63]);
  }
  return out;
}

// the dumb pipe: run ONE engine command, echo its stdout JSON verbatim. haiku-tier —
// zero judgment wanted. A garbled relay can only waste a step, never forge a receipt
// (the books live on disk; land re-verifies everything itself).
async function engine(label, phaseName, cmd) {
  const raw = await agent(
    `Run exactly this ONE command with Bash and then output the command's stdout VERBATIM as your ENTIRE final message — no commentary, no code fences, no reformatting, even if the command exits non-zero (lane commands print their result JSON on failure too). The output is a single compact JSON line under 1KB; copy it exactly:\n\n${cmd}\n\nIf stdout is empty, output the single word EMPTY.`,
    { phase: phaseName, label, model: 'haiku' }
  );
  const s = String(raw || '');
  const a = s.indexOf('{'), z = s.lastIndexOf('}');
  if (a === -1 || z <= a) return { __pipeError: `no JSON in pipe output: ${s.slice(0, 200)}` };
  try { return JSON.parse(s.slice(a, z + 1)); }
  catch (e) { return { __pipeError: `unparseable pipe JSON (${String(e).split('\n')[0]}): ${s.slice(0, 200)}` }; }
}

// Full lane output (inline briefs + packet YAML) exceeds what a relay agent can copy
// verbatim (run wf_cdc171e4: haiku re-typed 20KB and silently dropped brief_path —
// fail-closed as designed, wrong transport). The projector shrinks stdout to the exact
// field contract this driver reads; artifacts stay on disk where maker/judge read them.
const laneCmd = (sub, sel, extra = '') =>
  `cd ${REPO} && node ${HONE} lane ${sub} ${sel} --repo ${REPO} ${extra} 2>/dev/null | node ${HONE_DIR}/workflows/project-lane-json.mjs`;
const pkt = (id) => `--packet ${id}`;

// L1 tier ladder for THIS lane: the claude-family CALIBRATED candidates of the emitted
// routing (materialized from models.json by the engine — this script never invents a
// model); `short` is the harness model alias. MAKER_OVERRIDE pins a matrix arm.
// Two-strike escalation: strikes = gate reds + judge REVISEs; every 2 strikes step down.
function makerLadder(emitJson) {
  if (MAKER_OVERRIDE) return [{ model: MAKER_OVERRIDE, effort: 'high' }];
  const routed = (emitJson.routing && emitJson.routing.maker) || [];
  return routed
    .filter((m) => m.provider === 'claude' && m.calibrated !== false)
    .map((m) => ({ model: m.short || m.model, effort: m.effort }));
}
const tierFor = (ladder, strikes) => ladder[Math.min(Math.floor(strikes / 2), ladder.length - 1)];

// in-harness maker preamble: layered ON TOP of the engine's binding brief because a
// workflow agent (unlike the Bash-denied subprocess maker) has shell access.
const MAKER_PREAMBLE = (what) => `You are the MAKER for ${what}. The target repository is ${REPO} — every path in the brief(s) below is relative to it; edit files there.
IN-HARNESS ADDENDUM (binding, on top of the brief's rules):
- You have shell access. You MUST NOT run ANY git write operation (add/commit/stash/checkout/reset/rebase/branch). The engine detects a moved HEAD and voids the whole run as blocked(foreign-commit).
- You MAY run the packet's evidence_required commands (cwd ${REPO}) for fast feedback while you work. Your runs are ADVISORY ONLY — the engine re-runs everything itself as the only receipts. Do not run anything else destructive.
- Do not create, edit, or delete anything under ${REPO}/quality/.
When done, reply with the short summary the brief asks for (or the exact HONE-VERDICT line when no edit is warranted).`;

const JUDGE_PROMPT = (what, contextPath) => `You are the INDEPENDENT JUDGE in a repo-quality engine — a fresh context that did NOT write this change. A separate maker produced the diff for ${what}; your job is to certify it or refuse it. Makers overclaim — be adversarial.

Read the file ${contextPath} (written by the deterministic engine, not by any agent). It contains: the packet YAML contract(s), evidence (engine-run receipt digests + bounded real rung output), diff (the change under judgment).

Rules of judgment:
- The packet is the contract: its action, not_allowed, and evidence_required bind the maker.
- Evidence policy: you review evidence, you never replace it. Judge only from what is in front of you; if the supplied evidence does not cover the property at risk for this change class, that alone justifies REVISE or REJECT.
- Behavior preservation: for preserve_refactor packets, ANY observable behavior change (boundary/edge conditions, operator changes, ||/??/?. semantics, throw/error-string/return shapes) means REJECT unless the packet explicitly allows it.
- Reject relocation: moving a code blob behind a new name WITHOUT making captured context explicit or reducing real complexity is not decomplecting, even when green.
- Compare removed code to added code line by line before trusting any summary.
- For a BATCH: your verdict covers the COMBINED diff — one member failing the bar fails the batch (the engine reverts all members; per-order retries follow).
- The engine's quality/ state dir is engine bookkeeping, never maker work — ignore quality/ paths in any scope judgment.
You may additionally read the touched files in ${REPO} to verify context, but the diff + evidence in the context file are the record of judgment.

Verdict semantics: PASS = does what the packet says, violates nothing, evidence sufficient. REVISE = fixable defects or insufficient evidence. REJECT = behavior change where preservation is required, not_allowed violation, or relocation dressed as refactoring.`;

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'reasoning', 'confidence'],
  properties: {
    verdict: { enum: ['PASS', 'REVISE', 'REJECT'] },
    reasoning: { type: 'string', description: '2-5 sentences, the load-bearing reason' },
    confidence: { type: 'number', description: '0.0-1.0' },
  },
};

// stage-attributed usage for the ledger. Token counts are honest nulls: the harness does
// not expose per-agent usage to workflow scripts — NEVER fabricate numbers into the books
// (SUBSTRATE.md "Cost accounting"); model identities + stages still land the attribution.
const usageJson = (makerModel) => JSON.stringify([
  { role: 'maker', provider: 'claude', model: makerModel, stage: 'edit' },
  { role: 'judge', provider: 'claude', model: JUDGE_MODEL, stage: 'judge' },
]);

const results = [];
const tally = {}; // incremented by record(); every return path carries it
const record = (id, outcome, detail) => {
  results.push({ packet: id, outcome, ...(detail || {}) });
  tally[outcome] = (tally[outcome] || 0) + 1;
  log(`hone-lane [${id}]: ${outcome}${detail && detail.commit ? ` ${String(detail.commit).slice(0, 12)}` : ''}${detail && detail.reason ? ` — ${String(detail.reason).slice(0, 140)}` : ''}`);
};
async function abortPacket(id, reason) {
  await engine(`abort:${id}`, 'Land', laneCmd('land', pkt(id), `--abort --reason '${reason}' --usage-b64 ${b64(usageJson(MAKER_OVERRIDE || 'sonnet'))}`));
}

async function judgeOnce(label, what, contextPath) {
  const v = await agent(JUDGE_PROMPT(what, contextPath), {
    schema: VERDICT_SCHEMA, phase: 'Judge', label, model: JUDGE_MODEL, effort: 'high',
  });
  // fail closed: an unparseable judge can never PASS (provider.mjs parity)
  if (!v || !v.verdict) return { verdict: 'REVISE', reasoning: 'judge output unparseable — fail-closed (never land on garbage)', confidence: 0 };
  return v;
}
const verdictJsonOf = (verdict) => JSON.stringify({
  verdict: verdict.verdict,
  reasoning: String(verdict.reasoning || '(none)'),
  confidence: typeof verdict.confidence === 'number' ? Math.min(1, Math.max(0, verdict.confidence)) : null,
  judge: { provider: 'claude', model: JUDGE_MODEL },
});
const done = (extra) => {
  log(`hone-lane: done. ${JSON.stringify(tally)} — books in ${REPO}/quality/ (claims.jsonl, cost.jsonl, packets/, receipts/). Items flagged needs_attention require the orchestrator (possibly stranded in_progress packets: hone lane land --abort, or hone reset).`);
  return { results, tally, ...(extra || {}) };
};

// ===========================================================================
// BATCH ARM (L2): emit each member -> ONE persistent maker over all briefs (L4
// locality) -> gate --batch (one suite-level run + auto-bisect) -> one judge ->
// land --batch (per-order commits). The ENGINE refuses risky classes and
// overlapping touchsets fail-closed; this script only orchestrates.
// ===========================================================================
if (BATCH) {
  phase('Emit');
  const emitted = [];
  let ladder = MAKER_OVERRIDE ? [{ model: MAKER_OVERRIDE, effort: 'high' }] : null;
  for (const id of PACKETS) {
    const em = await engine(`emit:${id}`, 'Emit', laneCmd('emit', pkt(id), `--maker claude --judge claude`));
    if (em.__pipeError || em.refused || em.terminal || !em.ok || !em.brief_path) {
      record(id, em.refused ? 'refused' : (em.terminal || 'emit-failed'), { reason: em.reason || em.summary || em.__pipeError, needs_attention: !em.refused && !em.terminal });
      continue;
    }
    if (!ladder) {
      const l = makerLadder(em);
      if (l.length) ladder = l; // the first routed member's ladder governs the shared maker
    }
    emitted.push({ id, brief_path: em.brief_path });
  }
  if (emitted.length < 2) {
    for (const e of emitted) { await abortPacket(e.id, 'batch collapsed below 2 members at emit'); record(e.id, 'aborted', { reason: 'batch collapsed below 2 members' }); }
    log('hone-lane batch: fewer than 2 members emitted — nothing to batch.');
    return done();
  }
  const makerTier = (ladder && ladder[0]) || { model: 'sonnet', effort: 'high' };
  if (makerTier.model === JUDGE_MODEL) {
    for (const e of emitted) { await abortPacket(e.id, 'maker tier collides with judge model'); record(e.id, 'aborted', { reason: 'maker tier == judge model' }); }
    return done();
  }

  phase('Make');
  log(`hone-lane batch: ${emitted.length} member(s), shared maker ${makerTier.model}@${makerTier.effort} (context locality)`);
  await agent(
    `${MAKER_PREAMBLE(`a BATCH of ${emitted.length} hone work packets`)}\n\nExecute ALL of these work-packet briefs, one after another (they touch DISJOINT files by construction — the engine refuses overlaps):\n${emitted.map((e, i) => `${i + 1}. Read ${e.brief_path} and execute it exactly.`).join('\n')}\n\nDo every packet before replying. Reply with one short per-packet summary line each.`,
    { phase: 'Make', label: `make:batch(${emitted.length})`, model: makerTier.model, effort: makerTier.effort }
  );

  phase('Gate');
  const idsCsv = emitted.map((e) => e.id).join(',');
  const gate = await engine('gate:batch', 'Gate', laneCmd('gate', `--batch ${idsCsv}`));
  if (gate.__pipeError) {
    for (const e of emitted) { await abortPacket(e.id, 'gate pipe failure'); record(e.id, 'aborted', { reason: 'batch gate pipe failure', needs_attention: true }); }
    return done();
  }
  for (const r of gate.results || []) record(r.id, r.terminal, { reason: r.reason }); // no-diff skips + bisect offenders — books already written by the engine
  if (gate.refused) {
    for (const e of emitted) { await abortPacket(e.id, `batch gate refused`); record(e.id, 'aborted', { reason: gate.reason, needs_attention: true }); }
    return done();
  }
  if (gate.terminal) { return done({ engine_terminal: gate.terminal }); }
  if (gate.green !== true || !gate.judge_context_path || !Array.isArray(gate.members)) {
    for (const e of emitted) { await abortPacket(e.id, 'unrecognized batch gate state'); record(e.id, 'aborted', { reason: 'unrecognized gate state', needs_attention: true }); }
    return done();
  }

  phase('Judge');
  const verdict = await judgeOnce('judge:batch', `a BATCH of ${gate.members.length} hone work packets (combined diff)`, gate.judge_context_path);

  phase('Land');
  const land = await engine('land:batch', 'Land', laneCmd('land', `--batch ${gate.members.join(',')}`,
    `--judge-verdict-b64 ${b64(verdictJsonOf(verdict))} --usage-b64 ${b64(usageJson(makerTier.model))}`));
  if (land.__pipeError || land.refused) {
    for (const id of gate.members) record(id, 'land-refused', { reason: land.reason || land.__pipeError, needs_attention: true });
  } else if (land.results) {
    for (const r of land.results) record(r.id, r.terminal, { commit: r.commit, reason: r.reason });
  } else {
    for (const id of gate.members) record(id, land.terminal || 'unknown', { reason: land.summary });
  }
  return done({ batch_id: gate.batch_id });
}

// ===========================================================================
// PER-CHANGE ARM: emit -> routed maker (two-strike escalation) -> gate (engine
// attempt ceiling; escalated revision only when a higher tier exists) -> judge
// (≤1 REVISE cycle) -> land. Fail-closed per packet.
// ===========================================================================
for (const id of PACKETS) {
  try {
    phase('Emit');
    const emit = await engine(`emit:${id}`, 'Emit', laneCmd('emit', pkt(id), `--maker claude --judge claude`));
    if (emit.__pipeError) { record(id, 'pipe-error-at-emit', { reason: emit.__pipeError, needs_attention: true }); continue; }
    if (emit.refused) { record(id, 'refused', { reason: emit.reason }); continue; }
    if (emit.terminal) { record(id, emit.terminal, { reason: emit.summary }); continue; } // e.g. blocked(red baseline)
    if (!emit.ok || !emit.brief_path) {
      record(id, 'emit-unrecognized', { reason: JSON.stringify(emit).slice(0, 200), needs_attention: true });
      continue;
    }
    const ladder = makerLadder(emit);
    if (!ladder.length) {
      await abortPacket(id, 'routing requires a non-claude maker tier — route via hone work');
      record(id, 'routed-to-subprocess', { reason: `routing class '${emit.routing && emit.routing.class}' has no claude tier` });
      continue;
    }
    let strikes = 0;
    let lastTier = tierFor(ladder, 0);
    const makerCall = async (label, promptTail) => {
      const tier = tierFor(ladder, strikes);
      if (tier.model === JUDGE_MODEL) return { collided: tier.model }; // escalation would meet the judge — caller fail-closes
      log(`hone-lane [${id}]: maker ${tier.model}@${tier.effort} (strikes=${strikes}, class=${emit.routing && emit.routing.class})`);
      const text = await agent(`${MAKER_PREAMBLE(`hone packet ${id}`)}\n\n${promptTail}`,
        { phase: 'Make', label, model: tier.model, effort: tier.effort });
      return { text, tier };
    };

    phase('Make');
    const made = await makerCall(`make:${id}`, `Read your full work-packet brief at ${emit.brief_path} and execute it exactly.`);
    if (made.collided) { await abortPacket(id, 'maker tier collides with judge model — route to hone work'); record(id, 'aborted', { reason: 'tier==judge collision' }); continue; }
    lastTier = made.tier;

    phase('Gate');
    const summaryB64 = b64(String(made.text || '').slice(-2000));
    let gate = await engine(`gate:${id}#1`, 'Gate', laneCmd('gate', pkt(id), `--maker-summary-b64 ${summaryB64}`));
    // oracle red -> revisions inside the engine's attempt ceiling: first at the same
    // tier; a SECOND revision only when two-strike escalation reaches a HIGHER tier (L1).
    let revs = 0;
    while (!gate.__pipeError && !gate.terminal && gate.green === false && gate.attempts_left > 0 && revs < 2) {
      strikes++;
      const nextTier = tierFor(ladder, strikes);
      if (revs === 1 && nextTier.model === lastTier.model) break; // no escalation available — let the engine terminalize below
      log(`hone-lane [${id}]: oracle RED at '${gate.red && gate.red.rung}' — revision ${revs + 1} @ ${nextTier.model}`);
      const rev = await makerCall(`revise:${id}#${revs + 1}`, `Your previous attempt FAILED the deterministic oracle. Read the full revision brief at ${gate.revision_brief_path} (it contains the failure, the current diff, and every binding rule) and fix the failure.`);
      if (rev.collided) break; // escalation collides with the judge — stop revising, exhaust below
      lastTier = rev.tier;
      revs++;
      gate = await engine(`gate:${id}#${revs + 1}`, 'Gate', laneCmd('gate', pkt(id), `--revision-note-b64 ${b64(`oracle-red revision ${revs} @ ${lastTier.model}`)}`));
    }
    let safety = 0; // still red -> exhaust to the engine's fail-closed terminal (revert+record at the ceiling)
    while (!gate.__pipeError && !gate.terminal && gate.green === false && safety++ < 4) {
      gate = await engine(`gate:${id}#x${safety}`, 'Gate', laneCmd('gate', pkt(id), ''));
    }
    if (gate.__pipeError) {
      await abortPacket(id, 'gate pipe failure');
      record(id, 'aborted', { reason: 'gate pipe failure — engine abort requested', needs_attention: true });
      continue;
    }
    if (gate.terminal) { record(id, gate.terminal, { reason: gate.summary }); continue; } // skipped/reverted/blocked — honest books already written
    if (gate.green !== true || !gate.judge_context_path) {
      await abortPacket(id, 'gate returned unrecognized state');
      record(id, 'aborted', { reason: 'unrecognized gate state', needs_attention: true });
      continue;
    }

    phase('Judge');
    let verdict = await judgeOnce(`judge:${id}#1`, `hone packet ${id}`, gate.judge_context_path);
    if (verdict.verdict === 'REVISE') {
      strikes++;
      const tier = tierFor(ladder, strikes);
      if (tier.model === JUDGE_MODEL) {
        // escalation would collide with the judge — the REVISE stands as final (fail-closed;
        // the reverted packet can be re-run through hone work with a cross-provider pair)
        log(`hone-lane [${id}]: judge REVISE, but escalation tier == judge model — recording final REVISE`);
      } else {
        log(`hone-lane [${id}]: judge REVISE — one maker revision @ ${tier.model} + re-gate + one re-judge`);
        const rev = await makerCall(`judge-revise:${id}`, `An INDEPENDENT judge reviewed your applied diff and returned REVISE. You MUST address this exact demand while keeping every rule in your brief (${emit.brief_path}) — do not weaken or edit tests or evidence commands:\n\nJUDGE REVISE: ${verdict.reasoning}`);
        if (!rev.collided) {
          lastTier = rev.tier;
          const greenGate = gate; // keep the green receipt: a no-change revision leaves the tree state (and its receipt) valid
          gate = await engine(`gate:${id}#j`, 'Gate', laneCmd('gate', pkt(id), `--revision-note-b64 ${b64(`judge REVISE: ${String(verdict.reasoning).slice(0, 400)}`)}`));
          if (gate.refused && /already green/i.test(gate.reason || '')) gate = greenGate; // engine: "proceed to land" — run wf_67898fff misread this as red
          if (gate.terminal) { record(id, gate.terminal, { reason: gate.summary }); continue; } // revision broke the oracle at the ceiling — reverted+recorded
          if (gate.green !== true) {
            await abortPacket(id, 'post-judge-revision gate not green');
            record(id, 'aborted', { reason: 'judge-revision did not re-green the gate', needs_attention: true });
            continue;
          }
          verdict = await judgeOnce(`judge:${id}#2`, `hone packet ${id}`, gate.judge_context_path); // second non-PASS is FINAL (work parity)
        }
      }
    }

    // ---- LAND: engine verifies the green receipt against THIS tree and writes the books ----
    phase('Land');
    const landRes = await engine(`land:${id}`, 'Land',
      laneCmd('land', pkt(id), `--judge-verdict-b64 ${b64(verdictJsonOf(verdict))} --usage-b64 ${b64(usageJson(lastTier.model))}`));
    if (landRes.__pipeError) { record(id, 'pipe-error-at-land', { reason: landRes.__pipeError, needs_attention: true }); continue; }
    if (landRes.refused) { record(id, 'land-refused', { reason: landRes.reason, needs_attention: true }); continue; }
    record(id, landRes.terminal || 'unknown', { commit: landRes.commit, reason: landRes.summary });
  } catch (e) {
    // per-packet fail-closed: try to close the books honestly, then move on
    try { await abortPacket(id, 'workflow exception in the orchestrating script'); }
    catch (e2) { /* the packet may be stranded in_progress — flagged below */ }
    record(id, 'workflow-error', { reason: String(e).slice(0, 300), needs_attention: true });
  }
}

return done();
