# Decision model catalog changelog

## v0.2.0 — 2026-10-07

- First populated release. Data as of 2026-10-07 UTC (research ran the evening of 2026-10-06 US Central); every `generated_at` and `retrieved_at` uses 2026-10-07.
- `models.json`: all 111 Jev Decision Index 0.3 entrants plus the hosted decision APIs (Jev, Clef, Clef-flash, OpenAI Decisions, Perplexity, Liquid d1, Solar Decide, Mercury, Span-01, Tev1, Kev, meraGPT, ...). Provider-qualified router ids are aliases so rows keep their serving provider.
- `pricing/`: nine documents (TypeSafe, Cloudflare Workers AI with neuron-to-USD conversion, OpenAI, Perplexity, OpenRouter, Requesty, sference, Liquid, self-hosted). Perplexity direct $0.02/M vs routers $0.04/M and the third-party jevaihub.com conflict are recorded, not resolved.
- `performance/`: 25 documents, one per source (Decision Index 0.3 with every per-benchmark row and latency by vantage, Cloudflare launch tables as vendor-run, OpenRouter p50 latency, JevBench v1.6.1, Requesty with ECE, morrenhale, TypeSafe Evals, StreamDecisionBench, Nicia, siujev, papers, our local CPU runs, ...).
- `capabilities/`: six documents (question types, option and question caps, abstain, probabilities, context, billing of output). The reported Workers AI ~2K-token state truncation is a caveat against the advertised 64K context.
- `caveats.json`: 56 caveats with verbatim evidence (31 from the nuance research plus 25 added during population).
- `metrics.json`: one metric id per benchmark x publisher/harness; hosted-RTT and on-card latency have separate ids; official vs Jev-adapted BFCL/API-Bank/When2Call are different metrics.
- `evidence/local-decider-cpu-bench/`: vendored raw JSON of our own decider-2b/4b measurements.
- README: "At a glance (as of 2026-10-07)", decision rules the evidence does and does not support, and a worked cost-per-decision example.
- Conventions: per-token pricing rows set `cache_read_per_m` and `cache_write_per_m` equal to `fresh_input_per_m` when the vendor documents no caching terms (not a published rate); Decision Index and Cloudflare per-benchmark scores are stored as percent; large documents omit per-row `harness` and `task_family` (see `metrics.json`).

## v0.1.0 — 2026-10-06

- Seed release. Pack structure, schemas, validator integration and docs. Data files are valid but empty.
- Added `caveats.json` (nuance registry), `measurement` context on latency and cost rows, decision-model fields on `models.json`, decision-interface `capabilities/`, and raw-unit pricing with sourced USD conversion.
