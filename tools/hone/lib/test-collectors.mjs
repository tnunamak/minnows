#!/usr/bin/env node
// test-collectors.mjs — offline self-tests for collectors/test-signals.mjs + the agenda profile
// projection plumbing in profile.mjs (named_targets / budget_bands schema). Zero LLM calls, zero
// biome: the collector under test is pure find+read+regex, so the fixture repo is plain files.
//
// Matrix:
//   1. exportedNames — ESM + CJS export forms, `as` renames, default excluded
//   2. countSkips — .skip( modifiers + { skip: } options, domain-word 'skip' NOT counted
//   3. collectTestSignals — skip ranking, zero-by-name file-level signal, by_name_only labels,
//      referenced exports clear the file, deterministic ordering
//   4. profile plumbing — agenda.named_targets/budget_bands parse + deep-merge from
//      quality/hone.yaml; malformed projections fail LOUD (loadProfile throws)
//
// Exit 0 iff every check passes.
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { makeSh } from './util.mjs';
import { loadProfile } from './profile.mjs';
import { collectTestSignals, countSkips, exportedNames } from '../collectors/test-signals.mjs';

const checks = [];
const ok = (name, cond, detail = '') => {
  checks.push({ name, pass: !!cond });
  if (!cond) process.stderr.write(`FAIL ${name}${detail ? ` — ${detail}` : ''}\n`);
};

// ---------------------------------------------------------------- 1. exportedNames units

{
  const esm = [
    'export function alphaOne() {}',
    'export async function alphaTwo(x) { return x; }',
    'export const alphaThree = 1;',
    'export class AlphaBox {}',
    'const hidden = 2;',
    'export { hidden as alphaFour, alphaOne };',
    'export default function main() {}',
  ].join('\n');
  ok('exports: ESM forms extracted', exportedNames(esm).join(',') === 'AlphaBox,alphaFour,alphaOne,alphaThree,alphaTwo', exportedNames(esm).join(','));
  const cjs = [
    'function gammaOne() {}',
    'exports.gammaTwo = () => {};',
    'module.exports.gammaThree = 3;',
    'module.exports = { gammaOne, gammaFour: gammaOne };',
  ].join('\n');
  ok('exports: CJS forms extracted', exportedNames(cjs).join(',') === 'gammaFour,gammaOne,gammaThree,gammaTwo', exportedNames(cjs).join(','));
  ok('exports: default never counted', !exportedNames('export default class X {}').length);
}

// ---------------------------------------------------------------- 2. countSkips units

{
  ok('skips: modifiers counted', countSkips('test.skip("a");\nit.skip("b");\ndescribe.skip("c");\nt.skip();') === 4);
  ok('skips: node:test { skip: } option counted', countSkips('test("a", { skip: !process.env.PG }, () => {});') === 1);
  ok('skips: domain-word skip NOT counted', countSkips('assert.equal(event.status, "skipped");\n// the runtime states the skip fact') === 0);
}

// ---------------------------------------------------------------- 3. collectTestSignals (fixture repo)

{
  const root = mkdtempSync(join(tmpdir(), 'hone-tsig-'));
  mkdirSync(join(root, 'src'), { recursive: true });
  mkdirSync(join(root, 'test'), { recursive: true });
  writeFileSync(join(root, 'src', 'alpha.mjs'), 'export function alphaOne() {}\nexport const alphaTwo = 1;\n');
  writeFileSync(join(root, 'src', 'beta.mjs'), 'export function betaUsed() {}\nexport function betaSibling() {}\n');
  writeFileSync(join(root, 'src', 'gamma.cjs'), 'function gammaOne() {}\nmodule.exports = { gammaOne };\n');
  writeFileSync(join(root, 'src', 'noexports.mjs'), 'const internal = 1;\n');
  writeFileSync(join(root, 'test', 'one.test.mjs'),
    'import { betaUsed } from "../src/beta.mjs";\ntest.skip("a");\nit.skip("b");\ntest("c", { skip: true }, () => betaUsed());\n');
  writeFileSync(join(root, 'test', 'two.spec.js'), 'describe.skip("later", () => {});\n');
  writeFileSync(join(root, 'test', 'helper.js'), 'export const wire = () => {};\n'); // under test/: corpus, no skips

  const srcCache = new Map();
  const srcOf = (abs) => {
    if (!srcCache.has(abs)) { try { srcCache.set(abs, readFileSync(abs, 'utf8')); } catch { srcCache.set(abs, ''); } }
    return srcCache.get(abs);
  };
  const inv = {
    ctx: {
      repoRoot: root,
      git: { sha: 'fixturesha000' },
      profile: { analysis: { scan_depth: 2, exclude_names: ['*.test.*', '*.spec.*'] } },
      sh: makeSh(root),
    },
    ownedDirs: ['src'],
    rel: (p) => (String(p).startsWith(root + '/') ? String(p).slice(root.length + 1) : String(p)),
    srcOf,
  };
  const out = collectTestSignals(inv);
  ok('collector: skip files ranked desc, static totals', out.skips.total === 4 &&
    out.skips.files.map((f) => `${f.file}:${f.skips}`).join(',') === 'test/one.test.mjs:3,test/two.spec.js:1', JSON.stringify(out.skips));
  ok('collector: zero-by-name emits files with NO referenced exports',
    out.zero_by_name.files.map((f) => f.file).join(',') === 'src/alpha.mjs,src/gamma.cjs', JSON.stringify(out.zero_by_name.files.map((f) => f.file)));
  ok('collector: one by-name hit clears the whole file (betaSibling rides betaUsed)',
    !out.zero_by_name.files.some((f) => f.file === 'src/beta.mjs'));
  ok('collector: weak-signal label on the section AND every row',
    out.zero_by_name.by_name_only === true && out.zero_by_name.files.every((f) => f.by_name_only === true));
  ok('collector: export counts + sample names carried', out.zero_by_name.files[0].exports === 2 &&
    out.zero_by_name.files[0].unreferenced.join(',') === 'alphaOne,alphaTwo');
  ok('collector: no-exports files not flagged', !out.zero_by_name.files.some((f) => f.file === 'src/noexports.mjs'));
  ok('collector: generated_from stamps sha + test-file count', out.generated_from.repo_sha === 'fixturesha000' && out.generated_from.test_files === 3, JSON.stringify(out.generated_from));
  const again = collectTestSignals(inv);
  ok('collector: deterministic (same input → same JSON)', JSON.stringify(out) === JSON.stringify(again));
  rmSync(root, { recursive: true, force: true });
}

// ---------------------------------------------------------------- 4. profile plumbing (named_targets / budget_bands)

function repoWithProfile(yamlText) {
  const root = mkdtempSync(join(tmpdir(), 'hone-prof-'));
  mkdirSync(join(root, 'quality'), { recursive: true });
  writeFileSync(join(root, 'quality', 'hone.yaml'), yamlText);
  return root;
}

{
  const root = repoWithProfile([
    'version: 1',
    'agenda:',
    '  doctrine_path: /tmp/DOCTRINE.md',
    '  named_targets:',
    '    - id: storage-backend-contract-unification',
    '      description: "one storage seam: backends behind it"',
    '      evidence_hint: "sensor: hotspots server/postgres-storage.js"',
    '      keywords: [storage, sqlite]',
    '    - id: skipped-test-audit',
    '  budget_bands:',
    '    B: [40, 50]',
    '    T1: [10, 15]',
    '',
  ].join('\n'));
  const { profile } = loadProfile(root);
  const nt = profile.agenda.named_targets;
  ok('profile: named_targets parsed as list of maps', Array.isArray(nt) && nt.length === 2 &&
    nt[0].id === 'storage-backend-contract-unification' && nt[0].keywords.join(',') === 'storage,sqlite' &&
    /one storage seam/.test(nt[0].description) && nt[1].id === 'skipped-test-audit', JSON.stringify(nt));
  ok('profile: budget_bands parsed as class → [min, max]',
    profile.agenda.budget_bands.B.join('-') === '40-50' && profile.agenda.budget_bands.T1.join('-') === '10-15');
  ok('profile: repo doctrine_path override wins over the default', profile.agenda.doctrine_path === '/tmp/DOCTRINE.md');
  rmSync(root, { recursive: true, force: true });
}
{
  const defaults = loadProfile(mkdtempSync(join(tmpdir(), 'hone-noprof-'))).profile;
  ok('profile: generic defaults carry an empty agenda projection',
    Array.isArray(defaults.agenda?.named_targets) && defaults.agenda.named_targets.length === 0 &&
    defaults.agenda.budget_bands && Object.keys(defaults.agenda.budget_bands).length === 0 &&
    defaults.agenda.doctrine_path === null, JSON.stringify(defaults.agenda));
}
{
  const throwsWith = (yamlText) => {
    const root = repoWithProfile(yamlText);
    try { loadProfile(root); return null; }
    catch (e) { return e.message; }
    finally { rmSync(root, { recursive: true, force: true }); }
  };
  ok('profile: named_targets entry without id fails LOUD',
    /named_targets\[0\]\.id/.test(throwsWith('agenda:\n  named_targets:\n    - description: no id here\n') ?? ''));
  ok('profile: non-list named_targets fails LOUD',
    /named_targets: must be a list/.test(throwsWith('agenda:\n  named_targets: broken\n') ?? ''));
  ok('profile: malformed band fails LOUD (three elements)',
    /budget_bands\.B/.test(throwsWith('agenda:\n  budget_bands:\n    B: [40, 50, 60]\n') ?? ''));
  ok('profile: inverted band fails LOUD (min > max)',
    /budget_bands\.T1/.test(throwsWith('agenda:\n  budget_bands:\n    T1: [15, 10]\n') ?? ''));
  ok('profile: non-numeric band fails LOUD',
    /budget_bands\.A2/.test(throwsWith('agenda:\n  budget_bands:\n    A2: [low, high]\n') ?? ''));
}

// ---------------------------------------------------------------- verdict

const failed = checks.filter((c) => !c.pass);
process.stdout.write(`\ntest-collectors: ${checks.length - failed.length}/${checks.length} checks passed\n`);
for (const c of checks) process.stdout.write(`  ${c.pass ? 'ok  ' : 'FAIL'} ${c.name}\n`);
process.exit(failed.length ? 1 : 0);
