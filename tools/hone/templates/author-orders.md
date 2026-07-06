# Hone Order Authoring Brief

You are authoring candidate packets for the hone repo-quality engine. Return ONLY a fenced JSON object:

```json
{"orders": [/* candidate-packet objects */]}
```

Repo root: `{{REPO_ROOT}}`
Target dirs: `{{TARGET_DIRS}}`
Max orders: `{{MAX_ORDERS}}`
Requested class: `{{CLASS_FILTER}}` (`tst` = test/evidence orders, `pl` = production-logic refactors, `both` = either)

## Hard Requirements

- Every order must match the candidate packet schema exactly. No unknown fields.
- Use `routing_class` only when pinning routing; never pin a model name.
- Use disjoint `touchset` values across orders unless a dependency makes overlap unavoidable.
- Touchset paths are repo-relative. Commands must be portable across worktrees.
- Evidence commands should use `$REPO_ROOT`, `$GIT_ROOT`, or `$HONE_ROOT`; do not bake the authoring checkout path into a command.
- Use the ephemeral DB pattern for DB-backed tests: create a unique DB, run with the unique URL, capture rc, drop DB, then `exit $rc`.
- Capture rc before cleanup/restores: `rc=0; <check> || rc=1; <restore>; exit $rc`.
- Do not use post-change rungs that compare the dirty working tree against HEAD as the only pass condition.
- Security strings are hard-skip / owner-ratify territory: auth, token, password, secret, permission, authorize, authenticate, session, grant, scope, csrf, pkce.
- Fresh measurements below are the only metrics you may cite. Metrics nominate; they do not decide.
- For two-phase mutation ladders, include both the red-seed proof and the green-restored proof, with machine-checkable `expect_check` where possible.
- CRITICAL — extraction seam-pins MUST be GREEN on the UNCHANGED tree (the engine refuses any packet whose baseline rungs are red before work). A seam-pin that greps for the POST-extraction helper name (e.g. `function classifyFoo`) is red at baseline by construction — this is the #1 authoring defect. Instead: (a) pin only markers that EXIST in the current code and must be PRESERVED (the lock/read/branch tokens that stay behind after extraction), OR (b) make the pin two-phase and TOLERANT: `if grep-for-new-helper succeeds -> assert every preserved marker still present (POST); else -> assert the preserved markers present in their current inline location and print BASELINE-OK exit 0`. Never require the post-change symbol to exist at baseline. Verify mentally: would this rung pass `node -e` against the code AS IT IS NOW? If not, it is malformed.
- For review-free `exact_move` or `type_only` packets, include a deterministic equivalence rung named `byte-identity`, `ast-equivalence`, or `certified-equivalence`, or set `certified_equivalence_rung` to the exact rung name.
- ALWAYS set `execution_gate: autonomous`. The safe-pool classes you author here (behavior-preserving extractions, mutation-test coverage, certified moves) run unattended through the deterministic gates + cross-provider judge — that is the point. `owner_ratify` is reserved for judgment-tail campaigns (auth/grant/token/consent/scope-enforcement/boot/storage-unification), which you HARD-SKIP and never author. If an order would touch those, omit it entirely rather than gate it.
- If a good packet cannot be made self-sufficient from these measurements, omit it.

## Packet Shaping

- `proof_class` decides the evidence ladder. Use `exact_move` only for literal moves/hoists proven by equivalence. Use `type_only` only when typecheck is the actual proof. Use `pure_logic` or stronger for semantic refactors.
- `why_this_matters` must name the maintainability or product risk, not just "complexity is high".
- `authoring_evidence` must cite the collector rows used to create the packet.
- `not_allowed` must include at least `behavior-change` and `new-dependency` for preserve-refactor packets.
- `batch_key` shape: `<action>×<proof_class>×<subsystem>`.
- `outcome` must be the pending empty outcome object.

## Fresh Collector Data

```json
{{COLLECTOR_DATA_JSON}}
```
