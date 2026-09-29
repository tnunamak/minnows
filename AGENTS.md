# minnows

Agent tools and versioned data packs. See [README.md](README.md) for layout.

## Playbooks

- **New model, new provider, price change, new benchmark data, or stale data:** follow [docs/playbooks/model-rollout.md](docs/playbooks/model-rollout.md). It covers the catalog, third-party boards, routing policy, validation, release, and downstream checks.

## Rules

- Routing changes in `data/model-choice-policy/` need explicit owner approval.
- Run `scripts/validate_data_pack.py --require-jsonschema` and `scripts/check_freshness.py` before you open a PR.
- After editing a tool, `SKILL.md`, or `lib/`, run `bash sync.sh` and commit the `skills/` diff. CI fails without it.
- Sign off commits (`-s`) and end assisted commits with `Assisted-by: AI`. Set the author explicitly; the repo config defaults to `github-actions[bot]`.
