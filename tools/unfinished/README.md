# unfinished

`unfinished` finds unfinished business in your agent work: decisions an agent asked you for and never got, plans you deferred, work an agent promised, questions nobody answered, sessions cut off mid-task, PRs left open. It checks the top items against the current state and keeps a ledger you can read and close.

It is built for people who run many agents at once. Its sources:

- Claude Code, Codex, Gemini, Qwen and Pi sessions, read through [`convo`](../convo/)'s ledger
- T3 Code threads
- open PRs and review requests on GitHub
- waspflow lanes
- note folders

## How it works

1. **Segment.** Split sessions into your turns, the agent's end-of-turn replies, and session tails.
2. **Tag cheaply.** A decision model, [Jev](https://docs.typesafe.ai), answers yes/no questions about each segment: did a person write it, does it defer work, does it ask you something, does it say the work is complete, was the agent cut off. Answers are cached.
3. **Select candidates by steering events, not silence.** Candidates are replies that ask you something, your deferrals, your re-raises ("what about X?") and the reply before them, unanswered turns, the last reply of each day, and cut-off tails. Worker sessions count as evidence, not as sources.
4. **Extract.** A small Claude model turns each candidate into a typed item: action, kind, owner, object, repo, quote, why, weight. The code then drops any item whose quote does not appear in the source.
5. **Gather evidence.** Code checks later turns of the same session, the state of every PR the item names (repo-qualified), and later messages that mention the item's identifiers.
6. **Adjudicate.** A stronger model writes the verdict, next action, priority and effort. Text alone can make an item "likely done", never "done".
7. **Check.** For the top items, a headless Claude with read-only tools (`gh` reads, `git log`, `convo`, `t3code` reads) checks the current state and rewrites the items that are still open. In a hand-checked sample, about 70% of items that looked open had already been finished elsewhere. This step is what makes the top of the ledger trustworthy.
8. **Render.** The ledger has these sections:
   - "Start here"
   - "Top 25", which holds checked items only
   - checked items that are still open
   - parked projects
   - items not yet checked, grouped by project
   - work in flight
   - items found done
   - items likely done
   - items probably stale
   - hygiene bundles
   - coverage

## Data folder

Everything personal lives in one folder: `$UNFINISHED_HOME`, default `~/Documents/unfinished`. Nothing is written anywhere else.

| Path | Contents |
|---|---|
| `LEDGER.md` | the ledger. Tick a box to close an item; the next run records it. |
| `ledger.jsonl` | the same items, machine-readable, with stable ids |
| `evidence/<id>.md` | the redacted source excerpt and later evidence for each item |
| `decisions.jsonl` | your closes, drops and snoozes. Append-only, and it always wins over the pipeline. |
| `config.json` | optional settings, described below |
| `state/` | `work.sqlite3`, `jev-cache.sqlite3`, the run lock. You can also add `coverage.md` (shown at the end of the ledger) and `resolve-brief.md` (overrides the checker's brief). |

`config.json` keys, all optional (defaults are in `lib/uf_config.py`):

```json
{
  "owner_name": "Sam",
  "claude_config_dir": "~/.claude-work",
  "secret_commands": {"TYPESAFE_API_KEY": "my-secret-helper TYPESAFE_API_KEY"},
  "extract_model": "haiku",
  "adjudicate_model": "claude-sonnet-5-5",
  "resolve_model": "claude-sonnet-5-5",
  "window_days": 30,
  "automated_prefixes": ["\\[waiter\\]"],
  "relayed_prefixes": ["captain"],
  "waspflow_homes": ["~/.local/state/waspflow"],
  "inbox_dirs": [{"dir": "~/code/app/inbox", "project": "app", "triage_file": "~/code/app/inbox/triage.md", "exclude_regex": "triage"}],
  "misses_files": [{"path": "~/notes/MISSES.md", "id_prefix": "tools", "project": "tooling"}],
  "open_section_files": [{"path": "~/notes/reviews/README.md", "project": "reviews", "id_prefix": "reviews"}],
  "repo_roots": ["~/code"],
  "github": true,
  "t3": true
}
```

## Use

```bash
unfinished run                 # rebuild the ledger (incremental and cached)
unfinished run --check 48      # also check the top 48 unchecked items
unfinished list                # open items, highest first
unfinished done s:1a2b3c "shipped in #412"
unfinished snooze pr:org/repo#12 7d
unfinished self-test           # offline checks of redaction, quote checks and tick import
```

`systemd/` has a nightly user timer. To use it:

```bash
cp tools/unfinished/systemd/* ~/.config/systemd/user/
systemctl --user enable --now unfinished.timer
```

## Privacy and cost

- **Privacy.** Hosted calls (Jev, and Claude through `claude -p`) receive segments of your sessions. `jev.redact()` masks likely secrets before every hosted call and on every evidence page: keys, tokens, JWTs, URL passwords, `?code=`/`?token=` parameters, and long hex and base64 strings. Redaction is a mitigation, not a guarantee.
- **Cost.** A 30-day backfill over several thousand sessions costs a few dollars of Jev and a few tens of dollars of Claude usage. Nightly runs only pay for new segments and new items.
