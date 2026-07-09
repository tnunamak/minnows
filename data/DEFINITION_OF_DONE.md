# Definition of done — model economics stack

Checklist from Fable strategy assessment (2026-07-09). Status after v0.5.1 / policy v0.1.3 / local-evals v0.1.0.

## Defined

- [x] `models.json` + `metrics.json`; validator resolves model strings (and metric_id when present)
- [x] Performance enrichment: harness, source_type, evidence_grade, observed_at, cost{} (not only metric-name hacks)
- [x] Policy pack fully validated (op graph, expands_to vs capabilities, evidence refs, pin agreement)
- [x] Pricing validity windows (`valid_until` on Sonnet 5 intro); expired rows fail validation

## Populated

- [x] Every policy op model has pricing + capabilities surface + quality evidence **or** explicit missing (grok still `evidence_missing`)
- [x] Local-eval rows (grade A) for top-3 traffic ops: implement.standard, fanout.explore, review.audit
- [x] Terra credits verified on official rate card; Sonnet intro expiry machine-readable

## Useful

- [x] waspflow: policy at runtime, lane `policy_version`+`catalog_ref`, effort hard-fail honesty, capabilities-derived whitelist file
- [x] tokensmash: pricing synced from catalog (vendored_from provenance)
- [x] Query cookbook in model-catalog README
- [x] Lane→op→policy→catalog_ref demonstrable (`scripts/demo_lane_trace.sh`)
- [x] Freshness checker + GitHub Actions workflow
- [x] Kill list still holds (no silent routing, no invented Grok curves, no quota↔$, no single model score)

## Intentionally not “done forever”

- Multi-seed local evals / cost-per-task instrumentation on every cell
- Google as full waspflow provider (marked `role: reference`)
- Grok quality curves (stay missing until measured)
- Weekly board refresh automation beyond `check_freshness.py`
