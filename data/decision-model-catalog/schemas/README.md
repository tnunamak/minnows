# decision-model-catalog JSON Schemas

| File | Purpose |
|------|---------|
| `sources-v1.schema.json` | `SOURCES.json` provenance registry (may be empty in a seed) |
| `models-v1.schema.json` | `models.json` decision-model registry (join key) |
| `metrics-v1.schema.json` | `metrics.json` metric registry |
| `caveats-v1.schema.json` | `caveats.json` nuance registry |
| `pricing-v1.schema.json` | `pricing/*.json` (tokensmash-compatible + free output, per-request fees, raw units) |
| `performance-v1.schema.json` | `performance/*.json` claims + scores (+ `measurement`, `caveat_ids`) |
| `capabilities-v1.schema.json` | `capabilities/*.json` decision-interface surfaces |
| `pack-v1.schema.json` | Copy of the pack envelope (also under `data/schemas/`) |

All are enforced under `./scripts/validate_data_pack.py --require-jsonschema`
(unlike model-catalog's `models-v1`, which is documentation only).
Human contract: [../SCHEMA.md](../SCHEMA.md).
