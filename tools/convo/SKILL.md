---
name: convo-history
description: "Read past agent conversation history across harnesses (Claude Code, Codex CLI, Gemini CLI) via the `convo` CLI. Use when the user asks to recall, print, search, or summarize earlier sessions — 'what did we discuss', 'print the last N messages', 'find where we talked about X', 'what did I ask you yesterday', 'pull up that earlier conversation', 'read our history', 'what was the conclusion in the other session'. The signature view is the clean transcript: each user prompt paired with only the agent's FINAL reply before the next prompt, tool-call noise stripped. Works cross-harness so you can read Codex/Gemini logs from Claude and vice-versa. Triggers: 'last N messages', 'our conversation', 'previous session', 'what did we say', 'search my sessions', 'recall what I asked', 'read the transcript'."
---

# convo — cross-harness conversation history

`convo` reads your own (and sibling agents') session transcripts off disk and prints them
as a clean human/agent-readable transcript. It is the right tool whenever the user wants to
**look back at what was actually said** in this or another session — across Claude Code,
Codex CLI, and Gemini CLI.

It is NOT a memory store and NOT for the *current* live turn — use it to read **prior**
exchanges that have been written to the session log.

## The one thing to understand

The default view (`--mode final`) is the **clean transcript**: for each real user prompt it
shows that prompt plus **only the agent's last text reply** before the next user prompt —
tool calls, tool results, thinking blocks, and system/meta injections are stripped. This is
almost always what someone means by "print our last N messages" or "what did we say". Reach
for `--mode text` (all agent text) or `--mode full` (everything incl. tools) only when asked.

## Commands

```
convo list                     # recent sessions in the CURRENT project, all harnesses
convo list --all-projects --since 3d
convo show                     # clean transcript of the most recent session here
convo show -n 20               # just the last 20 exchanges (user + agent's final reply)
convo show <id-or-path>        # a specific session (id substring or file path)
convo show <id> --mode full    # debug view with tool calls/results/thinking
convo show <id> --json         # structured exchanges for programmatic use
convo grep 'god files'         # search turn text across sessions in this project
convo grep 'X' --all-projects --since 30d
```

### Common flags (on every command)
- `--harness claude|codex|gemini|all` (aliases `cc,cx,gm`; default **all**)
- `--project SUBSTR` — filter by project/cwd. **Defaults to the current directory.**
- `--all-projects` — don't filter by cwd (use when the user means "any session anywhere").
  Mutually exclusive with `--project` — pass one or the other, never both (it errors).
- `--since 7d|24h|30m|2w|<ISO date>` — time window.
- `--json` — structured output (prefer this when you'll process the result rather than show it).
- `--no-color` — plain text.

### `show --mode`
- `final` (default): user prompt + agent's **last** text reply per exchange.
- `text`: user prompt + **all** agent text blocks (no tools).
- `full`: everything, including tool calls/results/thinking (debugging a past run).

## How to answer common requests

- **"print the last 20 messages we exchanged"** → `convo show -n 20` (current session is the
  most recent one in this project). If the user is mid-session and wants *this* conversation,
  that's exactly it. Show the output; don't reconstruct from memory.
- **"what did we decide about X / where did we discuss X"** → `convo grep 'X' --all-projects`,
  then `convo show <id>` on the hit to read the surrounding exchange.
- **"read my Codex session from yesterday"** → `convo list --harness codex --since 1d` then
  `convo show <id> --harness codex`.
- **Summarize a long past session** → `convo show <id> --mode final --json` and summarize the
  JSON, or pipe the text form. Use `--json` if you'll process it (keeps output structured).

## Output / context hygiene

Transcripts can be large. Prefer `-n` to bound exchanges, `grep` to locate before `show`,
and `--json` when you intend to process rather than display. When the user just wants to *see*
the history, print the plain text form directly.

## Session completeness and provenance

`convo show` reads one **physical session**: one log file produced by one harness. Claude
Code and Codex CLI normally retain multiple compactions in that same file, so valid turns
from both before and after a compaction are included. A **compaction segment** is therefore
not a new session or proof that the logical conversation moved to another file.

`convo show --json` includes an additive `transcript` object. It reports the physical-file
read status (`complete` or `complete_prefix` when an active writer left a torn final JSONL
row), compaction segment count, replay records safely deduplicated, and any explicit harness
ancestry. The default transcript stays clean: synthetic compaction summaries are never shown
as user prompts.

Logical threads can span physical sessions or forks, but `convo` only reports harness-recorded
ancestry; it never guesses links from timestamps, topics, paths, or summary text. Codex native
`parent_thread_id` and `forked_from_id` values are exposed as provenance and are not joined
into the current transcript. Claude's UUID graph can be replayed or conflicting, so `convo`
keeps the verified chronological physical-file transcript and records leaf/duplicate diagnostics
rather than reconstructing an uncertain branch. No ancestry is treated as a claim that a
logical thread is complete.

Gemini supports its JSON snapshots and the verified JSONL mutation stream. Its top-level JSON
`summary` is metadata, not a real user prompt. Gemini does not currently provide verified
cross-file thread ancestry or a working-directory path; its project remains a hash when that
is all the harness stores.

## Known limitations (state these if relevant)

- **Logical-thread recovery**: `convo show` intentionally selects and loads one physical
  session. It does not concatenate parents, forks, sidechains, or subagent files, even when
  a harness records an identifier for one. This avoids duplicated replacement history and
  unverified cross-file joins.
- **Gemini project tag**: Gemini chat files don't store the cwd, so their project shows as a
  hash, not a path. Filter Gemini sessions by `--all-projects` + `--since` or by id.
- **Codex `+msg` count**: in `list`, a count like `133+msg` means the message count was capped
  during the fast header scan (huge rollout files); the full transcript is still read by `show`.
- **`pi` and other harnesses**: not yet supported (no logs found on disk). The loader registry
  in `tools/convo/convo` (`HARNESSES`) is the extension point — add a `load_*`/`peek_*` pair.

## Implementation

Single stdlib-only Python script: `tools/convo/convo` (vendored into the generated skill by
`./sync.sh`). To add a harness, add a `load_<h>` + `peek_<h>` and an entry in the
`HARNESSES` dict.
