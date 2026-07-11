// inventory.mjs — `hone inventory`: run collectors → <repo>/quality/inventory/*.json.
//
// Durable, idempotent (deterministic overwrite of the same filenames), stamped with repo_sha.
// One biome run + one AST-model pass are shared across all collectors; churn is computed once.
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildContext } from './profile.mjs';
import { loadTS, buildFileModel } from '../collectors/ast-scope.mjs';
import { resolveOwnedDirs, biomeFlaggedUniverse } from '../collectors/biome.mjs';
import { collectTierMass } from '../collectors/tier-mass.mjs';
import { collectCallbackSmells } from '../collectors/callback-smells.mjs';
import { collectHotspots } from '../collectors/hotspots.mjs';
import { collectTestSignals } from '../collectors/test-signals.mjs';

/** shared plumbing every collector consumes (built once per inventory run). */
export async function buildInventoryContext(ctx, flags = {}) {
  const cog = Number(flags.cog ?? ctx.profile.analysis?.cog_threshold ?? 5);
  const seamCc = Number(ctx.profile.analysis?.seam_cc ?? 12);
  const window = String(flags.window ?? ctx.profile.analysis?.churn_window ?? '6 months ago');
  const topN = Number(flags.top ?? 20);
  const markers = {
    security: ctx.profile.markers?.security || [],
    storage: ctx.profile.markers?.storage || [],
    public_contract: ctx.profile.markers?.public_contract || [],
  };

  const ownedDirs = resolveOwnedDirs(ctx);
  if (!ownedDirs.length) {
    throw new Error(`no analyzable dirs under ${ctx.repoRoot} — set analysis.owned_dirs in quality/hone.yaml`);
  }

  process.stderr.write(`hone inventory — repo=${ctx.repoRoot}\n`);
  process.stderr.write(`  profile: ${ctx.profileSource}\n`);
  process.stderr.write(`  owned dirs: [${ownedDirs.join(', ')}]  repo_sha: ${ctx.git.sha.slice(0, 12)}  cog>${cog}  window='${window}'\n`);

  const diags = biomeFlaggedUniverse(ctx, ownedDirs);
  process.stderr.write(`  biome: ${diags.length} function scores collected\n`);
  const ts = await loadTS(ctx.repoRoot);

  const srcCache = new Map();
  const srcOf = (abs) => {
    if (!srcCache.has(abs)) { try { srcCache.set(abs, readFileSync(abs, 'utf8')); } catch { srcCache.set(abs, ''); } }
    return srcCache.get(abs);
  };
  const modelCache = new Map();
  const modelsOf = (abs) => {
    if (!modelCache.has(abs)) {
      try { modelCache.set(abs, buildFileModel(abs, ts, srcOf(abs)).functions); } catch { modelCache.set(abs, []); }
    }
    return modelCache.get(abs);
  };
  const rel = (p) => {
    const s = String(p);
    return s.startsWith(ctx.repoRoot + '/') ? s.slice(ctx.repoRoot.length + 1) : s;
  };
  const bodyText = (abs, startLine, endLine) => srcOf(abs).split('\n').slice(startLine - 1, endLine).join('\n');

  // churn per file over the window — ONE git pass per owned dir (git paths are relative to the
  // git toplevel; strip the repo prefix so keys match repo-relative file paths).
  const churnByFile = new Map();
  const gitPrefix = ctx.git.prefix ? ctx.git.prefix + '/' : '';
  for (const dir of ownedDirs) {
    const out = ctx.sh(`git -C '${ctx.git.gitRoot}' log --since='${window}' --name-only --pretty=format: -- '${gitPrefix}${dir}' 2>/dev/null`);
    for (const l of out.split('\n')) {
      let f = l.trim();
      if (!f) continue;
      if (gitPrefix && f.startsWith(gitPrefix)) f = f.slice(gitPrefix.length);
      churnByFile.set(f, (churnByFile.get(f) || 0) + 1);
    }
  }

  return { ctx, ownedDirs, cog, seamCc, window, topN, markers, diags, srcOf, modelsOf, rel, bodyText, churnByFile };
}

export async function runInventory(flags) {
  const started = Date.now();
  const ctx = buildContext(flags.repo);
  const inv = await buildInventoryContext(ctx, flags);

  const tierMass = collectTierMass(inv);
  inv.universe = tierMass.universe; // hotspots derives per-file cog counts from the shared universe
  const smells = collectCallbackSmells(inv);
  const hotspots = collectHotspots(inv);
  const testSignals = collectTestSignals(inv);

  const outDir = join(ctx.repoRoot, 'quality', 'inventory');
  mkdirSync(outDir, { recursive: true });
  const write = (name, obj) => {
    const final = join(outDir, name);
    const temp = join(outDir, `.${name}.${process.pid}.tmp`);
    writeFileSync(temp, JSON.stringify(obj, null, 2) + '\n');
    renameSync(temp, final);
  };
  write('tier-mass.json', tierMass);
  write('callback-smells.json', smells);
  write('hotspots.json', hotspots);
  write('test-signals.json', testSignals);
  write('meta.json', {
    repo_sha: ctx.git.sha,
    repo_root: ctx.repoRoot,
    git_root: ctx.git.gitRoot,
    profile_source: ctx.profileSource,
    owned_dirs: inv.ownedDirs,
    cog_threshold: inv.cog,
    seam_cc: inv.seamCc,
    churn_window: inv.window,
    generated_at: new Date().toISOString(),
    duration_ms: Date.now() - started,
    counts: {
      flagged_fns: tierMass.universe.length,
      flagged_files: tierMass.by_file.length,
      total_excess_mass: tierMass.generated_from.total_excess_mass,
      callbacks: smells.callbacks.length,
      hotspot_files: hotspots.files.length,
      test_files: testSignals.generated_from.test_files,
      static_skips: testSignals.skips.total,
      zero_by_name_files: testSignals.zero_by_name.files.length,
    },
  });

  // ---- human summary (stderr) ----
  const w = (s) => process.stderr.write(s + '\n');
  const totalMass = tierMass.generated_from.total_excess_mass || 1;
  w(`\nUNIVERSE: ${tierMass.universe.length} flagged fns / ${tierMass.by_file.length} files, Σ excess-cc mass=${totalMass}`);
  w(`TIER MASS (Σ excess-cc):`);
  for (const [t, m] of Object.entries(tierMass.universe_tier_mass).sort((a, b) => b[1] - a[1])) {
    w(`  ${t.padEnd(24)} ${String(m).padStart(5)} mass  (${tierMass.universe_tier_count[t]} fns, ${Math.round((m / totalMass) * 1000) / 10}% of mass)`);
  }
  w(`CALLBACK SMELLS: ${smells.callbacks.length} (class ${JSON.stringify(smells.by_class)}, B-flagged ${smells.b_flagged})`);
  w(`TOP SUBSYSTEMS by mass:`);
  for (const r of tierMass.by_subsystem.slice(0, 6)) {
    w(`  ${r.subsystem.padEnd(20)} mass=${String(r.mass).padStart(5)}  files=${String(r.files).padStart(3)}  fns=${String(r.fns).padStart(4)}`);
  }
  w(`TOP HOTSPOTS (churn × cognitive-load × coupling — size never gates):`);
  w(`  score | churn | cog | cpl |   loc | file`);
  for (const e of hotspots.files.slice(0, 10)) {
    w(`  ${String(e.score).padStart(5)} | ${String(e.churn).padStart(5)} | ${String(e.cog).padStart(3)} | ${String(e.coupling).padStart(3)} | ${String(e.loc).padStart(5)} | ${e.file}${e.nogo ? '  [NO-GO/classify]' : ''}`);
  }
  w(`TEST SIGNALS (static): ${testSignals.skips.total} skip markers across ${testSignals.skips.files.length} test files; ` +
    `${testSignals.zero_by_name.files.length} owned files with ZERO by-name test refs (weak signal, by_name_only)`);
  w(`\nwrote quality/inventory/{tier-mass,callback-smells,hotspots,test-signals,meta}.json (repo_sha ${ctx.git.sha.slice(0, 12)}, ${Date.now() - started}ms)`);
}
