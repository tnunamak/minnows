# Data pack: `decision-model-catalog`

Source-backed, nuance-preserving data on **decision models**: models built for fast, typed
decisions on an agent's hot path. Think "Artificial Analysis for decision models".
Sibling of [`model-catalog`](../model-catalog/README.md): same envelope, ids, provenance
rules and registries. It differs only where decision models need it.

Not a CLI. Not a skill. Just versioned, **schema-validated** JSON with a **provenance registry**.

> Status: `v0.1.0`, the first release, data as of **2026-10-07 (UTC)**. The research ran the
> evening of 2026-10-06 US Central. Decision models are weeks old and their prices, limits and
> boards move weekly: re-check anything you depend on (see [FRESHNESS.md](FRESHNESS.md)).
> Contents: 132 models, 285 metrics, 25 performance documents (7,964 score rows),
> 9 pricing documents (38 price rows), 6 capability documents, 56 caveats, 92 sources.

## At a glance (as of 2026-10-07)

Every cell below is a row in this pack. **VENDOR** marks a number run or published by the model
vendor; **BOARD** is a third-party board; **IND** is an independent run. Read the caveat ids before
you quote a number. The point of this pack is that these numbers do **not** line up in one ranking.

### Main hosted decision models

| Model (surface) | Input $/M (output) | Max questions per request | Hosted p50, OpenRouter (1 week) | Other latency, with who and where | Decision Index 0.3 Full / public score |
|---|---|---|---|---|---|
| **Jev 1.13.0** (TypeSafe API; closed) | $0.042 (output free) | not documented | 210 ms | IND Nicia: 98-167 ms p50, US-west laptop, one request at a time (92-94 ms on the 2026-10-04 re-run). BOARD Decision Index: 524.1 ms median = hosted HTTPS round trip from the board lab, **not comparable with on-card figures**. IND typed-decisions card: 710 ms | 60.11 / 57.96 (rank 3 / 10) |
| **Clef 27B** (Cloudflare Workers AI; open weights) | $0.24 = 21,818 neurons/M (output rate not listed; routers list $0) | 64 | 510 ms | IND Nicia: 638-717 ms p50, public HTTPS. IND Requesty: 626-780 ms (router hop included). **VENDOR Cloudflare: 209.3 ms, hardware/region/payload unstated.** BOARD on-card: 102.1 ms | 53.08 / 61.71 (rank 19 / 3) |
| **Clef-flash 9B** (Workers AI; open weights) | $0.09 = 8,182 neurons/M (output not listed) | 64 | 170 ms | IND Nicia: 429-533 ms p50. **VENDOR Cloudflare: 38.8 ms, conditions unstated.** BOARD on-card: 53.4 ms | 47.61 / 56.15 (rank 31 / 16) |
| **Perplexity Decider v1.1 / v1 27B** (Perplexity API; open weights) | $0.02 direct, **$0.04 on OpenRouter and Requesty** (output free) | 128 | 290 ms (v1) | IND Requesty: 379-388 ms p50 (router hop included). BOARD on-card v1.1: 104.1 ms | v1.1: 62.75 / 62.25 (rank 1 / 2) |
| **OpenAI Decisions, gpt-6-luna** (public beta; closed) | $0.10 (vendor: "there are no cache-read, cache-write, or output-token charges"; regional and long-context premiums apply) | not documented | 300 ms (3-day window) | IND Nicia: 168-171 ms p50 from a cloud executor, region unrecorded. VENDOR: "about 10x faster than the Responses API", no ms. BOARD JevBench: 300 ms adjusted | not on the board |
| **Liquid d1** (Liquid API; closed) | $0.04 on OpenRouter; Liquid publishes no paid rate (free tier d1:free) | not read | 220 ms | IND siujev: p50 424 ms, p95 992 ms (OpenRouter, Poland) | not on the board |
| **Solar Decide** (Upstage beta; closed) | $0.05 on OpenRouter ("50% off" badge, price status unclear) | choice <= 26 options | **17,300 ms** | IND siujev: p50 720 ms, p95 12.5 s | not on the board |

Where the numbers come from: prices [`pricing/`](pricing/), questions and limits
[`capabilities/`](capabilities/), OpenRouter p50
[`performance/openrouter-decision-models-2026-10-06.json`](performance/openrouter-decision-models-2026-10-06.json),
Decision Index [`performance/jev-decision-index-0-3-headline-2026-10-06.json`](performance/jev-decision-index-0-3-headline-2026-10-06.json) and
[`...-latency-...`](performance/jev-decision-index-0-3-latency-2026-10-06.json),
Nicia [`performance/nicia-admission-decision-eval-2026-10-01.json`](performance/nicia-admission-decision-eval-2026-10-01.json),
Cloudflare claims [`performance/cloudflare-clef-launch-2026-10-01.json`](performance/cloudflare-clef-launch-2026-10-01.json).

How to read the Decision Index columns: **Full** is the board headline (20% public + 50% private same-skill
tests + 30% private new-domain tasks; 80% of it cannot be reproduced by outsiders); **public** is the public-only,
chance-corrected score. They flip the Clef/Jev order: on 0.3 public Clef (61.71, rank 3) is above Jev (57.96, rank 10) but is not first (Torchcast Decision 27B 65.10 and Perplexity Decider v1.1 62.25 rank above it); on Full, Jev is above Clef. Cloudflare's "Clef is currently the leader" (2026-10-01) belongs to the earlier 0.2.1 edition, where Clef scored 61.98 against a top of 57.44 on the 2026-09-28 board snapshot (which did not list Clef). The
`balanced_skill` field in `index.json` is the *Full* score; see caveat `decision-index-full-vs-public-field-trap`.
Latency by vantage is the same story: the hosted-vs-on-card ratios in launch material compare unlike quantities
(caveat `latency-hosted-rtt-vs-on-card`). Only OpenRouter's column uses one method for every model: on it hosted Clef
is **slower** than hosted Jev, and Clef-flash is slightly faster.

### Decision rules the evidence supports

1. **Choose by task shape, not by one score.** Wide-choice, fine-grained intent: Clef 27B or Perplexity. Requesty's own run: Banking77 Clef 94.3% vs Perplexity 79.5% (Requesty did not test Jev); Jev's 80.0% on Banking77 is a **separately published morrenhale result** (3,076 items, a different harness), not Requesty's run, so do not rank it against Requesty's rows. Bounded grading, judgment, when-to-call, reasoning-style decisions: Jev (Cloudflare's own card: When2Call 80.97 vs Clef 72.37; Hard-Decisions' separate ProofWriter result for Jev, 83.8%, is another publisher's harness and is not comparable with the When2Call figures). Tool-name selection: Clef and Clef-flash score well on the Jev-adapted BFCL and API-Bank (vendor table and live Index agree). Tool-call gating and 3-level complexity: keep an LLM (Requesty: 68.7% vs Opus 90.0%). Caveat `benchmark-vs-task-shape`.
2. **Prefer binary `noul` questions to multi-level `score` questions** (Requesty typed set: noul 79-82% vs score/choice 69-72%).
3. **Use probabilities, not argmax or `confidence`.** Fit thresholds on your own labeled traffic, per provider and version, and escalate the uncertain part to an LLM or human. `confidence` is defined differently by TypeSafe, sference, Cloudflare and Perplexity (caveat `confidence-field-non-portable`).
4. **Never compare a hosted round trip with an on-card number.** Measure from your region with your state size and concurrency (caveats `latency-hosted-rtt-vs-on-card`, `latency-independent-caller-rtt-disagrees`, `latency-scales-with-state-size-and-question-count`).
5. **Rank on cost per decision at your token profile.** Hosted decision models bill input only, but per-token prices differ 5x (Clef $0.24 vs Jev $0.042) and cost per request and per decision also depend on state length, fan-out and the router (worked example below; caveat `price-per-token-is-not-cost-per-decision`).
6. **Packing many questions over one state is cheap on hosted Jev** (TypeSafe cookbook; one independent user saw flat latency to about 50 questions at 4k tokens or less). On other models check independence and the per-request cap first (sference 16, Cloudflare 64, Perplexity 128; Clef cross-attends across questions; local decider packing changed answers).
7. **Add an explicit none/other option for unseen intents** and a probability floor; do not use Clef-flash for out-of-scope detection (Cloudflare's own table, vendor-run, CLINC150+OOS macro-F1: Clef-flash 66.77 vs Clef 97.43 and Jev 89.27).
8. **Shadow-test on your own labeled traffic, including out-of-scope and adversarial cases,** before switching or trusting a board. Pin model versions and test option and record order (both figures are from Nicia's single 85-case run, not a common harness: Jev probabilities moved by up to 0.12 between identical calls; GPT-6 Luna Decisions flipped 8 of 85 decisions when records were reversed).
9. For moderation, zero refusals is a real operational edge **in Requesty's run for the Clef and Perplexity deployments it tested** (Azure GPT models could not answer 33-36% of toxic-chat prompts; those two decision models 0). It is not a property of all decision models: OpenAI Decisions can return a per-question `refusal` answer ([API reference](https://developers.openai.com/api/reference/python/resources/decisions/methods/create.md): "Each question can return a refusal instead of a scored answer").
10. Treat hosted Clef state beyond about 2K tokens as **unverified** (OpenRouter's listing says Workers AI truncates long text state to roughly the first 2K tokens although the advertised context is 65,536; caveat `workers-ai-state-truncation-2k`).

### Claims the evidence does NOT support

- "Clef is 13x (or 2.5x) faster than Jev." It divides a self-reported Clef figure of unstated conditions by a hosted round trip from a third-party lab. Independent caller-side runs show hosted Clef slower than hosted Jev.
- "Clef is the leader of the Decision Index" or "wins 7 of 10." Dated to the 0.2.1 edition (public-only), vendor-run, vendor-shortlisted tasks. On 0.3 public Clef is rank 3, not 1; on the 0.3 Full score Clef is rank 19 and Jev rank 3. Jev beats both Clef models on 11 rows of Cloudflare's own 41-benchmark table (a Clef model beats Jev on 29).
- "Clef beats Jev on 3 of 4 TypeSafe workflow evals." Margins are 0.3-3 points with no sample sizes, Jev wins one, and Clef-flash wins 1 of 4.
- "Decision models are calibrated, so thresholds transfer." ECE is dataset-dependent and `confidence` is not portable.
- "Output is free, so decision models are cheaper than any LLM." Outputs are small; cheap flash LLMs cost $0.07-0.59 per 1,000 items vs Clef $0.13 on Requesty.
- "Questions are independent, so pack as many as you like." Documented and tested for Jev only.
- "200x or 444x cheaper." Best-case workflow results against frontier baselines (TypeSafe: "the higher end of real world gains").
- "Jev is just embeddings," and "Jev beats embeddings everywhere."
- "Open 26-28B models reach 85-91% of Jev" as a current fact (a 2026-09-28 public-score statement; edition 0.3 reorders and many entrants have coverage below 1).
- "Perplexity's decider costs $0.04/M" (direct price is $0.02/M) and "jevaihub.com figures are TypeSafe's" (it is an independent, unaffiliated site; it recorded 40 requests/s vs the official 80).

### Cost per request and per decision: worked example (computed, illustrative, not a data row)

```
cost per request  = input_tokens x price_per_M / 1,000,000
input_tokens      = state + all question text and options in the request
cost per decision = cost per request / questions_in_request     (one question = one decision)
```

Assumptions: one request with a 2,000-token state and 3 questions of about 100 tokens each, so 2,300 input tokens per
request and 3 decisions per request. Output is not billed on these surfaces (Cloudflare lists no output rate). The formula
**ignores prompt caching, fan-out (a state shared by k questions is billed once on Jev-style APIs; a per-question reading of
Liquid's docs would bill it k times), per-request minimums, regional premiums and free allowances.**

| Model (price source) | $/M input | Cost per request (2,300 tokens, 3 decisions) | Cost per decision (request / 3) | Per 1M decisions |
|---|---|---|---|---|
| Perplexity direct | $0.02 | $0.000046 | $0.0000153 | $15.33 |
| Jev (TypeSafe) | $0.042 | $0.0000966 | $0.0000322 | $32.20 |
| Clef-flash (Workers AI) | $0.09 | $0.000207 | $0.000069 | $69.00 |
| OpenAI Decisions | $0.10 | $0.00023 | $0.0000767 | $76.67 |
| Clef (Workers AI) | $0.24 | $0.000552 | $0.000184 | $184.00 |

The per-decision column assumes the 3 questions are billed as one request. Sent as 3 separate requests that each repeat the
2,000-token state, each decision bills about 2,100 tokens (state + one question), roughly 2.1x the per-decision figure above
(Jev: 2,100 x $0.042 / 1M = $0.0000882).

Measured costs are separate rows (`unit: usd_per_decision`) and differ because real items are shorter: Requesty billed $0.131 per 1,000
items for Clef and $0.023 for the Perplexity decider; Bonn et al. paid $9.15 for 346,009 Jev requests (630 input tokens on average).

## Get this pack (click / copy — no URL math)

| | |
|---|---|
| **Latest release** | [data-decision-model-catalog releases](https://github.com/tnunamak/minnows/releases?q=data-decision-model-catalog&expanded=true) — open the newest, hit **Assets → Download** |
| **This version** | [data-decision-model-catalog-v0.1.0](https://github.com/tnunamak/minnows/releases/tag/data-decision-model-catalog-v0.1.0) — published by CI after this is merged to main |
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
  public beta), the Perplexity decider, and open approximations and community fine-tunes
  (every Jev Decision Index 0.3 entrant is registered; most are `community_finetune`).
- General LLMs **only as baselines**, when a source benchmarks them on decision tasks. They
  live in `model-catalog`; score rows reference them by that id. Their pricing is not
  duplicated here. Baselines that `model-catalog` lacks are recorded in the `missing[]` of
  the document that used them, not added locally.

Out of scope for now: safety/guardrail classifiers, embedding-similarity routers, and LLM
routers that pick which LLM to call. Sources that frame one as a decision model are noted
(for example the Red Hat guardrail comparison and the Reddit embeddings thread), not ingested.

## Layout

| Path | Role |
|---|---|
| `pack.json` | Envelope (tag, file list, schema pointers). Lists every file on disk. |
| `models.json` | **L0 decision-model registry** — ids + aliases (join key): 132 models (87 community_finetune, 34 third_party_board_only, 5 ga, 3 research_release, 2 preview, 1 early_access) |
| `metrics.json` | Metric registry (comparability boundary): one id per benchmark x publisher/harness; latency per vantage |
| `caveats.json` | **Caveat registry** — nuance that must travel with the numbers |
| `SOURCES.json` | **Provenance registry** — id → URL / publisher / kind; third-party resellers are registered only to record conflicts |
| `SCHEMA.md` / `schemas/` | Contracts |
| `pricing/*.json` | USD rates by vendor/surface, free output, raw vendor units (Workers AI neurons) next to a sourced conversion, router and reseller conflicts |
| `performance/*.json` | One document per source: claims and scores with `measurement` context for latency and cost |
| `capabilities/*.json` | Decision-interface surface per model/surface (question types, option and question caps, abstain, probabilities, context incl. the Workers AI truncation caveat) |
| `evidence/` | Vendored raw runs of our own local decider measurements (`scenarios.py` withheld: private conversation) |

Performance documents: Decision Index 0.3 (headline/category/calibration/coverage for all 112 rows, **every per-benchmark
row, no cut**, and latency by vantage), Cloudflare launch tables (VENDOR), OpenRouter p50 and usage, JevBench v1.6.1, Requesty,
morrenhale, TypeSafe Evals (VENDOR), StreamDecisionBench, Nicia, siujev, DEV, Hard-Decisions, S1MB, typed-decisions card, four
papers, Perplexity cards (VENDOR), OpenAI and TypeSafe vendor claims, a Reddit fan-out anecdote, and our local CPU runs (grade D).

### Known gaps (not estimated)

JevBench: 41 model entries are stored out of about 130 systems listed (the rest are hobbyist rebuilds, controls, rerankers and author-hosted demos; see that document). No Jev price on Requesty;
no vendor-published absolute latency or accuracy for OpenAI Decisions, and its option cap is undocumented; no Cloudflare hosted latency with context; `gpt-5-nano`, `qwen3.6-27b`, `qwen3.5-4b`
and the Luna/Sol/Terra versions behind TypeSafe Evals and StreamDecisionBench are absent from `model-catalog` (recorded in `missing[]`);
Artificial Analysis, LMArena, Vals, Epoch, llm-stats, Helicone and the Hugging Face Open LLM Leaderboard have no decision-model coverage
(searched), and the official BFCL V4 board lists no decision model.

## Rules of use

1. **Pin a tag** for studies; only `data/index.json` is meant to float on `main`.
2. **Never invent numbers.** A gap is recorded under `missing[]`, not estimated.
3. **Latency needs context.** A `ms` row without who/where/percentile is rejected. Do not rank a hosted round-trip against an on-platform figure; see [SCHEMA.md](SCHEMA.md#latency-context-rule).
4. **Per-token price is not cost per decision.** See [SCHEMA.md](SCHEMA.md#cost-per-decision-rule).
5. **Scores compare only within one metric + harness + publisher** (`comparability_group`). Decision Index and Cloudflare per-benchmark rows are stored as percent; Jev-adapted BFCL/API-Bank/When2Call are *not* the official boards.
6. **Read the caveats.** If a row has `caveat_ids`, show them with the number.
7. **Vendor-run numbers are tagged** (`source_type: vendor_table | vendor_claim`, grade C). A vendor that ran a competitor's model (Cloudflare ran Jev) is a caveat of its own.
8. **Validate before shipping:** `./scripts/validate_data_pack.py decision-model-catalog --require-jsonschema`

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
