# Routes in detail

Each route lists when it applies, its shape, its budget, and the failure it
prevents. The route follows the reader's next action, never the shape of the
source material.

## Answer

- **When:** the reader asked a direct question ("what would the purpose be?",
  "is that normative?", "does X need Y?").
- **Shape:** the answer in line 1, in the reader's terms. At most three lines of
  support. A follow-up offer only when there is real depth they may want.
- **Fails when:** the answer sits in paragraph three, after background. Or the
  reply answers a different, nearby question (the one the agent finds easier).

## Digest

- **When:** the reader pastes or points at a long message, thread, report or
  document and wants to know what it means for them.
- **Shape:**
  1. Line 1: what, if anything, the reader must do (answer, decide, approve), and by when.
  2. The few items addressed to the reader or that change their plans.
  3. Claims that contradict what the reader knows or said, marked plainly.
  4. If a reply is due: a suggested reply in their voice.
- **Budget:** 3 to 7 items. Everything else stays in the source.
- **Fails when:** the digest mirrors the source's sections, explains the source's
  background, or keeps items "for completeness". A filtered list that is short
  but says nothing about what to do is also a failure.

## Catch-up

- **When:** the reader returns after time away ("catch me up", "status?",
  "I just restored this session", "what remains?").
- **Shape:**
  1. One line: where things stand, against the goal the reader set.
  2. What changed since they last read (outcomes, not activity).
  3. What needs them now, numbered.
  4. What happens next and when; what you will do without them.
- **Budget:** fits on one screen. One short line per item.
- **Fails when:** it lists every step taken, uses internal names for work items
  ("lane 4", "tranche B", "the r3 run"), quotes counts without saying what they
  mean, or hides the one thing the reader must do among status lines.

## Decisions

- **When:** the reader must choose, or asks "what do you need from me?",
  "what are the decisions?".
- **Shape:** numbered decisions. Each one: the choice in plain words, the
  realistic options, a recommendation or default, and the cost of waiting.
  Opinions of the reader (when you know them) as sub-bullets with their reason.
  Information that needs no decision goes in a separate short list, or nowhere.
- **Budget:** only decisions that are the reader's to make. Engineering details
  get a default and are mentioned only if the reader would want to overrule them.
- **Fails when:** options differ only in mechanism, a decision is phrased as a
  topic ("fees and grants"), a question has a verb without an object ("should
  anyone check fees?": check what, against what?), or decisions the agent could
  make alone are pushed to the reader.

## Steps

- **When:** the reader will do something by hand ("tell me how", "what do I run",
  "make it brainless").
- **Shape:** numbered steps; each one a single action with the exact command, URL
  or button text. Say where to run it. Assume nothing is installed, configured or
  remembered. End with what the reader will see when it worked, and what to send
  back.
- **Fails when:** a step bundles several actions, says "configure X" without how,
  or answers "I don't know" instead of finding out.

## Explanation

- **When:** the reader says they don't understand, asks "why", asks for the
  architecture, or asks to be built up from scratch.
- **Shape:** dependency order: each line uses only ideas already introduced.
  Name the concrete parts (this screen, this server, this file). Define each new
  noun against something the reader already knows. Use a small table when
  comparing parts.
- **Budget:** longer is fine. This is the route where a short key-point list
  fails, because key points assume the understanding the reader lacks.
- **Fails when:** the explanation uses the terms it is meant to explain, or
  responds to "I don't understand X" by repeating X with more words.

## Inventory

- **When:** the reader asks for "all", "a full table", "every candidate".
- **Shape:** one simple table, one row per item, the columns they asked for, plus
  one column for the judgment they need (confidence, owner, status). Sorted by
  what they act on first.
- **Fails when:** the agent filters to the items it is sure of, or replaces the
  table with prose.

## Questions

- **When:** the reader wants questions to ask other people.
- **Shape:** numbered questions, each answerable in one line, each naming the
  thing decided, who decides it and against what. The reader's own view, when
  given, goes as a sub-bullet with its reason. Drop questions the sources already
  answer and questions the recipient cannot answer.
- **Fails when:** questions are vague ("check fees"), hide an assertion that may
  be wrong, or include noise to look thorough.

## Message

- **When:** the reader will send the text in their own name (chat, email).
- **Shape:** the reader's register. The point first, at most 3 to 8 short
  bullets if a list helps, link last. No section labels, no "Here's a summary".
  Use the recipient's vocabulary.
- **Fails when:** it explains more than the recipient needs, decorates (bold
  labels, emoji, headers), or refers to context the recipient never saw.

## Handoff

- **When:** someone else will take over work.
- **Shape:** problem statement, what exists (with links), what has been tried,
  what the receiver must know to start, open questions, and who to ask.
- **Fails when:** it is a chat report of what the agent did, instead of what the
  next person needs.

## Document

- **When:** a reader outside the conversation will open it later (proposal,
  decision note, read-ahead, design doc, team brief).
- **Pick by next action:**
  - Decide: a decision note. The ask, options with comparable effects, the
    recommendation, assumptions, deadline. One screen, evidence linked.
  - Prepare for a meeting: a read-ahead. Why now, the outcome wanted, what to
    read, open disagreements.
  - Build from it: a design document. Problem, constraints, design, alternatives,
    open questions. Longer is fine.
  - Learn the current state: a reference or report with claims, evidence and
    limits.
- **Mixed readers:** the first layer must read without definitions for the
  reader with the least context. Precise terms go in a later section or tab.
- **Anchor:** match the length and structure of a document this reader already
  accepted, when one exists, unless the user said something newer that conflicts
  with it ("this reader has less context", "they can't choose between these").
  Newer words win.
- **Fails when:** an executive gets an engineering design, an engineer gets an
  executive note, or a reader who must decide gets engineering choices they do
  not own. Move those to "for information" with defaults.
