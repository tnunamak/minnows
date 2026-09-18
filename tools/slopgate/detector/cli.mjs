#!/usr/bin/env node
// slopgate — deterministic, zero-dependency slop gate.
//
// Composes five free (no LLM, no network) layers over authored prose:
//   1. cadence       — sentence-rhythm statistics (cadence.mjs). PRIMARY
//                       signal: measured 11.7x stronger discriminator than
//                       vocabulary (avoid-ai-writing v3.22.0, Pangram Labs,
//                       StoryScope — see SLOPGATE.md for sourcing) and pure
//                       statistics, so no register-specific false-positive
//                       risk (doesn't look at which words are used at all).
//   2. word tells     — avoid-ai-writing's analyzeText (tier1/tier2/tier3 vocab).
//                       WEAK signal on its own (measured ~0.9x lift) — gated
//                       accordingly, see gate() below.
//   3. syntax frames  — new rules for shapes the word-list misses (frames.mjs)
//   4. signature      — bland_clean_sentence (absence-of-grounding
//                       combinator) + tool-artifact leftovers (signature.mjs,
//                       ported from slopkit). Em-dash RATE was tried and
//                       DROPPED (2026-08-04): measured median rate across a
//                       real ~70-file corpus was 15.7/1000 words — nearly 2x
//                       the ported 8/1000 ceiling — because this corpus's
//                       own writing style uses em-dashes idiomatically at
//                       high frequency. avoid-ai-writing's own sourced
//                       research independently calls em-dash "low-value on
//                       its own" as an AI-tell; a threshold that fires on
//                       normal usage in this owner's actual writing style is
//                       worse than no rule. Kept out, not just down-weighted.
//   5. unearned names — narrow corpus-relative port of judge.mjs (unearned-name.mjs)
//
// Precision-over-recall by design: this is a hook-wired gate, not a report.
// A false positive here trains the owner to ignore or disable it, which is
// worse than missing a real slop instance. See gate() for the exact bar.
//
// Usage:
//   node cli.mjs check --file <path>        # read file, print findings
//   node cli.mjs check --stdin              # read raw text from stdin
//   node cli.mjs check --stdin --chat       # chat-mode: stricter gate, used by Stop hook
//   echo "text" | node cli.mjs check --stdin --json
//
// Exit code: 1 if any finding survives the gate, 0 otherwise. Never exits
// nonzero for I/O errors (missing file, empty input) — those exit 0 with no
// findings, so a broken caller never blocks a real workflow.

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { stripNonProse } from './strip.mjs';
import { scanFrames } from './frames.mjs';
import { checkUnearnedNames } from './unearned-name.mjs';
import { analyzeCadence } from './cadence.mjs';
import { scanBlandCleanSentences, scanToolArtifacts } from './signature.mjs';

const AVOID_AI_WRITING_PATTERNS =
  process.env.SLOPGATE_PATTERNS_PATH ||
  path.join(process.env.HOME || '', '.agents/skills/avoid-ai-writing/detector/patterns.js');

async function loadAnalyzeText() {
  try {
    const mod = await import(AVOID_AI_WRITING_PATTERNS);
    const AIDetector = mod.default || mod;
    if (AIDetector && typeof AIDetector.analyzeText === 'function') return AIDetector.analyzeText;
  } catch {
    /* avoid-ai-writing not installed on this machine — degrade gracefully */
  }
  return null;
}

// Two different confidence shapes from avoid-ai-writing's output:
//   - type 'tier1': a single VOCABULARY word (robust, leverage, landscape).
//     Measured (2026-08-04, live corpus of ~40 real technical docs) to be
//     common in honest long-form technical writing on its own — needs
//     DENSITY (>=2 distinct hits) before it's a reliable signal, matching
//     avoid-ai-writing's own denseAIVocab reasoning at a lower, more
//     sensitive threshold.
//   - every other type (hedge-stack, future-narrative, formulaic-opener,
//     rhetorical-question, cutoff-disclaimer, ai-placeholder, sycophantic,
//     ...): a full GRAMMATICAL SHAPE, not a bare word — already narrow and
//     precision-tuned upstream (see its scripts/fp-measure.js). Treated like
//     a frame hit: trusted individually, no density requirement.
function isStructuralHit(issue) {
  return issue.type !== 'tier1' && (issue.severity === 'high' || issue.severity === 'critical');
}

export async function analyze(rawText) {
  const text = stripNonProse(rawText);
  const analyzeText = await loadAnalyzeText();

  const wordHits = [];
  if (analyzeText) {
    const result = analyzeText(text, { contextMode: 'technical' });
    for (const issue of result.issues || []) {
      wordHits.push({
        type: issue.type,
        rule: issue.type,
        text: issue.text,
        index: issue.index,
        severity: issue.severity,
        suggestion: issue.suggestion || `avoid-ai-writing flagged this as ${issue.type}`,
        isTier1: issue.type === 'tier1',
        isStructural: isStructuralHit(issue),
      });
    }
  }

  const frameHits = scanFrames(text).map((h) => ({ ...h, highConfidence: true }));

  const cadence = analyzeCadence(text);
  const cadenceHits = cadence.findings;

  const signatureHits = [...scanBlandCleanSentences(text), ...scanToolArtifacts(text)];

  return { wordHits, frameHits, cadenceHits, signatureHits, text };
}

// Assigns each hit its paragraph index (paragraphs split on blank lines) so
// density can be measured LOCALLY. A long document (a research digest, a
// multi-page README) legitimately mentions several different tier1 words
// across UNRELATED sections without any single section being slop — whole-
// document density conflates "12 words scattered across 500 lines" with "12
// words in one paragraph", which measured false-positive on a real corpus
// (2026-08-04: AI-TELLS-RESEARCH.md, a doc cataloging AI-writing tells,
// naturally mentions many of them once each across unrelated sections).
function paragraphIndexOf(text, index) {
  if (index == null || index < 0) return -1;
  const before = text.slice(0, index);
  return (before.match(/\n\s*\n/g) || []).length;
}

// The gate: decide which findings are confident enough to surface, given the
// caller's mode. `file` mode (PostToolUse on Write/Edit) is lenient — content
// slop is common and cheap to flag. `chat` mode (Stop hook) requires a
// stronger bar before we ask the agent to redo a whole turn.
//
// CALIBRATED against a live-corpus run (2026-08-04, ~40 real technical docs):
// a SINGLE tier1 word hit ("robust", "landscape", "leverage", "actionable")
// is common in honest long-form technical writing and is NOT, on its own, a
// reliable slop signal. This gate requires >=2 DISTINCT tier1 hits in the
// SAME PARAGRAPH (density, not a single word, not scattered across a whole
// document) before word-tells surface; a single word tell only surfaces
// alongside another signal (a frame hit) that independently supports it.
export function gate(wordHits, frameHits, unearnedHits, mode, text = '', cadenceHits = [], signatureHits = []) {
  const surfaced = [];

  // Cadence and signature hits are the PRIMARY signals (measured strongest
  // discriminator, pure statistics / absence-of-grounding — see cli.mjs
  // header). Frame hits and structural word-tell hits are both full-shape
  // matches, already precision-tuned. All four surface unconditionally, no
  // density needed — each is independently a full-shape or statistical
  // signal, not a bare word.
  surfaced.push(...cadenceHits);
  surfaced.push(...signatureHits);
  surfaced.push(...frameHits);
  const structuralHits = wordHits.filter((h) => h.isStructural);
  surfaced.push(...structuralHits);

  // Tier1 vocabulary hits need corroboration: density WITHIN A PARAGRAPH
  // (>=2 distinct tier1 words in the same paragraph) OR support from an
  // independent frame/structural hit anywhere in the document.
  const tier1Hits = wordHits.filter((h) => h.isTier1);
  const byParagraph = new Map();
  for (const h of tier1Hits) {
    const p = paragraphIndexOf(text, h.index);
    if (!byParagraph.has(p)) byParagraph.set(p, []);
    byParagraph.get(p).push(h);
  }
  const tier1Supported =
    tier1Hits.length >= 1 &&
    (frameHits.length >= 1 || structuralHits.length >= 1 || cadenceHits.length >= 1 || signatureHits.length >= 1);
  for (const [, hits] of byParagraph) {
    const distinct = new Set(hits.map((h) => h.text.toLowerCase()));
    if (distinct.size >= 2 || tier1Supported) {
      surfaced.push(...hits);
    }
  }

  // Unearned-name: medium confidence by construction (corpus-relative,
  // already fails closed with no corpus) — surfaces in file mode only,
  // never forces a chat-mode redo on its own.
  if (mode !== 'chat') {
    surfaced.push(...unearnedHits);
  }

  return surfaced;
}

function locate(text, needle, index) {
  const upto = index >= 0 ? text.slice(0, index) : text.slice(0, text.indexOf(needle));
  const line = (upto.match(/\n/g) || []).length + 1;
  return line;
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  if (cmd !== 'check') {
    console.error('Usage: cli.mjs check [--file <path> | --stdin] [--json] [--chat]');
    process.exit(0);
  }

  const jsonOut = argv.includes('--json');
  const mode = argv.includes('--chat') ? 'chat' : 'file';
  const fileFlagIdx = argv.indexOf('--file');
  let filePath = fileFlagIdx >= 0 ? argv[fileFlagIdx + 1] : null;
  let rawText;

  if (argv.includes('--stdin')) {
    try {
      rawText = readFileSync(0, 'utf8');
    } catch {
      rawText = '';
    }
  } else if (filePath) {
    try {
      rawText = readFileSync(filePath, 'utf8');
    } catch {
      rawText = '';
    }
  } else {
    console.error('Usage: cli.mjs check [--file <path> | --stdin] [--json] [--chat]');
    process.exit(0);
  }

  if (!rawText || !rawText.trim()) {
    if (jsonOut) console.log(JSON.stringify({ findings: [] }));
    process.exit(0);
  }

  const { wordHits, frameHits, cadenceHits, signatureHits, text } = await analyze(rawText);
  const unearnedHits = filePath ? checkUnearnedNames(text, filePath) : [];
  const findings = gate(wordHits, frameHits, unearnedHits, mode, text, cadenceHits, signatureHits).map((h) => ({
    ...h,
    line: locate(text, h.text, h.index ?? -1),
  }));

  if (jsonOut) {
    console.log(JSON.stringify({ findings }, null, 2));
  } else if (findings.length) {
    for (const f of findings) {
      console.log(`[slopgate:${f.rule}] line ${f.line}: "${f.text}" — ${f.suggestion}`);
    }
  }

  process.exit(findings.length ? 1 : 0);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
