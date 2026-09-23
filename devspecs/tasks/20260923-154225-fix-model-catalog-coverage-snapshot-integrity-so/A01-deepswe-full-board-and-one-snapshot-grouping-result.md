# Task 20260923-154225-fix-model-catalog-coverage-snapshot-integrity-so A01 Result

## Summary
- Target: `A01` - DeepSWE full board and one-snapshot grouping
- Outcome: -

## Completion Contract
- Attempted slice: `A01` - DeepSWE full board and one-snapshot grouping
- Gate tested: promote, improve, rework, rollback, or block
- What changed: -
- Evidence for decision: -
- What remains: -
- Next iteration: -

## Changed Files
-

## Tests
-

## Decision
-

## Follow-up
-

## References
- `A00-index.md`
- `A01-deepswe-full-board-and-one-snapshot-grouping-plan.md`

## Checkpoint History

### Checkpoint
- Created At: 2026-09-23T16:11:03Z
- Stage: validated
- Decision: promote
- Source: `checkpoints/20260923-161103-validated.md`
- Structured Evidence: `checkpoints/20260923-161103-validated.json`
- What changed: Added all 70 live DeepSWE configurations with source precision and explicit snapshot grouping; added CI, n and pass_at_4 schema fields and one-group-per-snapshot validation.
- Evidence for decision: 3 file(s) edited; 2 test command(s)
- What remains: -
- Next iteration: promote to the next slice
- Files edited:
  - `data/model-catalog/performance/google-xai-board-snapshot-2026-09-23.json`
  - `data/model-catalog/schemas/performance-v1.schema.json`
  - `scripts/validate_data_pack.py`
- Tests run:
  - `./scripts/validate_data_pack.py model-catalog`
  - `python3 /home/tnunamak/.tmp/frontier/audit_values.py`
