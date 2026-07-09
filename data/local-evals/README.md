# Data pack: `local-evals`

## Status: harness smoke tests — NOT quality evidence

After independent audits (GPT-5.6 Sol + Claude Fable, 2026-07-09), the v0.1.0 cells are
**reclassified as harness smoke**:

- Prompts leaked expected answers / defect IDs (answer contamination).
- Single seed, no cost capture, no weak-model control.
- `evidence_grade: D`, unit `other` (not aggregate `pass_rate`).
- Policy ops that cite these rows use **`evidence_confidence: medium`** (expert prior + smoke), not high.

Do **not** use these rows for cross-model selection or “grade A / high confidence” claims.

## Purpose that remains valid

Prove waspflow can spawn `--op`, write artifacts, and that the runner + oracle path works.
Use `./scripts/run_local_eval.py` for wiring checks. A real multi-task, hidden-oracle suite is future work.

## Run

```bash
./scripts/run_local_eval.py --all
```
