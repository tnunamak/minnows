# DevSpecs feedback

## 2026-09-23 — frontier model catalog datafix

- `ds task` created a useful three-slice task record, but returned no visible confirmation output. I had to run `ds task status <id>` to discover the generated task ID and lifecycle state.
- `ds task status` rejects a human-readable title with spaces and requires the generated task ID; the error makes that constraint clear.
- The status command detected edits to generated plan/result files and correctly suggested `ds task refresh` before checkpoints. `ds task refresh` then stalled at about 29% CPU for more than a minute without output or a checkpoint. I stopped that process and used `ds task checkpoint --index=false` for the slice checkpoints; those completed and provide the audit trail.
