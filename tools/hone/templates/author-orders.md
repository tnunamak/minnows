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
- Authored orders are preflighted for baseline-greenness, DB isolation, and rung hygiene; malformed orders are discarded, never softened or repaired by the engine.
- Use the ephemeral DB pattern for DB-backed tests: create a unique DB, run with the unique URL, capture rc, drop DB, then `exit $rc`.
- Capture rc before cleanup/restores: `rc=0; <check> || rc=1; <restore>; exit $rc`.
- Do not use post-change rungs that compare the dirty working tree against HEAD as the only pass condition.
- Security strings are hard-skip / owner-ratify territory: auth, token, password, secret, permission, authorize, authenticate, session, grant, scope, csrf, pkce.
- Fresh measurements below are the only metrics you may cite. Metrics nominate; they do not decide.
- For two-phase mutation ladders (test-coverage orders): the mutation-kill rung MUST be BASELINE-GREEN. At baseline the new test file does NOT exist yet, so the rung MUST branch: `if [ -f test/<newfile>.test.js ]; then <seed mutation -> run new test EXPECTING FAILURE -> restore -> run EXPECTING PASS -> echo "MUTANT KILLED"; else echo "BASELINE: test not yet authored"; exit 0; fi`. The `expect_check` must be `stdout_regex` value `BASELINE:|MUTANT KILLED` (NOT plain stdout_includes "MUTANT KILLED" — that is red-by-construction at baseline, the #1 test-order defect). Verify mentally: at baseline the test file is absent, so the rung must hit the else-branch and exit 0.
- CRITICAL — extraction seam-pins MUST be GREEN on the UNCHANGED tree (the engine refuses any packet whose baseline rungs are red before work). A seam-pin that greps for the POST-extraction helper name (e.g. `function classifyFoo`) is red at baseline by construction — this is the #1 authoring defect. Instead: (a) pin only markers that EXIST in the current code and must be PRESERVED (the lock/read/branch tokens that stay behind after extraction), OR (b) make the pin two-phase and TOLERANT: `if grep-for-new-helper succeeds -> assert every preserved marker still present (POST); else -> assert the preserved markers present in their current inline location and print BASELINE-OK exit 0`. Never require the post-change symbol to exist at baseline. Verify mentally: would this rung pass `node -e` against the code AS IT IS NOW? If not, it is malformed.
- For review-free `exact_move` or `type_only` packets, include a deterministic equivalence rung named `byte-identity`, `ast-equivalence`, or `certified-equivalence`, or set `certified_equivalence_rung` to the exact rung name.

## CANONICAL RUNG SHAPES — COPY THESE VERBATIM (the preflight rejects anything that deviates)

Every rung MUST have an explicit `timeout_s`. Every `node --test` MUST use a per-file ephemeral DB. Every rung that cleans up after a graded command MUST capture rc first and `exit $rc`.

DB-backed test rung (baseline-green — greps for the test file, runs it isolated only if present):
```yaml
- rung: direct-test
  timeout_s: 600
  command: "cd \"$REPO_ROOT\" && sh -c 'set -e; f=<TESTFILE-NO-EXT>; if [ ! -f test/$f.test.js ]; then echo \"BASELINE: test not yet authored\"; exit 0; fi; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_<SLUG>_$f; dropdb --if-exists -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 540 node --test --test-force-exit test/$f.test.js || rc=1; dropdb --if-exists -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; exit $rc'"
  expect: "BASELINE: prints BASELINE line exit 0 (test absent). POST: the authored test passes fully."
  expect_check: { type: stdout_regex, value: "BASELINE:|# fail 0" }
```

Mutation-kill rung (baseline-green — only seeds+kills once the test exists):
```yaml
- rung: mutant-kill
  timeout_s: 600
  command: "cd \"$REPO_ROOT\" && sh -c 'set -e; f=<TESTFILE-NO-EXT>; src=<SOURCEFILE>; if [ ! -f test/$f.test.js ]; then echo \"BASELINE: test not yet authored\"; exit 0; fi; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_<SLUG>_mut; dropdb --if-exists -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; cp \"$src\" /tmp/<SLUG>.bak; sed -i \"<MUTATION-SED>\" \"$src\"; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 240 node --test --test-force-exit test/$f.test.js && rc=1; cp /tmp/<SLUG>.bak \"$src\"; rm -f /tmp/<SLUG>.bak; dropdb --if-exists -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; if [ $rc -eq 0 ]; then echo \"MUTANT KILLED\"; exit 0; else echo \"MUTANT SURVIVED\"; exit 1; fi'"
  expect: "BASELINE: prints BASELINE exit 0. POST: mutation makes the new test fail -> MUTANT KILLED, source restored."
  expect_check: { type: stdout_regex, value: "BASELINE:|MUTANT KILLED" }
```

Extraction seam-pin (baseline-green — preserve-only markers, tolerant of pre/post):
```yaml
- rung: seam-pin
  timeout_s: 60
  command: "cd \"$REPO_ROOT\" && node -e 'const s=require(\"fs\").readFileSync(\"<SOURCEFILE>\",\"utf8\");let rc=0;for(const m of [<PRESERVED-MARKER-1>,<PRESERVED-MARKER-2>]){const ok=s.includes(m);console.log((ok?\"PASS \":\"FAIL \")+m);if(!ok)rc=1;}process.exit(rc);'"
  expect: "every PRESERVED marker (tokens that stay after extraction) is present — GREEN before and after."
  expect_check: { type: exit_code, value: 0 }
```

EXTRACTION orders (preserve_refactor) do NOT create a new test file — they reuse an EXISTING test as the behavior oracle. Their `direct-test` rung must therefore be UNCONDITIONAL (no `if [ ! -f ... ]` branch, because the test already exists) and green at baseline. Use this shape for an extraction's direct-test rung:
```yaml
- rung: direct-test
  timeout_s: 600
  command: "cd \"$REPO_ROOT\" && sh -c 'set -e; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_<SLUG>; dropdb --if-exists -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 540 node --test --test-force-exit test/<EXISTING-TEST>.test.js || rc=1; dropdb --if-exists -h localhost -p 55432 -U pdpp \"$db\" >/dev/null 2>&1; exit $rc'"
  expect: "the existing behavior test passes fully — GREEN before the extraction (baseline) and after (post)."
  expect_check: { type: exit_code, value: 0 }
```
Only COVERAGE orders (generate_evidence, authoring a NEW test) use the `if [ ! -f ... ]; then echo BASELINE ...` branch — because their test file is absent at baseline. Never put the BASELINE branch on an extraction rung; never omit it on a coverage rung.

Rules for filling these in: `<TESTFILE-NO-EXT>` and `<SOURCEFILE>` are real paths relative to $REPO_ROOT; `<PRESERVED-MARKER-N>` are string literals that EXIST in the current source and MUST survive the change (never the post-extraction helper name); `<MUTATION-SED>` flips one real behavioral character. If you cannot express an order with these shapes, OMIT it — do not invent a rung the preflight will reject.
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
