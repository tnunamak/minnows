---
name: convo-history
description: "Read past agent conversation history across harnesses (Claude Code, Codex CLI, Gemini CLI, Qwen Code) via the `convo` CLI. Use when the user asks to recall, print, search, or summarize earlier sessions — 'what did we discuss', 'print the last N messages', 'find where we talked about X', 'what did I ask you yesterday', 'pull up that earlier conversation', 'read our history', 'what was the conclusion in the other session'. The signature view is the clean transcript: each user prompt paired with only the agent's FINAL reply before the next prompt, tool-call noise stripped. Works cross-harness so you can read Codex/Gemini/Qwen logs from Claude and vice-versa. Triggers: 'last N messages', 'our conversation', 'previous session', 'what did we say', 'search my sessions', 'recall what I asked', 'read the transcript'."
---

# convo — cross-harness conversation history

`convo` reads your own (and sibling agents') session transcripts off disk and prints them
as a clean human/agent-readable transcript. It is the right tool whenever the user wants to
**look back at what was actually said** in this or another session — across Claude Code,
Codex CLI, Gemini CLI, and Qwen Code.

The legacy `list`, `show`, and `grep` commands are direct/raw readers, not a memory
store. Use them for the *current* live turn and recent source logs. The additive local
ledger commands below retain normalized user and assistant text after an explicit sync;
that snapshot deliberately excludes tool payloads, hidden reasoning, and system text.

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
convo grep 'X' --include-subagents      # also search Task-tool subagent transcripts
convo show <id> --from-user             # only the human's own messages, no agent replies
convo sync                     # incrementally persist normalized text from all sources
convo search 'SQLite ledger'   # ranked FTS search of retained message snapshots
convo status                   # source presence, retained rows, and parser health
```

Claude Code sessions are discovered under **every** `~/.claude*/projects` config dir on this
machine, plus `$CLAUDE_CONFIG_DIR` if it points somewhere else — not just the default
`~/.claude`. This matters because Claude Code itself only writes to whichever config dir is
active for a given invocation; a machine with more than one profile (or a temporarily
repointed `CLAUDE_CONFIG_DIR`) has real sessions split across roots, and all of them are in
scope by default.

`sync`, `status`, and `search` use `$CONVO_DATA_DIR` when set (useful for isolated
tests), otherwise `$XDG_DATA_HOME/minnows/convo`. `sync` is safe to repeat. A missing
raw log stays searchable from its retained normalized snapshot and is labeled as such;
search never claims it can reconstruct raw tool traces.

`sync` exits 0 after any recorded outcome, including `skipped`, `partial`, and
`pending`, and `live` sources. Exit 2 is reserved for an access, parser, transaction, or unsafe source-race
failure that prevented the ledger from recording a valid source status; `status` separates
those failures from partial coverage.

Claude, Codex, and Qwen JSONL sources are normalized in a bounded-memory streaming pass;
they are not limited by the former whole-source 64 MiB cap. Gemini remains a whole-document
parser, so `CONVO_MAX_SOURCE_BYTES` (64 MiB by default) still applies to Gemini only. A
malformed complete JSONL row marks that source `partial` while retaining valid surrounding
messages. An unterminated final row is `pending` until its writer completes it. Qwen
`agent-fork-call_*.jsonl` files remain separate physical sources; their filenames never
establish ancestry or merge them with another session.

The streaming pass hashes the exact initial byte boundary as it reads. If that same file
only grows before the final stat check, `sync` records the safely parsed prefix as `live`
only after a second hash of that prefix proves it was not rewritten; it then retries on
the next run without exit 2. Stable sources need no second hash. A rewrite, replacement, or
truncation remains an unsafe race and is never promoted from a guessed prefix.

Streaming keeps individual JSONL rows and normalized assistant messages bounded. An unusually large
normalized assistant reply is retained in ordered chunks and marks the source `partial`; `sync --verbose`
shows the diagnostic. Progress is interactive-only; JSON reports observed corpus bytes separately from
source bytes parsed during this run and the rare live-prefix verification bytes.

### Flags for direct/raw read commands (`list`, `show`, `grep`)
- `--harness claude|codex|gemini|qwen|claude-agent|all` (aliases `cc,cx,gm,qw,cca`; default
  **all**, which deliberately EXCLUDES `claude-agent` — see Subagent transcripts below).
- `--project SUBSTR` — filter by project/cwd. **Defaults to the current directory.**
- `--all-projects` — don't filter by cwd (use when the user means "any session anywhere").
  Mutually exclusive with `--project` — pass one or the other, never both (it errors).
- `--since 7d|24h|30m|2w|<ISO date>` — time window.
- `--json` — structured output (prefer this when you'll process the result rather than show it).
- `--no-color` — plain text.
- `grep --include-subagents` — also search Claude Code subagent (Task-tool) transcripts,
  labeled distinctly from their parent session.
- `show --from-user` — print only the human's own messages (no agent replies): the
  ask/decision trail for "give me the whole story" without the noise.

### `show --mode`
- `final` (default): user prompt + agent's **last** text reply per exchange.
- `text`: user prompt + **all** agent text blocks (no tools).
- `full`: everything, including tool calls/results/thinking (debugging a past run).

### Subagent transcripts

A Task-tool subagent's transcript is a physically separate file:
`<slug>/<sessionId>/subagents/agent-<agentId>.jsonl`. It is registered as its own harness,
`claude-agent`, rather than merged into `claude`'s turns — a project can spawn far more
subagent transcripts than top-level sessions in a single day, so folding them into the
default `list`/`grep --harness all` would bury the "recent sessions" signal those commands
exist to give. They are never silently dropped, though: use `--harness claude-agent` (alias
`cca`) to list/show them directly, or `grep --include-subagents` to fold them into a text
search. Output always labels a subagent turn distinctly (`CCA` badge, `SUBAGENT [agentType]
description`, and `(subagent of <parent-id>)` on grep hits) — never as if it were part of
the parent thread. Subagent transcripts are **not yet** synced into the durable local ledger
(`sync`/`search`/`status`) — `status` discloses this explicitly; use the direct readers for
subagent content.

## How to answer common requests

- **"print the last 20 messages we exchanged"** → `convo show -n 20` (current session is the
  most recent one in this project). If the user is mid-session and wants *this* conversation,
  that's exactly it. Show the output; don't reconstruct from memory.
- **"what did we decide about X / where did we discuss X"** → `convo grep 'X' --all-projects`,
  then `convo show <id>` on the hit to read the surrounding exchange.
- **"read my Codex session from yesterday"** → `convo list --harness codex --since 1d` then
  `convo show <id> --harness codex`.
- **"read my Qwen session"** → `convo list --harness qwen --since 1d` then
  `convo show <id> --harness qwen`.
- **Summarize a long past session** → `convo show <id> --mode final --json` and summarize the
  JSON, or pipe the text form. Use `--json` if you'll process it (keeps output structured).
  If the user asks what was done, requests key events or epics, or emphasizes brevity, read
  [the action-summary workflow](references/action-summary.md) before answering.

## Output / context hygiene

Transcripts can be large. Prefer `-n` to bound exchanges, `grep` to locate before `show`,
and `--json` when you intend to process rather than display. When the user just wants to *see*
the history, print the plain text form directly.

## Known limitations (state these if relevant)

- **Compaction history**: compaction metadata can refer to earlier logical context. `convo`
  reads the physical source file and does **not** reconstruct that pre-compaction context, so
  very long histories may be partial. It does not assume compaction creates separate physical
  session files. The older `claude-export` tool traces Claude-only links if full history is essential.
- **Gemini project tag**: Gemini chat files don't store the cwd, so their project shows as a
  hash, not a path. Filter Gemini sessions by `--all-projects` + `--since` or by id.
- **`+msg` count**: in `list`, a count like `133+msg` means the message count was capped
  during the fast header scan (huge rollout files); the full transcript is still read by `show`
  and `grep` — nothing is dropped there. `list` prints a one-line reminder of this whenever
  it shows a `+msg` entry.
- **`grep`/`list --limit` is a per-harness scan bound, not "search everything"**: if a
  harness has more candidate session files than `--limit` allows, `convo` retries uncapped
  ONCE for any harness that came back completely empty (so a real session buried behind a
  pile of newer non-session files is never silently invisible). If the retry still finds
  hits within the cap but more candidates existed, `grep` prints `scan bounded by --limit
  <n> per harness` to stderr — raise `--limit` or narrow with `--project`/`--since` for a
  scan you know covers every candidate.
- **`pi` and other harnesses**: not yet supported (no logs found on disk). The loader registry
  in `tools/convo/convo` (`HARNESSES`) is the extension point — add a `load_*`/`peek_*` pair.
- **`--from-user`/task-notification turns**: Claude Code injects background-task-completion
  notices (`<task-notification>...</task-notification>`) as `type: "user"` rows. They are
  not yet recognized as system/meta content the way `<command-name>`/compaction markers are,
  so they currently appear as if they were a real human message in every mode, including
  `--from-user`. Known, not silently different from existing `final`/`text`/`full` behavior.

## Implementation

Single stdlib-only Python script: `tools/convo/convo` (vendored into the shipped skill by `sync.sh`).
To add a harness, add a `load_<h>` + `peek_<h>` and an entry in the `HARNESSES` dict.
