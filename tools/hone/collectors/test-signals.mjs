// test-signals.mjs — cheap deterministic TEST-SUITE sensors (no test run, no mutation testing —
// both too expensive/noisy for a collector; mutation stays campaign work).
//
// Two static signals:
//   skips         per-test-file count of skip markers: `.skip(` modifiers (test.skip / it.skip /
//                 describe.skip / t.skip) + node:test `{ skip: ... }` options, ranked desc.
//                 Runtime `# skipped` totals can differ (conditional/dynamic skips) — the static
//                 count is the cheap deterministic floor, not the runtime truth.
//   zero_by_name  owned source files NONE of whose exported names appear (as an identifier token)
//                 anywhere in the test corpus. KNOWN-WEAK (`by_name_only: true`): the B-inventory
//                 measured that dynamic dispatch / table-driven suites make 0-by-name ≠ untested.
//                 A lead for evidence-generation, never a coverage verdict.
import { join } from 'node:path';

const SKIP_MODIFIER_RE = /\b(?:test|it|describe|t)\.skip\s*\(/g;
const SKIP_OPTION_RE = /\{\s*skip\s*:/g;
const IDENT_RE = /[A-Za-z_$][\w$]*/g;
const SKIP_PATTERN_NOTE = 'static count: (test|it|describe|t).skip( modifiers + { skip: } options; runtime "# skipped" may differ';

/** exported identifier names of one JS/TS source text (ESM + CJS forms; `default` excluded). */
export function exportedNames(src) {
  const names = new Set();
  const collect = (re, group = 1) => {
    for (const m of String(src).matchAll(re)) if (m[group]) names.add(m[group]);
  };
  collect(/^\s*export\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/gm);
  collect(/^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)/gm);
  collect(/^\s*export\s+(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)/gm);
  collect(/(?:^|[^.\w$])exports\.([A-Za-z_$][\w$]*)\s*=/gm);
  collect(/module\.exports\.([A-Za-z_$][\w$]*)\s*=/g);
  // export { a, b as c } — the EXPORTED (post-`as`) name is the public one
  for (const m of String(src).matchAll(/^\s*export\s*\{([^}]*)\}/gm)) {
    for (const part of m[1].split(',')) {
      const p = part.trim();
      if (!p) continue;
      const asIdx = p.search(/\s+as\s+/);
      const name = (asIdx === -1 ? p : p.slice(asIdx).replace(/^\s+as\s+/, '')).trim();
      if (/^[A-Za-z_$][\w$]*$/.test(name) && name !== 'default') names.add(name);
    }
  }
  // module.exports = { a, b: impl } — keys are the public names
  const cjs = /module\.exports\s*=\s*\{([^}]*)\}/.exec(String(src));
  if (cjs) {
    for (const part of cjs[1].split(',')) {
      const key = part.split(':')[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(key) && key !== 'default') names.add(key);
    }
  }
  names.delete('default');
  return [...names].sort();
}

/** static skip-marker count of one test-file text. */
export function countSkips(src) {
  const s = String(src);
  return (s.match(SKIP_MODIFIER_RE) || []).length + (s.match(SKIP_OPTION_RE) || []).length;
}

const SRC_EXTS = `\\( -name '*.js' -o -name '*.mjs' -o -name '*.cjs' -o -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' \\)`;

/** repo-relative test files: *.test.* / *.spec.* anywhere + everything under test dirs. */
function findTestFiles(inv) {
  const { ctx } = inv;
  const out = ctx.sh(
    `find '${ctx.repoRoot}' -name node_modules -prune -o -type f ` +
    `\\( -name '*.test.*' -o -name '*.spec.*' -o -path '*/test/*' -o -path '*/tests/*' -o -path '*/__tests__/*' \\) ` +
    `${SRC_EXTS} -print 2>/dev/null`,
  );
  return [...new Set(out.split('\n').filter(Boolean).map((a) => inv.rel(a)))].sort();
}

/** owned source files (same walk as hotspots: bounded depth, profile exclusions). */
function findOwnedSources(inv) {
  const { ctx, ownedDirs } = inv;
  const depth = ctx.profile.analysis?.scan_depth ?? 2;
  const excludeArgs = (ctx.profile.analysis?.exclude_names || []).map((p) => `! -name '${p}'`).join(' ');
  const files = [];
  for (const dir of ownedDirs) {
    const found = ctx.sh(
      `find '${join(ctx.repoRoot, dir)}' -maxdepth ${depth} -type f ${SRC_EXTS} ${excludeArgs} 2>/dev/null`,
    );
    for (const abs of found.split('\n').filter(Boolean)) files.push(inv.rel(abs));
  }
  return [...new Set(files)].sort();
}

export function collectTestSignals(inv) {
  const { ctx } = inv;
  const testFiles = findTestFiles(inv);

  // ---- signal 1: static skip counts per test file ----
  const skipRows = [];
  let corpusTokens = new Set();
  for (const f of testFiles) {
    const src = inv.srcOf(join(ctx.repoRoot, f));
    const skips = countSkips(src);
    if (skips > 0) skipRows.push({ file: f, skips });
    for (const tok of String(src).match(IDENT_RE) || []) corpusTokens.add(tok);
  }
  skipRows.sort((a, b) => b.skips - a.skips || a.file.localeCompare(b.file));

  // ---- signal 2: owned files whose exports have ZERO by-name test references ----
  const zeroRows = [];
  for (const f of findOwnedSources(inv)) {
    const names = exportedNames(inv.srcOf(join(ctx.repoRoot, f)));
    if (!names.length) continue;
    const referenced = names.filter((n) => corpusTokens.has(n));
    if (referenced.length) continue; // any by-name hit clears the file (file-level signal)
    zeroRows.push({ file: f, exports: names.length, unreferenced: names.slice(0, 40), by_name_only: true });
  }
  zeroRows.sort((a, b) => b.exports - a.exports || a.file.localeCompare(b.file));

  return {
    generated_from: {
      repo_root: ctx.repoRoot, repo_sha: ctx.git.sha,
      test_files: testFiles.length,
      note: 'static signals only — no test run, no mutation testing',
    },
    skips: {
      total: skipRows.reduce((s, r) => s + r.skips, 0),
      pattern: SKIP_PATTERN_NOTE,
      files: skipRows,
    },
    zero_by_name: {
      by_name_only: true,
      note: 'KNOWN-WEAK signal: dynamic call patterns make 0-by-name ≠ untested (B-inventory coverage caveat) — a lead for evidence-generation, never a coverage verdict',
      files: zeroRows,
    },
  };
}
