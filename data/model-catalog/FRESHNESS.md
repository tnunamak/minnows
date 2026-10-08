# Freshness cadence

| Tier | Files | Cadence | Stale after |
|------|-------|---------|-------------|
| **Load-bearing** | pricing for models in policy ops; capabilities effort surfaces | monthly or on vendor price change | 45 days |
| **Boards** | AA live, Terminal-Bench, SEAL SWE, ARC | monthly | 45 days |
| **Launch tables** | vendor GPT/Sonnet launch docs | on new launch only | n/a |
| **Digitized** | chart extracts | only when an op decision needs them | n/a |

## CI hook

```bash
# Fail if any load-bearing retrieved_at is older than 45 days (CI passes --fail):
./scripts/check_freshness.py --max-age-days 45
```

Pricing and capability files are checked by name. Boards are checked by their **newest
snapshot**: the most recent `performance/` file that cites a source on that board's URL
(`BOARDS` in the script). A re-read that found no new rows still counts when its file
cites the board's source (for example, a board-coverage file). Superseded snapshots keep
their original `retrieved_at` and are not checked one by one, because old dated snapshots
are never rewritten. To clear a stale board, add a new dated snapshot or coverage note.

Promo rates must carry `valid_until` (e.g. Sonnet 5 intro → 2026-08-31). Expired
rows fail `validate_data_pack.py` — **unless** the row's model id resolves (via
`models.json`) to `status: "historical"`, in which case a closed `valid_from`…`valid_until`
window is the intended historical record (e.g. `gpt-5.6-sol-2026-07`, superseded by the
current `gpt-5.6-sol` row) and is not flagged as a lapsed promo. Mark a model
`"historical"` in `models.json` only when a current, non-expired row for the same
model family already exists — never as a way to silence a genuinely stale live rate.
