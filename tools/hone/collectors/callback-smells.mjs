// callback-smells.mjs — the CALLBACK/CLOSURE SMELL DETECTOR.
//
// PORTED from smell-callbacks.mjs. The finding it operationalizes: the bulk of excess-complexity
// mass concentrates in anonymous capturing `.map`/`.on`/`transaction` callbacks — a plausible
// AI-built-code smell (agents keep local state in sight, inline "just one more case", and never
// commit to a named concept). For every high-complexity CALLBACK (cc > cog) it emits captures,
// mutable captures, awaits, marker hits, and a recommended class:
//
//   T1a : non-capturing pure hoist (freeVars=∅, no security/async/txn) — hoist to a named fn.
//   T1b : capturing (immutable), no security/async/txn — implicit env → explicit context object.
//   T2  : security/DDL markers OR >=2 awaits + branching OR transaction callback OR mutable
//         captures — the genuinely dangerous tail; full judged proof.
//   B   : (orthogonal flag) >=2 distinct public-contract nouns in the body — product-surface
//         concern; queue as a behavior-CHANGING proposal, never auto-land.
//
// Marker lists come from the repo profile (markers.security/storage/public_contract).
import { matchDiag } from './ast-scope.mjs';
import { markerHits } from '../lib/util.mjs';

export function collectCallbackSmells(inv) {
  const { cog, markers } = inv;
  const rows = [];
  const seen = new Set();
  for (const d of inv.diags) {
    if (d.cc <= cog) continue;
    const fn = matchDiag(inv.modelsOf(d.absFile), d.line);
    if (!fn) continue;
    // A CALLBACK = anonymous function-like OR any function-like passed as a call argument. We want
    // the closure-smell population specifically — not every flagged named top-level function.
    if (!fn.isCallback) continue;
    const key = `${d.absFile}::${fn.line}::${fn.col}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const body = inv.bodyText(d.absFile, fn.line, fn.endLine);
    const sec = markerHits(body, markers.security);
    const db = markerHits(body, markers.storage);
    const pub = markerHits(body, markers.public_contract);
    const captured = fn.freeVars;
    const capturedMut = fn.freeMutableVars;
    const awaits = fn.awaitCount;
    const isTxn = fn.callbackKind === 'transaction';

    // ---- recommended_class (ported; capture sets are now function-scope-only per the v1.1 fix) ----
    let cls, why;
    const publicHeavy = pub.length >= 2;   // >=2 distinct high-signal public-contract nouns → product surface
    if (sec.length || isTxn || (awaits >= 2 && fn.hasBranch) || capturedMut.length > 0) {
      cls = 'T2';
      const reasons = [];
      if (sec.length) reasons.push(`security/DDL/marker: ${sec.slice(0, 3).join(',')}`);
      if (isTxn) reasons.push('DB transaction callback');
      if (awaits >= 2 && fn.hasBranch) reasons.push(`${awaits} awaits + branching (awaited-order)`);
      if (capturedMut.length) reasons.push(`captures MUTABLE [${capturedMut.join(',')}] (shared-cell hazard)`);
      why = reasons.join('; ');
    } else if (captured.length === 0) {
      cls = 'T1a'; why = 'non-capturing (free-vars=∅) → hoist to a named pure top-level fn';
    } else {
      cls = 'T1b'; why = `captures (immutable) [${captured.slice(0, 5).join(',')}] → turn implicit env into an explicit context param, then name it`;
    }

    rows.push({
      file: inv.rel(d.absFile),
      parent_fn: fn.enclosingFn,
      callback_anchor: fn.name,
      callback_kind: fn.callbackKind || 'other',
      cc: d.cc,
      excess: d.cc - cog,
      captured_vars: captured,
      captured_mutable_vars: capturedMut,
      module_refs: fn.moduleRefs,   // module-scope names — stay in scope after a hoist; never block T1a (v1.1)
      await_count: awaits,
      security_db_public_keywords: { security: sec, db, public: pub },
      recommended_class: cls,
      flag_B: publicHeavy,   // orthogonal: public-noun-heavy callbacks are B candidates regardless of class
      why,
      line: fn.line,
    });
  }

  // deterministic order regardless of biome's parallel completion order (idempotent inventory).
  rows.sort((a, b) => b.excess - a.excess || a.file.localeCompare(b.file) || a.line - b.line);

  const byClass = {}, byKind = {}, massByClass = {};
  for (const r of rows) {
    byClass[r.recommended_class] = (byClass[r.recommended_class] || 0) + 1;
    massByClass[r.recommended_class] = (massByClass[r.recommended_class] || 0) + r.excess;
    byKind[r.callback_kind] = (byKind[r.callback_kind] || 0) + 1;
  }
  const sortTally = (o) => Object.fromEntries(Object.entries(o).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])));

  return {
    generated_from: {
      repo_root: inv.ctx.repoRoot, repo_sha: inv.ctx.git.sha, cog_threshold: cog,
      callbacks: rows.length, total_excess_mass: rows.reduce((s, r) => s + r.excess, 0),
      resolver: 'ast-scope (TS compiler API)',
    },
    by_class: sortTally(byClass), mass_by_class: sortTally(massByClass), by_kind: sortTally(byKind),
    b_flagged: rows.filter((r) => r.flag_B).length,
    callbacks: rows,
  };
}
