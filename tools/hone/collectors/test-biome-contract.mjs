import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { biomeFlaggedUniverse } from './biome.mjs';
import { makeSh } from '../lib/util.mjs';

const root = mkdtempSync(join(tmpdir(), 'hone-biome-contract-'));
const ctx = { repoRoot: root, profile: { commands: { biome: 'inert-biome' } } };
const collect = (report) => biomeFlaggedUniverse({ ...ctx, sh: () => JSON.stringify(report) }, ['src']);
const quote = (s) => "'" + s.replaceAll("'", "'\\''") + "'";

for (const name of ['ordinary', "temp dir's config"]) {
  test(`config path survives the real shell: ${name}`, () => {
    const temp = join(root, name);
    mkdirSync(temp);
    const recorder = join(root, `recorder-${name}.cjs`);
    const receipt = join(temp, 'argv.json');
    writeFileSync(recorder, `
      const fs = require('node:fs');
      fs.writeFileSync(${JSON.stringify(receipt)}, JSON.stringify(process.argv.slice(2)));
      console.log('{"diagnostics":[]}');
    `);
    const previous = process.env.TMPDIR;
    process.env.TMPDIR = temp;
    try {
      const profile = { commands: { biome: `${quote(process.execPath)} ${quote(recorder)}` } };
      assert.deepEqual(biomeFlaggedUniverse({ ...ctx, profile, sh: makeSh(root) }, ['src']), []);
      const args = JSON.parse(readFileSync(receipt, 'utf8'));
      const cfg = join(temp, `hone-biome-${process.pid}.json`);
      assert.deepEqual(args, ['lint', `--config-path=${cfg}`, '--only=complexity/noExcessiveCognitiveComplexity',
        '--max-diagnostics=none', '--reporter=json', join(root, 'src')]);
      assert.equal(JSON.parse(readFileSync(cfg, 'utf8')).linter.rules.complexity.noExcessiveCognitiveComplexity.level, 'warn');
    } finally {
      if (previous === undefined) delete process.env.TMPDIR;
      else process.env.TMPDIR = previous;
    }
  });
}

test('requires a report object with a diagnostics array', () => {
  for (const report of [{}, null, [], { diagnostics: null }, { diagnostics: {} }, { diagnostics: 'none' }]) {
    assert.throws(() => collect(report), /invalid.*diagnostics/i, JSON.stringify(report));
  }
});

test('rejects error and fatal diagnostics, including alongside complexity warnings', () => {
  const complexity = { severity: 'warning', description: 'complexity of 6', location: { path: 'src/example.js', start: { line: 2, column: 17 } } };
  for (const severity of ['error', 'fatal']) {
    const failure = { severity, description: 'synthetic file parse failure' };
    assert.throws(() => collect({ diagnostics: [failure] }), /synthetic file parse failure/);
    assert.throws(() => collect({ diagnostics: [complexity, failure] }), /synthetic file parse failure/);
  }
});

test('accepts empty success and a positioned complexity warning', () => {
  assert.deepEqual(collect({ diagnostics: [] }), []);
  assert.deepEqual(collect({ diagnostics: [{ severity: 'warning', description: 'complexity of 6',
    location: { path: 'src/example.js', start: { line: 2, column: 17 } } }] }),
  [{ absFile: join(root, 'src/example.js'), line: 2, col: 17, cc: 6 }]);
});

test('real process wrapper preserves nonzero exit despite valid JSON', () => {
  const failing = join(root, 'nonzero.cjs');
  writeFileSync(failing, `console.log('{"diagnostics":[]}'); process.exitCode = 12;`);
  const profile = { commands: { biome: `${quote(process.execPath)} ${quote(failing)}` } };
  assert.throws(() => biomeFlaggedUniverse({ ...ctx, profile, sh: makeSh(root) }, ['src']),
    (error) => error.status === 12 && JSON.parse(error.stdout).diagnostics.length === 0);
});
