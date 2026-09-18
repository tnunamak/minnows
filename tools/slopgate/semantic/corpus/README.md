# Labelled corpus

## Label provenance — read this before trusting any threshold

| directory | who labelled it | trust |
| --- | --- | --- |
| `pr-slop/315.txt` | **Tim, explicitly** ("here's a terrible one") | high |
| `pr-slop/318.txt` | me, by analogy to 315 | medium — same defect, unconfirmed |
| `pr-good/*` | **me, guessing** | **low — not confirmed by Tim** |
| `../human/*` | real Django maintainer commits | high (authorship is a fact) |
| `../ai/*` | one local model's drafts | high for authorship, unrepresentative of frontier slop |

The `pr-good` labels are the weak link. Thresholds derived from them encode my
taste, not the owner's, which is the exact failure this tool exists to avoid.
Replace them with owner-confirmed examples before trusting precision numbers on
PR bodies.

## What 315 established

PDP-Connect/pdpp#315 is 3,591 words for a spec edit. Its defect is not the kind
the original bank was built for — there is no unearned benefit claim and no
guarantee language. It is relentless tutorial exposition:

- explains what PDPP is and what its `spec-*.md` files are for
- explains what RFC 2119 and RFC 8174 say about keyword case
- glosses its own domain terms mid-sentence ("a resource server, in this
  protocol, is the component that stores a person's records and serves them to
  clients, filtered by a grant — the artifact recording which streams, fields
  and time range a person consented to share")

The original 14 rules barely fired on it (`explains_the_obvious` at 0.33). Four
rules were added to target the real defect, and on 315 they score 0.90, 0.93,
0.91 and 0.75.

This is the audience-gate inversion the `pr-writing` skill already documents:
for a repo insider, glossing IS the failure. The rules encode that.

## Observed separation on the four added rules

| PR | teaches_project | defines_std | glosses_terms | length_excess |
| --- | --- | --- | --- | --- |
| 315 (owner: slop) | 0.90 | 0.93 | 0.91 | 0.75 |
| 318 (same defect) | 0.34 | 0.06 | **0.93** | 0.81 |
| 311 | 0.36 | 0.63 | 0.76 | 0.48 |
| 365 | 0.63 | 0.62 | 0.67 | 0.51 |
| 354 | 0.40 | 0.03 | 0.46 | 0.62 |
| 325 | 0.26 | 0.04 | 0.19 | 0.25 |
| 328 | 0.20 | 0.07 | 0.13 | 0.22 |

`teaches_the_project` separates cleanly. `glosses_own_terms` fires hard on 318,
which reads as a genuine hit ("npm (the package registry)", "pnpm, the workspace
package manager" — terms every contributor here knows), but 311 at 0.76 and 365
at 0.67 are unresolved: they may be true positives or the rule may be loose.

## What is still needed

Owner labels on more PRs, particularly:

1. Two or three more confirmed slop bodies, to check the four new rules fire for
   the same reason rather than fitting 315 specifically.
2. Three or four confirmed good bodies, to set thresholds against a real
   precision ceiling instead of my guesses.

Thresholds for the four new rules are currently set by hand at 0.45-0.55 and are
NOT calibrated. They are deliberately conservative pending those labels.

## Doc-kind rules: derived from Tim's own corrections

The five `--kind doc` rules are not my taste. They come from the words Tim
repeats when steering agents on documents, mined from agent sessions:
"neutral tone", "don't repeat yourself", "concise", "editorial", "executive",
"facts", "citations", "positive statements".

Verbatim instances that produced each rule:

- `repeats_itself` — "speak plainly without repeating yourself in a neutral tone
  only on the key information"; "never repeat ourselves"; "can you say it again
  without repeating yourself and without overexplaining"
- `editorial_tone` — "use a neutral tone"; "is this appropriate for the
  technical brief or is it more strategic/business-oriented?"
- `negative_framing` — "A TEE or ZK proof would add little value. this is a
  negative statement, can we avoid negative statements? what would it steer us
  toward?"
- `unsourced_assertion` — "write up the facts about the proposed architecture";
  the recurring "facts, citations" pair
- `overexplains` — "focus on just the key points without repeating yourself or
  overexplaining"

### A correction worth recording

I initially called `corpus/docs/dr-briefing-excerpt.md` "genuinely good
technical writing" on my own reading. The session log shows Tim steered that
document hard before it reached the state I read: no names or owner tags, no
build-status language, and a challenge on whether the framing was technical or
business-oriented. I was praising the post-correction version and taking credit
for it on the document's behalf. The excerpt is kept as a doc-kind sample, NOT
as a quality exemplar, and it still trips `repeats_itself` (0.88) and
`unsourced_assertion` (0.91).
