// tier-mass.mjs — the STANDING DISCOVERY INSTRUMENT: tier-routed flagged universe + mass maps.
//
// PORTED from tier-mass-report.mjs. Reports the WHOLE flagged universe, aggregated the two ways
// the doctrine ranks work: by FILE (tier-mass + churn — the file-grain owner-attention map) and
// by SUBSYSTEM, plus the top-N high-attention candidates (churn × excess-cc). Mass = Σ(cc − cog),
// the excess-cognitive-complexity mass — the owner-attention proxy (NOT function count).
//
// hone additionally emits the per-function `universe` rows (same routing) so `plan` never
// re-derives what inventory already knows (SPEC non-negotiable #9).
import { matchDiag } from './ast-scope.mjs';
import { routeFn } from './router.mjs';
import { markerHits, subsystemOf } from '../lib/util.mjs';

/** tally objects sorted value-desc then key — biome lints in parallel, so insertion order is not deterministic. */
const sortTally = (o) => Object.fromEntries(Object.entries(o).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])));

export function collectTierMass(inv) {
  const { cog, seamCc, markers, topN } = inv;

  // ---- build the flagged universe with tiers ----
  const universe = [];
  for (const d of inv.diags) {
    if (d.cc <= cog) continue;
    const fn = matchDiag(inv.modelsOf(d.absFile), d.line);
    const isCallback = fn ? fn.isCallback : false;
    const body = fn ? inv.bodyText(d.absFile, fn.line, fn.endLine) : '';
    const sec = markerHits(body, markers.security);
    const storage = markerHits(body, markers.storage);
    const pub = markerHits(body, markers.public_contract);
    const { tier, why } = routeFn({
      cc: d.cc, isCallback, callbackKind: fn?.callbackKind || null, secHits: sec,
      captures: fn?.freeVars || [], capturesMutable: fn?.freeMutableVars || [],
      awaitCount: fn?.awaitCount || 0, hasBranch: fn?.hasBranch || false, callers: null,
    }, { seamCc });
    universe.push({
      file: inv.rel(d.absFile), line: fn?.line || d.line, cc: d.cc, excess: d.cc - cog,
      fn: fn ? (fn.declaredName || fn.name) : `@L${d.line}`,
      is_anon: fn ? !fn.declaredName : true,
      is_callback: isCallback,
      callback_kind: fn?.callbackKind || null,
      enclosing_fn: fn?.enclosingFn || null,
      captured_vars: fn?.freeVars || null,
      captured_mutable_vars: fn?.freeMutableVars || null,
      module_refs: fn?.moduleRefs || null,   // module-scope names — informational, never classification (v1.1)
      await_count: fn ? fn.awaitCount : null,
      has_branch: fn ? fn.hasBranch : null,
      sec, storage, public: pub,
      tier, why,
    });
  }
  // deterministic order regardless of biome's parallel completion order (idempotent inventory).
  universe.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line || b.cc - a.cc);

  // ---- aggregate by FILE ----
  const byFile = new Map();
  for (const u of universe) {
    if (!byFile.has(u.file)) byFile.set(u.file, { file: u.file, churn: inv.churnByFile.get(u.file) || 0, fns: 0, mass: 0, tiers: {} });
    const e = byFile.get(u.file);
    e.fns++; e.mass += u.excess; e.tiers[u.tier] = (e.tiers[u.tier] || 0) + u.excess;
  }
  const fileRows = [...byFile.values()]
    .map((e) => ({ ...e, tiers: sortTally(e.tiers), attention: e.churn * e.mass }))
    .sort((a, b) => b.mass - a.mass || a.file.localeCompare(b.file));

  // ---- aggregate by SUBSYSTEM (top dir + one level of subdir) ----
  const bySub = new Map();
  for (const u of universe) {
    const s = subsystemOf(u.file);
    if (!bySub.has(s)) bySub.set(s, { subsystem: s, files: new Set(), fns: 0, mass: 0, tiers: {} });
    const e = bySub.get(s);
    e.files.add(u.file); e.fns++; e.mass += u.excess; e.tiers[u.tier] = (e.tiers[u.tier] || 0) + u.excess;
  }
  const subRows = [...bySub.values()]
    .map((e) => ({ subsystem: e.subsystem, files: e.files.size, fns: e.fns, mass: e.mass, tiers: sortTally(e.tiers) }))
    .sort((a, b) => b.mass - a.mass || a.subsystem.localeCompare(b.subsystem));

  // ---- TOP-N high-attention candidates (churn × excess-cc, function grain) ----
  const domTierOf = (file) => {
    const t = byFile.get(file)?.tiers || {};
    return Object.entries(t).sort((a, b) => b[1] - a[1])[0]?.[0] || '—';
  };
  const topCandidates = universe
    .map((u) => ({ ...u, churn: inv.churnByFile.get(u.file) || 0, attention: (inv.churnByFile.get(u.file) || 0) * u.excess }))
    .sort((a, b) => b.attention - a.attention || b.excess - a.excess || a.file.localeCompare(b.file) || a.line - b.line)
    .slice(0, topN)
    .map((u) => ({
      file: u.file, fn: u.fn, line: u.line, cc: u.cc, excess: u.excess, churn: u.churn,
      attention: u.attention, tier: u.tier, is_callback: u.is_callback, dominant_file_tier: domTierOf(u.file),
    }));

  // ---- universe tier totals (count + mass) ----
  const uniTier = {}, uniTierMass = {};
  for (const u of universe) {
    uniTier[u.tier] = (uniTier[u.tier] || 0) + 1;
    uniTierMass[u.tier] = (uniTierMass[u.tier] || 0) + u.excess;
  }
  const totalMass = universe.reduce((s, u) => s + u.excess, 0);

  return {
    generated_from: {
      repo_root: inv.ctx.repoRoot, repo_sha: inv.ctx.git.sha, cog_threshold: cog, seam_cc: seamCc,
      window: inv.window, flagged_fns: universe.length, files: byFile.size, total_excess_mass: totalMass,
      resolver: 'ast-scope (TS compiler API), router v1',
    },
    universe_tier_count: sortTally(uniTier),
    universe_tier_mass: sortTally(uniTierMass),
    by_subsystem: subRows,
    by_file: fileRows,
    top_candidates: topCandidates,
    universe,
  };
}
