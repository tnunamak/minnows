# Convo streaming backfill execution plan

Status: implementation and synthetic tests complete for Claude, Codex, and Qwen JSONL; full-corpus
performance validation remains pending. Gemini remains whole-document.
Date: 2026-08-11

## Objective

Index the complete supported corpus without loading a whole session into memory. Preserve the existing raw-reader behavior and the ledger's snapshot provenance.

## Observed scale

- Supported stores currently contain about 10,000 physical sources and 19 GiB of logs.
- The largest Codex files are about 1.5 GiB. The largest Claude file is about 287 MiB.
- A metadata-only pass over 9,983 supported sources completes in 1.64 seconds with 38.9 MiB maximum resident memory after batched writes.
- Claude, Codex, and Qwen normalize through a temporary disk spool instead of a full in-memory `Session`.
- Gemini remains subject to the configurable 64 MiB whole-document cap and is explicitly `oversized` when over it.

## Stop condition

The work is complete when a clean database can index every supported source with these properties:

1. Maximum resident memory stays below 512 MiB.
2. An interrupted run resumes without duplicating or losing messages.
3. A torn final row remains pending until completed.
4. A malformed complete row produces an explicit partial-source diagnostic without damaging other sources.
5. The full 19 GiB backfill completes within 15 minutes on the current workstation.

## Implementation slices

### 1. Stream normalized messages (implemented; full-corpus validation pending)

Claude, Codex, and Qwen read one bounded JSONL row at a time, write normalized messages to a
private temporary spool, then atomically replace the source snapshot in SQLite. This keeps parsing
outside write transactions and prevents a full source from becoming a Python object graph. Gemini's
small JSON documents use the existing whole-document path. A cap on normalized assistant messages
preserves unusually large replies in ordered chunks and records `partial` coverage rather than growing
memory without bound.

Before choosing a JSON parser, measure the largest physical line in each store. Python's standard JSON decoder still needs one complete value in memory. If a single event is too large for the memory target, evaluate a streaming JSON parser as a separate dependency decision rather than hiding the allocation.

### 2. Cache completed source outcomes (implemented; full-corpus validation pending)

Cache the physical identity (size, mtime, device, inode, ctime) plus parser version, policy version,
and applicable source cap for every outcome: present, skipped, partial, pending, corrupt, or oversized.
An unchanged outcome is not reparsed. A changed source is rebuilt atomically; a race during parsing or
hashing leaves the prior snapshot intact and records a retryable failure.

### 3. Publish accurate progress (implemented; full-corpus validation pending)

`convo sync` reports aggregate counts, elapsed time, and throughput; `--verbose` reveals individual
source diagnostics. Periodic progress is interactive-only. Its JSON result includes stable source, observed
corpus bytes, processed-source bytes, duration, throughput, skipped,
partial, and pending fields. `convo status` separates parser failures, partial, pending, skipped, and
oversized sources. `partial`, `pending`, and `skipped` are recorded outcomes and do not make a
sync fail; exit 2 is reserved for inability to record one.

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

## Scope boundary

The core CLI permanently excludes tmux and resurrection-sidecar ingestion and has no Waspflow runtime
dependency. Raw-log pruning, automatic upload, AI summaries, and a resident daemon are current non-goals,
not permanent product prohibitions. This implementation indexes supported harness logs only.
