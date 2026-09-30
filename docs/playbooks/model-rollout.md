# Playbook: new model, new provider, price change, new evidence

Use this when a vendor ships a model, a price changes, a benchmark board publishes new rows, or `check_freshness.py` reports stale files. It distills the Opus 5.5 rollout (2026-09-22) and the Sonnet 5.5 rollout (2026-09-29).

Two packs are involved. `model-catalog` holds facts (prices, capabilities, benchmark rows). `model-choice-policy` holds decisions (which model each operating point runs). Facts ship first. A policy change is a separate decision that needs owner approval.

## Which steps to run

| Trigger | Steps |
|---------|-------|
| New model from a known vendor | 1, 2, 3, 4, 5, 6, 7 |
| New vendor or provider | 1, 2, 4, 5, 6, 7, plus a new family and tier in `models.json` and a new `tools/hone/providers/` entry if Hone should route to it |
| Price change only | 1 (pricing row only), 4, 5, 6 |
| New board data for an existing model | 2, 4, 5, 6; step 3 only if the data could change a route |
| Stale files (`check_freshness.py`) | 2, 4, 5, 6 |

## Rules that came from mistakes

1. Do not trust a summary of a launch post. Parse the page's `<table>` in code and diff every cell against what you stored. Opus 5.5 lost cells this way.
2. Label an effort level only when a footnote states it. Otherwise record `unknown`.
3. Reuse metric ids from `metrics.json`. Put publisher or harness differences in `comparability_group`.
4. Pricing `patterns` match in order. Put `sonnet-5-5` and `sonnet 5.5` above `sonnet-5` and `sonnet 5`.
5. Never mark the previous model `historical` while it is still the only current row for its family (see `data/model-catalog/FRESHNESS.md`).
6. Missing third-party data is provisional. Record `retrieved_at` and a recheck date. Do not invent numbers; put unverified items in `missing` or `notes`.
7. Never rewrite an old dated snapshot. Add a new dated file.
8. Claude Code and the API can have different default efforts (Sonnet 5.5: medium and high). Record them as separate facts.
9. Do not add an entry for a model the vendor only announced ("Haiku 5.5 later").

## 1. Catalog facts (`data/model-catalog/`)

Read `README.md`, `SCHEMA.md`, `FRESHNESS.md` and `data/DEFINITION_OF_DONE.md` first. Copy how the previous model of the same family is represented. `git show` the last rollout's commits for the pattern.

Files to touch for a new model:

- `SOURCES.json`: one entry per source URL. Every number links here.
- `models.json`: id, status `ga`, family, tier, release date.
- `pricing/`: the row and the ordered `patterns`.
- `capabilities/`: effort surfaces.
- `performance/anthropic-<model>-launch-*.json`: the launch benchmark table. Add digitized effort curves (`digitized/`) and system-card rows only if they are published. Otherwise say so in the report.
- `metrics.json`: new metrics only.
- `pack.json` (`files[]`, version tag, description), `README.md` ("This version"), `CHANGELOG.md`.
- `tools/hone/models.json`: the Hone registry. A model with `calibration: null` cannot route until `hone calibrate` runs. Do not fake a calibration.
- Search the repo for lists of current models (`grep -rn "<previous-model-id>"`) and update them.

## 2. Third-party boards

Check every board the catalog tracks: Artificial Analysis (index, per-eval, cost, effort ladder), vals.ai (including Terminal-Bench 4.0), Terminal-Bench, ARC Prize, DeepSWE, SEAL and SWE-bench, LMArena, BrowseComp, Epoch, OpenRouter.

- If the model is listed, save its rows and same-snapshot rows for the comparison roster (the previous model in the tier, the current Opus, the GPT-6 tiers, Haiku) in a new dated file. Record the effort each row ran at.
- If it is not listed, write one `performance/<model>-board-coverage-<date>.json` with a recheck date.
- Look for low and medium effort data. The cheap-tier ops run at low and medium, and most boards publish only max.
- Note when a board says its runs used a pre-release deployment.

## 3. Policy (`data/model-choice-policy/`)

Get owner approval before you change any routing.

1. Apply the tier rule in the owner's `ai/AGENTS.md` rule 1: the cheapest tier whose evidence clears the bar, then the newest GA model in that tier.
2. Run `python3 scripts/recommend_ops.py`. Run it on plain `origin/main` too. If both say INSUFFICIENT_EVIDENCE, the change rests on the tier rule and the boards, and the PR must say so.
3. Compare against the tier above on the boards that have same-snapshot rows. State where the cheaper model loses at the effort you pick.
4. Edit `operating-points.json` (`expands_to`, `evidence_refs`, `known_gaps`), the table and changelog in `README.md`, and `op-requirements.json`.
5. Bump `policy_version` in `operating-points.json`, and `tag`, `generated_at`, `description` in `pack.json`. Bump `catalog_ref` and the pin text to the new catalog tag in all four files. The validator fails if `tag` and `policy_version` disagree.

If no op should change, still bump the pins and write "No routing change" in the changelog.

## 4. Validate

```bash
uv run --with jsonschema python scripts/validate_data_pack.py --require-jsonschema
python3 scripts/check_freshness.py --max-age-days 45
uv run --with pytest --with jsonschema python -m pytest tests/test_recommend_ops.py -q
```

CI also runs the Hone suites, which load `tools/hone/models.json`. The pack validator does not check that file, so run them yourself when you touch it. Redirect output to a file; some suites exit 1 when stdout is `/dev/null`.

```bash
for c in "tools/hone/providers/test-discrimination.mjs --self-test" "tools/hone/hone work --self-test" \
         "tools/hone/hone lane --self-test" tools/hone/lib/test-agenda.mjs \
         tools/hone/lib/test-collectors.mjs tools/hone/lib/test-report-run.mjs; do
  node $c >/tmp/hone.out 2>&1 || { echo "FAIL: $c"; tail -5 /tmp/hone.out; }
done
```

The Hone registry accepts only the efforts `low`, `medium`, `high`, `xhigh` and `max`. Do not copy a provider-only effort such as `ultra` into it; the registry then fails to load and every Hone suite breaks (this blocked the GPT-6.1 Sol release).

Validators must pass. Read every file you touched once more. Grep for the old version string to find stragglers.

## 5. Commit and PR

- Branch from `origin/main`. One objective per PR.
- The repo git config defaults to `github-actions[bot]`. Set the author with `git -c user.name=... -c user.email=...`. Sign off (`-s`) and end the message with `Assisted-by: AI`.
- Do not `git add -A`. Untracked `devspecs/tasks/*` directories appear during agent work and must stay out of commits.
- PR body: a table of routing changes first, then why, caveats and what was checked. Run `slopgate check --kind pr --fast --file <body>` before posting.

## 6. Release

Merging to `main` publishes any pack whose `pack.json` `tag` has no GitHub Release yet (`scripts/ci-publish-pending-packs.sh`, run by `.github/workflows/release.yml`).

1. Merge the PR. Then `gh run list --repo tnunamak/minnows --limit 3` and `gh release list --repo tnunamak/minnows --limit 4`.
2. If the Release run fails at "Generated skills are current", run `bash sync.sh`, commit the `skills/` diff, and merge that. Publishing is blocked until it passes.
3. Confirm both new tags appear in `gh release list`. A green PR check is not enough: `validate` on the PR only checks packs. The Release run also runs the Hone suites, the shell and Python syntax checks, and the skills check, and it skips publishing if any fails. Read `gh run view <id> --json jobs` for the failing step, not just the run status.

## 7. Downstream (after the release exists)

| Where | Action |
|-------|--------|
| This machine's installed packs (`~/.local/share/minnows-data/`) | Refresh with `./scripts/fetch-data-pack.sh <pack>` or `./install.sh`. Check `jq -r .tag` on both `pack.json` files. `model-policy-check` reads this copy. |
| `model-policy-check` (dotfiles `bin/.local/bin`) | Run it. It fails when the pack pins a catalog that differs from the installed one, so refresh both packs together. |
| waspflow | Its bundled `data/model-choice-policy/` copy refreshes from the released pack only. Add runtime-attestation test cases for the new model id (below). |
| Clawmeter | Read-only check with `clawmeter claude --json`. It reads model scopes generically. Last two rollouts needed no change. |
| Stale guidance | Grep dotfiles (`ai/`, `claude/`), pdpp, data-connect, waspflow, clawmeter docs and agent memory for the old model name used as a default. Update or list each hit. Aliases like `sonnet` follow the provider and do not need edits. |

### Runtime-attestation test cases (waspflow)

Waspflow asks for a model, then reads which model the lane actually served, and marks the lane matched or drifted. Matching is by id. Without care, `claude-sonnet-5` would count as matching `claude-sonnet-5-5` because one id is a prefix of the other. `model_id_corroborates_request` in `lib/core.sh` accepts exact ids, dated snapshots of the same version, and family aliases, and rejects prefix collisions. The `att-<model>-*` cases in `scripts/verify.sh` prove it: old pin served new, new pin served old, exact pin, and alias. Add a set for each new model id whose name extends an older one. Then run the full `scripts/verify.sh`. It takes about 15 minutes.

## How the Sonnet 5.5 rollout was run

Three lanes in parallel, each with a written brief that listed the rules above: catalog facts, third-party boards, and waspflow plus stale-guidance sweep. The orchestrator merged the branches, re-checked the effort-curve values against the live launch page itself, ran the validators, and presented the routing proposal for approval. Briefs and reports from that run were in `~/.tmp/sonnet55/`.
