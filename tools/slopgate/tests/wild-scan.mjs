#!/usr/bin/env node
// slopgate — wild-corpus scan: run the real gate over real files, report every
// finding for human triage. This is the live test that matters — the decoy
// suite proves the rules work on constructed cases; this proves precision on
// prose nobody wrote to test the tool.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { analyze, gate } from '../detector/cli.mjs';
import { checkUnearnedNames } from '../detector/unearned-name.mjs';

function walk(dir, exts, out = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch (error) {
    throw new Error(`cannot scan directory ${dir}: ${error.message}`);
  }
  for (const e of entries) {
    if (e.name === 'node_modules' || e.name === '.git') continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, exts, out);
    else if (exts.some((ext) => e.name.endsWith(ext))) out.push(full);
  }
  return out;
}

async function scanFile(file) {
  let raw;
  try {
    raw = readFileSync(file, 'utf8');
  } catch (error) {
    throw new Error(`cannot read file ${file}: ${error.message}`);
  }
  if (!raw.trim()) return [];
  const { wordHits, frameHits, cadenceHits, signatureHits, text } = await analyze(raw);
  const unearnedHits = checkUnearnedNames(text, file);
  return gate(wordHits, frameHits, unearnedHits, 'file', text, cadenceHits, signatureHits);
}

async function main() {
  const targets = process.argv.slice(2);
  let total = 0;
  let failed = 0;
  for (const target of targets) {
    try {
    const st = statSync(target, { throwIfNoEntry: false });
    const files = st?.isDirectory() ? walk(target, ['.md', '.html']) : [target];
    for (const file of files) {
      let findings;
      try {
        findings = await scanFile(file);
      } catch (error) {
        failed += 1;
        console.error(error.message);
        continue;
      }
      if (findings.length) {
        console.log(`\n=== ${file} ===`);
        for (const f of findings) {
          console.log(`  [${f.rule}] "${f.text}" — ${f.suggestion}`);
        }
        total += findings.length;
      }
    }
    } catch (error) {
      failed += 1;
      console.error(`cannot scan ${target}: ${error.message}`);
    }
  }
  console.log(`\nTotal findings: ${total}`);
  console.log(`Failed inputs: ${failed}`);
  if (failed) process.exitCode = 1;
}

main();
