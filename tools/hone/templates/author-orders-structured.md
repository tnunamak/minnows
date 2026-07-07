# Hone Structured Order Authoring Brief

You are authoring candidate packets for the hone repo-quality engine. Return ONLY a fenced JSON object:

```json
{"orders": [/* structured order objects */]}
```

Repo root: `{{REPO_ROOT}}`
Target dirs: `{{TARGET_DIRS}}`
Max orders: `{{MAX_ORDERS}}`
Requested class: `{{CLASS_FILTER}}` (`tst` = test/evidence orders, `pl` = production-logic refactors, `both` = either)

## Hard Requirements

- Return structured JSON only. Do NOT return candidate-packet YAML.
- Do NOT author shell commands or command-shaped fields anywhere.
- Top-level order fields are ONLY: `candidate_id`, `action`, `proof_class`, `routing_class`, `touchset`, `why`, `rungs`.
- `routing_class` is optional; if used, pin a routing class, never a model name.
- `touchset` paths are repo-relative and disjoint across orders unless a dependency makes overlap unavoidable.
- `why` must name the maintainability or product risk, not just "complexity is high".
- ALWAYS target `execution_gate: autonomous` work. Security strings are hard-skip / owner-ratify territory: auth, token, password, secret, permission, authorize, authenticate, session, grant, scope, csrf, pkce.
- Fresh measurements below are the only metrics you may cite. Metrics nominate; they do not decide.
- If a good packet cannot be expressed with the rung semantic fields below, omit it.

## Rungs

`rungs` is an array of semantic rung objects. These are the ONLY supported rung kinds:

```json
{"kind":"db-test","testFileNoExt":"<TESTFILE-NO-EXT>","slug":"<SLUG>","mode":"extraction"}
{"kind":"db-test","testFileNoExt":"<TESTFILE-NO-EXT>","slug":"<SLUG>","mode":"coverage"}
{"kind":"mutant-kill","testFileNoExt":"<TESTFILE-NO-EXT>","sourceFile":"<SOURCEFILE>","slug":"<SLUG>","mutationSed":"<MUTATION-SED>"}
{"kind":"seam-pin","sourceFile":"<SOURCEFILE>","preservedMarkers":["<MARKER-THAT-EXISTS-NOW>"],"slug":"<SLUG>"}
```

Forbidden inside every rung: `rung`, `command`, `timeout_s`, `expect`, `expect_check`.
The engine materializes those deterministically with `lib/rung-builder.mjs`.

Rules for choosing rungs:
- `db-test` `mode:"extraction"` is for preserve-refactor orders that reuse an EXISTING behavior test. It is unconditional.
- `db-test` `mode:"coverage"` is for generate-evidence orders that author a NEW test. It is baseline-branching because the test file is absent at baseline.
- `mutant-kill` is for coverage orders. It branches at baseline, seeds the mutation only after the new test exists, restores the source, and expects `BASELINE:` or `MUTANT KILLED`.
- `seam-pin` is for extraction orders. `preservedMarkers` must be literal strings that exist in the current `sourceFile` and must survive the refactor. Never use a post-extraction helper name.

## Packet Shaping

- `action` must be a candidate-packet action enum, usually `preserve_refactor` or `generate_evidence`.
- `proof_class` must be a candidate-packet proof enum, usually `pure_logic` for extraction or `effectful` / `property_at_risk` for test evidence.
- Use a stable `candidate_id` with a meaningful subsystem slug.
- Use a short shell-safe `slug` in rungs: lowercase letters, digits, `_`, or `-`.
- `<TESTFILE-NO-EXT>` is relative to `test/` and omits `.test.js`.
- `<SOURCEFILE>` is repo-relative.
- `<MUTATION-SED>` flips one real behavioral character and must not contain quotes, backticks, dollars, or backslashes.

## Fresh Collector Data

```json
{{COLLECTOR_DATA_JSON}}
```
