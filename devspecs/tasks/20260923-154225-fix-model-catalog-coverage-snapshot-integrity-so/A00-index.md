# Task 20260923-154225-fix-model-catalog-coverage-snapshot-integrity-so

## Task
Fix model catalog coverage, snapshot integrity, source-backed model dates, and validator checks from frontier datafix brief

## Status
packed

## Series
A

## Profile
code-change

## Created At
2026-09-23T15:42:25Z

## Original Query
Fix model catalog coverage, snapshot integrity, source-backed model dates, and validator checks from frontier datafix brief

## Repo / Workspace
- Repo: `/home/tnunamak/code/minnows-waspflow-frontier-datafix`
- Workspace: `/home/tnunamak/code/minnows-waspflow-frontier-datafix/devspecs/tasks/20260923-154225-fix-model-catalog-coverage-snapshot-integrity-so`

## Resources
- `task.json`
- `A01-deepswe-full-board-and-one-snapshot-grouping-plan.md`
- `A01-deepswe-full-board-and-one-snapshot-grouping-result.md`
- `A02-vendor-tables-corrections-qwen-surfaces-and-mode-plan.md`
- `A02-vendor-tables-corrections-qwen-surfaces-and-mode-result.md`
- `A03-validate-review-diff-commit-and-report-plan.md`
- `A03-validate-review-diff-commit-and-report-result.md`

## Task Slices
- A01: DeepSWE full board and one-snapshot grouping. Plan: `A01-deepswe-full-board-and-one-snapshot-grouping-plan.md`. Result: `A01-deepswe-full-board-and-one-snapshot-grouping-result.md`.
- A02: Vendor tables, corrections, Qwen surfaces, and model dates. Plan: `A02-vendor-tables-corrections-qwen-surfaces-and-mode-plan.md`. Result: `A02-vendor-tables-corrections-qwen-surfaces-and-mode-result.md`.
- A03: Validate, review diff, commit, and report. Plan: `A03-validate-review-diff-commit-and-report-plan.md`. Result: `A03-validate-review-diff-commit-and-report-result.md`.

## Relevant Map Areas
- `tools`
- `scripts`
- `tests`

## Likely Primary Files
- `tools/hone/lib/work.mjs` - tools/hone/lib/work.mjs (javascript)
  Evidence: query term match in body: backed; query term match in body: brief; query term match in body: checks
- `tools/hone/lib/test-lane.mjs` - tools/hone/lib/test-lane.mjs (javascript)
  Evidence: query term match in body: brief; query term match in body: checks; query term match in body: dates
- `tools/hone/lib/lane.mjs` - tools/hone/lib/lane.mjs (javascript)
  Evidence: query term match in body: brief; query term match in body: checks; query term match in body: dates
- `scripts/recommend_ops.py`
  Evidence: relationship expansion: source_manifest_family_recovery; query term match in body: catalog; query term match in body: coverage
- `tools/hone/lib/inventory-snapshot.mjs` - tools/hone/lib/inventory-snapshot.mjs (javascript)
  Evidence: relationship expansion: source_manifest_family_recovery; query term match in path: snapshot; query term match in body: from
- `scripts/validate_data_pack.py`
  Evidence: relationship expansion: source_manifest_family_recovery; query term match in body: catalog; query term match in body: integrity
- `tools/hone/lib/agenda.mjs` - tools/hone/lib/agenda.mjs (javascript)
  Evidence: query term match in body: coverage; query term match in body: dates; query term match in body: fix
- `tools/hone/lib/plan-orders.mjs` - tools/hone/lib/plan-orders.mjs (javascript)
  Evidence: query term match in body: backed; query term match in body: checks; query term match in body: coverage
- `tools/hone/lib/test-agenda.mjs` - tools/hone/lib/test-agenda.mjs (javascript)
  Evidence: relationship expansion: source_manifest_family_recovery; query term match in body: checks; query term match in body: dates

## Likely Tests
- `tests/test_recommend_ops.py`
  Evidence: relationship expansion: source_manifest_test_reservation; pack tier: related (reserved manifest test with direct query evidence); test-name token anchor
- `tests/test_convo.py`
  Evidence: relationship expansion: source_manifest_test_reservation; pack tier: related (reserved manifest test with direct query evidence); test-name token anchor

## Likely Docs / Plans / Config
None found in the initial preflight.

## Supporting Context
None found in the initial preflight.

## Related Git Receipts
- `84ba4dc` 2026-07-02 - feat(hone): token-economics levers — stage-level accounting, model-selection architecture, batch verification with bi...
  Matched paths: `tools/hone/lib/lane.mjs`, `tools/hone/lib/test-lane.mjs`, `tools/hone/lib/work.mjs`
- `7742472` 2026-09-23 - fix(recommend_ops): decimal version ordering; correct grok-4.7 CLI efforts; dispatch-id-aware known-answer test
  Matched paths: `scripts/recommend_ops.py`
- `e01dc55` 2026-09-22 - feat(model-choice-policy): derive op recommendations from the catalog
  Matched paths: `scripts/recommend_ops.py`

## Noise Risks
None found in the initial preflight.

## Freshness Warnings
These on-disk paths match the task wording but were not present in the indexed candidate set. Treat them as stale-index risk, not proof that the initial pack is wrong.

- `data/model-catalog/SOURCES.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, source
- `data/model-catalog/digitized/anthropic-opus5-frontier-bench-effort.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, frontier
- `data/model-catalog/performance/artificial-analysis-openweight-frontier-2026-09-23.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, frontier
- `data/model-catalog/performance/artificialanalysis-opus5-briefcase-2026-09.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, brief
- `data/model-catalog/performance/google-xai-board-snapshot-2026-09-23.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, snapshot
- `data/model-catalog/performance/gpt-6-sol-luna-board-coverage-2026-09-22.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, coverage
- `data/model-catalog/performance/openweight-vendor-frontier-2026-09-23.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, frontier
- `data/model-catalog/performance/opus-5-5-board-coverage-2026-09-22.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, coverage

## Risk Cards
Evidence-backed checks to run before trusting the initial task context. These are not required edit targets.

- On-disk paths matched the task but were not indexed [medium, freshness]
  Agent check: Inspect the warned files or refresh the index before trusting missing context.
  Evidence: `data/model-catalog/SOURCES.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, source; `data/model-catalog/digitized/anthropic-opus5-frontier-bench-effort.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, frontier; `data/model-catalog/performance/artificial-analysis-openweight-frontier-2026-09-23.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, frontier; `data/model-catalog/performance/artificialanalysis-opus5-briefcase-2026-09.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, brief

## Known Knowns
- The preflight found likely primary implementation files.
- The preflight found likely behavior/test artifacts.
- Git receipts provide historical trust evidence for packed paths.

## Known Unknowns
- Task-related on-disk paths may be missing from the indexed candidate set.
- Pack completeness is not high; verify the working set before editing.

## Confidence Summary
- Primary file confidence: high
- Test coverage confidence: high
- Docs/config coverage confidence: low
- Git receipt confidence: high
- Noise risk: low
- Pack completeness: medium

Why:
- found 9 likely primary file(s)
- found 2 likely test file(s)
- found 3 related Git receipt(s)

Agent instruction:
Validate the test and integration surface before editing. Record critical misses and distracting inclusions in the slice result or a task checkpoint.

## Suggested Starting Slice
Use `A01-deepswe-full-board-and-one-snapshot-grouping-plan.md` as the first bounded plan in this task thread. Refine it before editing if primary files, tests, or integration points look incomplete.

## Agent Preflight Checklist
- [ ] Verify the likely primary files against the repo before editing.
- [ ] Search for same-package or same-command tests if test confidence is not high.
- [ ] Check receipt-touched related files before assuming the pack is complete.
- [ ] Record files actually read, edited, tests run, misses, and noise in `A01-deepswe-full-board-and-one-snapshot-grouping-result.md` or `ds task checkpoint`.
- [ ] After all slices are terminal, complete the one-time durable record review at `A00`; record none, recorded artifacts, or a deferred target.
