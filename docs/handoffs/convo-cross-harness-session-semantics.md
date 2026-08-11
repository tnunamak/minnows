# Terra handoff: make `convo` correct across compaction, replay, and thread boundaries

## Mission

Improve `convo` so it reports history that is complete for each verified physical-session
format, deterministic under replay, and honest about unknown logical-thread completeness:

- Claude Code
- Codex CLI
- Gemini CLI

Implement the changes, add tests, update the shipped skill, commit the result, and push a
feature branch. Do not open a pull request unless repository policy requires one.

This is a behavior and correctness task. Do not treat it as a request to add speculative
cross-file stitching.

## Repository and delivery

Work in:

```text
/home/tnunamak/code/minnows
```

Before editing:

1. Read the repository `README.md` and all applicable `AGENTS.md` files.
2. Read `tools/convo/SKILL.md`.
3. Read the `code-quality-canon` skill and its full canonical theory.
4. Inspect `git status`, the current branch, remotes, and recent relevant history.
5. Preserve the existing untracked `inbox/` and `tmp/` directories. They belong to the
   user.
6. Use cheap subagents for bounded log-format or test-fixture research when useful.

Create a feature branch with a clear name, unless the current branch is already dedicated
to this work. A suitable name is:

```text
fix/convo-session-semantics
```

Use a conventional commit. Push the feature branch after all gates pass. Report the
branch name and pushed commit SHA.

## Why this work exists

The current skill documentation makes an incorrect claim:

> A compacted Claude session is split across multiple files.

Local evidence contradicts that claim. Claude Code normally stores multiple compactions
inside one JSONL file under one `sessionId`. Large local files contain 25, 62, and 106
compaction markers in one file.

Codex also stores multiple compactions inside one rollout file. Codex uses explicit
`type: "compacted"` rows. Some Codex metadata also identifies thread ancestry with fields
such as `forked_from_id` and `parent_thread_id`.

Gemini uses a different representation. Current JSON sessions contain a top-level
`summary` and a `messages` array. At least one local Gemini session uses a
`session-*.jsonl` filename, but current discovery only includes `session-*.json`.

The current implementation reads full Claude and Codex files. Ordinary in-file compaction
therefore does not, by itself, truncate the clean transcript. The primary defects are:

1. The documentation misstates the storage model.
2. The internal schema cannot represent compaction segments or thread provenance.
3. Claude logs can contain repeated UUID records. Raw file order can repeat transcript
   content.
4. Codex compaction records and explicit thread ancestry are ignored.
5. Gemini summaries are ignored, and Gemini JSONL discovery appears incomplete.
6. The tool cannot state whether a result is one physical session, one branch, one logical
   thread, or an inferred combination.
7. There are no dedicated `convo` tests.

## Terms and invariants

Use these terms consistently in code, documentation, JSON, and tests.

### Physical session

One log file produced by one harness.

### Logical thread

A conversation lineage that can contain multiple physical sessions or forks. Link physical
sessions only when the harness records explicit ancestry. Do not infer a thread from topic,
time proximity, working directory, summary similarity, or filenames.

### Branch

One path through a message graph. A physical session can contain replayed rows or forks.

### Compaction segment

A chronological section inside a physical session. A compaction event ends one segment and
starts another. Compaction is not the same as a new physical session.

### Transcript

The normalized human and agent conversation shown by `convo`. The default transcript must
remain the clean view:

- Each real user prompt.
- Only the last agent text reply before the next real user prompt.
- No system injections, tool noise, or synthetic compaction summaries.

### Required invariants

1. Never omit valid pre-compaction turns from the same physical session.
2. Never duplicate a real prompt or response because the harness replayed log records.
3. Never join separate physical sessions without explicit ancestry evidence.
4. Never present an inferred or partial lineage as guaranteed complete.
5. Keep `convo show` deterministic for unchanged input files.
6. Preserve existing `final`, `text`, and `full` output semantics unless a test proves a
   current semantic defect.
7. Keep the tool standard-library only.
8. Keep fast `list` behavior bounded for very large logs.
9. Treat active files safely. Continue to tolerate one torn, unterminated final JSONL row.
10. Do not include personal transcript contents in committed fixtures.

Use these metadata semantics unless the investigation proves that a harness needs a
stronger model:

- `physical_session_status` is `complete`, `complete_prefix`, or `unknown`.
- `complete` means that the loader reached a valid end of file.
- `complete_prefix` means that the loader safely ignored a torn final row from an active
  writer.
- `unknown` means that the loader cannot establish either condition.
- `segment_count` is the number of chronological segments. A valid session with no
  compaction has one segment.
- A deduplication key must use a harness record identifier plus semantic content. Do not
  discard conflicting records that share an identifier. Retain them deterministically and
  add a diagnostic.
- Keep harness-native ancestry names when their meanings differ. Do not label a Codex
  thread identifier as a physical session identifier.

## Current architecture

Read these locations before designing:

- Unified `Turn` and `Session` models:
  `tools/convo/convo`, near lines 58 and 75.
- Claude loader: `load_claude()`, near line 189.
- Codex loader: `load_codex()`, near line 281.
- Gemini loader: `load_gemini()`, near line 343.
- Fast `Peek` model and loaders: near line 438.
- Harness registry and discovery: near line 519.
- Pair construction and rendering: near lines 639 and 670.
- Session selection and `show`: near lines 876 and 932.
- Shared Claude boundary parser: `lib/claude_sessions.py`, near line 98.
- Vendoring: `sync.sh`.
- Installation: `install.sh`.

The current `Session` model stores normalized turns but has no provenance, segment, branch,
or completeness data. Do not bolt unrelated harness conditionals into the renderer. Add
one small, honest normalization boundary that hides harness-specific depth.

Avoid shallow abstractions. A new type or helper must encode a real invariant and reduce
harness-specific branching outside the loaders.

## Required investigation before implementation

Prove the raw semantics before choosing the final data model.

### Claude Code

Inspect several bounded local samples, including:

```text
~/.claude/projects/-home-tnunamak-code-pdpp/8b2c8ac0-a286-48e1-b140-253d6b93668c.jsonl
~/.claude/projects/-home-tnunamak-code-pdpp/98841246-1434-4539-8fac-869170a2b9e8.jsonl
```

Also inspect at least two large files with multiple compaction markers and duplicate UUIDs.
Do not read huge files into model context. Use local scripts or bounded shell output.

Determine:

- How `uuid`, `parentUuid`, and `last-prompt.leafUuid` define the active branch.
- Why UUID records repeat in large files.
- Whether repeated records are byte-identical replays, updated records, or branch copies.
- Whether the last leaf identifies a unique canonical branch.
- How `isSidechain`, `agentId`, and subagent files relate to the parent transcript.
- Whether compaction-marker rows participate in the UUID graph.
- Whether any explicit cross-file predecessor field exists.

Do not use topic or timestamp heuristics as ancestry evidence. If local evidence cannot
prove canonical branch selection, preserve current transcript order and add safe
deduplication only where the semantic identity is certain.

Default to the current physical-file chronological transcript. Reconstruct a graph branch
only if explicit graph evidence proves that the result is safer and more correct.

### Codex CLI

Inspect multiple rollout files with `type: "compacted"` records.

Determine:

- Whether pre-compaction `response_item` records remain in the rollout.
- Whether `payload.replacement_history` repeats material already present in the file.
- Whether replacement history is needed when earlier raw records are absent.
- Where `forked_from_id` and `parent_thread_id` occur.
- Whether those identifiers point to available physical rollout files.
- Whether a resumed or forked thread should be exposed as provenance without changing the
  default single-session transcript.

Prefer explicit Codex identifiers over heuristics. Do not automatically concatenate a
parent and child if that would repeat replacement history.

### Gemini CLI

Inspect representative `session-*.json` and `session-*.jsonl` files.

Determine:

- The exact JSONL schema and whether it is a supported current format.
- Whether the top-level `summary` represents compaction, display metadata, or both.
- Whether JSON sessions retain all pre-summary messages.
- Whether Gemini records explicit thread or parent identifiers.
- Whether project identity can be improved without inventing a working directory.

If the observed JSONL file is unrelated or obsolete, document the evidence and do not add
blind support.

Record a short format note in code comments or tests. Do not commit raw personal logs.

## Required implementation outcomes

The exact names can change if a better design emerges, but all outcomes below must hold.

### 1. Add tested session provenance

Extend the normalized model with enough information to answer:

- Which physical file produced this transcript?
- What harness session identifier does it use?
- How many compaction segments were detected?
- Was replay deduplication applied?
- Is explicit parent or fork ancestry available?
- Is the transcript complete for the physical file?
- Is logical-thread completeness known, partial, or not applicable?

Use structured values, not a collection of loosely related booleans. Keep the common
interface small. Keep raw harness metadata inside a typed or well-defined provenance
object.

Do not claim that a logical thread is incomplete merely because it has compactions.

### 2. Handle Claude in-file compactions correctly

- Preserve all real turns before and after every compaction.
- Exclude synthetic compaction-summary user rows from the clean transcript.
- Count and expose compaction segments.
- Handle repeated UUID records deterministically.
- Deduplicate only records with proven semantic identity.
- Preserve branch and sidechain provenance metadata and skipped-row counts when available.
- Keep the existing default behavior of excluding `isSidechain`.
- Do not include sidechain or subagent transcript rows in the default clean transcript.
- Do not add automatic cross-file stitching without an explicit predecessor relationship.

If the active branch can be reconstructed unambiguously from `parentUuid` and `leafUuid`,
implement it and test forks, replays, and missing parents. If it cannot, document the
limitation and implement the strongest safe subset.

Do not make branch reconstruction a goal by itself. Keep chronological behavior when graph
reconstruction does not have stronger evidence.

### 3. Handle Codex compactions and ancestry correctly

- Parse `type: "compacted"` rows as segment boundaries.
- Preserve the real user and assistant transcript across all in-file segments.
- Build the default transcript from primary `response_item` rows.
- Treat `replacement_history` as metadata unless real evidence proves that earlier primary
  rows are absent and replacement history is the only explicit transcript source.
- Use `replacement_history` as transcript input only when that condition is proven.
- Prevent replacement history from duplicating existing turns.
- Capture explicit `forked_from_id` and `parent_thread_id` provenance.
- Do not silently concatenate parent and child physical sessions in the default view.
- Provide a deterministic way for JSON consumers to see explicit ancestry.

### 4. Handle Gemini summaries and file formats correctly

- Preserve all real messages in the physical session.
- Capture the top-level summary as summary or segment metadata. Do not render it as a real
  user prompt.
- Support every verified local Gemini session format.
- Add JSONL discovery only after the investigation proves its schema.
- Keep the current honest project-hash behavior when no working directory exists.

### 5. Make completeness visible without breaking consumers

The existing `show --json` output is an object with session metadata and `exchanges`.
Extend it additively with a stable metadata object. A suggested shape is:

```json
{
  "harness": "claude",
  "session_id": "...",
  "path": "...",
  "project": "...",
  "transcript": {
    "physical_session_status": "complete",
    "logical_thread_status": "single-session|explicit-parent|fork|unknown",
    "segment_count": 2,
    "deduplicated_record_count": 0,
    "parent_session_id": null,
    "forked_from_session_id": null,
    "notes": []
  },
  "exchanges": []
}
```

Choose better field names if necessary. Define their semantics in tests and the skill.
Do not remove or change existing JSON fields.

This requirement applies to `show --json`. Keep `list --json` lightweight. Add only
metadata that `Peek` can obtain within its existing bounded scan. Do not fully load session
files for `list`.

Keep `grep --json` as a flat hit list unless an additive field is cheap and useful. Do not
duplicate synthetic summaries into grep results.

For human output, avoid noisy banners on ordinary complete sessions. Show a concise note
only when it prevents a false conclusion, such as:

- Explicit parent or fork exists but is not included.
- The loader detected unresolved graph references.
- A format is readable but logical-thread completeness is unknown.

Do not warn merely because a session contains compactions.

### 6. Correct all documentation

Update the source skill:

```text
tools/convo/SKILL.md
```

The documentation must explain:

- Claude and Codex normally keep multiple compactions in one physical file.
- `convo show` includes valid pre- and post-compaction turns.
- Compaction segments differ from physical sessions and logical threads.
- Cross-file ancestry is harness-specific.
- `convo` follows or reports only explicit ancestry. It does not guess from content.
- Any remaining harness-specific limitations.

Run `./sync.sh` after source changes. Verify the generated copies:

```text
skills/convo/SKILL.md
skills/convo/scripts/convo
skills/convo/scripts/lib/claude_sessions.py
```

Do not edit generated files directly.

## Test requirements

Add a stdlib-only test suite for `convo`. Prefer `unittest` unless the repository already
establishes another Python test convention.

Keep fixtures minimal and synthetic. Derive their structure from real logs, but remove all
personal content, paths, tokens, tool results, and identifiers.

### Loader and transcript fixtures

Cover the existing contracts before changing behavior:

- Claude string and list user content.
- Claude image markers.
- Claude tool results stored as user rows.
- Claude list content that contains both text and a tool result. Verify that events populate
  `Turn.events`, not `Turn.texts`.
- Claude `isMeta` filtering.
- Claude `isSidechain` filtering.
- Consecutive assistant row merging.
- Codex messages, reasoning, function calls, and function results.
- Codex developer and system filtering.
- Gemini string and list content.
- `final`, `text`, and `full` exchange construction.

### Compaction and provenance fixtures

Claude:

- No compaction.
- One in-file compaction.
- Multiple in-file compactions.
- Compaction summary excluded while earlier turns remain.
- Repeated identical UUID records.
- Repeated UUID with conflicting content.
- Forked `parentUuid` graph with a known leaf.
- Missing parent or missing leaf.
- `isSidechain` rows.
- No explicit cross-file parent.

Codex:

- No compaction.
- Multiple `type: "compacted"` rows.
- Replacement history that duplicates earlier response items.
- Replacement history needed because earlier response items are absent, if real evidence
  confirms this case.
- Explicit `parent_thread_id`.
- Explicit `forked_from_id`.
- Parent identifier whose file is unavailable.

Gemini:

- JSON session without summary.
- JSON session with summary.
- A verified JSONL schema, if local evidence supports it.
- An explicitly selected unsupported file produces a clear error.
- Normal discovery does not include an unverified schema or extension.

### File-integrity and CLI fixtures

- Torn, unterminated final JSONL row is tolerated.
- Corrupt terminated middle row reports the session and returns exit code 2.
- `show --json` retains all existing fields and adds provenance metadata.
- `list` remains bounded for huge JSONL files.
- `grep` does not search synthetic summaries as real conversation text.
- `-n` still limits exchanges, not raw rows or segments.
- Harness aliases still work.
- `--project` and `--all-projects` remain mutually exclusive.
- Active-file reads are deterministic for the complete prefix.

Use temporary home directories for discovery tests. Do not depend on live user history
for deterministic tests.

## Verification gates

Run at minimum:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile \
  tools/convo/convo \
  lib/claude_sessions.py \
  tools/uncompact/uncompact
./sync.sh
git diff --check
```

Also run end-to-end smoke tests against temporary synthetic home directories for all three
harnesses.

Then run bounded, read-only checks against representative live files:

```bash
./tools/convo/convo show <session-path> --json
./tools/convo/convo show <session-path> --mode final -n 5 --no-color
./tools/convo/convo list --all-projects --harness all --limit 5 --json --no-color
```

The live all-harness list currently encounters at least one old corrupt Codex file and can
exit 2 after printing valid results. Preserve or deliberately improve this behavior, and
test the chosen contract.

Compare old and new clean transcripts on non-replayed sessions. Use a deterministic script
as the behavior oracle. Expected differences must be limited to confirmed defects, new
metadata, and corrected warnings.

After `./sync.sh`:

1. Compare source and generated executables.
2. Compare source and generated skill documentation.
3. Verify the vendored shared library.
4. Run the generated skill executable in a temporary home directory.

Read every changed file before committing. Search for the obsolete claims and terms:

```bash
rg -n \
  'split across multiple files|chain-stitch|chain stitching|compaction chains|leafUuid' \
  README.md tools skills lib tests
```

Confirm that no inaccurate claim remains.
Keep legitimate, tested `leafUuid` references.

Because the maker is not the sole judge, ask a different agent to review the final diff.
Give the reviewer the invariants and acceptance criteria from this brief. Resolve every
correctness issue before push.

## Compatibility constraints

- Keep the CLI standard-library only.
- Preserve current command names and defaults.
- Keep default session-selection semantics. `convo show` must select and load one physical
  session. Provenance work must not make it scan or join all possible ancestors.
- Preserve existing JSON fields.
- Keep a bare `convo` equivalent to `convo list`.
- Keep `show --mode final|text|full`.
- Keep `grep` exit code 1 for no match.
- Keep clear exit code 2 behavior for skipped corrupt sessions, unless tests and
  documentation justify a compatible improvement.
- Do not make `list` fully parse hundreds of megabytes per candidate.
- Do not add global transcript indexes, caches, databases, or background services.
- Do not add fuzzy content-based session linking.
- Do not expose hidden reasoning in modes that currently exclude it.
- Do not broaden Claude subagent discovery as an incidental change. Treat it as separate
  scope unless it is required for correct provenance.

## Scope decisions

This work must distinguish the following outcomes:

### In scope

- Correct intra-file compaction behavior and metadata.
- Replay-aware deterministic transcript normalization.
- Explicit harness ancestry metadata.
- Verified Gemini format coverage.
- Honest completeness reporting.
- Tests and corrected documentation.

### Out of scope without new evidence

- Topic-based cross-file session matching.
- Timestamp-based cross-file session matching.
- Automatic Claude parent inference from a compaction summary.
- Merging subagent transcripts into the parent conversation.
- A database or persistent index.
- Reconstructing hidden reasoning omitted by a harness.
- Redesigning `full` mode event/text interleaving unless a failing correctness test
  requires it.

## Completion report

Do not report success from intent. Provide:

1. The semantic defects proven before implementation.
2. The final normalized model and why it is deep enough to justify itself.
3. Exact behavior changes for Claude, Codex, and Gemini.
4. Compatibility behavior that remains unchanged.
5. Test commands and exact results.
6. Live-log smoke checks and exact results.
7. Any format cases that remain unverified.
8. Different-agent review findings and resolutions.
9. The final diff summary.
10. The pushed branch and commit SHA.
11. Confidence by harness:
    - Claude
    - Codex
    - Gemini

Do not claim complete logical-thread recovery for a harness that does not record explicit
cross-file ancestry. The correct result is an honest transcript with explicit provenance,
not a guessed conversation.
