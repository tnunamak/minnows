# Definition of done — model economics stack

Updated 2026-07-09 after GPT-5.6 Sol + Claude Fable audits (honesty pass v0.5.2 / policy v0.1.4).

## Defined

- [x] `models.json` + `metrics.json`; validator resolves model strings (and metric_id when present)
- [x] Performance enrichment: harness, source_type, evidence_grade, observed_at, cost{}, comparable/comparability_group
- [x] Policy pack fully validated (op graph, expands_to vs capabilities, evidence refs, pin agreement)
- [x] Pricing validity windows (`valid_until` on Sonnet 5 intro); expired rows fail validation
- [x] Pack file list allows declared assets (png/py); CI installs jsonschema and uses `--require-jsonschema`

## Populated (honest)

- [x] Every policy op model has pricing + capabilities surface + quality evidence **or** explicit missing (Grok still `missing`)
- [x] Local harness smoke cells exist for top-3 ops — **grade D / smoke**, not quality evidence
- [x] Terra credits verified; Sonnet intro expiry machine-readable
- [ ] Multi-task, hidden-oracle local quality suite with cost capture (**not done** — future)
- [ ] Cost-per-success economics loop for ops (**not done** — future)

## Useful

- [x] waspflow: policy at runtime, lane `policy_version`/`catalog_ref`/`op_expands_to`/`explicit_overrides`, effort hard-fail + capabilities whitelist enforce, capabilities-derived whitelist file
- [x] tokensmash: pricing synced from catalog (vendored_from provenance)
- [x] Query cookbook in model-catalog README
- [x] Lane→op→evidence→source URL via `scripts/demo_lane_trace.sh` (executable, fails on broken links)
- [x] Freshness checker covers pricing + promised boards; GitHub Actions with jsonschema
- [x] Kill list: no silent routing, no invented Grok curves, no quota↔$, no single score, no bulk digitization, no runtime catalog in spawn; **mixed metric_id rows marked `comparable: false`**

## Product claim (current)

**Well-provenanced model catalog + explicit launch-policy presets (v0.x).**  
Not yet an empirical cost-per-success economics engine. Local v0.1.x cells are harness smoke only.
