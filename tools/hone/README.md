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
reversibility_cost)`) using mapped heuristic scores, and emits the top `--top` packets:

- **tier → action/evidence/model_tier** is a fixed table (see `lib/plan.mjs`): T0 →
  certified-local-transform (cheap), T1-extractable-callback → exact-move (cheap),
  T1b/T1-seam → differential-probes + judge (standard), T2-* → integration/property + judge
  (strong), 0-caller named fns → `delete` packets (owner-ratified, never auto-landed in v1).
- **behavior_status** defaults from the profile; `public_surface_globs` matches become `contract`.
- **confidence is honest:** `why_this_matters` carries the numeric confidence and the
  `metrics nominate; they never decide` caveat until wave-2 semantic validation.
- Every packet is schema-validated (`lib/validate-packet.mjs`, strict — unknown keys reject)
  and YAML round-trip-verified before it is written; a malformed packet crashes the plan.
