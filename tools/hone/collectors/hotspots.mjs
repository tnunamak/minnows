// hotspots.mjs — the churn × cognitive-load × coupling FILE ranker (size never gates).
//
// PORTED from discover.mjs, which superseded a size-gated target picker: ranking by
// churn×complexity (Tornhill/CodeScene) refutes naive god-file lists — a bad file nobody
// touches is LOW priority; a small hot hairy file outranks a big cold data file.
//
//   churn    = commits touching the file in the window (owner-attention frequency)
//   cog      = # functions exceeding the cognitive-complexity threshold (biome, read-difficulty)
//   coupling = fan-in proxy: # other owned files referencing the file's basename (ripple cost)
//   score    = churn * (1 + cog) * (1 + coupling/10)   — churn-dominant, complexity-weighted
//
// Files matching the profile's nogo_path_pattern are RANKED AND FLAGGED, never hidden
// (essential security complexity: classify + flag, don't auto-target).
import { join } from 'node:path';
import { escRe } from '../lib/util.mjs';

export function collectHotspots(inv) {
  const { ctx, ownedDirs } = inv;
  const depth = ctx.profile.analysis?.scan_depth ?? 2;
  const excludeNames = ctx.profile.analysis?.exclude_names || [];
  const nogoPattern = ctx.profile.markers?.nogo_path_pattern || null;
  const nogoRe = nogoPattern ? new RegExp(nogoPattern, 'i') : null;

  // ---- owned source files (find, bounded depth; exclusions from profile) ----
  const files = [];
  const excludeArgs = excludeNames.map((p) => `! -name '${p}'`).join(' ');
  for (const dir of ownedDirs) {
    const found = inv.ctx.sh(
      `find '${join(ctx.repoRoot, dir)}' -maxdepth ${depth} -type f ` +
      `\\( -name '*.js' -o -name '*.mjs' -o -name '*.cjs' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' \\) ` +
      `${excludeArgs} 2>/dev/null`,
    );
    for (const abs of found.split('\n').filter(Boolean)) files.push(inv.rel(abs));
  }
  files.sort();

  // cognitive hotspot count per file, from the shared flagged universe (cc > cog) —
  // identical to discover.mjs's per-file biome count, without re-linting.
  const cogByFile = new Map();
  for (const u of inv.universe) cogByFile.set(u.file, (cogByFile.get(u.file) || 0) + 1);

  const loc = (f) => {
    try { return inv.srcOf(join(ctx.repoRoot, f)).split('\n').length; } catch { return 0; }
  };

  // coupling proxy (ported): how many OTHER owned files reference this file's basename.
  const dirsAbs = ownedDirs.map((d) => `'${join(ctx.repoRoot, d)}'`).join(' ');
  const coupling = (f) => {
    const base = f.split('/').pop().replace(/\.(js|mjs|cjs|ts|tsx|jsx)$/, '');
    const n = inv.ctx.sh(
      `grep -rl -E "${base.replace(/[^a-zA-Z0-9]/g, '.')}" ${dirsAbs} 2>/dev/null | ` +
      `grep -v -E "${f.replace(/[^a-zA-Z0-9]/g, '.')}$|\\.test\\." | wc -l`,
    ).trim();
    return Number(n) || 0;
  };

  const rows = files.map((f) => {
    const l = loc(f), ch = inv.churnByFile.get(f) || 0;
    // skip files the owner never touches AND that are tiny — no attention cost (ported gate).
    if (ch === 0 && l < 400) return null;
    const cog = cogByFile.get(f) || 0;
    const cpl = coupling(f);
    const score = Math.round(ch * (1 + cog) * (1 + cpl / 10));
    return { file: f, loc: l, churn: ch, cog, coupling: cpl, score, nogo: nogoRe ? nogoRe.test(f) : false };
  }).filter(Boolean).sort((a, b) => b.score - a.score || (a.file < b.file ? -1 : 1));

  return {
    generated_from: {
      repo_root: ctx.repoRoot, repo_sha: ctx.git.sha, window: inv.window,
      cog_threshold: inv.cog, scan_depth: depth, files_considered: files.length,
      note: 'score = churn × (1+cog) × (1+coupling/10); size is context, never a gate; nogo files ranked+flagged, not hidden',
    },
    files: rows,
  };
}
