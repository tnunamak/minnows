# Freshness cadence

| Tier | Files | Cadence | Stale after |
|------|-------|---------|-------------|
| **Load-bearing** | pricing for models in policy ops; capabilities effort surfaces | monthly or on vendor price change | 45 days |
| **Boards** | AA live, Terminal-Bench, SEAL SWE, ARC | monthly | 45 days |
| **Launch tables** | vendor GPT/Sonnet launch docs | on new launch only | n/a |
| **Digitized** | chart extracts | only when an op decision needs them | n/a |

## CI hook

```bash
# Fail if any load-bearing retrieved_at is older than 45 days (when enforced):
./scripts/check_freshness.py --max-age-days 45
```

Promo rates must carry `valid_until` (e.g. Sonnet 5 intro → 2026-08-31). Expired
rows fail `validate_data_pack.py`.
