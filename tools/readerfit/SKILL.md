---
name: readerfit
description: Choose the right kind, length, structure and words for anything a person will read, before writing it. Use when you report to the user (catch-up, status, "key points", "what needs me", a summary of a pasted message or thread, a list of decisions or questions, steps to run, an explanation), when you draft a message, question list, handoff or document for someone else, and whenever the user corrects how your output reads ("too long", "wall of text", "speak plainly", "I don't understand X", "not a useful question", "use her language"). Not for code, commit messages or PR text (use pr-writing) or sentence-level slop cleanup alone (use slopgate).
---

# readerfit

Most corrections to agent writing are decided before the first sentence: the
wrong kind of output, the wrong length for the reader's time, the reader's
context assumed, or the source's structure copied instead of filtered. Polishing
sentences afterwards does not fix these. So decide the form first, write second,
check third.

## 1. Fill the card (silently, before writing)

Six lines. Fill them from the request, the conversation and the reader's profile.
Show the card only if the user asks, with one exception: **for a document or
message someone will send in their own name, show them the card (kind, outline,
length, the reason for the kind) and wait for their OK before drafting.** A
wrong kind costs an hour; the card costs them seconds and carries facts only
they have. When they question the form of a draft, rebuild the card; do not
revise the draft.

The user's newest words about the reader or the output beat an anchor document
and any profile line. Name the conflict on the card.

1. **Reader:** who reads it, what they last saw from you, what they do not know.
   Run `readerfit profile <name>` when the reader has a profile.
2. **Next action:** what the reader does right after reading: decide, act, run
   steps, understand, reply, forward, or file it.
3. **Kind:** pick one route from the table below.
4. **Keep:** only the items that change the reader's next action. Name the filter
   in one phrase ("asks addressed to Tim, decisions, risks to his plan").
5. **Budget:** lines or items. Default for a reader skimming a chat: the point in
   line 1, at most 7 items, one line each. The job sets length: a reader who
   must build understanding or asked for "all" gets more.
6. **Words:** terms this reader may not know. Replace each with plain words, or
   define it in one clause at first use. Use the reader's own words for things.

The request's own form instructions ("numbered", "a table", "key points",
"I have 5 minutes", "no jargon") override every default here.

## 2. Pick the route

| Reader's next action | Route | Shape |
|---|---|---|
| Get an answer to their question | Answer | Answer in line 1. At most three supporting lines. |
| Know what a pasted message, thread or doc means for them | Digest | Items that need them first (asks, decisions, corrections of their claims), then what changed for them. End with what they could reply, if a reply is due. Never mirror the source's sections. |
| Re-orient after time away | Catch-up | Where things stand in one line. What changed. What needs them. What happens next, and when. |
| Decide | Decisions | Numbered decisions. Each: the choice in plain words, options, a recommendation or default, the cost of waiting. The user's own opinion goes as a sub-bullet with its reason. |
| Do something by hand | Steps | Numbered, copy-pasteable, assume nothing is installed or known, end with what they will see when it worked. |
| Understand something new | Explanation | Build up in dependency order. Name concrete parts the reader can see. Define each new noun against things they already know. Longer is fine. |
| See everything of one type | Inventory | One simple table, one row per item, with the column they asked for (confidence, owner, status). |
| Ask others something | Questions | Each question names what is decided, by whom, against what. Drop questions the source already answers. |
| Send a message in the user's voice | Message | Plain, short, no section labels, in the user's register, link last. |
| Hand off work | Handoff | Problem statement, what exists, references, what the receiver must know to start. |
| Receive a document | Document | Read [references/kinds.md](references/kinds.md); use `sendable-docs` if installed. |

When two routes fit, pick the one that matches the next action, not the
source. A long technical message asking the user for a decision is a Decisions
route, not a summary of the message. Details of each route, with failure
examples: [references/kinds.md](references/kinds.md).

## 3. Write

1. **Line 1 is the thing the reader must know or do.** A busy reader may stop there.
2. **Filter, then compress.** Remove items that do not change the reader's next
   action before you shorten what is left. Short and unfiltered is still a wall.
3. **One idea per line. Say each point once.**
4. **No private vocabulary.** Names you or other agents coined during the work
   (lane names, tranche, gate names, internal IDs, "Option E", "#9") mean nothing
   to the reader unless defined. Use the concrete thing: the file, the screen,
   the person, the command.
5. **Say what it means for the reader**, not how you got there. No process narrative.
6. **Format:** numbered lists for steps, decisions and asks; a simple table for
   comparisons and inventories; plain bullets for facts. In chat, no headings for
   short replies, no bold lead-in labels, no checkboxes, no blockquotes. If the
   reader pointed at a reference document, copy its structure and markup exactly.
7. **Your opinion is marked as yours**, once, with its reason.
8. **Don't overcorrect.** "Shorter" for a catch-up does not mean "shorter" for an
   explanation the reader asked to understand from scratch.

## 4. Check before sending

1. Run `readerfit check --file DRAFT --budget N --medium chat|slack|doc`. Fix
   every `high` finding. It catches over-budget length, markup the medium
   doesn't want, back-references, repeated lines and undefined acronyms.
2. **Fresh-reader pass.** Reread as the reader, knowing only what they last saw.
   Mark every noun they would stop at, and every line that does not change what
   they do. Fix or cut each one.
3. **Next-action test.** Write, without looking, the one thing the reader will do
   after reading. If the draft does not make it obvious in the first lines, the
   draft is wrong, not the test.
4. Documents and messages others will receive: also run `slopgate`.

## 5. Learn from corrections

When the reader corrects the form ("too long", "what does X mean", "use her
words"), fix the output, then add one line to their profile with the date and
where it came from. Generalise one step: "used checkboxes" becomes "copy the
markup of the reference doc they point to". Format and rules:
[references/profiles.md](references/profiles.md).

## Example

Request: "The hosting vendor sent a long email about their pricing change. I won't read it. Key points?"

Weak (mirrors the source, explains everything):

> **Background.** The vendor is moving from per-seat to usage pricing...
> **Option A.** Stay on the legacy plan. Pros: ... Cons: ...
> **Option B.** Migrate now... (40 lines)

Strong (Digest route, filtered for what needs the user):

> The vendor needs your choice by 1 December: keep the old plan until March, or move now for 10% off.
> 1. Our usage last month would cost about the same on either plan.
> 2. One claim is wrong: they say we use 40 seats; the admin page shows 12.
> 3. Suggested reply: "We'll stay until March. Please correct our seat count to 12."
