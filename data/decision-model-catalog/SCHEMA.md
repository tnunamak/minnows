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

Per-token rows use the tokensmash field names: `fresh_input_per_m`, `cache_read_per_m`,
`cache_write_per_m`, `output_per_m` (USD per 1M tokens). All four keys are required when
`billing_basis` is `per_token`; the two cache keys may be `null` (see "Cache fields"). Every row has `billing_basis`:
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
| `measured_by` | Who ran it (vendor, named third party, `local`); never the board that merely lists a submitted run |
| `vantage` | `hosted_rtt`, `on_platform`, `on_card`, `local_cpu`, `local_gpu`, `vendor_claim` |
| `hardware`, `region` | As stated |
| `percentile` | `p50`, `p95`, `p99`, `mean` |
| `input_tokens`, `questions_per_request` | Payload size and fan-in |
| `prefix_cache` | `true`, `false` or `null` (unknown) |
| `n` | Sample count |
| `published_by` | Optional. Who published the number when that differs from `measured_by` (for example a board listing an author-submitted run) |

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
Name the unit exactly: a request carrying k questions costs `input_tokens x price`; the
per-decision cost is that figure divided by k (README worked example shows both).

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

## Conventions

- **Dates.** `retrieved_at` and `generated_at` are UTC dates. The research ran the evening of 2026-10-06 US Central (2026-10-07 UTC); everything is dated 2026-10-07.
- **Cache fields.** A `per_token` row has all four keys, but a cache rate is a number only when the source prints it. Otherwise the key is `null` and `cache_rates_status.cache_read` / `.cache_write` says why: `published` (number printed by the source, for example an OpenRouter `input_cache_read`), `not_published` (the source gives no cache rate; null), or `vendor_states_none_charged` (the vendor says there is no such charge; null, with the verbatim sentence kept in `notes` and not turned into a number). A null cache rate must carry one of the last two statuses, and those statuses forbid a number. Never copy `fresh_input_per_m` into a cache field. Consumers that need a number (tokensmash-style cost math) must choose a fallback themselves and say so; this pack does not choose one.
- **Raw units.** Workers AI is billed in neurons: the row is `billing_basis: raw_units`, `raw_units.unit: neurons`, the USD figure the vendor prints sits in `fresh_input_per_m`, and `usd_conversion` (`0.000011` USD per neuron, source cited) is stored separately.
- **Score scale.** Per-benchmark scores from the Decision Index and Cloudflare tables are percent (the Index publishes 0-1 fractions; stored x100 and rounded to four decimals). `ece` and `brier` are stored as published (0-1). Chance-corrected "skill" is an index, not an accuracy.
- **Metric ids.** One id per benchmark and publisher/harness/edition. Latency has one id per vantage (`...-hosted-rtt-ms` vs `...-on-card-ms`). `ci_lo`/`ci_hi` are fractions (0-1) even when `score` is percent.
- **Router ids.** Provider-qualified ids such as `sference/clef` and `cloudflare/clef` are aliases of the model, so a row can name the serving provider while resolving to one model.
- **LLM baselines.** `effort` carries the reasoning mode a baseline was run at (for example `none`, `low`, `medium`). A baseline missing from `model-catalog` is recorded in the document `missing[]`.
- **Compact documents.** The two largest Decision Index documents omit per-row `harness` and `task_family`; both are on the metric entry in `metrics.json`.
- **Evidence.** `evidence/` holds vendored raw runs of our own local measurements (the `local_eval` source). A private input file is withheld and named in the source `notes`.
