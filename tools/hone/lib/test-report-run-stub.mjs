#!/usr/bin/env node
// test-report-run-stub.mjs — TEST-ONLY stand-in for `hone work` (the real one is another
// lane's deliverable; run.mjs integrates with it strictly through the CLI contract).
//
// Honors the exact contract run.mjs relies on:
//   invoked as: <this> <candidate_id> --repo PATH [--maker P] [--judge Q]
//   exit 0 = landed · nonzero = other terminal state · details live in the ledgers
//
// Behavior is scripted per candidate via env HONE_STUB_PLAN → JSON file:
//   { "<candidate_id>": { "result": "landed|reverted|skipped|blocked|crash", "sleep_ms": 400 } }
// `crash` simulates an INFRASTRUCTURE failure: exit nonzero WITHOUT writing any ledger
// line or terminal packet status (work died mid-flight). All other results write
// schema-conformant claims.jsonl / cost.jsonl lines and a terminal packet status.
//
// It also appends {candidate_id, start_ms, end_ms} to <repo>/quality/stub-trace.jsonl —
// the timestamp evidence the lane-conflict serialization test asserts on.
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parseArgs, djb2 } from './util.mjs';
import { parseYaml, stringifyYaml } from './yaml.mjs';

const flags = parseArgs(process.argv.slice(2));
const id = flags._[0];
if (!id || !flags.repo) { process.stderr.write('stub-work: usage <id> --repo PATH\n'); process.exit(64); }
const repo = resolve(String(flags.repo));
const plan = process.env.HONE_STUB_PLAN ? JSON.parse(readFileSync(process.env.HONE_STUB_PLAN, 'utf8')) : {};
const spec = plan[id] ?? { result: 'landed' };

const startMs = Date.now();
await new Promise((r) => setTimeout(r, spec.sleep_ms ?? 200));
const endMs = Date.now();
appendFileSync(join(repo, 'quality', 'stub-trace.jsonl'),
  JSON.stringify({ candidate_id: id, start_ms: startMs, end_ms: endMs, result: spec.result }) + '\n');

if (spec.result === 'crash') {
  process.stderr.write(`stub-work: simulating infrastructure crash for ${id}\n`);
  process.exit(3); // died without writing anything terminal — run.mjs must classify as infra
}

// ---- terminal packet status (what the real work owns) ----
const packetPath = join(repo, 'quality', 'packets', `${id}.yaml`);
const packet = parseYaml(readFileSync(packetPath, 'utf8'));
packet.status = spec.result;
packet.outcome.commit = spec.result === 'landed' ? `stub${djb2(id)}` : null;
packet.outcome.skip_reason = spec.result === 'skipped' ? 'stub: validated-non-defect (seeded)' : null;
packet.outcome.blocked_on = spec.result === 'blocked' ? 'stub: no oracle configured for this subsystem' : null;
packet.outcome.judge_verdict = spec.result === 'landed'
  ? 'PASS — stub judge: real concept seam, public signature stable, guards untouched'
  : spec.result === 'reverted' ? 'REJECT — stub judge: relocation without decomplecting' : null;
packet.outcome.evidence_receipts = spec.result === 'landed' ? ['stub: node --test → 12/12 pass, 0 skipped'] : [];
packet.outcome.tokens_actual = spec.result === 'landed' ? 42000 : null;
writeFileSync(packetPath, stringifyYaml(packet));

// ---- schema-conformant ledger lines ----
const created = new Date().toISOString();
const claimLines = {
  landed: [
    { claim_id: `clm-${id}-1`, created, candidate_id: id, type: 'behavior_preserved',
      statement: `refactor of ${id} preserved behavior; the packet's evidence obligations all passed`,
      evidence: [{ command: 'node --test', output_digest: djb2(`${id}-tests`) }], judge: null },
    { claim_id: `clm-${id}-2`, created, candidate_id: id, type: 'judged_design_claim',
      statement: `the extraction is a real concept seam, not relocation`,
      evidence: [], judge: { provider: flags.judge ? String(flags.judge) : 'stub-judge', verdict: 'PASS — real seam, guards untouched' } },
  ],
  reverted: [
    { claim_id: `clm-${id}-1`, created, candidate_id: id, type: 'hypothesis',
      statement: `extraction likely needs explicit context for captured state; reverted rather than land relocation`,
      evidence: [], judge: null },
  ],
  skipped: [
    { claim_id: `clm-${id}-1`, created, candidate_id: id, type: 'verified_fact',
      statement: `metric finding is a validated non-defect: flagged complexity is essential to the guard`,
      evidence: [{ command: `grep -n guard ${id}`, output_digest: djb2(`${id}-skip`) }], judge: null },
  ],
  blocked: [
    { claim_id: `clm-${id}-1`, created, candidate_id: id, type: 'uncertainty',
      statement: `no oracle exists for this subsystem; blocked until an evidence-generation packet lands`,
      evidence: [], judge: null },
  ],
}[spec.result] ?? [];
for (const c of claimLines) appendFileSync(join(repo, 'quality', 'claims.jsonl'), JSON.stringify(c) + '\n');

const costEntry = {
  job_id: `job-${id}-1`, created, candidate_id: id, workflow: packet.action,
  maker: { provider: flags.maker ? String(flags.maker) : 'stub-maker', tier: packet.maker_tier },
  judge: { provider: flags.judge ? String(flags.judge) : 'stub-judge', tier: packet.judge_tier },
  tokens_in: 1000, tokens_out: 200,
  cost_usd: spec.result === 'landed' ? 0.05 : 0.01,
  wall_time_s: (endMs - startMs) / 1000,
  landed: spec.result === 'landed',
  revision_count: spec.result === 'reverted' ? 1 : 0,
  judge_result: spec.result === 'landed' ? 'PASS' : spec.result === 'reverted' ? 'REJECT' : null,
  outcome: spec.result,
  followup_created: [],
};
appendFileSync(join(repo, 'quality', 'cost.jsonl'), JSON.stringify(costEntry) + '\n');

process.stdout.write(`stub-work: ${id} → ${spec.result}\n`);
process.exit(spec.result === 'landed' ? 0 : 2);
