# Data pack: `model-choice-policy`

**Policy**, not facts. Task-shaped **operating points** that expand to explicit
`provider` / `model` / `effort` / `mode` flags for `waspflow` (and any thin
resolver).

Separates from [`model-catalog`](../model-catalog/) so recommendations never
contaminate pricing or benchmark evidence.

## Get this pack

| | |
|---|---|
| **This version** | Tag **`data-model-choice-policy-v0.1.8`** — not yet released; latest published release remains [data-model-choice-policy-v0.1.6](https://github.com/tnunamak/minnows/releases/tag/data-model-choice-policy-v0.1.6) until `./scripts/release-data-pack.sh` is run |
| **Latest** | [releases](https://github.com/tnunamak/minnows/releases?q=data-model-choice-policy&expanded=true) |
| **Facts catalog** | [model-catalog](../model-catalog/) — pin is `catalog_ref` in the policy file |

```bash
./scripts/fetch-data-pack.sh model-choice-policy
# or
TAG=data-model-choice-policy-v0.1.6  # latest RELEASED tag; v0.1.8 is working-tree only until released
curl -fsSL -L \
  "https://github.com/tnunamak/minnows/releases/download/${TAG}/${TAG}.tar.gz" \
  | tar -xz
```

## Doctrine

1. Operating points are **task-shaped** (`implement.standard`), not `cheap|default|max`.
2. Expansion must be **explicit and logged** — no silent auto-routing.
3. **Evidence confidence** is as important as sticker cost.
4. **Quota ≠ dollars** — never merge without an explicit exchange rate.
5. Update ops only from source-backed catalog facts or local evals.
6. Raw flags always win: `--provider` / `--model` / `--effort` override `--op`.

## Use with waspflow

```bash
waspflow ops list --task implementation --constraint balanced
waspflow ops explain implement.standard
waspflow ops resolve implement.standard --json
waspflow spawn --op implement.standard --lane fix -- "…"
```

Waspflow resolves from (first hit):

1. `$WASPFLOW_OPS_POLICY` (file path)
2. `$DATA_PACKS_HOME/model-choice-policy/operating-points.json`
3. Bundled `waspflow/data/model-choice-policy/operating-points.json`

## Operating points (10)

| Op | Provider / model / effort |
|----|---------------------------|
| `recover.report` | claude / sonnet-5 / low |
| `fanout.explore` | claude / sonnet-5 / medium |
| `docs.lookup` | claude / sonnet-5 / low |
| `implement.standard` | claude / sonnet-5 / medium |
| `implement.quota-tight` | claude / sonnet-5 / low |
| `implement.accuracy-first` | codex / gpt-6-astra / high |
| `review.audit` | codex / gpt-6-astra / high |
| `advisor.deep` | claude / sonnet-5 / high |
| `ui.computer-use` | codex / gpt-6-astra / medium |
| `grok.explore-only` | grok / grok-4.5 / high |

## Changelog

### v0.1.8 — 2026-09-09

- Move Codex ops to GA **gpt-6-astra** per owner model policy (retire gpt-5.6-sol): `review.audit` and `implement.accuracy-first` to effort **high** (never xhigh/max by default), `ui.computer-use` to effort **medium** (mechanical/implementation-shaped work). Trigger: `waspflow doctor`'s stale-edge warning on the `preferred_over` entry below, surfaced after gpt-6-astra reached GA.
- Retire the `gpt-5.6-luna over gpt-5.4-mini` `preferred_over` edge — it was authored rot-aware and the owner policy no longer prefers any 5.x model. No replacement edge added: the catalog's gpt-6 family has only `gpt-6-astra` as GA, no cheap-tier gpt-6 model yet, so there is nothing to prefer over gpt-5.6-luna without inventing evidence.
- `evidence_refs` re-pointed at existing catalog rows for gpt-6-astra (`performance/openai-gpt-6-astra-launch-2026-09`, `performance/terminal-bench-4-astra-audit-2026-09`, `pricing/openai-api-2026-07`, `pricing/codex-credits-2026-07`). `evidence_confidence` held at **medium**, not raised — the model/effort swap is owner-policy + GA-status driven, not new local evidence (the cited terminal-bench-4 audit row is itself grade C / `comparable: false`).
- Catalog pin carries forward unchanged from v0.1.7: **v0.5.4**.

### v0.1.7 — 2026-09-05

- Pin catalog **v0.5.4**. Operating-point recommendations are unchanged.

### v0.1.6 — 2026-08-02

- Pin catalog **v0.5.3** after the GPT-5.6 Codex effort-tier value-claim check.

### v0.1.5 — 2026-07-11

- Refresh `review.audit` to Codex **gpt-5.6-sol** / xhigh.

### v0.1.4 — 2026-07-09

- Pin catalog **v0.5.2**. Top-3 ops: `evidence_confidence: medium` (not high); local rows are harness smoke only.

### v0.1.4 — 2026-07-09

- Pin catalog to **`data-model-catalog-v0.5.2`** (was v0.3.0).
- Evidence refs for Sonnet ops cite digitized effort curves (`anthropic-sonnet5-digitized-2026-07`).
- Validator now enforces: unique op ids, escalate graph, `expands_to` vs capabilities, catalog:// and source:// resolvability, pack.json pin agreement.

### v0.1.0 — 2026-07-09

- Initial 10 operating points from expert recommendation (README previously said 8).
- Pins `model-catalog@data-model-catalog-v0.3.0`.
