# Data pack: `decision-model-catalog`

Source-backed, nuance-preserving data on **decision models**: models built for fast, typed
decisions on an agent's hot path. Think "Artificial Analysis for decision models".
Sibling of [`model-catalog`](../model-catalog/README.md): same envelope, ids, provenance
rules and registries. It differs only where decision models need it.

Not a CLI. Not a skill. Just versioned, **schema-validated** JSON with a **provenance registry**.

> Status: seed (`v0.1.0`). Structure, schemas and validation are in place; the data files
> are valid but empty until the populate step.

## Get this pack (click / copy — no URL math)

| | |
|---|---|
| **Latest release** | [data-decision-model-catalog releases](https://github.com/tnunamak/minnows/releases?q=data-decision-model-catalog&expanded=true) — open the newest, hit **Assets → Download** |
| **This version** | [data-decision-model-catalog-v0.1.0](https://github.com/tnunamak/minnows/releases/tag/data-decision-model-catalog-v0.1.0) — published by CI on push to main |
| **All data packs** | [data/README.md](../README.md) |
| **Machine index** | [data/index.json](../index.json) on `main` |
| **Schemas** | [SCHEMA.md](SCHEMA.md) · [schemas/](schemas/) |
| **Provenance** | [SOURCES.json](SOURCES.json) — every score/rate links here |
| **Nuance** | [caveats.json](caveats.json) — caveats rows cite by id |

### Full pack

```bash
TAG=data-decision-model-catalog-v0.1.0
curl -fsSL -L \
  "https://github.com/tnunamak/minnows/releases/download/${TAG}/${TAG}.tar.gz" \
  | tar -xz

# or
./scripts/fetch-data-pack.sh decision-model-catalog
./scripts/fetch-data-pack.sh decision-model-catalog v0.1.0
```

### Local

```bash
export DATA_PACKS_HOME="${DATA_PACKS_HOME:-$HOME/.local/share/minnows-data}"
# after ./install.sh → $DATA_PACKS_HOME/decision-model-catalog/pack.json
./scripts/validate_data_pack.py decision-model-catalog --require-jsonschema
```

## What counts as a decision model

In scope:

- "System One" / typed decision models: TypeSafe Jev (hosted, closed), Cloudflare Clef and
  Clef-flash (Workers AI, open weights), the OpenAI Decisions API (GPT-6 Luna variant,
  limited preview), and open approximations and community fine-tunes.
- General LLMs **only as baselines**, when a source benchmarks them on decision tasks. They
  live in `model-catalog`; score rows reference them by that id. Their pricing is not
  duplicated here.

Out of scope for now: safety/guardrail classifiers, embedding-similarity routers, and LLM
routers that pick which LLM to call. If a source frames one of these as a decision model,
note it (in `notes` or a caveat); do not ingest it.

## Layout

| Path | Role |
|---|---|
| `pack.json` | Envelope (tag, file list, schema pointers). Lists every file on disk. |
| `models.json` | **L0 decision-model registry** — ids + aliases (join key) |
| `metrics.json` | Metric registry (comparability boundary) |
| `caveats.json` | **Caveat registry** — nuance that must travel with the numbers |
| `SOURCES.json` | **Provenance registry** — id → URL / publisher / kind |
| `SCHEMA.md` / `schemas/` | Contracts |
| `pricing/*.json` | USD rates, free output, per-request fees, raw vendor units (tokensmash-compatible per-token rows) |
| `performance/*.json` | Claims and scores, with `measurement` context for latency and cost |
| `capabilities/*.json` | Decision-interface surface per model (question types, options, probabilities, packing) |

## Rules of use

1. **Pin a tag** for studies; only `data/index.json` is meant to float on `main`.
2. **Never invent numbers.** A gap is recorded under `missing[]`, not estimated.
3. **Latency needs context.** A `ms` row without who/where/percentile is rejected. Do not rank
   a hosted round-trip against an on-platform figure; see [SCHEMA.md](SCHEMA.md#latency-context-rule).
4. **Per-token price is not cost per decision.** See [SCHEMA.md](SCHEMA.md#cost-per-decision-rule).
5. **Scores compare only within one metric + harness + publisher** (`comparability_group`).
6. **Read the caveats.** If a row has `caveat_ids`, show them with the number.
7. **Validate before shipping:** `./scripts/validate_data_pack.py decision-model-catalog --require-jsonschema`

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
