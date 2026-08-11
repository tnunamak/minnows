# Convo session ledger V1

Status: product brief
Date: 2026-08-11
Owner: Minnows

## Outcome

Extend `convo` into the local session ledger for Tim's agent harnesses. It should answer four questions quickly:

1. What sessions exist, and which ones are active or recent?
2. Where did we discuss or decide something?
3. How do I inspect or resume that work?
4. What evidence still exists if a raw log was archived, deleted, lost, or corrupted?

The tool remains local-first and CLI-first. Minnows owns the code. Dotfiles may install it, but has no
runtime role in the ledger design.

## Confirmed product decisions

- Keep the `convo` name. Do not create a second `ledger` or `sessions` executable.
- Use SQLite FTS5 for durable, message-level search. The local Python build supports FTS5.
- Keep raw-log reading as an explicit stateless path. The ledger must not become the only way to use `convo`.
- Keep normalized user and assistant text by default. This makes search useful after raw logs are deleted.
- Do not copy thinking, system prompts, or raw tool arguments and results into the durable default snapshot.
- Treat summaries as derived data. V1 does not use an LLM.
- The core ledger depends only on supported harness logs and SQLite. It does not ingest tmux, Waspflow,
  resurrection sidecars, cgroups, or dotfiles-specific state.
- Do not delete raw harness logs in V1.

## Users and jobs

### Tim

- See recent work across Claude Code, Codex, Gemini, and Qwen.
- Search by ordinary words and find the matching message, not only a session title.
- Resume with the correct harness command and working directory.
- Know whether a result is backed by a current raw log, a verified archive, a retained snapshot, or metadata only.

### Agents and scripts

- Read bounded context without loading a full transcript.
- Receive stable, versioned JSON or NDJSON.
- Follow a result to the next read window or an exact resume command.
- Detect partial data, parser failures, and unsupported resume operations.

## CLI surface

Preserve the existing `list`, `show`, and `grep` behavior unless a versioned migration says otherwise.

```text
convo                              recent sessions for the current project
convo list [filters]               deterministic session list
convo search <query>               ranked FTS search by message
convo grep <pattern>               exhaustive literal or regex search
convo show <session>               bounded clean transcript
convo show <session> --around <message-id> --window 10
convo resume <session>             print the safest resume command
convo resume <session> --exec      execute only after explicit request
convo status                       source, index, retention, and parser health
convo sync                         incremental refresh; safe to repeat
convo doctor                       paths, parsers, SQLite, and stale-data checks
```

Common output options:

```text
--json                             one versioned JSON value
--ndjson                           one versioned record per line
--no-color                         stable plain text
--all-projects                     override the current-project default
--harness <name>                   filter by harness
--since <duration-or-date>         filter by message time
```

Search results should show:

- harness, project, title, and last real message time;
- the matching message and a small context window;
- the first and last conversation bookends;
- raw-source and completeness labels;
- the exact next `show` and `resume` commands.

Rank `search` by text relevance, then matching-message time. Do not rank it by source-file mtime. Keep `grep` exhaustive and unranked so it can answer "show every occurrence."

The first ledger release is additive. `list`, `show`, and `grep` continue to read raw logs directly. `sync`, `status`, and ranked `search` use the durable database. Before any existing command moves to the ledger, add and test an explicit `--no-index` path that preserves direct raw-log behavior.

## Freshness without a daemon

Warm reads query the ledger without walking every source directory. Human output reports the last completed sync time. Callers can request refresh explicitly when they need current data.

`convo` has no timer or resident process. Invoke `convo sync` explicitly from the owner’s chosen
automation if desired; concurrent invocations coalesce through one SQLite lock.

## Evidence and retention contract

Track these facts separately. Do not compress them into one vague status.

- `source_status`: `present`, `skipped`, `partial`, `pending`, `missing`, `corrupt`, or `oversized`.
- `archive_status`: `none`, `unverified`, `verified`, or `failed`.
- `snapshot_policy`: `conversation`, `metadata_only`, or an explicit future policy.
- `completeness`: `complete_raw_verified`, `complete_snapshot_only`, `partial_range_verified`, `metadata_only`, or `unknown`.

Definitions:

- `missing` means the file vanished without a recorded owner action.
- `tombstoned` means the owner intentionally removed or expired it.
- `archived` is not a source status. A source may be tombstoned and have a verified archive.
- `verified` requires a locator, byte size, content hash, and successful verification time.
- FTS is a derived index. Its presence never proves that a raw transcript was complete.

V1 stores normalized user and assistant text in the durable ledger with owner-only permissions. Search can therefore survive source deletion, but the result must say `content_basis=snapshot`. The tool must not claim it can reproduce tool traces or exact raw events from that snapshot.

External deletion changes a source to `missing`; it does not imply user intent. V1 does not remove raw
logs or manage archives.

## Identity and relationships

Use a physical source file as the base evidence unit. Store the harness-native session ID when present, but do not assume it is globally unique. The core CLI intentionally keeps only harness-native identity:

| Identity | Meaning |
| --- | --- |
| Physical session | One source file from one harness. |
| Harness-native session ID | The identifier retained from that source when present. |

The ledger never infers cross-process ancestry from matching lane names, cwd, timestamps, or conversation
text. V1 has no relationship tables and permanently excludes tmux/resurrection-sidecar ingestion. The
future boundary is generic append-only `agent-provenance/v1` receipts: producer, receipt ID, observed time,
subject and object exact native IDs, relationship kind, evidence, and confidence. Exact-ID receipts can
join automatically. Explicit but non-verifiable claims remain visibly `asserted`; heuristic candidates
remain visibly `inferred` with their evidence and confidence. Neither is silently promoted to exact or
used to merge sessions. Waspflow may emit such receipts as one producer, but `convo` has no runtime
dependency on Waspflow.

## Resume contract

Each result uses one of these states:

- `exact`: harness and working directory produce a known resume command.
- `cwd_only`: the tool can return to the project but cannot select the exact session.
- `unsupported`: no safe resume operation is known.

`convo resume` prints the command by default. It does not execute, open a terminal, or write to the clipboard unless the user asks.

## Minimal data model

| Table | Responsibility |
| --- | --- |
| `source_files` | Records harness, path, size, mtime, hashes, parser version, and source status. |
| `messages` | Stores retained user and assistant text with its role, ordinal, and message time. |
| `messages_fts` | Projects retained messages into FTS5. |

The database belongs under `$XDG_DATA_HOME/minnows/convo/`, not a cache directory. Use mode `0600` for the database and `0700` for its parent. The data may be the only retained conversation snapshot after source deletion.

## Machine contract

- Put data on stdout and progress or diagnostics on stderr.
- Add `schema_version` to every JSON object.
- Use stable exit codes for success, no match, invalid input, and internal failure. A retained `partial`,
  `pending`, or `skipped` source is success with explicit coverage metadata; exit 2 means the source
  could not be recorded with a valid source status.
- Keep list and search excerpts bounded by default.
- Never emit full transcript text in an error.
- Make interrupted sync safe to rerun.
- Make unchanged sync idempotent at the row and relationship level.

## Performance targets

These are product targets, not claims about the current implementation.

- Warm `list`, `status`, and ranked `search`: p95 under 200 ms.
- Cold source discovery: under 10 seconds for the current 10,267 files.
- Incremental indexing of one ordinary changed source: p95 under 1 second.
- Full backfill of the current 19 GiB corpus: under 15 minutes and 512 MiB maximum resident memory, with progress, interruption safety, and resume.
- Bounded `show` around a message: p95 under 50 ms after indexing.
- Search and list must not parse every full source log after the first index.

Claude, Codex, and Qwen JSONL sources stream through a bounded temporary spool, including multi-GiB
files. Gemini remains a whole-document parser and reports an explicit `oversized` state when it exceeds
the configured cap. A malformed complete JSONL record makes only that source `partial`; valid surrounding
messages remain searchable. An unterminated final row is `pending` rather than corrupt.

## Privacy and safety

- No network, telemetry, or upload in the core CLI.
- Treat the database and all exports as sensitive.
- Exclude system prompts, hidden reasoning, and raw tool payloads from the default durable snapshot.
- Bound excerpts and redact known credential shapes in diagnostic output.
- Make export explicit. Do not create Markdown transcripts automatically.
- Do not assume a deleted local source has a PDPP or other backup.

## Build slices and gates

### 1. Freeze current behavior and add the storage spine

- Add deterministic fixtures for every current harness, compaction, replay, torn final row, and malformed event case.
- Preserve current `convo list/show/grep` results through the storage change.
- Create SQLite schema, migrations, source discovery, and idempotent incremental import.
- Gate: reindex twice; normalized rows and FTS results do not change.

### 2. Ship useful search, status, and resume

- Add ranked message search, bounded context, bookends, source labels, and exact next commands.
- Add versioned JSON and NDJSON contracts with golden tests.
- Add resume support states and print-only default behavior.
- Gate: recover a known old discussion whose file mtime is misleading.

### 3. Prove deletion and corruption semantics

- Add source hashes, range checkpoints, source-state events, and archive locators.
- Test external disappearance, intentional tombstone, truncated file, changed bytes, verified archive, and failed archive.
- Gate: a deleted raw log remains searchable only from an allowed snapshot and is labeled `complete_snapshot_only`.

### 4. Keep external orchestration out of the core

- Do not add tmux, resurrection, cgroup, PDPP, dotfiles, or Waspflow runtime dependencies.
- Permanently exclude tmux/resurrection sidecars. Do not infer parentage from names, timestamps, cwd, or
  text similarity.
- A later generic `agent-provenance/v1` importer may automatically join exact native IDs, but must render
  asserted and inferred candidates with their grades and must never silently promote or merge them.

## Product boundary

V1 ends at the explicit CLI ledger. It does not add automatic refresh, a timer, a resident daemon,
source-data management, relationship tables, or external orchestration integration.
