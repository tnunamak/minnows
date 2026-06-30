---
name: uncompact
description: Recover a Claude Code session that was lost to context compaction. Use this skill whenever the user says they lost context, their session was compacted, they want to go back before a compaction, undo a compaction, or recover a previous session. Also trigger when they say "I lost my session", "Claude forgot everything", "can we go back", or anything suggesting unwanted context loss. Be proactive — compaction loss is frustrating and this skill reliably fixes it.
---

# Uncompact — traverse beyond compaction boundaries

Claude Code auto-compacts a session when context fills up, replacing the conversation
with a lossy summary. The original messages are still on disk. This skill writes a NEW
session file that picks up from just before a chosen compaction point, so the user can
`claude --resume` it and recover the lost context.

The `uncompact` CLI is on PATH (installed via minnows). Run it directly — no python path.

## How it works

Sessions live at `~/.claude/projects/<project-slug>/<session-uuid>.jsonl`. A compaction
boundary is a `user` message containing "continued from a previous conversation...". A file
can have several. The tool: finds boundaries → truncates before the chosen one (stripping
trailing noise that would re-trigger compaction) → rewrites every embedded `sessionId` to a
fresh UUID → saves the new file in the same project dir so `--resume` works.

## Running it

### 1. Identify the session
Ask for the session id, or find the most recent for a directory:
```bash
uncompact --find /path/to/project        # prints the most recent session id
```

### 2. List boundaries
```bash
uncompact <session-id> --list
```
Show the user the list — each entry has a snippet of what Claude was saying just before
that compaction. Ask which one to go back to (default: the most recent, i.e. 1 back).

### 3. Create the recovered session
```bash
uncompact <session-id>                # 1 compaction back (default)
uncompact <session-id> --go-back 2    # N compactions back
```

### 4. Give the user the resume command
The tool prints the exact command. It must run in a **new terminal** (not inside Claude),
from the original project directory:
```
cd /path/to/original/project
claude --resume <new-session-id>
```

## Critical details

- **New terminal required**: `--resume` inside an existing Claude session won't work.
- **Same project directory**: the new file must stay in the original `~/.claude/projects/<slug>/`. The tool handles this.
- **SessionId in every line**: the UUID is embedded in each record, not just the filename — the tool rewrites all occurrences.
- **Trailing noise causes re-compaction**: empty `last-prompt`/`system`/`permission-mode`/`file-history-snapshot` entries at the end trigger "Conversation compacted" on load; the tool trims them.
- **Multiple compactions**: `--list` shows all boundaries so the user can pick.

## Try multiple boundaries

Create one session per boundary and hand the user every resume command:
```bash
uncompact <session-id> --go-back 1
uncompact <session-id> --go-back 2
```

## Troubleshooting

- **"Conversation compacted" immediately on load** — the cutoff landed on a boundary/noise entry. Check the last lines of the new JSONL; the final line should be a real `user`/`assistant` message with non-empty `content`.
- **Session not found** — the tool searches all project dirs; if still missing, it may be from another machine or deleted.
- **Wrong cwd in the resume command** — the tool reads the real `cwd` from the records (falling back to slug-decode); for unusual paths, double-check.

## Known gaps

- **No evals yet** (`tools/uncompact/evals/` is empty) — behavior is verified manually against real compacted sessions.

## Implementation

`uncompact` CLI (minnows `tools/uncompact/`); shared session/boundary parsing in
minnows `lib/claude_sessions.py` (vendored beside the script when shipped).
