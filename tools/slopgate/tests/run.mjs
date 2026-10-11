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
import { MUST_FLAG, MUST_PASS, KNOWN_MISSES } from './fixtures.mjs';

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

  // Explicit positions test gate policy independently of upstream heuristics.
  const policyText = 'first second\n\nthird';
  const word = (text, index) => ({ rule: 'tier1', text, index, isTier1: true, isStructural: false });
  const cases = [
    ['one word without support', [word('first', 0)], [], 0],
    ['two words in one paragraph', [word('first', 0), word('second', 6)], [], 2],
    ['words in separate paragraphs', [word('first', 0), word('third', 14)], [], 0],
    ['one word with independent frame', [word('first', 0)], [{ rule: 'frame' }], 2],
  ];
  for (const [id, words, frames, expected] of cases) {
    const findings = gate(words, frames, [], 'file', policyText);
    results.push({ id: `gate-policy: ${id}`, kind: 'policy', ok: findings.length === expected, required: expected, raised: findings.length });
  }

  console.log('slopgate decoy/control suite\n');
  for (const r of results) {
    const mark = r.ok ? 'ok' : 'XX';
    if (r.kind === 'flag' || r.kind === 'policy') {
      console.log(`  [${mark}] ${r.id} (${r.kind === 'policy' ? 'GATE-POLICY' : 'MUST-FLAG'}): required ${JSON.stringify(r.required)}, raised ${JSON.stringify(r.raised)}`);
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
  console.log(`  Required-case recall: ${(recall * 100).toFixed(1)}%`);

  let knownDetected = 0;
  for (const row of KNOWN_MISSES) {
    const result = await runRow(row, 'flag');
    if (result.ok) knownDetected += 1;
    console.log(`  [known miss] ${row.id}: ${result.ok ? 'now detected' : 'not detected'}`);
  }
  const observedRecall = (truePositives + knownDetected) / (flags.length + KNOWN_MISSES.length || 1);
  console.log(`  Observed recall (including known misses): ${(observedRecall * 100).toFixed(1)}%`);
  const policyPassed = results.filter((r) => r.kind === 'policy').every((r) => r.ok);
  const gatePassed = falsePositives === 0 && falseNegatives === 0 && policyPassed;
  console.log(`\nGate: ${gatePassed ? 'PASS' : 'FAIL'}`);
  process.exit(gatePassed ? 0 : 1);
}

main();
