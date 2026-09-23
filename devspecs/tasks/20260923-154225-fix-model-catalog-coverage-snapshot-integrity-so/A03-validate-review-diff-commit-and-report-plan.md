# Task 20260923-154225-fix-model-catalog-coverage-snapshot-integrity-so A03 Plan

## Goal
Validate, review diff, commit, and report

## Description
Create a bounded implementation slice for `Fix model catalog coverage, snapshot integrity, source-backed model dates, and validator checks from frontier datafix brief`. This plan is grounded by the task index preflight, but it is not authoritative; confirm predicted files and tests before making edits.

## Resources
- `A00-index.md`
- `A03-validate-review-diff-commit-and-report-result.md`
- `task.json`
- `tools/hone/lib/work.mjs`
- `tools/hone/lib/test-lane.mjs`
- `tools/hone/lib/lane.mjs`
- `scripts/recommend_ops.py`
- `tools/hone/lib/inventory-snapshot.mjs`
- `scripts/validate_data_pack.py`
- `tools/hone/lib/agenda.mjs`
- `tools/hone/lib/plan-orders.mjs`

## Starting Context
### Files to Inspect First
- `tools/hone/lib/work.mjs`
- `tools/hone/lib/test-lane.mjs`
- `tools/hone/lib/lane.mjs`
- `scripts/recommend_ops.py`
- `tools/hone/lib/inventory-snapshot.mjs`
- `scripts/validate_data_pack.py`
- `tools/hone/lib/agenda.mjs`
- `tools/hone/lib/plan-orders.mjs`
- `tools/hone/lib/test-agenda.mjs`

### Tests to Inspect First
- `tests/test_recommend_ops.py`
- `tests/test_convo.py`

## Expected Change Surface
- `tools/hone/lib/work.mjs`
- `tools/hone/lib/test-lane.mjs`
- `tools/hone/lib/lane.mjs`
- `scripts/recommend_ops.py`
- `tools/hone/lib/inventory-snapshot.mjs`
- `scripts/validate_data_pack.py`
- `tools/hone/lib/agenda.mjs`
- `tools/hone/lib/plan-orders.mjs`
- `tools/hone/lib/test-agenda.mjs`

## Out-of-Scope Areas
- Replanning the whole thread unless evidence says this slice should split or be superseded.
- Broad pack-ranking changes unless they are necessary for this task.
- Treating the generated context as complete without verification.

## Risks
- Task-related on-disk paths may be missing from the indexed candidate set.
- Pack completeness is not high; verify the working set before editing.
- On-disk paths matched the task but were not indexed: Inspect the warned files or refresh the index before trusting missing context. Evidence: `data/model-catalog/SOURCES.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, source; `data/model-catalog/digitized/anthropic-opus5-frontier-bench-effort.json` - on-disk path matched task terms but was not in the indexed candidate set: model, catalog, frontier.

## Success Criteria
- [ ] Primary implementation surface is verified before edits.
- [ ] Relevant tests are found or the test-surface miss is recorded.
- [ ] Changes stay inside the bounded slice.
- [ ] A checkpoint records actual files, tests, misses, noise, and decision.

## Tasks
- [ ] Inspect the predicted primary files.
- [ ] Inspect same-package, same-stem, or receipt-related tests.
- [ ] Refine the slice if context is incomplete.
- [ ] Implement the smallest useful change.
- [ ] Run focused validation.
- [ ] Update `A03-validate-review-diff-commit-and-report-result.md` or run `ds task checkpoint`.

## Decision Gates
- Promote: the workspace was useful enough and misses are actionable.
- Improve: useful start, but incomplete/noisy enough to require template or retrieval changes.
- Rework: task workspace feels like planning overhead or fails to capture useful evidence.
- Rollback: workspace creates false confidence or worsens agent performance.
- Block: external input or a missing prerequisite prevents useful progress.
