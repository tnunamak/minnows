# Convo streaming backfill execution plan

Status: ready for implementation after the ledger draft
Date: 2026-08-11

## Objective

Index the complete supported corpus without loading a whole session into memory. Preserve the existing raw-reader behavior and the ledger's snapshot provenance.

## Observed scale

- Supported stores currently contain about 10,000 physical sources and 19 GiB of logs.
- The largest Codex files are about 1.5 GiB. The largest Claude file is about 287 MiB.
- A metadata-only pass over 9,983 supported sources completes in 1.64 seconds with 38.9 MiB maximum resident memory after batched writes.
- The current in-memory normalizers are capped at 64 MiB. Larger sources remain explicit `oversized` rows and make sync exit 2.

## Stop condition

The work is complete when a clean database can index every supported source with these properties:

1. Maximum resident memory stays below 512 MiB.
2. An interrupted run resumes without duplicating or losing messages.
3. A torn final row remains pending until completed.
4. A malformed complete row produces an explicit partial-source diagnostic without damaging other sources.
5. The full 19 GiB backfill completes within 15 minutes on the current workstation.

## Implementation slices

### 1. Stream normalized messages

Add one streaming adapter per JSONL harness. Feed normalized user and assistant messages directly into a bounded database batch. Do not construct a full `Session` object for backfill. Keep Gemini's small JSON documents on the existing whole-document path.

Before choosing a JSON parser, measure the largest physical line in each store. Python's standard JSON decoder still needs one complete value in memory. If a single event is too large for the memory target, evaluate a streaming JSON parser as a separate dependency decision rather than hiding the allocation.

### 2. Checkpoint source progress

Record the source identity, byte offset, last complete record boundary, parser version, and a rolling range hash. Resume an append-only source from the last verified boundary. If device, inode, ctime, size, or prior range hash changes incompatibly, discard only that source's pending generation and rebuild it.

### 3. Publish honest progress

Add `index_runs` and per-source diagnostics. `convo sync --json` should report scanned bytes, completed sources, partial sources, pending torn rows, elapsed time, and the last durable checkpoint. `convo status` should report the last completed run and whether search is complete or partial.

### 4. Prove the full journey

Run synthetic fixtures first, then the real corpus with transcript output suppressed. Capture time, maximum resident memory, source counts, message counts, partial reasons, and a second no-change run. Search several seeded unique markers and verify their exact message timestamps.

## Required oracle

```text
uv run python -m unittest discover -s tests -v
python3 -m py_compile lib/*.py tools/convo/lib/*.py tools/convo/convo
./sync.sh
git diff --check
```

The real-corpus gate must use an isolated `CONVO_DATA_DIR`. Remove or trash that test database after recording aggregate evidence. Do not print transcript text during performance runs.

## Deferred

Do not add raw-log pruning, automatic PDPP upload, Waspflow relationships, AI summaries, or a resident daemon in this slice. The streaming backfill must be complete and recoverable first.
