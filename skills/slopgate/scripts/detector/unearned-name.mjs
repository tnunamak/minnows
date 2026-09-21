// slopgate — narrow port of judge.mjs's UNEARNED-NAME check (from the
// pdpp-site-concept judge, 2026-08). That original is HTML-specific and
// tied to one site's own vocabulary corpus; this port keeps only the
// mechanically decidable core and makes the corpus generic per-repo:
//
//   A name is earned by recurrence. A coined term used in a copular naming
//   frame ("X is the ...") exactly ONCE anywhere in the repo's own prose
//   (this file plus README/docs it can find) reads as ornamental, not real.
//
// FAILS CLOSED: if no repo corpus can be found (no README, no docs dir),
// this check does not run at all rather than guessing — an empty corpus
// would make everything look unearned, which is a false-positive machine,
// not a real signal. This is the load-bearing precision decision: recall on
// this rule is intentionally low so it only ever fires with real evidence.

import { readFileSync, readdirSync, existsSync } from 'node:fs';
import path from 'node:path';

// Two copular naming shapes, matching judge.mjs's namingFrames() intent:
//   subject-side: "The Coherence Layer is the beating heart of ..."
//   predicate-side: "... is the Coherence Layer that coordinates ..."
// Both require a Title-Case multi-word (or single capitalized) phrase acting
// as the NAME half of the copula — a coined term, not an ordinary noun.
const SUBJECT_NAMING_FRAME =
  /\b(?:The|Our|Your|This)\s+((?:[A-Z][\w-]*\s+){0,2}[A-Z][\w-]{2,30})\s+(?:is|are|becomes|remains)\s+(?:the|its|our|your|a|an)\b/g;
const PREDICATE_NAMING_FRAME =
  /\b(?:is|are|becomes|remains)\s+(?:the|its|our|your)\s+((?:[A-Z][\w-]*\s+){0,2}[A-Z][\w-]{2,30})\b/g;

const GENERIC_EXEMPT = new Set([
  'same', 'only', 'first', 'last', 'best', 'main', 'default', 'result',
  'case', 'point', 'goal', 'issue', 'problem', 'solution', 'answer',
]);

function findRepoRoot(startDir) {
  let dir = startDir;
  for (let i = 0; i < 8; i++) {
    if (existsSync(path.join(dir, '.git'))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

function buildCorpus(repoRoot) {
  const parts = [];
  const candidates = ['README.md', 'CLAUDE.md', 'CONTRIBUTING.md'];
  for (const c of candidates) {
    const p = path.join(repoRoot, c);
    if (existsSync(p)) {
      try {
        parts.push(readFileSync(p, 'utf8'));
      } catch {
        /* unreadable — skip */
      }
    }
  }
  const docsDir = path.join(repoRoot, 'docs');
  if (existsSync(docsDir)) {
    try {
      for (const e of readdirSync(docsDir, { withFileTypes: true })) {
        if (e.isFile() && e.name.endsWith('.md')) {
          try {
            parts.push(readFileSync(path.join(docsDir, e.name), 'utf8'));
          } catch {
            /* unreadable — skip */
          }
        }
      }
    } catch {
      /* unreadable dir — skip */
    }
  }
  return parts.join('\n');
}

function countOccurrences(haystack, needle) {
  if (!needle) return 0;
  const re = new RegExp(`\\b${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'g');
  return (haystack.match(re) || []).length;
}

export function checkUnearnedNames(text, filePath) {
  const repoRoot = filePath ? findRepoRoot(path.dirname(path.resolve(filePath))) : null;
  if (!repoRoot) return []; // fail closed — no repo, no corpus, no check

  const corpus = buildCorpus(repoRoot);
  if (!corpus.trim()) return []; // fail closed — no README/docs to compare against

  const findings = [];
  const seen = new Set();
  for (const frameRe of [SUBJECT_NAMING_FRAME, PREDICATE_NAMING_FRAME]) {
    frameRe.lastIndex = 0;
    let m;
    while ((m = frameRe.exec(text))) {
      const name = m[1].trim();
      const key = name.toLowerCase();
      if (seen.has(key)) continue;
      if (GENERIC_EXEMPT.has(key)) continue;
      if (/^[A-Z0-9]{2,6}$/.test(name)) continue; // bare acronym, exempt

      const inFileCount = countOccurrences(text, name);
      const inCorpusCount = countOccurrences(corpus, name);
      // Earned if it recurs in the file itself OR appears anywhere in the
      // repo's own README/docs corpus — either is real-world evidence of
      // standing use, not a one-off coinage.
      if (inFileCount > 1 || inCorpusCount >= 1) continue;

      seen.add(key);
      findings.push({
        type: 'unearned-name',
        rule: 'UNEARNED-NAME',
        text: m[0].trim(),
        index: m.index,
        severity: 'medium',
        suggestion: `"${name}" is named here but does not recur in this file or in the repo's README/docs — use a plain description instead of coining a term for a one-off concept`,
      });
    }
  }
  return findings;
}
