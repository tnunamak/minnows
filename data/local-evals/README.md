# Data pack: `local-evals`

**Local, fixed-harness** success evidence for `model-choice-policy` operating points.
Rows use performance score shape with `source_type: local_eval` and `evidence_grade: A`.

## This version

| | |
|---|---|
| **Tag** | `data-local-evals-v0.1.0` |
| **Measured cells** | implement.standard · fanout.explore · review.audit (all **pass** on 2026-07-09) |

## Run

```bash
./scripts/run_local_eval.py --all
./scripts/run_local_eval.py data/local-evals/tasks/implement-standard-oracle-v1.json
```

## Results (v0.1.0)

| Task | Op | Model / effort | Pass |
|------|-----|----------------|------|
| implement-standard-oracle-v1 | implement.standard | claude-sonnet-5 / medium | 1.0 |
| fanout-explore-oracle-v1 | fanout.explore | claude-sonnet-5 / medium | 1.0 |
| review-audit-oracle-v1 | review.audit | gpt-5.5 / xhigh | 1.0 |

Mirrored into catalog: `performance/local-evals-2026-07.json`.
