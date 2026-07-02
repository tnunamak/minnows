// reset.mjs — `hone reset <id>`: deliberately reopen a terminal packet (owner decision).
//
// `work` refuses non-pending packets by design ("never re-litigate a persisted outcome —
// reset status to pending only by owner decision"); this verb IS that owner decision,
// recorded durably: every reset appends {at, from_status, reason} to the packet's
// `resets` list, so a reopened packet carries the fact that it was reopened forever.
// The prior outcome block is preserved until the next terminal write; execution-time
// provider pins (set by the previous run, not by plan) are cleared so the reopened
// packet can be worked by any maker ≠ judge pair.
//
// Fail-closed refusals (exit 2, packet unchanged): unknown id, already pending,
// unsupported --to target, and `landed` without --force — resetting a landed packet
// would disown a commit that exists in history, so it needs the explicit override.
import { resolve } from 'node:path';
import { loadPacket, writePacket } from './packet-io.mjs';

export async function runReset(flags) {
  const id = flags._?.[0];
  if (!id || typeof id !== 'string') {
    throw new Error('usage: hone reset <candidate-id> --repo PATH [--to pending] [--force] [--reason TEXT]\n(put flags AFTER the id; bare flags greedily consume a following bare word)');
  }
  const res = executeReset({
    id,
    repoRoot: resolve(flags.repo || '.'),
    to: String(flags.to ?? 'pending'),
    force: !!flags.force,
    reason: typeof flags.reason === 'string' && flags.reason.trim() ? flags.reason.trim() : null,
  });
  process.stdout.write(res.summary + '\n');
  process.exitCode = res.exitCode;
}

/** pure core (self-tested): returns {outcome, exitCode, summary}; writes only on success. */
export function executeReset({ id, repoRoot, to = 'pending', force = false, reason = null }) {
  const refuse = (why) => ({
    outcome: 'refused', exitCode: 2,
    summary: `hone reset — ${id}: REFUSED (packet unchanged)\n  ${why}`,
  });
  if (to !== 'pending') return refuse(`--to '${to}' unsupported — the only reset target is 'pending'`);

  let loaded;
  try { loaded = loadPacket(repoRoot, id); }
  catch (e) { return refuse(e.message); }
  const { packet, path } = loaded;
  const from = packet.status;

  if (from === 'pending') return refuse('packet is already pending — nothing to reset');
  if (from === 'landed' && !force) {
    return refuse(`packet is landed (commit ${packet.outcome?.commit ?? 'unknown'}) — resetting would disown a commit that exists in history; pass --force only if that commit was reverted/removed`);
  }

  const entry = {
    at: new Date().toISOString(),
    from_status: from,
    reason: reason || `owner reset via hone reset (${from} → pending)`,
  };
  packet.resets = [...(packet.resets ?? []), entry];
  packet.status = 'pending';
  packet.maker_provider = null; // execution pins from the prior run, not plan intent
  packet.judge_provider = null;
  writePacket(path, packet); // schema-validated + round-trip-verified; crashes before corrupting

  return {
    outcome: 'reset', exitCode: 0,
    summary: [
      `hone reset — ${id}: ${from} → pending`,
      `  reset #${packet.resets.length} recorded (${entry.at}): ${entry.reason}`,
      `  provider pins cleared; prior outcome preserved until the next terminal write`,
      `  packet: ${path}`,
    ].join('\n'),
  };
}
