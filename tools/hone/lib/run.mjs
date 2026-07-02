// run.mjs — `hone run`: the plan→work loop with conflict-aware lanes.
//
// Selects up to K executable packets (status=pending, execution_gate=autonomous,
// depends_on all landed) in rank order and executes each by spawning
// `hone work <id> --repo ... [--maker P] [--judge Q]` as a SUBPROCESS. The subprocess
// boundary is the whole contract: exit 0 = landed, nonzero = some other terminal
// state; the details live in the ledgers work writes, never in stdout parsing.
//
// LANES v1 LIMITATION (deliberate): all lanes share ONE git worktree. That is only
// safe because (a) two packets run concurrently ONLY when their touchsets are
// provably disjoint (any shared file, or either packet touching a file under the
// other's subsystem, serializes — and a missing/empty touchset conflicts with
// EVERYTHING), and (b) `work` commits per-packet. Default lanes=2; per-lane
// worktrees are v2. Fail-safe: on any doubt, packets serialize (lanes degrade to 1).
//
// INFRASTRUCTURE failure vs honest outcome: after each work exit we re-read the
// packet from disk. Nonzero exit + terminal packet status (reverted/skipped/blocked)
// is an HONEST negative result — first-class knowledge, the loop continues. Nonzero
// exit with the packet still pending/in_progress (or unreadable) means work CRASHED;
// 2 consecutive crashes stop that lane, all lanes stopped stops the run.
//
// Testability: `--work-cmd 'node .../stub.mjs'` (or env HONE_WORK_CMD) overrides the
// spawned executable so the loop is provable offline before the real `hone work`
// lands. The override is split on whitespace — no spaces in the override path.
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildContext, HONE_ROOT } from './profile.mjs';
import { parseYaml } from './yaml.mjs';
import { readPacketPool, runReport } from './report.mjs';
import { costPath } from './ledger.mjs';
import {
  packetPriority, readAgendaArtifacts, agedNotChosenIds, orderExecutableByAgenda,
  applyAgendaFloor, appendBatchRecord,
} from './agenda-consume.mjs';

export { packetPriority }; // moved to agenda-consume.mjs (report renders formula-vs-agenda rank); re-exported for compat

const TERMINAL_NONLANDED = new Set(['reverted', 'skipped', 'blocked']);
const MAX_CONSECUTIVE_INFRA = 2; // per lane

// ---------------------------------------------------------------- scheduling

/**
 * conservative touchset conflict test (exported for the self-test):
 * true (= must serialize) when any file is shared, when either packet touches a file
 * under the other's subsystem, when the subsystems are equal, or when either
 * touchset/subsystem is missing or empty (fail-safe: doubt serializes).
 */
export function packetsConflict(a, b) {
  const ta = a.touchset, tb = b.touchset;
  if (!Array.isArray(ta) || !ta.length || !Array.isArray(tb) || !tb.length) return true;
  if (typeof a.subsystem !== 'string' || !a.subsystem || typeof b.subsystem !== 'string' || !b.subsystem) return true;
  if (ta.some((f) => tb.includes(f))) return true;
  if (a.subsystem === b.subsystem) return true;
  const under = (file, sub) => file === sub || file.startsWith(sub + '/');
  if (ta.some((f) => under(f, b.subsystem))) return true;
  if (tb.some((f) => under(f, a.subsystem))) return true;
  return false;
}

function selectExecutable(packets, warn) {
  const byId = new Map(packets.map((p) => [p.candidate_id, p]));
  const executable = [];
  for (const p of packets) {
    if (p.status !== 'pending') continue;
    if (p.execution_gate !== 'autonomous') {
      warn(`not executable: ${p.candidate_id} — execution_gate=${JSON.stringify(p.execution_gate ?? null)} (run refuses owner_ratify and ungated packets, fail-closed)`);
      continue;
    }
    const deps = Array.isArray(p.depends_on) ? p.depends_on : [];
    const unmet = deps.filter((d) => byId.get(d)?.status !== 'landed');
    if (unmet.length) {
      warn(`not executable: ${p.candidate_id} — depends_on not landed: ${unmet.join(', ')}`);
      continue;
    }
    executable.push(p);
  }
  executable.sort((a, b) => packetPriority(b) - packetPriority(a) || a.candidate_id.localeCompare(b.candidate_id));
  return executable;
}

// ---------------------------------------------------------------- work subprocess

function workArgv(id, repoRoot, { workCmd, maker, judge }) {
  const parts = workCmd
    ? String(workCmd).split(/\s+/).filter(Boolean)
    : [process.execPath, join(HONE_ROOT, 'hone'), 'work'];
  const args = [...parts.slice(1), id, '--repo', repoRoot];
  if (maker) args.push('--maker', String(maker));
  if (judge) args.push('--judge', String(judge));
  return { cmd: parts[0], args };
}

function pipeLines(stream, tag, sink) {
  let buf = '';
  stream.on('data', (d) => {
    buf += d.toString();
    let nl;
    while ((nl = buf.indexOf('\n')) !== -1) {
      sink(`${tag} ${buf.slice(0, nl)}\n`);
      buf = buf.slice(nl + 1);
    }
  });
  stream.on('end', () => { if (buf.trim()) sink(`${tag} ${buf}\n`); });
}

function spawnWork(packet, laneIdx, repoRoot, opts) {
  const { cmd, args } = workArgv(packet.candidate_id, repoRoot, opts);
  const tag = `[lane${laneIdx + 1} ${packet.candidate_id}]`;
  const startedAt = Date.now();
  const child = spawn(cmd, args, { cwd: repoRoot, stdio: ['ignore', 'pipe', 'pipe'], detached: true });
  pipeLines(child.stdout, tag, (s) => process.stdout.write(s));
  pipeLines(child.stderr, `${tag} !`, (s) => process.stderr.write(s));
  const timeoutMs = Number(process.env.HONE_WORK_TIMEOUT_MS ?? 0);
  let timedOut = false, timer = null;
  if (timeoutMs > 0) {
    timer = setTimeout(() => {
      timedOut = true;
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already gone */ }
    }, timeoutMs);
    timer.unref?.();
  }
  return new Promise((resolvePromise) => {
    child.on('error', (e) => {
      if (timer) clearTimeout(timer);
      resolvePromise({ laneIdx, packet, exitCode: null, spawnError: e.message, timedOut, wallS: (Date.now() - startedAt) / 1000 });
    });
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      resolvePromise({ laneIdx, packet, exitCode: code, spawnError: null, timedOut, wallS: (Date.now() - startedAt) / 1000 });
    });
  });
}

function reloadPacketStatus(fileById, id) {
  const path = fileById.get(id);
  if (!path) return null;
  try {
    const doc = parseYaml(readFileSync(path, 'utf8'));
    return typeof doc?.status === 'string' ? doc.status : null;
  } catch { return null; } // unreadable after work ran = treat as crash evidence
}

/** classify a finished work subprocess → { kind: landed|honest|infra, status, note } */
function classifyResult(res, fileById) {
  const status = reloadPacketStatus(fileById, res.packet.candidate_id);
  if (res.spawnError) return { kind: 'infra', status, note: `work could not be spawned: ${res.spawnError}` };
  if (res.timedOut) return { kind: 'infra', status, note: `work exceeded HONE_WORK_TIMEOUT_MS and was killed` };
  if (res.exitCode === 0) {
    if (status !== 'landed') {
      return { kind: 'landed', status, note: `CONTRACT WARNING: exit 0 but packet status is ${JSON.stringify(status)} (expected landed) — ledgers are the truth, report will show it` };
    }
    return { kind: 'landed', status, note: null };
  }
  if (status && TERMINAL_NONLANDED.has(status)) {
    return { kind: 'honest', status, note: null }; // honest negative result — first-class knowledge
  }
  return { kind: 'infra', status, note: `work exited ${res.exitCode} with packet status ${JSON.stringify(status)} (not terminal) — work crashed` };
}

// ---------------------------------------------------------------- the loop

export async function runLoop(flags) {
  const ctx = buildContext(flags.repo);
  const out = (s) => process.stdout.write(s + '\n');
  const warn = (s) => process.stderr.write(`WARN: ${s}\n`);

  if (flags.budget !== undefined) {
    throw new Error('--budget is not implemented in v1 — use --n K (packet count)');
  }
  const n = Math.max(1, Number(flags.n ?? 1));
  const lanes = Math.max(1, Number(flags.lanes ?? 2));
  if (!Number.isFinite(n) || !Number.isFinite(lanes)) throw new Error('--n and --lanes must be numbers');
  const maker = flags.maker ? String(flags.maker) : null;
  const judge = flags.judge ? String(flags.judge) : null;
  if (maker && judge && maker === judge) {
    throw new Error(`maker and judge MUST differ (maker ≠ judge is structural, SPEC non-negotiable #1), both are '${maker}'`);
  }
  const workCmd = flags['work-cmd'] ? String(flags['work-cmd']) : (process.env.HONE_WORK_CMD || null);

  if (flags['plan-first']) {
    const { runPlan } = await import('./plan.mjs');
    await runPlan({ repo: ctx.repoRoot });
  }

  const pool = readPacketPool(ctx.repoRoot);
  for (const e of pool.errors) warn(`malformed packet excluded from pool: ${e}`);
  let executable = selectExecutable(pool.packets, warn);

  // AGENDA consumption (AGENDA-DESIGN.md v2): when quality/AGENDA.json exists, selection
  // order = agenda rank (verified-first by construction) behind the deterministic floor the
  // agenda CANNOT displace — (a) negative controls/seeded traps, (b) in-flight campaigns
  // before new ones, (c) aged NOT-chosen minimum. No AGENDA.json → behavior unchanged.
  const agendaArts = readAgendaArtifacts(ctx.repoRoot);
  for (const e of agendaArts.errors) warn(`agenda artifact problem: ${e}`);
  const agenda = agendaArts.agenda;
  let queue;
  if (agenda) {
    executable = orderExecutableByAgenda(executable, pool.packets, agenda);
    queue = executable.slice(0, n);
    const { queue: floored, notes } = applyAgendaFloor(queue, executable, { n, agedIds: agedNotChosenIds(agendaArts.notChosen) });
    queue = floored;
    out(`agenda: ${agenda.agenda_id} governs selection order (verified-first); deterministic floor applied${notes.length ? '' : ' (no floor insertions needed)'}`);
    for (const note of notes) out(`  floor: ${note}`);
  } else {
    queue = executable.slice(0, n);
  }
  const costLinesBefore = existsSync(costPath(ctx.repoRoot))
    ? readFileSync(costPath(ctx.repoRoot), 'utf8').split('\n').filter((l) => l.trim()).length : 0;
  out(`hone run — repo ${ctx.repoRoot}`);
  out(`pool: ${pool.packets.length} packets · ${executable.length} executable · selected ${queue.length} (n=${n}) · lanes=${lanes}${workCmd ? ` · work-cmd override: ${workCmd}` : ''}`);
  for (const [i, p] of queue.entries()) {
    out(`  ${String(i + 1).padStart(2)}. ${p.candidate_id}  [${p.action}×${p.proof_class}]  priority=${packetPriority(p).toFixed(3)}  touchset: ${(p.touchset || []).join(', ') || '(MISSING — serializes)'}`);
  }

  const laneState = Array.from({ length: lanes }, () => ({ stopped: false, consecInfra: 0 }));
  const inFlight = new Map(); // laneIdx → promise
  const runningPackets = new Map(); // laneIdx → packet
  const summary = { selected: queue.length, landed: 0, reverted: 0, skipped: 0, blocked: 0, infra: 0, unexecuted: 0, executed: [] };

  while (true) {
    // fill free, unstopped lanes with the highest-ranked non-conflicting queued packet
    for (let lane = 0; lane < lanes; lane++) {
      if (laneState[lane].stopped || inFlight.has(lane) || !queue.length) continue;
      const running = [...runningPackets.values()];
      const idx = queue.findIndex((p) => running.every((r) => !packetsConflict(p, r)));
      if (idx === -1) continue; // everything queued conflicts with something running — wait
      const packet = queue.splice(idx, 1)[0];
      out(`[lane${lane + 1}] start ${packet.candidate_id}`);
      runningPackets.set(lane, packet);
      inFlight.set(lane, spawnWork(packet, lane, ctx.repoRoot, { workCmd, maker, judge }));
    }

    if (!inFlight.size) break; // queue empty, or every remaining lane is stopped

    const res = await Promise.race(inFlight.values());
    inFlight.delete(res.laneIdx);
    runningPackets.delete(res.laneIdx);

    const cls = classifyResult(res, pool.fileById);
    if (cls.note) warn(`[lane${res.laneIdx + 1}] ${res.packet.candidate_id}: ${cls.note}`);
    summary.executed.push({ id: res.packet.candidate_id, kind: cls.kind, status: cls.status, exitCode: res.exitCode, wallS: res.wallS });
    if (cls.kind === 'infra') {
      summary.infra++;
      laneState[res.laneIdx].consecInfra++;
      out(`[lane${res.laneIdx + 1}] INFRA-FAILURE ${res.packet.candidate_id} (exit ${res.exitCode}, ${res.wallS.toFixed(1)}s) — consecutive in lane: ${laneState[res.laneIdx].consecInfra}`);
      if (laneState[res.laneIdx].consecInfra >= MAX_CONSECUTIVE_INFRA) {
        laneState[res.laneIdx].stopped = true;
        out(`[lane${res.laneIdx + 1}] STOPPED after ${MAX_CONSECUTIVE_INFRA} consecutive infrastructure failures`);
      }
    } else {
      laneState[res.laneIdx].consecInfra = 0;
      summary[cls.status] = (summary[cls.status] || 0) + 1;
      out(`[lane${res.laneIdx + 1}] ${cls.status} ${res.packet.candidate_id} (exit ${res.exitCode}, ${res.wallS.toFixed(1)}s)`);
    }
    // keep the pool's status view current so later depends_on checks see fresh facts
    if (cls.status) res.packet.status = cls.status;
  }

  summary.unexecuted = queue.length;
  if (queue.length) {
    out(`unexecuted (all lanes stopped before the queue drained): ${queue.map((p) => p.candidate_id).join(', ')}`);
  }
  out(`hone run — done: landed ${summary.landed} · reverted ${summary.reverted} · skipped ${summary.skipped} · blocked ${summary.blocked} · infra-failures ${summary.infra} · unexecuted ${summary.unexecuted}`);

  // batch record (agenda runs only): the input for the report's fail-loud divergence
  // thresholds (named-target starvation / class allocation outside the doctrine band).
  if (agenda) {
    const costRows = existsSync(costPath(ctx.repoRoot))
      ? readFileSync(costPath(ctx.repoRoot), 'utf8').split('\n').filter((l) => l.trim()).slice(costLinesBefore)
        .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
      : [];
    const record = appendBatchRecord(ctx.repoRoot, {
      agenda, profileAgenda: ctx.profile.agenda ?? {}, poolPackets: pool.packets,
      executed: summary.executed, costRows,
    });
    out(`batch record: ${record.batch_id} → quality/agendas/batches.jsonl (spend $${record.spend_usd} · by class ${JSON.stringify(record.spend_by_class)})`);
  }

  // finish by compiling the report — the ledgers, not this loop's printout, are the record
  summary.reportPath = await runReport({ repo: ctx.repoRoot, out: flags.out });
  return summary;
}
