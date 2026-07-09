# model-catalog schemas (v1)

Contracts for every JSON file in this pack. **Validated by**
`scripts/validate_data_pack.py` on every release.

| Schema | Applies to |
|--------|------------|
| [`sources-v1.schema.json`](schemas/sources-v1.schema.json) | `SOURCES.json` |
| [`pricing-v1.schema.json`](schemas/pricing-v1.schema.json) | `pricing/*.json` |
| [`performance-v1.schema.json`](schemas/performance-v1.schema.json) | `performance/*.json` |
| [`../../schemas/pack-v1.schema.json`](../../schemas/pack-v1.schema.json) | `pack.json` |
| [`../../schemas/index-v1.schema.json`](../../schemas/index-v1.schema.json) | `data/index.json` |

## Design principles

1. **Never invent rates or scores.** Omit models / use `missing[]` for gaps.
2. **Pricing is tokensmash-compatible** (`kind`, `agent`, `models`, `match`, four rate fields).
3. **Performance is sparse and source-backed.** Claims and scores share one document kind.
4. **`schema_version: 1`** on every payload; bump major only on breaking changes.
5. **Provenance is mandatory and resolvable:**
   - Document: `retrieved_at` + `source_urls[]` + `source_ids[]`
   - Registry: `SOURCES.json` (canonical id → url / publisher / kind)
   - Row (recommended): `source_id` on each score/claim

## Provenance (`SOURCES.json`)

```json
{
  "id": "model-catalog-sources",
  "schema_version": 1,
  "retrieved_at": "2026-07-09",
  "sources": [
    {
      "id": "openai-gpt-5-6-2026-07-09",
      "url": "https://openai.com/index/gpt-5-6/",
      "title": "GPT-5.6: …",
      "publisher": "OpenAI",
      "published": "2026-07-09",
      "retrieved_at": "2026-07-09",
      "kind": "vendor_blog"
    }
  ]
}
```

Kinds: `vendor_blog` | `vendor_docs` | `third_party_eval` | `academic` | `other`.

Consumers: resolve `source_ids` / `source_id` → registry entry → URL. Do not treat bare
scores as ground truth without checking `kind` (vendor vs third_party).

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
  "source_ids": ["anthropic-models-overview-2026-07-09"],
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
  "provider": "openai",
  "retrieved_at": "2026-07-09",
  "source_urls": ["https://…"],
  "source_ids": ["openai-gpt-5-6-2026-07-09"],
  "notes": "optional",
  "claims": [
    {
      "models": ["gpt-5.6-sol"],
      "task_families": ["coding"],
      "axes": ["quality", "cost", "effort"],
      "statement": "Verbatim-backed claim…",
      "implication": "optional operator guidance",
      "source_id": "openai-gpt-5-6-2026-07-09"
    }
  ],
  "scores": [
    {
      "model": "gpt-5.6-sol",
      "metric": "Terminal-Bench 2.1",
      "score": 0.888,
      "unit": "accuracy",
      "effort": "ultra",
      "source_id": "openai-gpt-5-6-2026-07-09",
      "comparisons": { "gpt-5.5": 0.856 },
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
