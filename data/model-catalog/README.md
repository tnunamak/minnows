# Data pack: `model-catalog`

Versioned **pricing** and **source-backed quality/effort** facts for coding-agent model choice.

Not a CLI. Not a skill. Just versioned, **schema-validated** JSON with a **provenance registry**.

## Get this pack (click / copy — no URL math)

| | |
|---|---|
| **Latest release** | [data-model-catalog releases](https://github.com/tnunamak/minnows/releases?q=data-model-catalog&expanded=true) — open the newest, hit **Assets → Download** |
| **This version** | Tag **`data-model-catalog-v0.5.0`** — [release](https://github.com/tnunamak/minnows/releases/tag/data-model-catalog-v0.5.0) |
| **All data packs** | [data/README.md](../README.md) |
| **Machine index** | [data/index.json](../index.json) on `main` |
| **Schemas** | [SCHEMA.md](SCHEMA.md) · [schemas/](schemas/) |
| **Provenance** | [SOURCES.json](SOURCES.json) — every score/rate links here |

### Full pack

```bash
TAG=data-model-catalog-v0.5.0
curl -fsSL -L \
  "https://github.com/tnunamak/minnows/releases/download/${TAG}/${TAG}.tar.gz" \
  | tar -xz

# or
./scripts/fetch-data-pack.sh model-catalog
./scripts/fetch-data-pack.sh model-catalog v0.4.2
```

### Local

```bash
export DATA_PACKS_HOME="${DATA_PACKS_HOME:-$HOME/.local/share/minnows-data}"
# after ./install.sh → $DATA_PACKS_HOME/model-catalog/pack.json
./scripts/validate_data_pack.py model-catalog
```

## Layout

| Path | Role |
|---|---|
| `pack.json` | Envelope (tag, file list, schema pointers) |
| `models.json` | **L0 model registry** — canonical ids + aliases (join key) |
| `metrics.json` | Metric registry (comparability boundary)
| `SOURCES.json` | **Provenance registry** — id → URL / publisher / kind |
| `SCHEMA.md` / `schemas/` | Contracts — pricing + performance + sources v1 |
| `pricing/*.json` | USD/MTok or Codex credits (tokensmash-compatible) |
| `performance/*.json` | Vendor + third-party scores/claims |
| `capabilities/*.json` | Effort/mode surfaces per model |
| `digitized/` | Chart extraction artifacts (case-by-case) |

### Performance documents (v0.3)

| File | What | Primary sources |
|------|------|-----------------|
| `anthropic-effort-quality-2026-07.json` | Sonnet 5 effort×cost framing | Anthropic Sonnet 5 post |
| `openai-quality-2026-07.json` | GPT-5.5 launch evals | OpenAI GPT-5.5 post |
| `openai-gpt-5-6-launch-2026-07.json` | **Full GPT-5.6 Sol/Terra/Luna tables** (coding, cyber, science, long-context, ARC-AGI-3 headline, …) | [openai.com/index/gpt-5-6](https://openai.com/index/gpt-5-6/) |
| `arcprize-gpt-5-6-2026-07.json` | **Effort-stratified ARC-AGI-1/2/3** + Sol cost/task | [ARC Prize GPT-5.6](https://arcprize.org/results/openai-gpt-5-6) |

### How to see where a number came from

1. Open a score/claim row → read `source_id` (or the document’s `source_ids[]`).
2. Look up that id in `SOURCES.json` → get URL, publisher, published date, `kind`.
3. Prefer `third_party_eval` over `vendor_blog` when they disagree on the same metric family (e.g. ARC).

## Rules of use

1. **Pin a tag** for studies; only `data/index.json` is meant to float on `main`.
2. **Never invent rates or scores** — omit or list under `missing[]`.
3. **Quota ≠ cost** — [clawmeter](https://github.com/tnunamak/clawmeter) for remaining allowance.
4. **Vendor tables are directional** until independently reproduced.
5. **Validate before shipping:** `./scripts/validate_data_pack.py model-catalog`

## Changelog

### v0.5.0 — 2026-07-09

- **`metrics.json`** metric registry (78 metrics).
- **Score enrichment:** `harness`, `source_type`, `evidence_grade`, `observed_at`, `cost{}`, `metric_id` (cost sibling rows folded into quality).
- **Pricing:** `valid_until` on Sonnet 5 intro; `family_default` on match rules; `role: reference` for Google; Terra Codex credits **verified** vs official rate card.
- **SOURCES:** `digitized_chart` kind for Sonnet 5 digitization entry.
- **FRESHNESS.md** + `scripts/check_freshness.py`; `scripts/sync_tokensmash_pricing.sh`.

### v0.4.2 — 2026-07-09

- **`models.json` L0 registry** — canonical model ids, providers, families, aliases (e.g. board naming drift → pricing ids). Validator resolves every model string in pricing/performance/capabilities against it (including `model@harness` scores).

### v0.4.1 — 2026-07-09

- **Chart digitization pipeline** (`scripts/digitize_chart.py`): density-peak marker extraction + labeled-value reads with asset hashes and dual-read error estimates.
- **Sonnet 5 pilot:** BrowseComp & OSWorld-Verified effort×cost curves; printed benchmark table; misaligned-behavior and Firefox147 bar labels → `performance/anthropic-sonnet5-digitized-2026-07.json` + `digitized/`.


### v0.4.0 — 2026-07-09

**Breadth expansion** (multi-agent research pass — only source-backed numbers):

- **32 SOURCES** (was 10): Anthropic pricing/effort/Fable/Opus, OpenAI reasoning/API, xAI reasoning, Google Gemini pricing, ARC full leaderboard, Artificial Analysis, Terminal-Bench 2.1/2.0, SWE-Bench Pro SEAL, vals.ai, LMArena, BrowseComp aggregator.
- **Pricing:** full Anthropic cache rates + Mythos; OpenAI mini/nano/pro/5.3-codex; Codex credits for GPT-5.6 Sol/Terra/Luna; Google Gemini 3.x/2.5; xAI multi-agent row.
- **Performance:** full GPT-5.5 launch table (~29 metrics); Terminal-Bench official + AA; AA Intelligence/Coding indexes + Grok 4.5; expanded ARC multi-vendor + full cost/task matrix; SWE Pro SEAL + vals Verified (clearly labeled); LMArena Elo; BrowseComp aggregator.
- **Capabilities:** 18 surfaces across Anthropic/OpenAI/xAI with valid efforts and modes (fast/ultra/multi-agent).
- **agent enum** extended: `google` | `other` for non-coding-agent pricing tables.

Still **missing** (not invented): Anthropic chart digitization, official SWE Verified full client table, Agents' Last Exam public board, GPT-5.6 on AA/Arena boards.


### v0.3.0 — 2026-07-09

- **`SOURCES.json` provenance registry** + `sources-v1` schema; validator requires resolvable `source_ids`.
- **Full GPT-5.6 GA eval tables** (`openai-gpt-5-6-launch-2026-07.json`) from [OpenAI launch post](https://openai.com/index/gpt-5-6/) — professional, coding, science, computer use, cyber, self-improvement, multimodal, academic, tool use, long context, ARC-AGI-3.
- **ARC Prize effort ladders** for Sol/Terra/Luna ARC-AGI-1/2/3 + leaderboard cost/task (`arcprize-gpt-5-6-2026-07.json`).
- GPT-5.6 Sol/Terra/Luna **API pricing** (+ cache write 1.25×) in `pricing/openai-api-2026-07.json`.
- Row-level `source_id` on scores/claims throughout.

### v0.2.0 — 2026-07-09

- Formal **JSON Schema** (Draft 2020-12) for pack envelope, index, pricing, performance.
- Unified **performance** documents (`kind: performance` with `claims` and/or `scores`).
- `schema_version: 1` + `$schema` on all payloads.
- Stdlib **validator** (`scripts/validate_data_pack.py`); release path runs it.

### v0.1.0 — 2026-07-09

- Initial pricing (Anthropic / OpenAI / Codex credits / xAI) + sparse quality notes.


## Query cookbook

Cheapest-ish operational models with grade ≥ B evidence on a coding metric (illustrative jq):

```bash
# models with at least one grade-B coding score
jq -r '
  .scores[]?
  | select(.evidence_grade=="B" and .task_family=="coding")
  | [.model, .metric, .score, .effort // "-", .harness // "-"] | @tsv
' data/model-catalog/performance/*.json | sort -u
```

Join price (API USD) for a model id:

```bash
jq -r --arg m claude-sonnet-5 '
  .models[$m] // empty
' data/model-catalog/pricing/anthropic-api-2026-07.json
```
