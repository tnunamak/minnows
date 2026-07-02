// calibrate.mjs — `hone calibrate --model X --replay N`: onboard/refresh a model by
// replaying already-LANDED work orders from this repo's own ledger (the ledger IS the
// benchmark — no external eval needed). Ground truth per replay = the landed commit's
// diff + its green gates; a candidate model re-executes the packet against the commit's
// parent in a scratch worktree and is scored on pass-rate / $ / effort sensitivity.
//
// v1 is a STUB WITH REAL MECHANICS (per the L1 amendment's build scope): it proves the
// whole replay seam — ledger -> landed ground truth -> scratch worktree -> report under
// quality/reports/ — without any model invocation, and its report is EXPLICITLY marked
// insufficient for routing eligibility. The eligibility gate is enforced by
// routing.mjs selectAgent: a models.json entry with calibration:null is never routed
// (fail-closed; override requires allowUncalibrated + a ledger note).

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { mkdtempSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { parseYaml } from './yaml.mjs';
import { loadRegistry, loadRouting, resolveRoutingClass } from './routing.mjs';

export async function runCalibrate(flags) {
  const res = executeCalibrate({
    model: typeof flags.model === 'string' ? flags.model : null,
    replay: Number(flags.replay ?? 0),
    repoRoot: resolve(flags.repo || '.'),
    log: (s) => process.stderr.write(s + '\n'),
  });
  process.stdout.write(JSON.stringify(res.json, null, 2) + '\n');
  process.exitCode = res.exitCode;
}

const refuse = (reason) => ({ exitCode: 2, json: { ok: false, refused: true, reason } });

export function executeCalibrate({ model, replay, repoRoot, log = () => {} }) {
  if (!model) return refuse('usage: hone calibrate --model <models.json name> --replay N --repo PATH');
  let registry;
  try { registry = loadRegistry(); }
  catch (e) { return refuse(`models.json unavailable: ${e.message}`); }
  const entry = registry.models[model];
  if (!entry) return refuse(`unknown model '${model}' — not a models.json registry name (known: ${Object.keys(registry.models).join(', ')})`);
  if (!Number.isInteger(replay) || replay < 1) return refuse('--replay must be an integer >= 1');

  const git = (args, cwd = repoRoot) => spawnSync('git', args, { cwd, encoding: 'utf8' });
  if (git(['rev-parse', '--show-toplevel']).status !== 0) return refuse(`--repo is not inside a git repository: ${repoRoot}`);

  // ---- the replay set: most recent LANDED orders from the packet pool (ledger-backed) ----
  const packetsDir = join(repoRoot, 'quality', 'packets');
  if (!existsSync(packetsDir)) return refuse(`no packet pool at ${packetsDir} — nothing landed to replay against`);
  let policy = null;
  try { policy = loadRouting(undefined, registry); } catch { /* class column degrades to null */ }
  const landed = [];
  for (const f of readdirSync(packetsDir)) {
    if (!f.endsWith('.yaml')) continue;
    try {
      const p = parseYaml(readFileSync(join(packetsDir, f), 'utf8'));
      if (p?.status === 'landed' && typeof p.outcome?.commit === 'string' && p.outcome.commit.length === 40) {
        landed.push(p);
      }
    } catch { /* foreign yaml */ }
  }
  if (!landed.length) return refuse('no landed packets with commits in the pool — calibration replays ground truth; land something first');
  landed.sort((a, b) => String(b.created).localeCompare(String(a.created)));
  const set = landed.slice(0, replay);

  // ---- ground-truth extraction + scratch-worktree seam proof (no model calls in v1) ----
  const replaySet = [];
  for (const p of set) {
    const sha = p.outcome.commit;
    const item = {
      candidate_id: p.candidate_id, commit: sha, parent: null,
      action: p.action, proof_class: p.proof_class,
      routing_class: policy ? resolveRoutingClass(p, policy) : null,
      replayable: false, note: null,
    };
    if (git(['cat-file', '-e', `${sha}^{commit}`]).status !== 0) {
      item.note = 'landed commit not found in this repository (shallow clone? rewritten history?)';
      replaySet.push(item);
      continue;
    }
    const parent = git(['rev-parse', `${sha}~1`]);
    if (parent.status !== 0) {
      item.note = 'commit has no parent (root commit) — cannot replay';
      replaySet.push(item);
      continue;
    }
    item.parent = parent.stdout.trim();
    // prove the scratch-worktree seam: add at the parent, verify, remove. The real
    // replay arm re-runs the packet here with the candidate model + gates it against
    // the ground truth; v1 stops at proving the mechanics.
    const scratch = mkdtempSync(join(tmpdir(), `hone-calibrate-${p.candidate_id.slice(0, 16)}-`));
    rmSync(scratch, { recursive: true, force: true }); // git worktree add wants to create it
    const add = git(['worktree', 'add', '--detach', scratch, item.parent]);
    if (add.status !== 0) {
      item.note = `worktree add failed: ${(add.stderr || '').slice(0, 200)}`;
      replaySet.push(item);
      continue;
    }
    const ok = existsSync(scratch) && git(['rev-parse', 'HEAD'], scratch).stdout.trim() === item.parent;
    git(['worktree', 'remove', '--force', scratch]);
    item.replayable = ok;
    item.note = ok ? 'scratch worktree at parent verified (seam green)' : 'scratch worktree HEAD mismatch';
    replaySet.push(item);
    log(`  replay seam ${p.candidate_id}: ${item.note}`);
  }

  // ---- report (quality/reports/ — the calibration artifact the registry gate reads) ----
  const reportsDir = join(repoRoot, 'quality', 'reports');
  mkdirSync(reportsDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const reportPath = join(reportsDir, `calibration-${model}-${stamp}.json`);
  const report = {
    model,
    registry_snapshot: { provider: entry.provider, id: entry.id, tier_rank: entry.tier_rank, quota_pool: entry.quota_pool, efforts: entry.efforts, currently_calibrated: entry.calibration !== null },
    requested_replays: replay,
    replay_set: replaySet,
    measured: { pass_rate: null, usd_per_land: null, quota_pts_per_land: null, effort_sensitivity: null },
    status: 'stub-v1',
    honest_note: 'NO model invocations were performed. This report proves the replay seam only (ledger -> landed ground-truth commits -> scratch worktree) and is NOT sufficient for routing eligibility. A real calibration fills `measured` by re-executing each packet against its parent tree with the candidate model and gating against the landed diff + green rungs.',
    next: `after a real replay run, set models.json models.${model}.calibration = {type: 'ledger-replay', source: '${reportPath}', date: '<date>'} to make the model routing-eligible`,
    created: new Date().toISOString(),
  };
  writeFileSync(reportPath, JSON.stringify(report, null, 2));
  return {
    exitCode: 0,
    json: {
      ok: true, model, report_path: reportPath,
      replayable: replaySet.filter((r) => r.replayable).length,
      of: replaySet.length,
      status: 'stub-v1',
      summary: `hone calibrate — ${model}: replay seam proven for ${replaySet.filter((r) => r.replayable).length}/${replaySet.length} landed order(s); report ${reportPath} (stub-v1: NOT routing-eligibility evidence)`,
    },
  };
}
