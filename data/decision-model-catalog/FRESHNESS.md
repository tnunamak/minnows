# Freshness cadence

| Tier | Files | Cadence | Stale after |
|------|-------|---------|-------------|
| **Load-bearing** | `pricing/*.json` (vendor rates, free-output and per-request terms) | monthly or on vendor price change | 45 days |
| **Boards** | independent decision benchmarks and the Jev Decision Index (`performance/*.json` from third-party boards) | monthly | 45 days |
| **Launch tables** | vendor launch posts, changelogs, model cards | on new launch only | n/a |
| **Capabilities** | `capabilities/*.json` | on vendor interface change | 90 days |
| **Local measurements** | our own latency/cost runs | only when a decision needs them | n/a |

Documents by tier in v0.1.0: pricing/* (load-bearing); jev-decision-index-0-3-*, openrouter-decision-models-*, jevbench-*, morrenhale-*, streamdecisionbench-*, s1mb-*, localllama-typed-decisions-card-*, hard-decisions-* (boards); cloudflare-clef-launch-*, typesafe-evals-*, perplexity-model-cards-*, typesafe-launch-claims-*, openai-decisions-claims-* (vendor launch tables and claims); requesty-*, nicia-*, siujev-*, devto-*, reddit-*, arxiv-* (one-off independent reports); local-decider-cpu-measurements-* (local). Only documents holding `third_party_board` rows are age-checked.

Early-access and limited-preview pricing and interfaces change fast. Re-check them at the
board cadence, not the launch-table cadence.

## CI hook

```bash
# Fail if any load-bearing or board file's retrieved_at is older than 45 days:
./scripts/check_freshness.py --max-age-days 45 --fail
```

`scripts/check_freshness.py` checks every `pricing/*.json` in this pack, and every
`performance/*.json` that holds at least one `source_type: "third_party_board"` row (a
live board). Launch-table and local-measurement documents are not age-checked. An empty
seed has nothing to check.

Promo rates must carry `valid_until`. Expired rows fail `validate_data_pack.py`, unless the
row's model resolves to `status: "historical"`.
