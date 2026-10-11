# local-evals schema

Rows follow **model-catalog performance score shape** (v1 + enrichment fields) and describe harness smoke tests, not quality evidence:
`model`, `metric`, `score`, `unit`, `effort`, `harness`, `task_family`,
`source_type=local_eval`, `evidence_grade=D`, `unit=other`, `observed_at`, optional `cost{}`.

Tasks under `tasks/` are free-form markdown with:

- goal
- oracle (how to pass/fail)
- max_steps / timeout
- allowed tools
