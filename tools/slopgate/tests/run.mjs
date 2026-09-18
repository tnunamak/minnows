#!/usr/bin/env node
// slopgate — decoy/control test runner (slopkit decoy_rejection.py style).
//
// MUST-FLAG rows assert that gate() surfaces a finding whose `rule` matches
// one of the row's mustRules — not just "some finding", the SPECIFIC right
// reason. MUST-PASS rows assert gate() surfaces NOTHING. Reports a real
// confusion matrix; exits nonzero if any row is mishandled.

import { writeFileSync, mkdtempSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { analyze, gate } from '../detector/cli.mjs';
import { checkUnearnedNames } from '../detector/unearned-name.mjs';
import { MUST_FLAG, MUST_PASS } from './fixtures.mjs';

async function runRow(row, kind) {
  const { wordHits, frameHits, cadenceHits, signatureHits, text } = await analyze(row.text);
  let unearnedHits = [];
  if (row.needsRepoRoot) {
    // Build a throwaway repo with a REAL (non-empty) README that never
    // mentions the coined term — the check fails closed on an EMPTY corpus
    // by design (a repo with no README at all is a false-positive risk, not
    // a signal), so a realistic fixture needs a populated corpus that
    // simply doesn't recognize this one-off name.
    const dir = mkdtempSync(path.join(tmpdir(), 'slopgate-fixture-'));
    mkdirSync(path.join(dir, '.git'));
    writeFileSync(path.join(dir, 'README.md'), 'This project coordinates state across requests using a shared store and a request router.');
    const filePath = path.join(dir, 'doc.md');
    writeFileSync(filePath, row.text);
    unearnedHits = checkUnearnedNames(text, filePath);
  }
  const findings = gate(wordHits, frameHits, unearnedHits, 'file', text, cadenceHits, signatureHits);

  if (kind === 'flag') {
    const raised = new Set(findings.map((f) => f.rule));
    const hit = row.mustRules.some((r) => raised.has(r));
    return { id: row.id, kind, ok: hit, required: row.mustRules, raised: [...raised], findings };
  }
  return { id: row.id, kind, ok: findings.length === 0, findings };
}

async function main() {
  const results = [];
  for (const row of MUST_FLAG) results.push(await runRow(row, 'flag'));
  for (const row of MUST_PASS) results.push(await runRow(row, 'pass'));

  const flags = results.filter((r) => r.kind === 'flag');
  const passes = results.filter((r) => r.kind === 'pass');
  const truePositives = flags.filter((r) => r.ok).length;
  const falseNegatives = flags.filter((r) => !r.ok).length;
  const falsePositives = passes.filter((r) => !r.ok).length;
  const trueNegatives = passes.filter((r) => r.ok).length;

  const precision = truePositives / (truePositives + falsePositives || 1);
  const recall = truePositives / (truePositives + falseNegatives || 1);

  console.log('slopgate decoy/control suite\n');
  for (const r of results) {
    const mark = r.ok ? 'ok' : 'XX';
    if (r.kind === 'flag') {
      console.log(`  [${mark}] ${r.id} (MUST-FLAG): required ${JSON.stringify(r.required)}, raised ${JSON.stringify(r.raised)}`);
    } else {
      console.log(`  [${mark}] ${r.id} (MUST-PASS): ${r.ok ? 'clean' : `FALSE POSITIVE — ${JSON.stringify(r.findings.map((f) => f.rule))}`}`);
    }
  }

  console.log('\nConfusion matrix:');
  console.log(`  True positives:  ${truePositives} / ${flags.length}`);
  console.log(`  False negatives: ${falseNegatives} / ${flags.length}`);
  console.log(`  True negatives:  ${trueNegatives} / ${passes.length}`);
  console.log(`  False positives: ${falsePositives} / ${passes.length}`);
  console.log(`  Precision: ${(precision * 100).toFixed(1)}%`);
  console.log(`  Recall:    ${(recall * 100).toFixed(1)}%`);

  const gatePassed = falsePositives === 0 && falseNegatives === 0;
  console.log(`\nGate: ${gatePassed ? 'PASS' : 'FAIL'}`);
  process.exit(gatePassed ? 0 : 1);
}

main();
