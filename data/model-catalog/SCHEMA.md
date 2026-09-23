# model-catalog schemas (v1)

Contracts for every JSON file in this pack. **Validated by**
`scripts/validate_data_pack.py` on every release.

| Schema | Applies to |
|--------|------------|
| [`sources-v1.schema.json`](schemas/sources-v1.schema.json) | `SOURCES.json` |
| [`models-v1.schema.json`](schemas/models-v1.schema.json) | `models.json` (stdlib-validated; schema is documentation, not enforced under `--require-jsonschema`) — L0 model registry — join key for the pack |
| [`pricing-v1.schema.json`](schemas/pricing-v1.schema.json) | `pricing/*.json` |
| [`performance-v1.schema.json`](schemas/performance-v1.schema.json) | `performance/*.json` |
| [`capabilities-v1.schema.json`](schemas/capabilities-v1.schema.json) | `capabilities/*.json` |
| [`../../schemas/pack-v1.schema.json`](../../schemas/pack-v1.schema.json) | `pack.json` |
| [`../../schemas/index-v1.schema.json`](../../schemas/index-v1.schema.json) | `data/index.json` |

## Design principles

1. **Never invent rates or scores.** Omit models / use `missing[]` for gaps.
2. **`models.json` is the join key.** Every model string in pricing/performance/capabilities must resolve (exact id, alias, or strip `@harness`).
3. **Pricing is tokensmash-compatible** (`kind`, `agent`, `models`, `match`, four rate fields).
4. **Performance is sparse and source-backed.** Claims and scores share one document kind.
5. **`schema_version: 1`** on every payload; bump major only on breaking changes.
6. **Provenance is mandatory and resolvable:**
   - Document: `retrieved_at` + `source_urls[]` + `source_ids[]`
   - Registry: `SOURCES.json` (canonical id → url / publisher / kind)
   - Row (recommended): `source_id` on each score/claim

## Model registry (`models.json`)

Each entry: `id`, `provider`, `family`, `status` (`ga` | `preview` | `historical` |
`third_party_board_only`), `aliases[]`, and optional `tier`, `access`, and `effort_parameter`.

`access` — `restricted` marks a model that is trusted-access only (e.g. `claude-mythos-5-1`):
GA for its program, but never a routing candidate. Omitted means generally available.

`effort_parameter` — `false` marks a model that accepts no effort parameter (e.g.
`claude-haiku-4-5`); routing uses it with no effort flag, and its score rows carry `effort: null`.

`tier` — **capability tier**: vendors ship concurrent tiers on separate cadences; policy
picks a tier per task, then the newest GA model in that tier. Populated for OpenAI,
Anthropic, and xAI models:

- **openai**: `astra` | `sol` | `terra` | `luna` | `pro` | `mini` | `nano` | `codex` | `base`
  (`base` = the flagship generation id with no tier suffix, e.g. `gpt-5.5`, `gpt-5.4`)
- **anthropic**: `fable` | `mythos` | `opus` | `sonnet` | `haiku`
- **xai**: the vendor's own tiering if documented, else omitted — xAI does not currently
  publish a named capability-tier scheme (`grok-4.x` numbering only), so `tier` is omitted
  for all xAI rows as of 2026-09-22.

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
- `agent` is the session family used when resolving (`claude-code` | `codex` | `grok` | `google` | `other`).

## Metric task families and success units

`metrics.json` may tag an unambiguous benchmark with one `task_family`:
`coding`, `agentic`, `research/browsing`, `knowledge/factuality`, `reasoning`,
or `computer-use`. The operating-point recommender selects these tags; untagged
metrics remain in the catalog but are not selected by that policy.

Performance row `unit` can be `accuracy`, `pass_rate`, `error_rate`, `elo`, or
`other`. The first three are fractional success or error rates in 0..1.
`error_rate` is used for explicitly named error-rate metrics such as the
factual-error-rate-difficult-prompts series.

## Performance (`kind`: `performance`)

At least one of `claims` or `scores` must be non-empty.

```json
{
  "$schema": "schemas/performance-v1.schema.json",
| [`capabilities-v1.schema.json`](schemas/capabilities-v1.schema.json) | `capabilities/*.json` |
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
