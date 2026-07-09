# Data pack: `model-catalog`

Versioned **pricing** and sparse **quality/effort** facts for coding-agent model choice.

Not a CLI. Not a skill. Just versioned, **schema-validated** JSON.

## Get this pack (click / copy — no URL math)

| | |
|---|---|
| **Latest release** | [data-model-catalog releases](https://github.com/tnunamak/minnows/releases?q=data-model-catalog&expanded=true) — open the newest, hit **Assets → Download** |
| **This version** | Tag **`data-model-catalog-v0.2.0`** — [release](https://github.com/tnunamak/minnows/releases/tag/data-model-catalog-v0.2.0) |
| **All data packs** | [data/README.md](../README.md) |
| **Machine index** | [data/index.json](../index.json) on `main` |
| **Schemas** | [SCHEMA.md](SCHEMA.md) · [schemas/](schemas/) |

### Full pack

```bash
TAG=data-model-catalog-v0.2.0
curl -fsSL -L \
  "https://github.com/tnunamak/minnows/releases/download/${TAG}/${TAG}.tar.gz" \
  | tar -xz

# or
./scripts/fetch-data-pack.sh model-catalog
./scripts/fetch-data-pack.sh model-catalog v0.2.0
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
| `SCHEMA.md` / `schemas/` | **Contracts** — pricing + performance v1 |
| `pricing/*.json` | USD/MTok or Codex credits (tokensmash-compatible) |
| `performance/*.json` | Sparse vendor claims + optional scores |

## Rules of use

1. **Pin a tag** for studies; only `data/index.json` is meant to float on `main`.
2. **Never invent rates or scores** — omit or list under `missing[]`.
3. **Quota ≠ cost** — [clawmeter](https://github.com/tnunamak/clawmeter) for remaining allowance.
4. **Validate before shipping:** `./scripts/validate_data_pack.py model-catalog`

## Changelog

### v0.2.0 — 2026-07-09

- Formal **JSON Schema** (Draft 2020-12) for pack envelope, index, pricing, performance.
- Unified **performance** documents (`kind: performance` with `claims` and/or `scores`).
- `schema_version: 1` + `$schema` on all payloads.
- Stdlib **validator** (`scripts/validate_data_pack.py`); release path runs it.

### v0.1.0 — 2026-07-09

- Initial pricing (Anthropic / OpenAI / Codex credits / xAI) + sparse quality notes.
