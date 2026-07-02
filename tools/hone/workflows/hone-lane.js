export const meta = {
  name: 'hone-lane',
  description: 'Workflow-substrate execution lane for hone packets: in-harness Sonnet maker + different-model fresh-context judge ride the Claude Code Workflow tool, while the hone CLI (lane emit/gate/land) keeps EVERY receipt, gate, and ledger write. Books identical to hone work; execution ~5-10x cheaper for bulk classes (measured: fable-workflow-baseline-2026-07-02).',
  whenToUse: 'When executing proven-trustworthy bulk-class hone packets (extraction/certified-transform family) from a repo with quality/packets/, and the orchestrator wants in-harness economics instead of subprocess claude -p/codex exec. Pass args={packets:[ids], repo, honeDir, makerModel?, judgeModel?}. Cross-provider pairs (codex judge/maker pins) still route through `hone work`.',
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
//   hone lane emit  -> gates + in_progress + GREEN BASELINE + maker brief (engine)
//   maker agent     -> applies the brief in the repo; may run rungs for fast feedback
//                      (advisory only); MUST NOT run git write ops
//   hone lane gate  -> authoritative: re-runs every rung vs the current tree, touchset
//                      gate, receipts; green receipt bound to head_sha+diff hash
//   judge agent     -> different MODEL, fresh context; reads the engine-written
//                      judge-context.json (packet + evidence + diff); schema verdict
//   hone lane land  -> refuses without a matching green gate receipt; records verdict
//                      + explicit usage into the ledgers; commits or reverts
// Agent claims are never trusted: a lying pipe/maker/judge cannot forge a land —
// land itself is the engine and re-verifies tree state + receipts on disk.
//
// Prior art: .claude/workflows/refactor-loop.js (34 zero-regression lands overnight,
// maker agent -> independent fresh checker agent). Workflow scripts cannot import/
// require, so engine commands run through a MINIMAL haiku pipe agent that executes
// one command and echoes its JSON stdout verbatim (a dumb pipe, no judgment — the
// values stay the ENGINE's). If the harness ever exposes a native shell primitive,
// the pipe collapses to it with no design change.
// ---------------------------------------------------------------------------

const REPO = args && args.repo;
const HONE_DIR = args && args.honeDir;
const PACKETS = (args && args.packets) || [];
const MAKER_MODEL = (args && args.makerModel) || 'sonnet';
const JUDGE_MODEL = (args && args.judgeModel) || 'opus';
if (!REPO || !HONE_DIR || !PACKETS.length) {
  log('hone-lane: args {packets:[ids], repo, honeDir} are required. Nothing to do.');
  return { error: 'missing-args', results: [] };
}
if (MAKER_MODEL === JUDGE_MODEL) {
  // structural maker != judge, enforced before any work; hone lane land re-refuses.
  log(`hone-lane: makerModel == judgeModel ('${MAKER_MODEL}') — refusing (non-negotiable #1).`);
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
    `Run exactly this ONE command with Bash and then output the command's stdout VERBATIM as your ENTIRE final message — no commentary, no code fences, no reformatting, even if the command exits non-zero (lane commands print their result JSON on failure too):\n\n${cmd}\n\nIf stdout is empty, output the single word EMPTY.`,
    { phase: phaseName, label, model: 'haiku' }
  );
  const s = String(raw || '');
  const a = s.indexOf('{'), z = s.lastIndexOf('}');
  if (a === -1 || z <= a) return { __pipeError: `no JSON in pipe output: ${s.slice(0, 200)}` };
  try { return JSON.parse(s.slice(a, z + 1)); }
  catch (e) { return { __pipeError: `unparseable pipe JSON (${String(e).split('\n')[0]}): ${s.slice(0, 200)}` }; }
}

const laneCmd = (sub, id, extra = '') =>
  `cd ${REPO} && node ${HONE} lane ${sub} --packet ${id} --repo ${REPO} ${extra} 2>/dev/null`;

// in-harness maker preamble: layered ON TOP of the engine's binding brief because a
// workflow agent (unlike the Bash-denied subprocess maker) has shell access.
const MAKER_PREAMBLE = (id) => `You are the MAKER for hone packet ${id}. The target repository is ${REPO} — every path in the brief below is relative to it; edit files there.
IN-HARNESS ADDENDUM (binding, on top of the brief's rules):
- You have shell access. You MUST NOT run ANY git write operation (add/commit/stash/checkout/reset/rebase/branch). The engine detects a moved HEAD and voids the whole run as blocked(foreign-commit).
- You MAY run the packet's evidence_required commands (cwd ${REPO}) for fast feedback while you work. Your runs are ADVISORY ONLY — the engine re-runs everything itself as the only receipts. Do not run anything else destructive.
- Do not create, edit, or delete anything under ${REPO}/quality/.
When done, reply with the short summary the brief asks for (or the exact HONE-VERDICT line when no edit is warranted).`;

const JUDGE_PROMPT = (id, contextPath) => `You are the INDEPENDENT JUDGE in a repo-quality engine — a fresh context that did NOT write this change. A separate maker produced a diff for work packet ${id}; your job is to certify it or refuse it. Makers overclaim — be adversarial.

Read the file ${contextPath} (written by the deterministic engine, not by any agent). It contains: packet_yaml (the contract), evidence (engine-run receipt digests + bounded real rung output), diff (the change under judgment), receipts.

Rules of judgment:
- The packet is the contract: its action, not_allowed, and evidence_required bind the maker.
- Evidence policy: you review evidence, you never replace it. Judge only from what is in front of you; if the supplied evidence does not cover the property at risk for this change class, that alone justifies REVISE or REJECT.
- Behavior preservation: for preserve_refactor packets, ANY observable behavior change (boundary/edge conditions, operator changes, ||/??/?. semantics, throw/error-string/return shapes) means REJECT unless the packet explicitly allows it.
- Reject relocation: moving a code blob behind a new name WITHOUT making captured context explicit or reducing real complexity is not decomplecting, even when green.
- Compare removed code to added code line by line before trusting any summary.
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

const usageJson = () => JSON.stringify([
  { role: 'maker', provider: 'claude', model: MAKER_MODEL },
  { role: 'judge', provider: 'claude', model: JUDGE_MODEL },
]); // token counts: the harness does not expose per-agent usage to workflow scripts —
    // recorded as honest nulls, NEVER fabricated numbers (SUBSTRATE.md "Cost accounting").

const results = [];
const record = (id, outcome, detail) => {
  results.push({ packet: id, outcome, ...(detail || {}) });
  log(`hone-lane [${id}]: ${outcome}${detail && detail.commit ? ` ${String(detail.commit).slice(0, 12)}` : ''}${detail && detail.reason ? ` — ${String(detail.reason).slice(0, 140)}` : ''}`);
};

for (const id of PACKETS) {
  try {
    // ---- EMIT: engine gates + green baseline + brief -----------------------
    phase('Emit');
    const emit = await engine(`emit:${id}`, 'Emit', laneCmd('emit', id, `--maker claude --judge claude`));
    if (emit.__pipeError) { record(id, 'pipe-error-at-emit', { reason: emit.__pipeError, needs_attention: true }); continue; }
    if (emit.refused) { record(id, 'refused', { reason: emit.reason }); continue; }
    if (emit.terminal) { record(id, emit.terminal, { reason: emit.summary }); continue; } // e.g. blocked(red baseline)
    if (!emit.ok || !emit.brief_path) {
      record(id, 'emit-unrecognized', { reason: JSON.stringify(emit).slice(0, 200), needs_attention: true });
      continue;
    }

    // ---- MAKE: in-harness maker (Sonnet-tier default, the measured value knee) ----
    phase('Make');
    const makerReply = await agent(
      `${MAKER_PREAMBLE(id)}\n\nRead your full work-packet brief at ${emit.brief_path} and execute it exactly.`,
      { phase: 'Make', label: `make:${id}`, model: MAKER_MODEL, effort: 'high' }
    );

    // ---- GATE: authoritative engine oracle (attempt 1; ≤1 oracle-revision, work parity) ----
    phase('Gate');
    const summaryB64 = b64(String(makerReply || '').slice(-2000));
    let gate = await engine(`gate:${id}#1`, 'Gate', laneCmd('gate', id, `--maker-summary-b64 ${summaryB64}`));
    if (!gate.__pipeError && !gate.terminal && gate.green === false && gate.attempts_left > 0) {
      log(`hone-lane [${id}]: oracle RED at '${gate.red && gate.red.rung}' — one maker revision cycle`);
      await agent(
        `${MAKER_PREAMBLE(id)}\n\nYour previous attempt FAILED the deterministic oracle. Read the full revision brief at ${gate.revision_brief_path} (it contains the failure, the current diff, and every binding rule) and fix the failure.`,
        { phase: 'Make', label: `revise:${id}`, model: MAKER_MODEL, effort: 'high' }
      );
      gate = await engine(`gate:${id}#2`, 'Gate', laneCmd('gate', id, `--revision-note-b64 ${b64(`oracle-red revision: ${((gate.red && gate.red.rung) || '?')}`)}`));
    }
    // still red and not terminal -> exhaust to the engine's fail-closed terminal
    // (each further red gate call marches to the attempt ceiling, which reverts + records)
    let safety = 0;
    while (!gate.__pipeError && !gate.terminal && gate.green === false && safety++ < 4) {
      gate = await engine(`gate:${id}#x${safety}`, 'Gate', laneCmd('gate', id, ''));
    }
    if (gate.__pipeError) {
      await engine(`abort:${id}`, 'Land', laneCmd('land', id, `--abort --reason 'gate pipe failure' --usage-b64 ${b64(usageJson())}`));
      record(id, 'aborted', { reason: 'gate pipe failure — engine abort requested', needs_attention: true });
      continue;
    }
    if (gate.terminal) { record(id, gate.terminal, { reason: gate.summary }); continue; } // skipped/reverted/blocked — honest books already written
    if (gate.green !== true || !gate.judge_context_path) {
      await engine(`abort:${id}`, 'Land', laneCmd('land', id, `--abort --reason 'gate returned unrecognized state' --usage-b64 ${b64(usageJson())}`));
      record(id, 'aborted', { reason: 'unrecognized gate state', needs_attention: true });
      continue;
    }

    // ---- JUDGE: different model, fresh context, engine-written record (≤1 REVISE cycle) ----
    phase('Judge');
    const judgeOnce = (label) => agent(JUDGE_PROMPT(id, gate.judge_context_path), {
      schema: VERDICT_SCHEMA, phase: 'Judge', label, model: JUDGE_MODEL, effort: 'high',
    });
    let verdict = await judgeOnce(`judge:${id}#1`);
    if (verdict && verdict.verdict === 'REVISE') {
      log(`hone-lane [${id}]: judge REVISE — one maker revision + re-gate + one re-judge`);
      await agent(
        `${MAKER_PREAMBLE(id)}\n\nAn INDEPENDENT judge reviewed your applied diff and returned REVISE. You MUST address this exact demand while keeping every rule in your brief (${emit.brief_path}) — do not weaken or edit tests or evidence commands:\n\nJUDGE REVISE: ${verdict.reasoning}`,
        { phase: 'Make', label: `judge-revise:${id}`, model: MAKER_MODEL, effort: 'high' }
      );
      gate = await engine(`gate:${id}#j`, 'Gate', laneCmd('gate', id, `--revision-note-b64 ${b64(`judge REVISE: ${String(verdict.reasoning).slice(0, 400)}`)}`));
      if (gate.terminal) { record(id, gate.terminal, { reason: gate.summary }); continue; } // revision broke the oracle at the ceiling — reverted+recorded
      if (gate.green !== true) {
        await engine(`abort:${id}`, 'Land', laneCmd('land', id, `--abort --reason 'post-judge-revision gate not green' --usage-b64 ${b64(usageJson())}`));
        record(id, 'aborted', { reason: 'judge-revision did not re-green the gate', needs_attention: true });
        continue;
      }
      verdict = await judgeOnce(`judge:${id}#2`); // second non-PASS is FINAL (work parity)
    }
    if (!verdict || !verdict.verdict) {
      // fail closed: an unparseable judge can never PASS; treat as final REVISE (provider.mjs parity)
      verdict = { verdict: 'REVISE', reasoning: 'judge output unparseable — fail-closed (never land on garbage)', confidence: 0 };
    }

    // ---- LAND: engine verifies the green receipt against THIS tree and writes the books ----
    phase('Land');
    const verdictJson = JSON.stringify({
      verdict: verdict.verdict,
      reasoning: String(verdict.reasoning || '(none)'),
      confidence: typeof verdict.confidence === 'number' ? Math.min(1, Math.max(0, verdict.confidence)) : null,
      judge: { provider: 'claude', model: JUDGE_MODEL },
    });
    const landRes = await engine(`land:${id}`, 'Land',
      laneCmd('land', id, `--judge-verdict-b64 ${b64(verdictJson)} --usage-b64 ${b64(usageJson())}`));
    if (landRes.__pipeError) { record(id, 'pipe-error-at-land', { reason: landRes.__pipeError, needs_attention: true }); continue; }
    if (landRes.refused) { record(id, 'land-refused', { reason: landRes.reason, needs_attention: true }); continue; }
    record(id, landRes.terminal || 'unknown', { commit: landRes.commit, reason: landRes.summary });
  } catch (e) {
    // per-packet fail-closed: try to close the books honestly, then move on
    try {
      await engine(`abort:${id}`, 'Land', laneCmd('land', id, `--abort --reason 'workflow exception in the orchestrating script' --usage-b64 ${b64(usageJson())}`));
    } catch (e2) { /* the packet may be stranded in_progress — flagged below */ }
    record(id, 'workflow-error', { reason: String(e).slice(0, 300), needs_attention: true });
  }
}


const tally = {};
for (const r of results) tally[r.outcome] = (tally[r.outcome] || 0) + 1;
log(`hone-lane: done. ${JSON.stringify(tally)} — books in ${REPO}/quality/ (claims.jsonl, cost.jsonl, packets/, receipts/). Items flagged needs_attention require the orchestrator (possibly stranded in_progress packets: hone lane land --abort, or hone reset).`);
return { results, tally };
