# Decision model catalog changelog

## v0.1.2 — 2026-11-26

Freshness refresh (minnows #149). Every pricing and performance file older than 45 days was re-read at its source and re-dated `2026-11-26`, each with a dated `SOURCES.json` entry. New models and rows at the sources were listed but not added.

- Values changed: Cloudflare `clef-flash` $0.090 to $0.038 per M input; OpenRouter `clef` $0.24 to $0.192; OpenAI Decisions cache read $0.01 and write $0.125 are now published; sference questions-per-request limit 16 to 64; JevBench v1.6.1 composite ranks (34 systems joined that board after the snapshot; no score changed); OpenRouter latency, weekly-request and uptime figures.
- Removed because the source no longer states them: the Liquid d1 pricing file (`pricing/liquid-ai-api-2026-10.json`, only `d1:free`), three OpenRouter models (`pplx-decider-v1-27b`, `liquid-d1`, `mercury-decide`), `jet-v6.2` from the self-hosted open-weights file, the sference "EU GPUs" claim, Typesafe rate limits, and 98 Jev per-benchmark latency figures that the source leaves empty.
- Kept on cited archives: 56 JevBench hosted-API figures that the live board no longer states; two web.archive.org captures of the frozen v1.6.1 board (2026-10-06, 2026-10-11) still state them.
- Jev Decision Index: the live index is now 0.4.1 (it was 0.3). 60 of the 112 stored models carry 0.4.1 rows with their own `snapshot_id` and comparability group; the other 52 keep their 0.3 rows. The `di03-*` metric ids still say 0.3 in `metrics.json`.

## v0.1.1 — 2026-10-11

Data corrections from reviewed fix PRs (#34, #36, #37, #40, #41, #46). No new models or sources.

- Pricing lookup order: Span-01 Lite now matches its free row before paid Span-01, and self-hosted Clef Flash matches before Clef.
- The three Clef AG News rows limited to 62 items by quota set `comparable: false`, so consumers do not rank them against full-dataset results.
- Local CPU latency rows: the 66 ms repeated-request row sets `prefix_cache: true`; server and basic-case rows describe their real sampling (one first request, median of three later requests, or a median across 12 cases) instead of the in-process five-repeat description.
- The local benchmark scripts in `evidence/` abort when their server dies or never becomes ready, instead of measuring anyway.

## v0.1.0 — 2026-10-07

- First release. Data as of 2026-10-07 UTC (research ran the evening of 2026-10-06 US Central); every `generated_at` and `retrieved_at` uses 2026-10-07.
- Pack structure: `caveats.json` (nuance registry), `measurement` context on latency and cost rows, decision-model fields on `models.json`, decision-interface `capabilities/`, raw-unit pricing with sourced USD conversion, schemas, validator integration and docs.
- `models.json`: all 111 Jev Decision Index 0.3 entrants plus the hosted decision APIs (Jev, Clef, Clef-flash, OpenAI Decisions, Perplexity, Liquid d1, Solar Decide, Mercury, Span-01, Tev1, Kev, meraGPT, ...): 132 models. Provider-qualified router ids are aliases so rows keep their serving provider. Different checkpoints under one name get separate ids (Kev 9B v2 `kev-9b` vs the earlier `kev-9b-raised` that Cloudflare's card table used; Perplexity v1 vs v1.1).
- `pricing/`: nine documents (TypeSafe, Cloudflare Workers AI with neuron-to-USD conversion, OpenAI, Perplexity, OpenRouter, Requesty, sference, Liquid, self-hosted). Perplexity direct $0.02/M vs routers $0.04/M and the third-party jevaihub.com conflict are recorded, not resolved. Cache rates are a number only where the source prints one; otherwise `null` with `cache_rates_status` (`not_published` or `vendor_states_none_charged`). No cache rate is inferred from the input price.
- `performance/`: 25 documents, one per source (Decision Index 0.3 with every per-benchmark row and latency by vantage, Cloudflare launch tables as vendor-run, OpenRouter p50 latency, JevBench v1.6.1, Requesty with ECE, morrenhale, TypeSafe Evals, StreamDecisionBench, Nicia (re-verified against the repo's raw result files), siujev, papers, our local CPU runs, ...). Rows from author-submitted Decision Index latency runs name the author as `measured_by` and the board as `published_by`.
- `capabilities/`: six documents (question types, option and question caps, abstain, probabilities, context, billing of output). The reported Workers AI ~2K-token state truncation is a caveat against the advertised 64K context.
- `caveats.json`: 56 caveats with verbatim evidence.
- `metrics.json`: 285 metric ids, one per benchmark x publisher/harness; hosted-RTT and on-card latency have separate ids; official vs Jev-adapted BFCL/API-Bank/When2Call are different metrics. JevBench capability is the mean of the Intelligence and Calibration axes (not accuracy); its composite is the 25/25/25/25 harmonic mean of Intelligence, Calibration, Speed and Cost.
- Validator: a score row's unit must equal its registered metric's `unit_default`, and the latency/cost context requirement follows the metric, not the row's own unit label.
- `evidence/local-decider-cpu-bench/`: vendored raw JSON of our own decider-2b/4b measurements.
- README: "At a glance (as of 2026-10-07)", decision rules the evidence does and does not support, and a worked example that shows cost per request and cost per decision (request cost divided by the questions in the request).
- Conventions: Decision Index and Cloudflare per-benchmark scores are stored as percent; large documents omit per-row `harness` and `task_family` (see `metrics.json`).
