# Data pack: `model-catalog`

Versioned **pricing** and sparse **quality/effort** facts for coding-agent model choice.

Not a CLI. Not a skill. Just JSON.

## Get this pack (click / copy — no URL math)

| | |
|---|---|
| **Latest release** | [data-model-catalog releases](https://github.com/tnunamak/minnows/releases?q=data-model-catalog&expanded=true) — open the newest, hit **Assets → Download** |
| **This version** | Tag **`data-model-catalog-v0.1.0`** — [release page](https://github.com/tnunamak/minnows/releases/tag/data-model-catalog-v0.1.0) (created when published) |
| **All data packs** | [data/README.md](../README.md) |
| **Machine index** | [data/index.json](../index.json) on `main` (points at latest tags) |

### Full pack (recommended)

After the release exists:

```bash
TAG=data-model-catalog-v0.1.0
curl -fsSL -L \
  "https://github.com/tnunamak/minnows/releases/download/${TAG}/${TAG}.tar.gz" \
  | tar -xz
# → ./model-catalog/pack.json and files listed in pack.json
```

Or use the helper from a minnows checkout:

```bash
./scripts/fetch-data-pack.sh model-catalog          # latest (via data/index.json)
./scripts/fetch-data-pack.sh model-catalog v0.1.0   # specific (tag suffix)
```

### Local checkout

```bash
# from a clone of minnows
ls data/model-catalog/
export DATA_PACKS_HOME="$PWD/data"   # or after install: ~/.local/share/minnows-data
# pack root: $DATA_PACKS_HOME/model-catalog
```

`./install.sh` symlinks `data/*` into `~/.local/share/minnows-data/` when present.

## What's inside

| Path | Contents |
|---|---|
| `pack.json` | Identity, tag, file list |
| `pricing/*` | USD/MTok or Codex credits (tokensmash-compatible shape) |
| `performance/*` | Sparse vendor quality/effort claims — **not** invented chart digits |

## Rules of use

1. **Pin a tag** for studies and CI; don't load pack files from floating `main` except `data/index.json`.
2. **Missing quality is OK** — Grok has rates, not effort curves, in this pack.
3. **Quota ≠ cost** — remaining allowance is [clawmeter](https://github.com/tnunamak/clawmeter); this pack is rates + sparse quality.
4. **Re-fetch sources** before spend-critical decisions; `retrieved_at` goes stale.

## Schema notes (pricing files)

Compatible with tokensmash pricing tables:

- `kind`: `api_usd` | `codex_credits`
- `agent`: `claude-code` | `codex` | `grok`
- `models`: map of id → `{fresh_input_per_m, cache_read_per_m, cache_write_per_m, output_per_m}`
- `match`: ordered substring patterns for fuzzy model resolution

## Changelog

### v0.1.0 — 2026-07-09

- Initial pack: Anthropic / OpenAI API rates, Codex credits, xAI API rates.
- Sparse Anthropic effort/cost-performance claims (Sonnet 5 post); OpenAI GPT-5.5 vendor scores.
