# Reader profiles

A profile is a short, explicit record of what one reader needs. It is a file
the reader can read and correct. It is not a persona and not a guess presented
as fact.

## Where profiles live

`readerfit profile <name>` searches, in order, every directory in
`$READERFIT_PROFILES` (colon-separated), then `~/.config/readerfit/profiles`.
A profile is `<name>.md`, lower-case, for example `anna.md`. Keep profiles of
real people out of public repositories.

## Format

```markdown
---
name: anna
role: chief executive; decides company strategy and fees
updated: 2026-11-20
---

## Knows
- The product and the business model. Not the protocol internals.

## Words
- Says "users", not "members". (2026-11-20, chat correction)

## Wants
- Questions she owns, framed as outcomes, each with a recommendation. (2026-11-20, chat)
- Plain bullets, no checkboxes; the format of the doc she approved: <link>. (2026-11-20, chat)

## Avoid
- Engineering choices she does not own. (2026-11-20, chat)

## Anchor
- <link to a document this reader accepted>
```

## Rules

1. **Every line has a date and a source** (a chat correction, the reader's own
   document, a meeting). A line without provenance is a guess; mark it
   `(unconfirmed)`.
2. **Generalise one step, not more.** "Used checkboxes" becomes "copy the
   markup of the reference doc". It does not become "never use lists".
3. **Keep it under about 40 lines.** A profile nobody reads is no profile. Merge
   lines that say the same thing; keep the newer date.
4. **Contradictions:** when a new correction contradicts a line, keep both only
   if both are true in different situations, and say which situation each
   covers ("catch-ups: short"; "explanations he asked for: build up from
   scratch"). Otherwise replace the old line.
5. **Expire:** a line not confirmed in about 90 days, and contradicted once,
   gets removed.
6. **Use the profile, don't just read it.** After drafting, check each `Wants`
   and `Avoid` line against the draft.
