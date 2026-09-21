// slopgate — shared prose-line extraction and sentence splitting, used by
// both cadence.mjs and signature.mjs. Was originally duplicated (each had
// its own naive `.split(SENTENCE_BOUNDARY)`); consolidated 2026-08-04 after
// signature.mjs's copy missed cadence.mjs's hardening and flagged bare
// citation markers like `[linear-filters]` as their own "sentence".

export const SENTENCE_BOUNDARY = /(?<=[.!?])\s+/;
export const WORD = /\b[\w'-]+\b/g;

// `text` is expected to have already passed through strip.mjs's
// stripNonProse, which blanks non-prose spans (inline code, paths, quotes)
// to same-length whitespace so callers needing character offsets stay
// valid. Rules operating on WORDS (not offsets) need real prose lines, so
// this drops: headings, list items, table-row remnants, legend/arrow
// fragments, lines carrying a blanked (stripped) span, and short non-
// sentence fragments with no terminator.
const BLANKED_SPAN = /\s{3,}/;
// A trailing bare citation marker ("[linear-filters]", "[1]") is not part
// of the sentence — strip it before checking if content remains.
const TRAILING_CITATION_MARKER = /(?:\s*\[[\w-]+\])+\s*$/;

export function extractProseLines(text) {
  const lines = text.split('\n');
  const prose = [];
  for (const line of lines) {
    let trimmed = line.trim();
    if (!trimmed) continue;
    if (/^#{1,6}\s/.test(trimmed)) continue; // heading
    if (/^[-*+]\s/.test(trimmed)) continue; // bullet list item
    if (/^\d+[.)]\s/.test(trimmed)) continue; // numbered list item
    if (trimmed.includes('|')) continue; // table row remnant
    if (trimmed.includes('→') || trimmed.includes('->')) continue; // legend/arrow fragment
    if (BLANKED_SPAN.test(trimmed)) continue; // line contains a blanked (stripped) span
    trimmed = trimmed.replace(TRAILING_CITATION_MARKER, '').trim();
    if (!trimmed) continue; // was ONLY a citation marker — not a sentence
    if (/^\[[\w-]+\]/.test(trimmed)) continue; // reference-link-shaped line remnant
    if (!/[.!?]$/.test(trimmed) && trimmed.length < 40) continue; // short non-sentence fragment
    prose.push(trimmed);
  }
  return prose.join(' ');
}

export function wordCount(sentence) {
  return (sentence.match(WORD) || []).length;
}

// Real sentences carry signal; short labels/status markers ("Not
// pushed.", "**executed**.") do not — these fragments cluster at
// near-identical (tiny) length purely because they're all short labels,
// manufacturing false statistics for any rule that measures sentence shape.
export const MIN_WORDS_PER_SENTENCE = 5;

export function splitSentences(text) {
  const prose = extractProseLines(text);
  return prose
    .split(SENTENCE_BOUNDARY)
    .map((s) => s.trim())
    .filter((s) => /[.!?]$/.test(s)) // drop trailing fragments with no terminator
    .filter((s) => wordCount(s) >= MIN_WORDS_PER_SENTENCE);
}
