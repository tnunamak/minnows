import { WORD, splitSentences, wordCount } from './prose.mjs';

// slopgate — sentence-cadence/rhythm signals, ported from slopkit's
// cadence_score.py (2026-08, /home/tnunamak/.tmp/slopkit-study). Sourced
// reasoning: avoid-ai-writing's own v3.22.0 measurement, Pangram Labs, and
// the StoryScope academic paper independently found rhythm statistics a far
// stronger AI/human discriminator than vocabulary in general — but ported
// thresholds were tuned on short chat-transcript outputs, not real
// technical documents, and did NOT hold up under direct testing here.
//
// DROPPED (2026-08-04): length-uniformity (cv < 0.28) and same-length-run
// (>=3 consecutive sentences within 2 words) both fired on a paragraph of
// genuinely honest, freshly hand-written technical prose written during
// this build specifically to test them — disciplined explanatory writing
// is naturally fairly uniform in sentence length, which is statistically
// indistinguishable from the ported AI-tell threshold. The "zero
// register-risk" claim for cadence does NOT hold for these two sub-rules;
// kept in this module (and still computed/returned) for diagnostic use, but
// excluded from findings/the gate by default.
//
// KEPT: repeated-start (same 2-word sentence opener >=3x within a nearby
// proximity window). This one survived tightening against real prose,
// including a genuine RFC-style spec that repeats the same defined term
// as a subject across MANY sentences (see MUST-PASS `rfc-enumeration-uniform`
// control) — proximity-window + count>=3 correctly distinguishes normal
// spec repetition from a templated AI run.
//
// MINIMUM SENTENCE COUNT: cadence statistics are unstable on short text (a
// 2-sentence reply has an undefined "rhythm"). Silent below this floor —
// same fail-closed posture as unearned-name.mjs on an empty corpus.
export const MIN_SENTENCES_FOR_CADENCE = 6;

function mean(nums) {
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : 0;
}

function pstdev(nums) {
  if (nums.length < 2) return 0;
  const m = mean(nums);
  return Math.sqrt(mean(nums.map((n) => (n - m) ** 2)));
}

// Longest run of consecutive sentences whose word counts are all within
// `tolerance` words of their immediate neighbor — mechanically uniform
// rhythm, the AI-cadence tell.
function longestSameLengthRun(lengths, tolerance) {
  let longest = 0;
  let current = 1;
  for (let i = 1; i < lengths.length; i++) {
    if (Math.abs(lengths[i] - lengths[i - 1]) <= tolerance) {
      current += 1;
    } else {
      longest = Math.max(longest, current);
      current = 1;
    }
  }
  return Math.max(longest, current);
}

function firstWords(sentence, n) {
  const words = (sentence.toLowerCase().match(WORD) || []).slice(0, n);
  return words.join(' ');
}

export function analyzeCadence(text, opts = {}) {
  const lengthCvThreshold = opts.lengthCvThreshold ?? 0.28;
  const runTolerance = opts.runTolerance ?? 2;
  const runThreshold = opts.runThreshold ?? 3;
  // >=2 was measured too permissive even within a proximity window on real
  // spec-style prose, where the same defined term ("The resource server...")
  // legitimately opens several nearby, unrelated-content sentences by
  // construction — normal for normative technical writing, not a tell.
  const repeatedStartThreshold = opts.repeatedStartThreshold ?? 3;

  const sentences = splitSentences(text);
  if (sentences.length < MIN_SENTENCES_FOR_CADENCE) {
    return { applicable: false, sentenceCount: sentences.length, findings: [] };
  }

  const lengths = sentences.map(wordCount);
  const avg = mean(lengths);
  const cv = avg ? pstdev(lengths) / avg : 0;
  const longestRun = longestSameLengthRun(lengths, runTolerance);

  // Repeated-start is scoped to a PROXIMITY WINDOW, not the whole document.
  // slopkit's cadence_score.py was measured on short chat-transcript
  // outputs (one AI response, ~200-2000 words) where "repeated start"
  // anywhere in the text is a real signal. A multi-thousand-word technical
  // document naturally reuses common 2-word openers ("the check", "this
  // is") purely by chance across unrelated sections — measured 2026-08-04:
  // 456 false positives on a real ~40-file corpus at whole-document scope.
  // Requiring repeats within a short sentence window reproduces the actual
  // AI-tell shape (a templated run of consecutive-ish sentences) without
  // false-triggering on coincidental reuse across a long document.
  const proximityWindow = opts.proximityWindow ?? 8;
  const starts = new Map();
  sentences.forEach((s, i) => {
    const key = firstWords(s, 2);
    if (!key) return;
    if (!starts.has(key)) starts.set(key, []);
    starts.get(key).push(i);
  });
  const repeatedStarts = [...starts.entries()].filter(([key, idxs]) => {
    if (key.split(' ').length < 2) return false;
    if (idxs.length < repeatedStartThreshold) return false;
    // At least `repeatedStartThreshold` of the occurrences must fall within
    // one proximity window of each other (checked via a sliding count).
    for (let i = 0; i < idxs.length; i++) {
      let count = 1;
      for (let j = i + 1; j < idxs.length; j++) {
        if (idxs[j] - idxs[i] <= proximityWindow) count++;
      }
      if (count >= repeatedStartThreshold) return true;
    }
    return false;
  });

  // cv/longestRun are computed and returned for diagnostics (opts.includeLengthSignals
  // re-enables them as findings for on-demand/CI deep review — see SLOPGATE.md) but are
  // NOT emitted as findings by default: both measured false-positive on honest technical
  // prose (see module header). Only repeated-start gates the hook by default.
  const findings = [];
  if (opts.includeLengthSignals) {
    if (cv < lengthCvThreshold) {
      findings.push({
        type: 'cadence-uniform-length',
        rule: 'cadence-uniform-length',
        text: sentences[0].slice(0, 60),
        severity: 'high',
        suggestion: `sentence lengths are unnaturally uniform (coefficient of variation ${cv.toFixed(2)}, below ${lengthCvThreshold}) — vary sentence length`,
      });
    }
    if (longestRun >= runThreshold) {
      findings.push({
        type: 'cadence-same-length-run',
        rule: 'cadence-same-length-run',
        text: sentences[0].slice(0, 60),
        severity: 'high',
        suggestion: `${longestRun} consecutive sentences within ${runTolerance} words of each other — break up the rhythm`,
      });
    }
  }
  for (const [start, idxs] of repeatedStarts) {
    findings.push({
      type: 'cadence-repeated-start',
      rule: 'cadence-repeated-start',
      text: `"${start}..." (${idxs.length}x)`,
      severity: 'high',
      suggestion: 'vary how sentences open — repeated openers read as templated',
    });
  }

  return { applicable: true, sentenceCount: sentences.length, cv, longestRun, findings };
}
