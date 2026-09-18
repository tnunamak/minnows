// slopgate — syntactic-frame rules.
//
// avoid-ai-writing's analyzeText is a word-level detector: it catches tier-1
// vocabulary ("leverage", "seamlessly") but is blind to slop SHAPES that use
// ordinary words in a tell-tale sentence pattern. Measured gap (2026-08-04):
// it misses "in the world of X", "not just X, it's Y", "empowers X to Y",
// "unlocking new possibilities". Each rule here is a SHAPE, not a word list —
// swapping the filler noun doesn't defeat it. Precision-first: every regex
// requires the full grammatical frame, not just a keyword, to avoid flagging
// ordinary sentences that happen to share a word with the pattern.

export const FRAME_RULES = [
  {
    id: 'in-the-world-of',
    re: /\bin (?:today's|the) world of\b/gi,
    suggestion: 'name the actual domain, or delete the preamble',
  },
  {
    id: 'not-just-x-but-y',
    // "not just a linter, it's a philosophy" / "not just fast, but reliable" /
    // "isn't just fast — it's reliable" / "wasn't just a bug. it was a wake-up
    // call". Broadened 2026-08-16 to also match isn't/wasn't/aren't/weren't
    // (not only bare "not") and an em-dash/period continuation (not just a
    // comma), since the antithetical-parallelism family reads the same
    // across all of these — only the linking punctuation/contraction varies.
    re: /\b(?:not|isn'?t|wasn'?t|aren'?t|weren'?t) just [a-z][\w' -]{2,40}?[,.—-]\s*(?:it'?s|it is|it was|they'?re|but|it also)\b/gi,
    suggestion: 'state the one true claim directly, drop the false-contrast setup',
  },
  {
    id: 'empowers-to',
    re: /\bempowers?\s+\w+(?:\s+\w+){0,3}\s+to\b/gi,
    suggestion: 'use "lets" or "gives" — say what actually changes',
  },
  {
    id: 'unlock-possibilities',
    re: /\bunlocks?\s+(?:new\s+)?(?:possibilities|potential|opportunities)\b/gi,
    suggestion: 'name the specific capability instead of this stock phrase',
  },
  {
    id: 'stands-testament',
    re: /\bstands? as (?:a\s+)?testament to\b/gi,
    suggestion: 'state the fact directly; drop the testament framing',
  },
  {
    id: 'game-changer',
    re: /\b(?:game[- ]changer|changes? the game|revolutioniz\w+)\b/gi,
    suggestion: 'say what specifically changed, skip the hype label',
  },
  {
    id: 'whether-x-or-y-rule',
    // "Whether you're a beginner or an expert, ..." — the AI-tic false-binary lead-in.
    re: /\bwhether you'?re\s+[\w' -]{2,30}\s+or\s+[\w' -]{2,30},/gi,
    suggestion: 'address the reader directly, drop the false-binary preamble',
  },
  {
    id: 'its-not-about-its-about',
    // "It's not about X, it's about Y" / "It's not about X. It's Y." —
    // broadened 2026-08-16 to allow the continuation to drop the repeated
    // "about" (a common paraphrase: "it's not about speed. it's trust.").
    re: /\bit'?s not (?:just\s+)?about [\w' -]{2,40}?[.,;-]\s*it'?s(?: about)?\b/gi,
    suggestion: 'state the point once, plainly',
  },
  {
    id: 'triple-adjective-stack',
    // "a fast, reliable, and scalable solution" — tricolon marketing stack
    // immediately before a generic noun. Requires the generic-noun anchor so
    // legitimate technical enumerations (three real distinct nouns) don't fire.
    re: /\b\w+,\s*\w+,\s*and\s+\w+\s+(?:solution|platform|experience|approach|tool)\b/gi,
    suggestion: 'name what is actually true about it, not a hype adjective stack',
  },
];

export function scanFrames(text) {
  const hits = [];
  for (const rule of FRAME_RULES) {
    rule.re.lastIndex = 0;
    let m;
    while ((m = rule.re.exec(text))) {
      hits.push({
        type: 'frame',
        rule: rule.id,
        text: m[0].trim(),
        index: m.index,
        severity: 'high',
        suggestion: rule.suggestion,
      });
      if (m[0].length === 0) rule.re.lastIndex++;
    }
  }
  return hits;
}
