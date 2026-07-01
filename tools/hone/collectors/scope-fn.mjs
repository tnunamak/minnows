#!/usr/bin/env node
// scope-fn.mjs — DETERMINISTIC single-function recon (ground truth for the work phase).
//
// PORTED from scope-fn.mjs. Why it exists: an LLM recon step twice mis-read cc-17 as cc-1 by
// eyeballing biome's text output and burned ~11% of a run on dead rounds — a measurement a
// script should do. For one `file::fn` this returns the exact biome cognitive complexity, the
// definition line, a grep-verified caller count, and a FULL-BODY security-marker scan (so the
// per-function no-go decision is deterministic and honest). It does NOT decide is_nogo and does
// NOT pick targets — it removes the MEASUREMENT from the LLM's plate.
//
// `hone work` (wave 2) consumes this; until then it is runnable standalone:
//   node collectors/scope-fn.mjs --repo <path> --target 'server/records.js::queryRecords' [--cog 5]
import { existsSync, readFileSync, writeFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

/** @returns ground-truth recon JSON for one file::fn in the target repo. */
export function scopeFn(ctx, ownedDirs, target) {
  const [file, fnName] = String(target || '').split('::');
  if (!file || !fnName) throw new Error("need --target 'path/file.js::fnName'");
  const cog = ctx.profile.analysis?.cog_threshold ?? 5;
  const absFile = join(ctx.repoRoot, file);
  if (!existsSync(absFile)) return { error: `file not found: ${file}` };
  const src = readFileSync(absFile, 'utf8');

  // per-file biome run, maxAllowedComplexity=1 so EVERY function reports its real score.
  const cfg = join(tmpdir(), `hone-scope-fn-${process.pid}.json`);
  writeFileSync(cfg, JSON.stringify({
    linter: { rules: { complexity: { noExcessiveCognitiveComplexity: { level: 'warn', options: { maxAllowedComplexity: 1 } } } } },
  }));
  const biome = ctx.profile.commands?.biome || 'npx biome';
  const out = ctx.sh(`${biome} lint --config-path=${cfg} --only=complexity/noExcessiveCognitiveComplexity --max-diagnostics=none --reporter=json '${absFile}' 2>/dev/null`);
  let diags = [];
  try { diags = JSON.parse(out).diagnostics || []; } catch {}
  const flagged = diags.map((d) => {
    const m = (d.message || '').match(/complexity of (\d+)/);
    const line = d.location?.start?.line, col = d.location?.start?.column;
    return m && line ? { line, col, cc: Number(m[1]) } : null;
  }).filter(Boolean);

  // map a biome (line,col) name-anchor to the identifier at that exact token.
  const nameAtToken = (line, col) => {
    const ln = src.split('\n')[line - 1] || '';
    const m = ln.slice(Math.max(0, col - 1)).match(/[A-Za-z_$][\w$]*/);
    return m ? m[0] : null;
  };

  let hit = flagged.find((f) => nameAtToken(f.line, f.col) === fnName);
  if (!hit) {
    // fallback: biome anchored at the body-open line (arrow/method) — scan for the def line.
    const defLine = src.split('\n').findIndex((l) =>
      new RegExp(`\\b(function\\s+${fnName}\\b|${fnName}\\s*[:=]\\s*(async\\s*)?(function|\\())`).test(l));
    if (defLine >= 0) {
      hit = flagged.filter((f) => Math.abs(f.line - (defLine + 1)) <= 3).sort((a, b) => b.cc - a.cc)[0] || null;
      if (hit) hit.defLine = defLine + 1;
    }
  }

  // full-body security scan: def line to matching close brace (brace-depth walk).
  const bodyRedScan = (defLine) => {
    const lines = src.split('\n');
    let depth = 0, started = false;
    const body = [];
    for (let i = defLine - 1; i < Math.min(lines.length, defLine + 400); i++) {
      body.push(lines[i]);
      for (const ch of lines[i]) { if (ch === '{') { depth++; started = true; } if (ch === '}') depth--; }
      if (started && depth <= 0 && i > defLine - 1) break;
    }
    const text = body.join('\n');
    const markers = (ctx.profile.markers?.security || []).filter((mk) =>
      new RegExp(mk.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i').test(text));
    return { markers, bodyLines: body.length };
  };
  const defLine = hit?.defLine || hit?.line || null;
  const red = defLine ? bodyRedScan(defLine) : { markers: [], bodyLines: 0 };

  // caller count: grep the name across owned dirs, minus its own definition occurrence.
  const dirsAbs = ownedDirs.map((d) => `'${join(ctx.repoRoot, d)}'`).join(' ');
  const callerCount = () => {
    const n = ctx.sh(`grep -rncw '${fnName}' ${dirsAbs} 2>/dev/null | awk -F: '{s+=$2} END{print s+0}'`).trim();
    return Number.isFinite(Number(n)) ? Math.max(0, Number(n) - 1) : null;
  };

  return {
    target, file, function: fnName,
    found: !!hit,
    cognitive_before: hit?.cc ?? null,
    line: defLine,
    caller_count: callerCount(),
    red_scan: red.markers,
    red_hint: red.markers.length
      ? 'security markers present in body — decomplect ONLY around them, checker must prove the guard byte-identical; is_nogo=true if the function IS the security decision'
      : 'no security markers — pure decomplect target',
    note: hit ? null : `function '${fnName}' not found among ${flagged.length} flagged functions in ${file} (may be below the cog-${cog} threshold — not worth cutting)`,
  };
}

// standalone CLI (wave-2 `work` will call scopeFn() directly)
let isMain = false;
try { isMain = !!process.argv[1] && fileURLToPath(import.meta.url) === realpathSync(process.argv[1]); } catch {}
if (isMain) {
  const { parseArgs } = await import('../lib/util.mjs');
  const { buildContext } = await import('../lib/profile.mjs');
  const { resolveOwnedDirs } = await import('./biome.mjs');
  const flags = parseArgs(process.argv.slice(2));
  const ctx = buildContext(flags.repo);
  console.log(JSON.stringify(scopeFn(ctx, resolveOwnedDirs(ctx), flags.target), null, 2));
}
