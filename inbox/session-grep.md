# Feature request: `session-grep` — find the agent session that discussed topic X

**Filed:** 2026-07-14
**Kind:** tool minnow (CLI-first, optional SKILL.md wrapper)
**Origin:** real friction, dotfiles session — "which Claude/Codex session was I talking to
yesterday about bravo SSD firmware?" Took ~8 hand-rolled `find | grep | python3 -c` passes
to answer what should be one command.

## Problem

Claude Code and Codex both persist every session as JSONL on disk, but there's no way to
ask *"which session discussed X, and when?"* and get a resumable answer. Doing it by hand
hit four distinct traps, each of which is a requirement below:

1. **mtime lies about topic recency.** Long-running sessions get *resumed*, so their file
   mtime is "today" even when the topic was discussed weeks ago (the bravo session started
   2026-05-21, was active 2026-07-13, and the firmware work was buried mid-file). Filtering
   by mtime alone surfaced ~90 irrelevant resumed sessions and nearly buried the real one.
   → Need to filter/rank by the timestamp of the *matching message*, not the file mtime.

2. **Keyword ≠ topic.** `bravo` matched 99× in an unrelated Prometheus-config session (host
   label noise); `firmware` matched systemd man-page output. The real signal was `bravo`
   **co-occurring with** `ssd|nvme|firmware` **in actual message prose**.
   → Need multi-term co-occurrence scoring, not single-keyword grep.

3. **Two formats, two locations, two schemas.**
   - Claude: `~/.claude/projects/<slug>/<uuid>.jsonl`, text at `message.content` (string
     *or* array of `{type,text}` blocks); subagents nested under `subagents/agent-*.jsonl`.
   - Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, text at `payload.content`;
     `cwd` and timestamps in their own envelope shape.
   → Tool must normalize both into `(source, session_id, cwd, ts, role, text)` rows.

4. **Blob pollution.** base64 tokens and hashes false-matched `p41`, `bravo`, `raVo`, etc.
   → Restrict matching to human/assistant *message text*; skip tool-output/data payloads,
   or at least rank prose matches far above blob matches.

## Proposed CLI surface

```
session-grep <terms...> [--since DATE] [--until DATE] [--source claude|codex|all]
             [--cwd SUBSTR] [--json] [--limit N]

  <terms...>     one or more terms; a session scores higher when MORE distinct terms
                 co-occur in the same session (AND-ish ranking, not OR flood)
  --since/--until   filter on the matching MESSAGE timestamp, not file mtime
  --source          default all; searches both Claude + Codex trees
  --cwd             narrow to sessions whose cwd contains SUBSTR (e.g. "sandbox")
  --json            machine output for agents; default is human table
```

**Human output** — one block per session, best-scoring first:

```
proxmox-bravo NVMe/SSD firmware        score 7  claude  cwd:~/sandbox
  last active 2026-07-13 23:48 · first 2026-05-21
  [2026-07-13 23:48] assistant: WD SN740 firmware updated 73103012 → 73914109; AER storm gone…
  [2026-07-14 03:48] assistant: SK hynix P41 2TB — NOT updated (51060A20 → target 51061A20)…
  resume: claude --resume 42569d54-d339-4dac-a5d0-9fe01269743e   (from ~/sandbox)
```

The **resume line is the payload** — the whole point is "get me back into that conversation,"
so emit the exact `claude --resume <uuid>` / `codex resume <path>` incantation incl. the cwd.

## Non-goals / notes

- Not MCP — plain CLI, agent shells out (matches minnows tool doctrine).
- Don't index/watch; scan-on-demand is fine at personal scale (a few thousand sessions).
- Respect `.gitignore`/`.aiignore` spirit — read-only over the transcript dirs, never writes.
- Optional `SKILL.md` so an agent can discover it for "which session did we…" questions.

## Reference: the manual version this replaces

Session located by hand:
`~/.claude/projects/-home-tnunamak-sandbox/42569d54-d339-4dac-a5d0-9fe01269743e.jsonl`
(bravo NVMe thread; SN740 boot-drive firmware flashed 2026-07-13, SK hynix P41 flash still open).
Found only after ~8 `find -newermt | grep -c | python3` passes to defeat traps 1–4 above.
