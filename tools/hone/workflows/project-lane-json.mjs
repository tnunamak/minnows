#!/usr/bin/env node
// Stdin→stdout projector for the hone-lane workflow driver. The lane CLI prints large
// JSON (inline briefs, packet YAML, receipts) that a dumb-pipe relay agent cannot copy
// verbatim reliably; the driver only reads a small field contract. This shrinks engine
// output to exactly that contract (<1KB) so the relay is trivially verbatim-copyable.
// Unparseable stdin degrades to {__pipeError} — same fail-closed shape the driver
// already handles. Never throws; always prints exactly one JSON line.

const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('end', () => {
  const s = Buffer.concat(chunks).toString('utf8');
  const a = s.indexOf('{');
  const z = s.lastIndexOf('}');
  let out;
  if (a === -1 || z <= a) {
    out = { __pipeError: `engine printed no JSON: ${s.slice(0, 200)}` };
  } else {
    try {
      const d = JSON.parse(s.slice(a, z + 1));
      const t = (v, n) => (typeof v === 'string' ? v.slice(0, n) : v);
      out = {
        ok: d.ok,
        refused: d.refused,
        terminal: d.terminal,
        green: d.green,
        reason: t(d.reason, 600),
        summary: t(d.summary, 400),
        candidate_id: d.candidate_id,
        brief_path: d.brief_path,
        revision_brief_path: d.revision_brief_path,
        judge_context_path: d.judge_context_path,
        attempts_left: d.attempts_left,
        batch_id: d.batch_id,
        commit: d.commit,
        red: d.red ? { rung: d.red.rung } : undefined,
        members: Array.isArray(d.members) ? d.members : undefined,
        results: Array.isArray(d.results)
          ? d.results.map((r) => ({ id: r.id, terminal: r.terminal, commit: r.commit, reason: t(r.reason, 200) }))
          : undefined,
        routing:
          d.routing && Array.isArray(d.routing.maker)
            ? {
                maker: d.routing.maker.map((m) => ({
                  short: m.short,
                  model: m.model,
                  effort: m.effort,
                  provider: m.provider,
                  calibrated: m.calibrated,
                })),
              }
            : undefined,
      };
      for (const k of Object.keys(out)) if (out[k] === undefined) delete out[k];
    } catch (e) {
      out = { __pipeError: `unparseable engine JSON (${String(e).split('\n')[0]}): ${s.slice(0, 200)}` };
    }
  }
  process.stdout.write(JSON.stringify(out) + '\n');
});
