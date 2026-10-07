# decision-model-catalog schemas (v1)

Contracts for every JSON file in this pack. **Validated by**
`scripts/validate_data_pack.py decision-model-catalog` on every release. This pack reuses
`model-catalog`'s conventions (see [../model-catalog/SCHEMA.md](../model-catalog/SCHEMA.md))
and lists only the differences in full.

| Schema | Applies to |
|--------|------------|
| [`sources-v1`](schemas/sources-v1.schema.json) | `SOURCES.json` (id `decision-model-catalog-sources`) |
| [`models-v1`](schemas/models-v1.schema.json) | `models.json` (id `decision-model-catalog-models`) |
| [`metrics-v1`](schemas/metrics-v1.schema.json) | `metrics.json` (id `decision-model-catalog-metrics`) |
| [`caveats-v1`](schemas/caveats-v1.schema.json) | `caveats.json` (id `decision-model-catalog-caveats`) |
| [`pricing-v1`](schemas/pricing-v1.schema.json) | `pricing/*.json` |
| [`performance-v1`](schemas/performance-v1.schema.json) | `performance/*.json` |
| [`capabilities-v1`](schemas/capabilities-v1.schema.json) | `capabilities/*.json` |
| [`../../schemas/pack-v1.schema.json`](../schemas/pack-v1.schema.json) | `pack.json` |

All seven payload schemas are enforced under `--require-jsonschema`. The validator adds the
cross-file checks JSON Schema cannot express (id resolution, caveat ids, file list).

## Category definition

Decision models are models built for fast, typed decisions on an agent's hot path: routing,
intent classification with out-of-scope, tool selection, when-to-call, scoring/grading,
yes/no probability.

In scope: "System One" / typed decision models (TypeSafe Jev, Cloudflare Clef and
Clef-flash, the OpenAI Decisions API, open approximations and fine-tunes), and any others
found. General LLMs enter **only as baselines** when a source benchmarks them on decision
tasks. Baselines live in `model-catalog`; reference them by that id and do not duplicate
their pricing.

Out of scope for now: safety/guardrail classifiers, embedding-similarity routers, and LLM
routers that pick which LLM to call (unless a source frames them as decision models; then
note, do not ingest).

## Design principles

1. **Never invent numbers.** A gap is recorded as missing, not estimated.
2. **Every number traces to a source** you opened: URL, retrieval date, and a verbatim quote
   or exact table location. Mark vendor claims, independent measurements and our own local
   measurements (`source_type`, `evidence_grade`).
3. **`models.json` is the join key** — with a baseline fallback (below).
4. **Scores compare only within the same metric + harness + publisher.** Record the
   harness/suite version and the publisher.
5. **Preserve nuance.** Where sources disagree, record both with attribution. Wins depend on
   task shape. Put the nuance in `caveats.json` so a consumer cannot drop it.
6. **`schema_version: 1`** on every payload.

## Model resolution

A model string in pricing, performance, capabilities or a caveat resolves in this order:
(1) this pack's `models.json` (exact id, alias, or `model@harness` with the suffix
stripped); (2) `data/model-catalog/models.json` (ids + aliases). Local entries win. A local
id or alias that already exists in the baseline is a validation error: reference the
baseline, do not duplicate it.

## Model registry (`models.json`)

Each entry: `id`, `provider`, `family`, `status`, `aliases[]`, `weights`, and optional
fields below. `provider` is a lowercase slug (not a closed enum).

| Field | Meaning |
|-------|---------|
| `status` | `ga`, `preview`, `early_access`, `limited_preview`, `community_finetune`, `research_release`, `historical`, `deprecated`, `third_party_board_only` |
| `weights` | `open` or `closed` (required) |
| `license` | License string as stated by the source, or `null` |
| `base_model` | Parent model for fine-tunes, or `null` |
| `params_total`, `params_active` | Plain numbers (e.g. `26e9`) or `null`. `params_active` is for MoE. It must not exceed `params_total`. |
| `hf_repo` | `owner/name` on Hugging Face, or `null` |
| `surfaces[]` | Where it is served: `typesafe-api`, `workers-ai`, `openai-api`, `openrouter`, `self-host`, `hf-inference`, `other` |
| `tier` | Optional vendor-named family tier (e.g. `clef` vs `clef-flash`). Free-form. No effort fields. |
| `released`, `release_source_id` | As in model-catalog; the source id must resolve |
| `caveat_ids[]` | Caveats that qualify the model |

`null` means unknown. Do not guess.

## Provenance (`SOURCES.json`)

Same shape as model-catalog. `kind` adds `vendor_changelog`, `model_card`,
`third_party_blog` and `community_post` to the model-catalog kinds. The registry may be
empty in a seed.

## Pricing (`kind`: `api_usd`)

Per-token rows are tokensmash-compatible: `fresh_input_per_m`, `cache_read_per_m`,
`cache_write_per_m`, `output_per_m` (USD per 1M tokens), all four required when
`billing_basis` is `per_token`. Every row has `billing_basis`:
`per_token`, `per_request`, `per_decision`, `raw_units`, `free`, `self_host`.

Extra fields:

- `output_free: true` — output is not billed. `output_per_m` must then be 0 or absent.
- `per_request_usd`, `min_request_usd`, `per_decision_usd` — fees that are not per token.
- `raw_units` — the vendor's native unit, verbatim: `{unit, input_per_m, output_per_m, per_request}`
  (for example Workers AI neurons).
- `usd_conversion` — `{usd_per_unit, source_id}`: the USD value of one raw unit and where
  that conversion comes from. USD rates next to `raw_units` are invalid without it. Keep
  the raw unit and the conversion separate; never overwrite one with the other.
- `source_id`, `caveat_ids`, `notes`.

## Performance (`kind`: `performance`)

Same document shape as model-catalog (`claims[]`, `scores[]`, `missing[]`, `comparable`,
`comparability_group`, `harness`, `source_type`, `evidence_grade`, `observed_at`, `caveat`,
`source_id`, `snapshot_id`). Differences:

- `provider` is a lowercase slug for the publisher.
- Every score row needs `metric_id` (registered in `metrics.json`).
- `unit` adds `ms`, `ece`, `brier`, `usd_per_decision`, `ratio`. For a metric with no listed
  unit, use `other` and name it in the metric.
- `source_type` adds `vendor_claim`, `third_party_report`, `community_report`.
- `caveat_ids[]` on scores and claims cite `caveats.json`.
- Optional `measurement` object:

| Field | Meaning |
|-------|---------|
| `measured_by` | Who ran it (vendor, named third party, `local`) |
| `vantage` | `hosted_rtt`, `on_platform`, `on_card`, `local_cpu`, `local_gpu`, `vendor_claim` |
| `hardware`, `region` | As stated |
| `percentile` | `p50`, `p95`, `p99`, `mean` |
| `input_tokens`, `questions_per_request` | Payload size and fan-in |
| `prefix_cache` | `true`, `false` or `null` (unknown) |
| `n` | Sample count |

### Latency context rule

A latency number is meaningless without its context. A score row with `unit: "ms"` must
carry `measurement.measured_by`, `measurement.vantage` and `measurement.percentile`. Add
`hardware`, `region`, `input_tokens`, `questions_per_request` and `prefix_cache` whenever
the source states them. Rows with different `vantage` values are not comparable: a hosted
HTTPS round trip from a lab includes network time that an on-platform figure does not, so a
speed ratio across them can be apples to oranges. Set `comparable: false` or use separate
`comparability_group`s, and cite a caveat.

### Cost-per-decision rule

A per-token price is not the cost of a decision. Cost per decision depends on input size,
whether output is billed, per-request minimums, how many questions share one request, and
cache behavior. Record per-token rates in `pricing/`. Record decision-level cost as a score
row with `unit: "usd_per_decision"` and a `measurement` (at least `measured_by`; add
`input_tokens`, `questions_per_request`, `prefix_cache` when stated). Never compute a
cost-per-decision from per-token rates in this pack without saying so in `caveat`.

## Capabilities (`kind`: `capabilities`)

One entry per model per serving surface (`surfaces[]`). Facts only. `null` = unknown,
`false` = documented as unsupported.

`model`, `surface`, `question_types[]` (`choice`, `score`, `noul`, `binary`,
`multi_label`, ...), `max_options`, `abstain_supported`, `returns_probabilities`,
`calibration_claimed`, `rationale_supported`, `context{state_tokens, total_tokens}`,
`questions_per_request{max, fan_out_supported, packing_supported}`,
`output_tokens_billed`, `source_ids[]`, `caveat_ids[]`, `notes`.

## Caveats (`caveats.json`)

Nuance lives here so a consumer cannot drop it. Each caveat:

```json
{
  "id": "short-kebab-id",
  "statement": "What is true, stated plainly.",
  "affects": { "models": ["..."], "metrics": ["..."], "sources": ["..."] },
  "strength": "strong | moderate | anecdotal",
  "implication": "What a consumer should do differently.",
  "evidence": [{ "source_id": "...", "quote": "verbatim" }]
}
```

- `affects` names at least one of models (resolve via the registry + baseline), metrics
  (`metrics.json`), sources (`SOURCES.json`).
- `evidence` is required: at least one source id with a verbatim quote.
- `strength`: `strong` = measured or primary-source; `moderate` = credible but indirect;
  `anecdotal` = a single report or comment.
- Rows cite caveats with `caveat_ids[]` (scores, claims, models, pricing rows, capability
  surfaces). An unknown id fails validation.

## Validate

```bash
./scripts/validate_data_pack.py decision-model-catalog --require-jsonschema
./scripts/validate_data_pack.py   # all packs + index
```
