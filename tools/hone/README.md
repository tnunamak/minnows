# hone — repo-quality engine

`hone` turns sparse owner intent into evidence-backed repo improvements: durable inventory →
candidate packets → classification → ranking → (wave 2) workflow execution with maker≠judge.
The design is frozen in [SPEC.md](SPEC.md); the packet format in
[schemas/candidate-packet.yaml](schemas/candidate-packet.yaml). This README is operational docs only.

**v1 wave 1 scope:** `inventory` + `plan` (deterministic-heuristic classification). `work`,
`report`, `run` are stubs that exit 1. Packet enrichment via LLM providers is wave 2 — every
judgment-dependent packet field is emitted with an explicit `[v1 deterministic heuristic …]`
label, never presented as validated.

## Usage

```bash
hone inventory --repo /path/to/repo            # collectors → <repo>/quality/inventory/*.json
hone plan      --repo /path/to/repo --top 20   # packets    → <repo>/quality/packets/*.yaml
hone work <id> --repo /path/to/repo            # execute one packet (maker ≠ judge, evidence-gated)
hone reset <id> --repo /path/to/repo           # deliberately reopen a terminal packet (landed needs --force)
```

Requirements: Node >= 22.7; `biome` runnable inside the target repo (default `npx biome`);
`typescript` resolvable from the target repo's node_modules (its own tsc — hone ships no deps).
Git history is used for churn; a non-git target degrades to churn 0 with `repo_sha: no-git`.

### Engine vs per-repo state

Engine code lives here (repo-independent). Per-repo state lives **in the target repo**:

```
<repo>/quality/
  hone.yaml          # the repo profile (optional; generic defaults otherwise)
  inventory/         # durable inventory, stamped with repo_sha (idempotent overwrite)
    meta.json            # repo_sha, profile source, counts, timings
    tier-mass.json       # flagged-fn universe w/ tier routing + mass by file & subsystem
    callback-smells.json # high-complexity callback/closure smells (T1a/T1b/T2 + B flags)
    hotspots.json        # churn × cognitive-complexity × coupling file ranking
  packets/           # candidate packets (*.yaml, schema-validated at emit time)
```

Terminal packets (landed/reverted/skipped/blocked) are never overwritten by `plan` — they are
the engine's memory. Pending packets regenerate freely.

## Profile format (`<repo>/quality/hone.yaml`)

Deep-merged over [profiles/default.yaml](profiles/default.yaml) (maps merge per key; arrays and
scalars replace). A fully specified real profile: [profiles/example-pdpp.yaml](profiles/example-pdpp.yaml).

| key | default | meaning |
|---|---|---|
| `commands.biome` | `npx biome` | how to invoke biome (cwd = `--repo`) |
| `commands.test` / `.typecheck` | `null` | oracle commands, used by `work` (wave 2) |
| `analysis.owned_dirs` | `[]` | dirs to analyze (repo-relative); `[]` = auto-detect top-level dirs containing JS/TS |
| `analysis.scan_depth` | `2` | `find -maxdepth` for the churn-ranking file walk |
| `analysis.exclude_names` | test/spec/d.ts/min.js | filename patterns excluded from the file walk |
| `analysis.cog_threshold` | `5` | functions with biome cognitive complexity > this are "flagged" |
| `analysis.seam_cc` | `12` | cc at/above which a named-fn split is T1-seam (concept work), not T0 |
| `analysis.churn_window` | `6 months ago` | git-log window for churn |
| `markers.security` | generic list | body substrings that force the strongest proof class (T2-property) |
| `markers.storage` | generic list | informational; raises silent-wrongness cost |
| `markers.public_contract` | generic list | caller-visible contract nouns; >=2 distinct hits flags a callback for B (product-surface) |
| `markers.nogo_path_pattern` | auth\|token\|… | path regex: essential security complexity — ranked + flagged, never auto-targeted |
| `classification.behavior_status_default` | `likely_intended` | packet default |
| `classification.public_surface_globs` | `[]` | files matching → `behavior_status: contract` |
| `classification.generated_globs` | `**/*.generated.*`, `**/generated/**` | ownership GENERATED — excluded from packets |
| `classification.freeze_globs` | `[]` | ownership FREEZE — excluded from packets |
| `policy.autonomy` | `autonomous_branch` | owner policy knob (SPEC §owner interface) |

YAML support is a deliberate stdlib-only subset (block maps/lists, inline arrays, quoted
scalars, comments — **no** anchors, multi-line block scalars, or inline maps); `lib/yaml.mjs`
crashes loudly on anything outside it, and every emitted packet is round-trip-verified.

## Collectors (ported, provenance)

The analysis logic is ported from the proven PDPP code-quality instruments
(`pdpp-cq-sweep/reference-implementation/scripts/code-quality/`) — validated over a
falsification experiment + ~45 gated refactors. Ported means: same AST/scope analysis
(TypeScript compiler API — true free-variable captures, not regex), same router-v1 tier
predicate, same ranking formulas; every PDPP-ism replaced by profile config, and the
four separate biome invocations de-duplicated into one shared flagged-universe run.

| collector | ported from | emits |
|---|---|---|
| `collectors/ast-scope.mjs` | `ast-scope.mjs` | (library) per-function AST models: real captures, callback anchors, awaits |
| `collectors/router.mjs` | `falsify.mjs`/`tier-mass-report.mjs` router v1 | (library) deterministic tier routing; ambiguity always escalates |
| `collectors/tier-mass.mjs` | `tier-mass-report.mjs` | flagged universe + tier mass by file/subsystem + top attention candidates |
| `collectors/callback-smells.mjs` | `smell-callbacks.mjs` | capturing-callback smells with T1a/T1b/T2 class + B (public-heavy) flag |
| `collectors/hotspots.mjs` | `discover.mjs` | churn × cognitive-load × coupling file ranking (size never gates) |
| `collectors/scope-fn.mjs` | `scope-fn.mjs` | (wave-2 recon) ground-truth cc/callers/red-scan for one `file::fn` |

`falsify.mjs`'s stratified sampling half is an experiment harness, not an engine part; only its
router was ported.

## Plan (deterministic v1)

`plan` groups the flagged universe by (file × tier), ranks groups with the SPEC formula
(`gain × attention × product_impact × confidence / (risk × evidence_cost × token_cost ×
reversibility_cost)`; the numerator uses continuous log2(mass)/log2(churn) proxies so the
enum scores don't saturate and degenerate the plan to only-cheap tiers), and emits the top
`--top` packets (schema v1.1):

- **tier → proof_class/plan/tiers** is a fixed table (see `TIER_EXEC` in `lib/plan.mjs`):
  T0 → certified_transform (cheap maker), T1-extractable-callback → exact_move (cheap),
  T1b/T1-seam → pure_logic (standard), T2-* → effectful/property_at_risk (strong maker),
  0-caller named fns → `delete` packets. `judge_tier` is strong whenever
  `silent_wrongness_cost` is high, independently of the maker.
- **execution_gate:** `owner_ratify` for delete/rent/contract-shaped actions AND for files
  matching the profile's `nogo_path_pattern`; everything else `autonomous`.
- **evidence_required** entries are literal runnable commands built from the profile's
  `commands.test`/`commands.typecheck` plus tier-specific rungs (`git diff -w` body-move check,
  guard-byte-identity grep for T2-property, scope-fn complexity re-measure). No oracle
  configured → an explicit `no-oracle-configured` rung directing `work` to blocked(missing-oracle).
- **behavior_status** defaults from the profile; `public_surface_globs` matches become `contract`.
- **confidence is honest:** `why_this_matters` carries the numeric confidence and the
  `metrics nominate; they never decide` caveat until wave-2 semantic validation.
- **priority is persisted:** each packet carries an optional `priority` block
  (`{score, computed, inputs: {mass, churn}}`) — the plan-time ranking PRIOR, recalibrated by
  cost-ledger actuals over time and never a quality claim. `run` prefers `priority.score` for
  ordering and falls back to a coarse enum-derived rank when the block is absent, so
  hand-authored packets stay executable without it.
- Every packet is schema-validated (`lib/validate-packet.mjs`, strict — unknown keys reject,
  missing execution_gate rejects) and YAML round-trip-verified before it is written; a
  malformed packet crashes the plan. Terminal packets (landed/reverted/skipped/blocked) are
  never overwritten; candidate ids are content-derived and stable across reruns.

## Authoring evidence rungs (packet authors, read this)

Evidence commands run in the maker's DIRTY working tree, before anything is committed.
Every rung must therefore be satisfiable pre-commit:

- **Rungs that compare the working tree against HEAD are invalid as pre-land evidence.**
  `git diff --exit-code`, a `check:generated`-style regenerate-then-compare-to-HEAD script,
  or anything that fails when tracked files differ from HEAD is structurally unwinnable:
  the maker's uncommitted edit is precisely what it flags. (Dogfood packet
  `df-surface-mcp-token-kinds-enum-0001` burned $6.83 and a full revision cycle against
  such a rung.) `work` emits a WARNING when a rung command matches known compare-vs-HEAD
  patterns (`git diff … --exit-code`, `check:generated`) — warning only, behavior unchanged.
- HEAD-comparisons are fine as *baseline preconditions inside a rung* (e.g. the
  red-then-green shape `git diff --quiet -- <file> && mutate && test && restore`), never as
  post-change pass criteria on their own.
- Touchset paths are `--repo`-relative by convention; `work` normalizes entries to
  git-root-relative (an entry that exists under `--repo` resolves there, otherwise it is
  tried relative to the git root), so a touchset may legitimately name files outside the
  `--repo` subtree of a monorepo. All touchset/revert/no-diff checks run against the FULL
  git root — a maker edit anywhere in the repository is seen.
