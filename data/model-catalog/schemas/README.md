# model-catalog schemas (v1)

Contracts for every JSON file in this pack. **Validated by**
`scripts/validate_data_pack.py` on every release.

| Schema | Applies to |
|--------|------------|
| [`pricing-v1.schema.json`](pricing-v1.schema.json) | `pricing/*.json` |
| [`performance-v1.schema.json`](performance-v1.schema.json) | `performance/*.json` |
| [`../../schemas/pack-v1.schema.json`](../../schemas/pack-v1.schema.json) | `pack.json` |
| [`../../schemas/index-v1.schema.json`](../../schemas/index-v1.schema.json) | `data/index.json` |

## Design principles

1. **Never invent rates or scores.** Omit models / use `missing[]` for gaps.
2. **Pricing is tokensmash-compatible** (`kind`, `agent`, `models`, `match`, four rate fields).
3. **Performance is sparse and source-backed.** Claims and scores share one document kind.
4. **`schema_version: 1`** on every payload; bump major only on breaking changes.
5. **`retrieved_at` + `source_urls`** are mandatory provenance.

## Pricing (`kind`: `api_usd` | `codex_credits`)

```json
{
  "$schema": "schemas/pricing-v1.schema.json",
  "id": "provider-kind-YYYY-MM",
  "schema_version": 1,
  "kind": "api_usd",
  "agent": "claude-code",
  "retrieved_at": "2026-07-09",
  "source_urls": ["https://…"],
  "notes": "optional",
  "models": {
    "model-id": {
      "fresh_input_per_m": 0,
      "cache_read_per_m": 0,
      "cache_write_per_m": 0,
      "output_per_m": 0
    }
  },
  "match": [{ "pattern": "substring", "model": "model-id" }]
}
```

- Rates are **per 1M tokens** (USD or Codex credits).
- `match` is **ordered**; first substring hit wins; every `model` must exist in `models`.
- `agent` is the session family used when resolving (`claude-code` | `codex` | `grok`).

## Performance (`kind`: `performance`)

At least one of `claims` or `scores` must be non-empty.

```json
{
  "$schema": "schemas/performance-v1.schema.json",
  "id": "…",
  "schema_version": 1,
  "kind": "performance",
  "provider": "anthropic",
  "retrieved_at": "2026-07-09",
  "source_urls": ["https://…"],
  "notes": "optional",
  "claims": [
    {
      "models": ["claude-sonnet-5"],
      "task_families": ["BrowseComp"],
      "axes": ["quality", "cost", "effort"],
      "statement": "Verbatim-backed claim…",
      "implication": "optional operator guidance"
    }
  ],
  "scores": [
    {
      "model": "gpt-5.5",
      "metric": "Terminal-Bench 2.0",
      "score": 0.827,
      "unit": "accuracy",
      "effort": "xhigh",
      "comparisons": { "gpt-5.4": 0.751 },
      "caveat": "optional"
    }
  ],
  "missing": ["Digitized chart series for …"]
}
```

## Validate

```bash
./scripts/validate_data_pack.py model-catalog
./scripts/validate_data_pack.py              # all packs + index
# optional full JSON Schema:
#   pip install jsonschema && ./scripts/validate_data_pack.py
```

Release packaging runs validation automatically.
