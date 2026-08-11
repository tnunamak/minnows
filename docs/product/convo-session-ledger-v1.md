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

The tool remains local-first and CLI-first. Minnows owns the code. Dotfiles installs it and may schedule background refresh later.

## Confirmed product decisions

- Keep the `convo` name. Do not create a second `ledger` or `sessions` executable.
- Use SQLite FTS5 for durable, message-level search. The local Python build supports FTS5.
- Keep raw-log reading as an explicit stateless path. The ledger must not become the only way to use `convo`.
- Keep normalized user and assistant text by default. This makes search useful after raw logs are deleted.
- Do not copy thinking, system prompts, or raw tool arguments and results into the durable default snapshot.
- Treat summaries as derived data. V1 does not use an LLM.
- Keep Waspflow lane metadata separate from transcript ancestry. Never join sessions from topic, time, cwd, or tmux names alone.
- Do not delete raw harness logs in V1.

## Users and jobs

### Tim

- See recent work across Claude Code, Codex, Gemini, and Qwen.
- Search by ordinary words and find the matching message, not only a session title.
- Resume with the correct harness command and working directory.
- Know whether a result is backed by a current raw log, a verified archive, a retained snapshot, or metadata only.
- See explicit Waspflow and tmux relationships without merging unrelated sessions.

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
convo related <session>            explicit ancestry and orchestration links
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

A later dotfiles-managed user timer may call `convo sync` every minute. Concurrent syncs must coalesce through one SQLite lock. The timer removes routine effort, but the CLI remains correct without it and reports stale data honestly.

## Evidence and retention contract

Track these facts separately. Do not compress them into one vague status.

- `source_status`: `present`, `missing`, `tombstoned`, or `corrupt`.
- `archive_status`: `none`, `unverified`, `verified`, or `failed`.
- `snapshot_policy`: `conversation`, `metadata_only`, or an explicit future policy.
- `completeness`: `complete_raw_verified`, `complete_snapshot_only`, `partial_range_verified`, `metadata_only`, or `unknown`.

Definitions:

- `missing` means the file vanished without a recorded owner action.
- `tombstoned` means the owner intentionally removed or expired it.
- `archived` is not a source status. A source may be tombstoned and have a verified archive.
- `verified` requires a locator, byte size, content hash, and successful verification time.
- A PDPP record, local path, or external URI is only an unverified locator until checked.
- FTS is a derived index. Its presence never proves that a raw transcript was complete.

V1 stores normalized user and assistant text in the durable ledger with owner-only permissions. Search can therefore survive source deletion, but the result must say `content_basis=snapshot`. The tool must not claim it can reproduce tool traces or exact raw events from that snapshot.

External deletion changes a source to `missing`; it does not imply user intent. A future retention command must use a dry run and refuse to remove a raw log unless the required snapshot or archive has been verified.

## Identity and relationships

Use a physical source file as the base evidence unit. Store the harness-native session ID when present, but do not assume it is globally unique.

Keep six identities distinct:

| Identity | Meaning |
| --- | --- |
| Physical session | One source file from one harness. |
| Logical thread | Physical sessions joined by explicit harness ancestry. |
| Compaction segment | One bounded portion of a physical session. |
| Parent or fork | An explicit ancestry edge. |
| Waspflow lane life | One orchestration run keyed by its stable lane identity. |
| Terminal location | A tmux pane or restored terminal associated with the work. |

Only explicit harness ancestry may join physical sessions into a logical thread. Waspflow lane UUIDs, lane markers, tmux sidecars, and cwd are association evidence; they do not alter transcript ancestry. Reused lane names must remain separate lane lives.

Every relationship row records its kind, source, observed time, and confidence. Human output labels inferred associations. Machine output never reports them as exact.

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
| `source_ranges` | Verifies incremental JSONL byte ranges. |
| `sessions` | Stores normalized identity, project, cwd, timestamps, completeness, and title. |
| `messages` | Stores retained user and assistant text with its role, source event ID, message time, and snapshot hash. |
| `messages_fts` | Projects retained messages into FTS5. |
| `compaction_segments` | Records explicit boundaries and completeness. |
| `relationships` | Records ancestry and orchestration associations with evidence and confidence. |
| `resume_targets` | Stores the command, cwd, support state, and derivation source. |
| `archive_locators` | Stores the location, size, hash, last verification, and error. |
| `source_state_events` | Preserves append-only evidence for status changes. |
| `index_runs`, `diagnostics` | Records parser failures, torn rows, conflicts, and timings. |

The database belongs under `$XDG_DATA_HOME/minnows/convo/`, not a cache directory. Use mode `0600` for the database and `0700` for its parent. The data may be the only retained conversation snapshot after source deletion.

## Machine contract

- Put data on stdout and progress or diagnostics on stderr.
- Add `schema_version` to every JSON object.
- Use stable exit codes for success, no match, partial result, invalid input, and internal failure.
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

The current corpus includes two Codex files near 1.5 GiB and one Claude file near 287 MiB. Until a streaming normalizer exists, sync must report oversized files as partial. It must not load them into memory or call the ledger complete.

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

### 4. Add explicit Convo and Waspflow relationships

- Import compaction and ancestry semantics from the existing handoff.
- Ingest Waspflow lane UUIDs and tmux resume sidecars as associations.
- Gate: reused lane names and similar sessions never merge without explicit ancestry.

### 5. Remove ongoing effort

- Make every read command refresh changed sources automatically.
- Measure contention and crash recovery before adding a timer.
- Add an optional dotfiles user timer only if it improves first-query latency.
- Gate: kill indexing mid-write, restart it, and get the same database as a clean run.

## Deferred work

V1 ends at the CLI ledger. Defer any feature that does not directly improve deterministic discovery, inspection, provenance, or resumption. This excludes AI-based interpretation, secondary interfaces, source-data management, inferred ancestry, and spend governance.

Add any of these features only after the CLI ledger proves insufficient. CodeBurn shows the value of shared calculations. It also shows how quickly a focused local tool can grow into several products.
