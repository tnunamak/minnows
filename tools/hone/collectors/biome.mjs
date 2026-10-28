// biome.mjs — the shared biome cognitive-complexity front-end for all collectors.
//
// The four PDPP instruments each wrote an identical one-rule biome config and ran the same
// lint; hone runs it ONCE per inventory and shares the flagged universe. maxAllowedComplexity=1
// so EVERY function is reported with its real score; consumers filter cc > profile cog_threshold.
import { readFileSync, writeFileSync, existsSync, readdirSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { walkSourceFiles } from '../lib/util.mjs';

const AUTO_EXCLUDED_DIRS = new Set([
  'node_modules', 'dist', 'build', 'out', 'coverage', 'vendor', 'tmp', 'fixtures', 'test', 'tests', '__tests__',
]);

const HONE_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const quote = (s) => "'" + s.replaceAll("'", "'\\''") + "'";

/** Explicit profile command > target installation > hone installation > pinned npx. */
export function resolveBiomeCommand(ctx) {
  if (ctx.profile.commands?.biome) return ctx.profile.commands.biome;
  const local = join(ctx.repoRoot, 'node_modules/.bin/biome');
  if (existsSync(local)) return quote(local);
  const bundled = join(HONE_ROOT, 'node_modules/.bin/biome');
  if (existsSync(bundled)) return quote(bundled);
  return 'npx --yes --package=@biomejs/biome@1.9.4 biome';
}

/**
 * The dirs to analyze: profile analysis.owned_dirs, or (when empty) auto-detected top-level
 * dirs of the repo that contain at least one JS/TS source file.
 */
export function resolveOwnedDirs(ctx) {
  const conf = (ctx.profile.analysis?.owned_dirs || []).filter((d) => existsSync(join(ctx.repoRoot, d)));
  if (conf.length) return conf;
  if ((ctx.profile.analysis?.owned_dirs || []).length) return []; // configured dirs all missing → honest empty
  const dirs = [];
  for (const e of readdirSync(ctx.repoRoot, { withFileTypes: true })) {
    if (!e.isDirectory() || e.name.startsWith('.') || AUTO_EXCLUDED_DIRS.has(e.name)) continue;
    if (walkSourceFiles(join(ctx.repoRoot, e.name)).length) dirs.push(e.name);
  }
  return dirs.sort();
}

/**
 * Run biome's noExcessiveCognitiveComplexity over the owned dirs, JSON reporter, and return
 * raw diagnostics as [{ absFile, line, col, cc }] (unfiltered — every function's real score).
 */
export function biomeFlaggedUniverse(ctx, ownedDirs) {
  const cfg = join(tmpdir(), `hone-biome-${process.pid}.json`);
  writeFileSync(cfg, JSON.stringify({
    linter: { rules: { complexity: { noExcessiveCognitiveComplexity: { level: 'warn', options: { maxAllowedComplexity: 1 } } } } },
  }));
  const dirsAbs = ownedDirs.map((d) => quote(join(ctx.repoRoot, d))).join(' ');
  const biome = resolveBiomeCommand(ctx);
  const raw = ctx.sh(
    `${biome} lint --config-path=${cfg} --only=complexity/noExcessiveCognitiveComplexity ` +
    `--max-diagnostics=none --reporter=json ${dirsAbs} 2>/dev/null`,
  );
  let diags = [];
  try { diags = JSON.parse(raw).diagnostics || []; }
  catch {
    throw new Error(
      `biome produced unparseable output (cmd: ${biome}). Is biome runnable inside ${ctx.repoRoot}? ` +
      `First 200 chars: ${raw.slice(0, 200)}`,
    );
  }
  const out = [];
  for (const d of diags) {
    const message = d.description || (typeof d.message === 'string' ? d.message : '');
    const m = message.match(/complexity of (\d+)/);
    if (!m) continue;
    const p = d.location?.path?.file || d.location?.path;
    if (!p) continue;
    // biome emits paths relative to its cwd (the target repo) — resolve so collectors running
    // from anywhere read the right file (the reference scripts ran from inside the repo).
    const absFile = isAbsolute(String(p)) ? String(p) : join(ctx.repoRoot, String(p));
    let line = d.location?.start?.line, col = d.location?.start?.column;
    if (!line && Number.isInteger(d.location?.span?.[0])) {
      // Biome 1.9 JSON anchors use UTF-8 byte spans, not line/column fields.
      const source = d.location.sourceCode ?? readFileSync(absFile, 'utf8');
      const prefix = Buffer.from(source).subarray(0, d.location.span[0]).toString('utf8');
      const lines = prefix.split('\n');
      line = lines.length;
      col = lines.at(-1).length + 1;
    }
    if (!line) throw new Error(`biome diagnostic has no source position: ${absFile}`);
    out.push({ absFile, line, col, cc: Number(m[1]) });
  }
  return out;
}
