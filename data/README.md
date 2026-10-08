# Data packs

Versioned **JSON (and friends)** for tools and agents — not CLIs, not skills.
Each pack lives in `data/<name>/` with a `pack.json`. For an overview of the whole repo, see the [root README](../README.md).

## Packs

| Pack | What it is | Browse |
|------|------------|--------|
| **model-catalog** | Model pricing, source-backed quality and effort scores, effort/mode **capabilities**, L0 `models.json`, and the generated charts | [README](model-catalog/README.md) · [SOURCES](model-catalog/SOURCES.json) · [SCHEMA](model-catalog/SCHEMA.md) · [releases](https://github.com/tnunamak/minnows/releases?q=data-model-catalog&expanded=true) |
| **decision-model-catalog** | Decision models (typed decisions on an agent's hot path): models, metrics, **caveats**, pricing (incl. raw units), performance with latency/cost context, decision-interface capabilities. Baselines resolve via model-catalog | [README](decision-model-catalog/README.md) · [SOURCES](decision-model-catalog/SOURCES.json) · [SCHEMA](decision-model-catalog/SCHEMA.md) · [releases](https://github.com/tnunamak/minnows/releases?q=data-decision-model-catalog&expanded=true) |
| **model-choice-policy** | Task-shaped **operating points** (policy only; pins a catalog version) | [README](model-choice-policy/README.md) · [releases](https://github.com/tnunamak/minnows/releases?q=data-model-choice-policy&expanded=true) |
| **local-evals** | Fixed-harness local P(success) for policy ops (smoke only) | [README](local-evals/README.md) |

Versions are not repeated here, so this table cannot go stale. Each pack's `pack.json` holds its tag, and [index.json](index.json) lists the latest tag per pack.

Machine-readable index (always on `main`): **[index.json](index.json)** — lists each pack’s `latest_tag` and ready-made URLs.

## How to get a pack (humans & agents)

### Latest

1. Open this README or [index.json](index.json).
2. Click **Latest** / copy the tarball URL for the pack (no path algebra).
3. Or:

```bash
./scripts/fetch-data-pack.sh model-catalog
```

### Specific version

1. Open [Releases](https://github.com/tnunamak/minnows/releases?q=data-) filtered by pack name.
2. Open the version → **Assets** → download the `.tar.gz`.
3. Or:

```bash
./scripts/fetch-data-pack.sh model-catalog v0.1.0
# resolves tag data-model-catalog-v0.1.0
```

## Layout of a pack

```text
data/<pack-name>/
  pack.json       # required: name, tag, files[], description, schema_version
  README.md       # required: Get this pack table with real links
  SCHEMA.md       # recommended: human contract for payloads
  schemas/        # recommended: JSON Schema files
  …               # pack-defined files
data/schemas/     # shared: pack-v1 + index-v1 envelopes
```

Validate: `./scripts/validate_data_pack.py` (all packs + index).

## Tag & release convention

- Tags: `data-<pack-name>-v<semver>` (e.g. `data-model-catalog-v0.1.0`)
- Each version: GitHub **Release** with the same name, assets:
  - `data-<pack>-vX.Y.Z.tar.gz` (directory named `<pack>/`)
  - `pack.json` (copy)
  - `SHA256SUMS`
- After cutting a release: update the pack README “this version” links and `data/index.json`.

Helper: `./scripts/release-data-pack.sh model-catalog 0.1.0` (tarball + optional `gh release create`).

## Env conventions

```bash
export DATA_PACKS_HOME=~/.local/share/minnows-data   # set by install.sh
# each pack: $DATA_PACKS_HOME/<pack-name>/pack.json
```

Tools resolve packs from `DATA_PACKS_HOME` or a local minnows checkout `data/`. Prefer **pinned tags** for reproducibility.
