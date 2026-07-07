// rung-builder.mjs — deterministic evidence-rung command assembly for plan-orders.
//
// The authoring model supplies semantic slots only. These pure builders own the
// brittle shell scaffolding: DB isolation, rc capture, baseline branching,
// timeouts, and portable repo-relative paths.

const PATH_RE = /^[A-Za-z0-9_./-]+$/;
const SLUG_RE = /^[A-Za-z0-9_-]+$/;

function requireString(name, value, re = null) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${name}: non-empty string required`);
  }
  if (value.includes('\n') || value.includes('\r') || value.includes("'")) {
    throw new Error(`${name}: must not contain newlines or single quotes`);
  }
  if (re && !re.test(value)) {
    throw new Error(`${name}: contains unsupported characters`);
  }
  return value;
}

function rungObject({ rung, timeout_s, command, expect, expect_check }) {
  return { rung, timeout_s, command, expect, expect_check };
}

export function dbTestRung({ testFileNoExt, slug, mode }) {
  const f = requireString('testFileNoExt', testFileNoExt, PATH_RE);
  const s = requireString('slug', slug, SLUG_RE);
  if (!['extraction', 'coverage'].includes(mode)) {
    throw new Error(`mode: must be 'extraction' or 'coverage'`);
  }
  if (mode === 'coverage') {
    return rungObject({
      rung: 'direct-test',
      timeout_s: 600,
      command: `cd "$REPO_ROOT" && sh -c 'set -e; f=${f}; if [ ! -f test/$f.test.js ]; then echo "BASELINE: test not yet authored"; exit 0; fi; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_${s}_$f; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 540 node --test --test-force-exit test/$f.test.js || rc=1; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; exit $rc'`,
      expect: 'BASELINE: prints BASELINE line exit 0 (test absent). POST: the authored test passes fully.',
      expect_check: { type: 'stdout_regex', value: 'BASELINE:|# fail 0' },
    });
  }
  return rungObject({
    rung: 'direct-test',
    timeout_s: 600,
    command: `cd "$REPO_ROOT" && sh -c 'set -e; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_${s}; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 540 node --test --test-force-exit test/${f}.test.js || rc=1; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; exit $rc'`,
    expect: 'the existing behavior test passes fully — GREEN before the extraction (baseline) and after (post).',
    expect_check: { type: 'exit_code', value: 0 },
  });
}

export function mutantKillRung({ testFileNoExt, sourceFile, slug, mutationSed }) {
  const f = requireString('testFileNoExt', testFileNoExt, PATH_RE);
  const src = requireString('sourceFile', sourceFile, PATH_RE);
  const s = requireString('slug', slug, SLUG_RE);
  const sed = requireString('mutationSed', mutationSed);
  if (/["`$\\]/.test(sed)) throw new Error('mutationSed: must not contain shell interpolation characters');
  return rungObject({
    rung: 'mutant-kill',
    timeout_s: 600,
    command: `cd "$REPO_ROOT" && sh -c 'set -e; f=${f}; src=${src}; if [ ! -f test/$f.test.js ]; then echo "BASELINE: test not yet authored"; exit 0; fi; export PGHOST=localhost PGPORT=55432 PGUSER=pdpp PGPASSWORD=pdpp; db=pdpp_hone_${s}_mut; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; createdb -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; cp "$src" /tmp/${s}.bak; sed -i "${sed}" "$src"; rc=0; PDPP_TEST_POSTGRES_URL=postgres://pdpp:pdpp@localhost:55432/$db timeout 240 node --test --test-force-exit test/$f.test.js && rc=1; cp /tmp/${s}.bak "$src"; rm -f /tmp/${s}.bak; dropdb --if-exists -h localhost -p 55432 -U pdpp "$db" >/dev/null 2>&1; if [ $rc -eq 0 ]; then echo "MUTANT KILLED"; exit 0; else echo "MUTANT SURVIVED"; exit 1; fi'`,
    expect: 'BASELINE: prints BASELINE exit 0. POST: mutation makes the new test fail -> MUTANT KILLED, source restored.',
    expect_check: { type: 'stdout_regex', value: 'BASELINE:|MUTANT KILLED' },
  });
}

export function seamPinRung({ sourceFile, preservedMarkers, slug }) {
  const src = requireString('sourceFile', sourceFile, PATH_RE);
  requireString('slug', slug, SLUG_RE);
  if (!Array.isArray(preservedMarkers) || preservedMarkers.length === 0) {
    throw new Error('preservedMarkers: non-empty array required');
  }
  const markers = preservedMarkers.map((m, i) => {
    if (typeof m !== 'string' || m.length === 0) throw new Error(`preservedMarkers[${i}]: non-empty string required`);
    return JSON.stringify(m);
  });
  return rungObject({
    rung: 'seam-pin',
    timeout_s: 60,
    command: `cd "$REPO_ROOT" && node -e 'const s=require("fs").readFileSync(${JSON.stringify(src)},"utf8");let rc=0;for(const m of [${markers.join(',')}]){const ok=s.includes(m);console.log((ok?"PASS ":"FAIL ")+m);if(!ok)rc=1;}process.exit(rc);'`,
    expect: 'every PRESERVED marker (tokens that stay after extraction) is present — GREEN before and after.',
    expect_check: { type: 'exit_code', value: 0 },
  });
}

export function buildRung(spec) {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) throw new Error('rung spec must be an object');
  switch (spec.kind) {
    case 'db-test':
      return dbTestRung(spec);
    case 'mutant-kill':
      return mutantKillRung(spec);
    case 'seam-pin':
      return seamPinRung(spec);
    default:
      throw new Error(`kind: must be one of db-test|mutant-kill|seam-pin, got ${JSON.stringify(spec.kind)}`);
  }
}
