// slopgate — bland_clean_sentence + tool-artifact rules, ported from
// slopkit's signature_score.py (2026-08, /home/tnunamak/.tmp/slopkit-study).
// Sourced reasoning: bland_clean_sentence is the most sophisticated single
// detector found in either surveyed project because it requires ABSENCE of
// grounding as the condition, not presence of a banned word — the same
// "measure against what's actually there" move as ABSTRACT-NOUN-DRIFT,
// structurally more precise than a flat wordlist.
//
// Em-dash rate (also ported from signature_score.py) was TRIED and DROPPED
// after live-corpus measurement — see cli.mjs header for the full account.

import { WORD, splitSentences } from './prose.mjs';

const BLAND_TERMS = [
  'better', 'clearer', 'clarity', 'effective', 'efficient', 'helpful',
  'improve', 'improved', 'improves', 'improvement', 'impact', 'outcome',
  'outcomes', 'people', 'process', 'results', 'stronger', 'success',
  'support', 'supports', 'teams', 'value', 'work',
];

const LOAD_MARKER =
  /\b(?:because|but|unless|if|when|except|requires?|must|cannot|risk|cost|constraint|evidence|proof|metric|owner|deadline|failure|tradeoff|specific|number|example|workflow|source|sourced)\b/gi;

const CAPITALIZED_TERM = /\b(?:[A-Z][a-z0-9]+(?:[- ][A-Z][a-z0-9]+){0,4}|[A-Z]{2,})\b/g;
const CAPITALIZED_STOPWORDS = new Set([
  'A', 'An', 'And', 'As', 'At', 'But', 'By', 'For', 'From', 'I', 'If', 'In',
  'It', 'Its', 'On', 'Or', 'That', 'The', 'This', 'To', 'We', 'When', 'With',
  'Without', 'You',
]);
const NUMBER_ANCHOR = /\b\d+(?:[.,:]\d+)*(?:%| percent|x|ms|s|m|h| days| years)?\b/gi;

function countLiteralTerms(sentence, terms) {
  const lower = sentence.toLowerCase();
  return terms.filter((t) => new RegExp(`(?<!\\w)${t}(?!\\w)`, 'i').test(lower));
}

function anchorCount(sentence) {
  let count = 0;
  count += (sentence.match(NUMBER_ANCHOR) || []).length;
  const caps = sentence.match(CAPITALIZED_TERM) || [];
  count += caps.filter((c) => !CAPITALIZED_STOPWORDS.has(c)).length;
  count += (sentence.match(LOAD_MARKER) || []).length;
  return count;
}

// bland_clean_sentence: >=8 words AND >=2 bland/abstract terms AND ZERO
// factual anchor (number, capitalized proper-noun-shaped term, or a
// load-bearing structural word like "because"/"requires"). Flags prose that
// is clean, confident, and grounds nothing.
export function scanBlandCleanSentences(text) {
  const findings = [];
  for (const sentence of splitSentences(text)) {
    const words = sentence.match(WORD) || [];
    if (words.length < 8) continue;
    const bland = countLiteralTerms(sentence, BLAND_TERMS);
    if (bland.length < 2) continue;
    if (anchorCount(sentence) > 0) continue;
    findings.push({
      type: 'bland-clean-sentence',
      rule: 'bland-clean-sentence',
      text: sentence.slice(0, 80),
      severity: 'high',
      suggestion: `reads as confident and clean but grounds nothing — no number, name, or concrete constraint (bland terms: ${bland.join(', ')})`,
    });
  }
  return findings;
}

// Tool-artifact leftovers: citation-marker leaks and unfilled placeholders.
// Near-zero false-positive by construction — no legitimate technical prose
// contains these strings; they only appear when an AI tool's own scaffolding
// leaked into the output unedited.
const TOOL_ARTIFACT_PATTERNS = [
  { id: 'chatgpt-citation-marker', re: /\bturn\d+search\d+\b/gi },
  { id: 'openai-content-reference', re: /\bcontentReference\b/g },
  { id: 'openai-citation-tag', re: /\boaicite\b|\boai_citation\b/gi },
  { id: 'ai-tool-utm-source', re: /[?&]utm_source=(?:chatgpt|openai|claude|perplexity|gemini)\b/gi },
  { id: 'unfilled-placeholder', re: /\[(?:TODO|PLACEHOLDER|INSERT[ _-][A-Z]+|YOUR[ _-][A-Z]+)\]/g },
  { id: 'lorem-ipsum', re: /\blorem ipsum\b/gi },
];

export function scanToolArtifacts(text) {
  const findings = [];
  for (const p of TOOL_ARTIFACT_PATTERNS) {
    p.re.lastIndex = 0;
    let m;
    while ((m = p.re.exec(text))) {
      findings.push({
        type: 'ai-placeholder',
        rule: p.id,
        text: m[0],
        index: m.index,
        severity: 'critical',
        suggestion: 'AI-tool scaffolding leaked into the output unedited — remove it',
      });
      if (m[0].length === 0) p.re.lastIndex++;
    }
  }
  return findings;
}
