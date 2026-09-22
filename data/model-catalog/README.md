# Data pack: `model-catalog`

Versioned **pricing** and **source-backed quality/effort** facts for coding-agent model choice.

Not a CLI. Not a skill. Just versioned, **schema-validated** JSON with a **provenance registry**.

## Get this pack (click / copy — no URL math)

| | |
|---|---|
| **Latest release** | [data-model-catalog releases](https://github.com/tnunamak/minnows/releases?q=data-model-catalog&expanded=true) — open the newest, hit **Assets → Download** |
| **This version** | [data-model-catalog-v0.5.6](https://github.com/tnunamak/minnows/releases/tag/data-model-catalog-v0.5.6) — published by CI on push to main |
| **All data packs** | [data/README.md](../README.md) |
| **Machine index** | [data/index.json](../index.json) on `main` |
| **Schemas** | [SCHEMA.md](SCHEMA.md) · [schemas/](schemas/) |
| **Provenance** | [SOURCES.json](SOURCES.json) — every score/rate links here |

### Full pack

```bash
TAG=data-model-catalog-v0.5.6
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

### v0.5.6 — 2026-09-22

- **Added GPT-6 Sol and GPT-6 Luna** (launched 2026-09-22, 19 days after GPT-6 Astra on 2026-09-03): `models.json` entries (family `gpt-6`, GA); API USD pricing (`pricing/openai-api-2026-07.json`: sol $2.00/$0.20/$2.50/$10.00, luna $0.10/$0.01/$0.125/$0.50 — short-context, input/cached/cache-write/output per 1M, same 1.25×/0.1× cache-write/read ratio as gpt-6-astra); Codex credit pricing (`pricing/codex-credits-2026-07.json`: sol 50/5/0/250, luna 2.5/0.25/0/12.5 credits per 1M). GPT-5.6 Sol/Terra/Luna and gpt-6-astra rates re-verified UNCHANGED against the same pages on this date in both files — no rate changed today except the two new additions. Fixed a pre-existing gap while touching `codex-credits-2026-07.json`: `gpt-6-astra` had a `models{}` row but no `match[]` rule (unresolvable by substring match); added alongside the new sol/luna rules.
- **New `tier` field** on the L0 model registry (`models.json`, documented in `SCHEMA.md`, new `schemas/models-v1.schema.json` — documentation contract, not enforced under `--require-jsonschema` since `models.json` is stdlib-validated): "capability tier — vendors ship concurrent tiers on separate cadences; policy picks a tier per task, then the newest GA model in that tier." Populated for every OpenAI (`astra|sol|terra|luna|pro|mini|nano|codex|base`), Anthropic (`fable|mythos|opus|sonnet|haiku`), and xAI model; xAI rows are left without a `tier` because no vendor-documented capability-tier naming scheme was found in this pack's xAI sources (only `grok-4.x` version numbering). GPT-5.6 models are **not** marked historical — they remain served and listed in Codex.
- `capabilities/effort-surfaces-2026-07.json`: `api` surfaces for gpt-6-sol/gpt-6-luna from their dedicated model-reference pages — `reasoning.effort` supports none/low/medium(default)/high/xhigh/max, with **no `minimal` level** (unlike gpt-5.6's api surface, which lists `minimal`). `codex_cli` surfaces added but left EMPTY (valid_efforts `[]`, default_effort `null`): this host's live `codex debug models` (codex-cli 0.154.0, checked 2026-09-22, same day as launch) does not list either slug yet — recorded as an auth-scoped rollout gap, not a capability negation, consistent with this pack's existing docs-vs-live-catalog convention for GPT-5.6.
- **New:** `performance/openai-gpt-6-sol-luna-launch-2026-09.json` — the launch post's two `<table>` elements (an API-pricing summary and an AutomationBench score/cost-per-task table: GPT-6 Sol xhigh 33.2%/$0.27, GPT-6 Astra low 30.3%, Claude Opus 5 max 26.9%, Claude Fable 5.1 w/ Opus 5 Fallback max 31.4%) plus the page's vendor claims verbatim (AutomationBench: "GPT-6 Sol at xhigh effort outperforms Claude Opus 5 at max effort at just 9% of Opus 5's cost per task"; Luna: "+5.4 percentage points at 58% lower cost per task" vs GPT-5.6 Luna). The page's 11 vegaLite interactive charts are explicitly NOT ingested here (separate chart-digitization lane). Claude Opus 5's 26.9% score matches the independently-published value on Anthropic's own Opus 5.5 launch table exactly — cross-vendor corroboration, kept in a separate `comparability_group` per this pack's mixed-provenance convention rather than merged.
- **Cache facts** from `better-prompt-caching-for-gpt-6.html` folded into pricing notes: GPT-6's improved caching system gives "discounts of up to 90% on cached input tokens" (matches the stored 0.1× cache-read ratio) and now caches eligible shared prefixes for a **30-minute reuse window by default**; reasoning effort can be changed between responses without breaking cache via `configuration_update`.
- Observed, not acted on (flagged for a future pricing-audit pass, out of this lane's scope): GPT-5.6 Terra and GPT-5.6 Luna model rows are no longer present anywhere on the live `developers.openai.com/api/docs/pricing` page as of 2026-09-22 (only `gpt-5.6-sol` and `gpt-5.6-cyber` remain listed under "All models"). GPT-5.6 Sol's own rate and promotional-pricing footnote ("available at least through November 21, 2026") are unchanged.
- **New SOURCES:** `openai-gpt-6-sol-luna-2026-09-22` (launch post), `openai-api-pricing-2026-09-22`, `openai-codex-pricing-2026-09-22`, `openai-gpt-6-prompt-caching-2026-09-22`, `openai-api-reasoning-2026-09-22`, `openai-api-model-page-gpt-6-sol-2026-09-22`, `openai-api-model-page-gpt-6-luna-2026-09-22`, `openai-codex-debug-models-2026-09-22`.

### v0.5.5 — 2026-09-22

- **Added Claude Opus 5.5** (launched 2026-09-22): `models.json` entry (family `claude-opus`, GA, aliases "Claude Opus 5.5"/"Opus 5.5"); pricing row in `pricing/anthropic-api-2026-07.json` ($4/$20 input/output, cache read $0.20 at a 0.05x-base override multiplier, cache write $5 at 1.25x, fast mode $8/$40 Claude-API-first-party-only); `capabilities/effort-surfaces-2026-07.json` api surface (levels low/medium/high/xhigh/max, **default medium** — different from Opus 5's default high; thinking cannot be disabled at ANY effort level, stricter than Opus 5's xhigh/max-only restriction; forced `tool_choice` any/tool return a 400 error; computer use requires `computer_toolset_20260801` on the Claude API/Google Cloud). `claude-opus-5` is **not** marked historical — it remains served.
- **New:** `performance/anthropic-opus-5-5-launch-2026-09.json` — the launch page's headline benchmark table (Opus 5.5 / Fable 5.1 / Opus 5 / GPT-6 Astra / GPT-5.6 Sol) across Terminal-Bench 4.0, FrontierCode v1.1 (Main), CursorBench 4.0, GDPval-AA v2.1, AutomationBench, Humanity's Last Exam (with tools), Terminal-Bench-Science 0.1, OSWorld 2.0, and Chartography, plus the page's own vendor claims (cost-per-quality comparisons vs Opus 5/GPT-6 Astra/GPT-5.6 Sol, and a customer-quote claim about BigFinance Bench).
- **New:** `performance/anthropic-opus5-5-digitized-2026-09.json` — 130 score rows, the full per-effort (low/medium/high/xhigh/max) curves behind six of the launch page's charts (Terminal-Bench 4.0, FrontierCode v1.1 Main, CursorBench 4.0, GDPval-AA v2.1, AutomationBench, and the new WANDR metric), read as exact values from the page's inline-SVG chart aria-labels. Every headline-table cell with a corresponding curve point matches it exactly (0 mismatches; verified 2026-09-22).
- **New:** `performance/anthropic-opus-5-5-system-card-2026-09.json` — 88 score rows from the 230-page system card: the full Table 8.1.A capability suite (SWE-bench variants, FrontierCode, Terminal-Bench 4.0/-Science, FrontierSWE v2, CursorBench 4.0, ArXivMath, ProgramBench, HLE, OSWorld 2.0, Chartography, BenchCAD, GDPval-AA v2.1, AA-Briefcase v1.1, Toolathlon Verified, AutomationBench, HealthBench(+Pro), GMMLU/MILU, a life-sciences suite, and cyber evals), plus quantitative safety/alignment results (sandbox-escape propensity, package-registry harmful-publish propensity, prompt-injection compliance, reward-hacking rate, SHADE-Arena/LinuxArena stealth rates, verbalized-evaluation-awareness).
- **New:** third-party board coverage — `performance/artificial-analysis-intelligence-index-opus-5-5-2026-09.json` (AA Intelligence Index v4.3.2, new composite metric, Opus 5.5 #1/212), `performance/terminal-bench-4-vals-opus-5-5-2026-09.json` (vals.ai secondary Terminal-Bench 4.0 reading), and `performance/opus-5-5-board-coverage-2026-09-22.json` (an explicit CONFIRMED-ABSENT/PROVISIONAL record of every other board checked at launch — ARC Prize, LMArena, DeepSWE, SWE-bench vals.ai, BrowseComp, Epoch AI, LiveBench — so absence is recorded, not silently omitted).
- **Metric registry reconciliation:** four workers independently ingested Opus 5.5 data and initially minted different `metric_id`s for the same benchmarks. Per this pack's existing convention (one shared `metric_id` per benchmark, cross-publisher/cross-harness distinction expressed via row-level `comparable: false` + `comparability_group`, not distinct ids — see `terminal-bench-4-0`'s pre-existing mixed-provenance pattern), these were unified: the launch table's `terminal-bench-4-0`/`frontiercode-1-1-main`/`cursorbench-4-0` rows now share ids with the digitized curves and system card; `frontiercode-v1-1-main`/`-extended` (system card naming) folded into `frontiercode-1-1-main`/`-extended`; `gdpval-aa-v2-1`, `wandr`, and `chartography` definitions deduplicated to one entry each. All affected rows carry `comparable: false` and a distinct `comparability_group`.
- **Fixed (validator, no data change):** a pre-existing error on `performance/openai-gpt-5-6-launch-2026-07.json`'s three `automationbench` rows (GPT-5.6 Sol/Terra/Luna), which mixed source/harness classes under a shared metric_id without `comparable: false` — tagged, consistent with the same convention.
- **Fixed (schema):** 25 rows in the system card file used `source_type: "third_party_eval"`, not a valid enum value in `schemas/performance-v1.schema.json`; remapped to the schema's `third_party_board` (the closest existing "independent, non-vendor" value) so `--require-jsonschema` passes.
- **New SOURCES:** `anthropic-opus-5-5-2026-09-22` (launch post, merged notes covering pricing/headline-table/footnotes and the SVG-curve extraction method), `anthropic-pricing-2026-09-22`, `anthropic-models-overview-2026-09-22`, `anthropic-effort-guide-2026-09-22`, `anthropic-opus-5-5-system-card-2026-09-22`, plus 12 board-sweep sources (Artificial Analysis, vals.ai, Steel, ARC Prize, Arena, DeepSWE, Epoch AI, LiveBench, OpenRouter).
- Not recorded (no source found): a tokenizer-identity claim for Opus 5.5 vs Opus 5, and an Opus 5.5 lifecycle/retirement floor.

### v0.5.4 — 2026-09-05

- Added current, rendered original-publisher snapshots for Terminal-Bench 2.1 and Artificial Analysis Intelligence Index v4.2; preserved earlier board reads as separate historical observations.
- Rechecked the fixed ARC GPT-5.6 table and the public Scale SWE-Bench Pro table; their stored rows still match the publisher pages.
- Closed the remaining model-label, metric-id, and local-eval source-id joins without treating board labels or vendor table configurations as new comparable model runs.
- **Fixed (validator, no data change):** `scripts/validate_data_pack.py`'s `valid_until` expiry check was flagging the six intentionally-retained `gpt-5.6-{sol,terra,luna}-2026-07` pricing rows in `pricing/openai-api-2026-07.json` and `pricing/codex-credits-2026-07.json` as "expired" — these are closed historical windows (their `models.json` entries already carry `status: "historical"`, added when the current rows were introduced), not live promos that lapsed unnoticed. The check now skips the expiry error when the row's model resolves to a `status: "historical"` entry in `models.json`; it still fails loudly for any non-historical model whose `valid_until` has passed. Re-verified against live vendor pages 2026-09-09: current `gpt-5.6-sol/terra/luna` and `gpt-6-astra` rows in both files match published rates exactly (OpenAI API pricing docs and `learn.chatgpt.com/docs/pricing`); no rates changed.

### v0.5.3 — 2026-08-02

- **Investigated the community claim "Luna at max reasoning effort is the best Codex setting."** Verdict: not supported as stated — real value point, not a dominant one. Full writeup: `~/code/dotfiles/ai/research/model-routing/gpt-5-6-luna-at-max-effort-is-a-defensible-not-dominant-codex-value-pick.md`.
- **New:** `performance/deepswe-leaderboard-gpt-5-6-2026-08.json` — third-party DeepSWE leaderboard (datacurve.ai, mini-swe-agent harness, 113 tasks) reading for gpt-5.6-sol/terra/luna at effort=max, with cost/task. New metric_id `deepswe-leaderboard-datacurve` (distinct from OpenAI's own `deepswe-v1-1` vendor table) added to `metrics.json`.
- **Corrected:** `capabilities/effort-surfaces-2026-07.json` — Codex CLI's official config-reference lists no `max` value for `model_reasoning_effort` (only minimal..xhigh); `max` is a separate opt-in UI toggle per Codex's Models doc, not a config-settable value, confirmed 2026-08-02. Added `gpt-5.6-terra` and `gpt-5.6-luna` `codex_cli` surface entries (previously only `gpt-5.6-sol` had one).
- **New SOURCES:** `openai-codex-models-2026-08-02`, `deepswe-leaderboard-2026-08-02`, `majesticlabs-luna-max-2026-08-02` (secondary commentary, explicitly not cited for numeric scores).

### v0.5.2 — 2026-07-09

- **Honesty pass (Sol+Fable):** local rows reclassified harness_smoke / grade D; mixed `metric_id`s set `comparable: false` + `comparability_group`.
- Pack schema allows png/py assets; CI requires jsonschema; freshness includes boards.

### v0.5.1 — 2026-07-09

- **Local evals** mirrored: `performance/local-evals-2026-07.json` (grade A) for implement.standard, fanout.explore, review.audit on waspflow harness.
- SOURCES kind `local_eval`.

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
