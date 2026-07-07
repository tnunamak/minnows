// validate-packet.mjs — STRICT candidate-packet validator (schema v1.1).
//
// Hand-mirrors schemas/candidate-packet.yaml (the schema file is documentation-YAML, not
// machine-readable JSON Schema — this module IS the executable form; keep the two in lockstep).
// Strict by design: unknown keys reject, enums reject, missing execution_gate rejects
// (fail-closed — SPEC acceptance test #4: malformed packets crash loudly, they never land
// half-valid in the packet stream).
//
// Engine-iteration-3 lints (run-1 defect classes):
// - generate_evidence guard lint (structural, always on): an evidence command that
//   references a touchset file with no existence guard executes a file the packet has
//   not created yet — GREEN BASELINE goes red and the packet blocks for $0.
// - touchset path lint (best-effort, only with ctx.repoDir — validation often runs
//   where --repo is unknown): duplicated repo-dir prefixes and paths resolving nowhere
//   are caught BEFORE they corrupt touchset enforcement on a land.
//
// Engine-iteration-4 lint (run-2/3 defect class, only with ctx.repoDir):
// - shared-DB rung lint: `node --test` pointed at the SHARED Postgres DB
//   (PDPP_TEST_POSTGRES_URL=postgres://…/pdpp) rejects — dirty shared state reddens
//   unrelated tests and concurrent schema bootstrap races block packets; the repaired
//   spine-0003 rung (per-file ephemeral DB + --test-force-exit) is the exemplar.
//   The --test-force-exit half is best-effort: WARN (via ctx.warn), never reject.
//
// Engine-iteration-5 lint (run-3/5 defect class, only with ctx.repoDir):
// - restore-masks-rc lint: a mutate→test→restore rung ending `; git checkout -- <file>`
//   records the CHECKOUT's exit code, never the test's — the rung can never fail by exit
//   code and its PASS column lies. WARN with the canonical rc-capture shape.
//
// ctx naming (engine-iteration-4 fix): the context key is `repoDir` — it is the --repo
// directory, NOT necessarily the git root (the old name `repoRoot` was misread twice as
// git-root). `repoRoot` is still accepted as a deprecated alias, with a warning.

import { existsSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, resolve } from 'node:path';

const ENUMS = {
  behavior_status: ['contract', 'likely_intended', 'provisional', 'accidental', 'unknown'],
  ownership: ['OWN', 'RENT', 'DELETE', 'FREEZE', 'QUARANTINE', 'GENERATED', 'TEMPORARY'],
  action: ['preserve_refactor', 'idealize_rewrite', 'surface_repair', 'generate_evidence',
    'delete', 'rent', 'freeze', 'quarantine', 'document', 'propose_contract_change'],
  proof_class: ['type_only', 'exact_move', 'certified_transform', 'pure_logic', 'effectful',
    'property_at_risk', 'judgment_first', 'liveness_roots', 'proposal'],
  execution_gate: ['autonomous', 'owner_ratify'],
  expected_quality_gain: ['low', 'medium', 'high'],
  owner_attention_reduction: ['low', 'medium', 'high'],
  product_impact: ['none', 'low', 'medium', 'high'],
  maker_tier: ['cheap', 'standard', 'strong'],
  judge_tier: ['standard', 'strong'],
  status: ['pending', 'in_progress', 'landed', 'reverted', 'skipped', 'blocked'],
  review_status: ['pending', 'reviewed-pass', 'reviewed-revise', 'reviewed-reject', 'certified'],
};

const TOP_KEYS = [
  'candidate_id', 'created', 'repo_sha', 'subsystem', 'files', 'symbols', 'public_surface',
  'behavior_status', 'ownership', 'action', 'proof_class', 'execution_gate',
  'why_this_matters', 'plan', 'expected_quality_gain', 'owner_attention_reduction', 'product_impact',
  'risk', 'authoring_evidence', 'evidence_required', 'not_allowed',
  'maker_tier', 'judge_tier', 'maker_provider', 'judge_provider', 'batch_key', 'touchset',
  'estimates', 'depends_on', 'unlocks', 'status', 'outcome',
];

// OPTIONAL top-level keys: may be absent (hand-authored packets), strict when present.
const OPTIONAL_KEYS = ['priority', 'resets', 'routing_class', 'rung_timeout_s', 'certified_equivalence_rung'];

// routing.json class names (kept in lockstep with lib/routing.mjs ROUTING_CLASSES —
// no import so bare packet validation never depends on the routing table file).
const ROUTING_CLASS_NAMES = ['certified-mechanical', 'extraction', 'async-order-oracle', 'hard-ambiguous'];
// model-name shapes a packet author might try to pin — tier choice stays OUT of maker hands
const MODEL_PIN_RE = /^(haiku|sonnet|opus|fable|gpt-|o[0-9]|claude-|codex)/i;

// machine-checkable half of an evidence rung (optional, additive; prose `expect` stays
// for humans/judges). `hone work` enforces these deterministically, fail-closed.
const EXPECT_CHECK_TYPES = ['exit_code', 'stdout_includes', 'stdout_regex', 'scope_fn_lt', 'file_excess_lt', 'failing_test_named'];

const isStr = (v) => typeof v === 'string';
const isNonEmptyStr = (v) => isStr(v) && v.trim().length > 0;
const isStrOrNull = (v) => v === null || isStr(v);
const isInt = (v) => Number.isInteger(v);
const isMap = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const validTimeoutS = (v) => isInt(v) && v > 0;

// an existence guard / explicit fallback that makes a rung green while the file the
// packet will CREATE is still absent: `[ -f`/`[[ -f`/`test -f` (or -e), or a `||` fallback.
const EXISTENCE_GUARD_RE = /\[{1,2}\s+-[fe]\s|\btest\s+-[fe]\s|\|\|/;

/** nearest ancestor of `start` containing .git, or null (no git spawn — validator stays cheap). */
function findGitRoot(start) {
  let dir = start;
  for (;;) {
    if (existsSync(join(dir, '.git'))) return dir;
    const up = dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

/**
 * @param p the packet
 * @param ctx optional context: {repoDir} (the --repo directory) enables the touchset
 *   path lint + shared-DB rung lint (plan/work pass it; bare validation stays
 *   structural — best-effort by design). {repoRoot} is a deprecated alias for repoDir
 *   (it never meant the git root); accepted with a warning. {warn} is an optional
 *   callback for non-fatal lints; defaults to stderr.
 * @returns string[] — empty when valid.
 */
export function validatePacket(p, ctx = {}) {
  const errs = [];
  const err = (m) => errs.push(m);
  const warn = typeof ctx?.warn === 'function' ? ctx.warn : (m) => process.stderr.write(`hone validate WARNING: ${m}\n`);
  let repoDir = isNonEmptyStr(ctx?.repoDir) ? ctx.repoDir : null;
  if (!repoDir && isNonEmptyStr(ctx?.repoRoot)) {
    repoDir = ctx.repoRoot;
    warn("ctx.repoRoot is deprecated — it means the --repo dir, not the git root (misread twice); pass ctx.repoDir");
  }
  if (!isMap(p)) return ['packet is not a map'];

  for (const k of Object.keys(p)) if (!TOP_KEYS.includes(k) && !OPTIONAL_KEYS.includes(k)) err(`unknown top-level key: ${k}`);
  for (const k of TOP_KEYS) if (!(k in p)) err(`missing required key: ${k}`);
  if (errs.length) return errs; // shape is wrong — field checks below would just cascade

  if (!isNonEmptyStr(p.candidate_id)) err('candidate_id: non-empty string required');
  if (!isStr(p.created) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(p.created)) err(`created: iso-timestamp required, got ${JSON.stringify(p.created)}`);
  if (!isNonEmptyStr(p.repo_sha)) err('repo_sha: non-empty string required');
  if (!isNonEmptyStr(p.subsystem)) err('subsystem: non-empty string required');

  if (!Array.isArray(p.files) || !p.files.length || !p.files.every(isNonEmptyStr)) err('files: non-empty [string] required');
  if (!Array.isArray(p.symbols)) err('symbols: array required');
  else for (const [i, s] of p.symbols.entries()) {
    if (isNonEmptyStr(s)) continue;
    if (isMap(s)) {
      const keys = Object.keys(s).sort().join(',');
      if (keys !== 'anchor,file,line,parent_fn') { err(`symbols[${i}]: anchor map must have exactly {file, parent_fn, anchor, line}, got {${keys}}`); continue; }
      if (!isNonEmptyStr(s.file) || !isNonEmptyStr(s.parent_fn) || !isNonEmptyStr(s.anchor) || !isInt(s.line)) {
        err(`symbols[${i}]: anchor map needs string file/parent_fn/anchor + int line`);
      }
    } else err(`symbols[${i}]: string or {file, parent_fn, anchor, line} required`);
  }
  if (!Array.isArray(p.public_surface) || !p.public_surface.every(isNonEmptyStr)) err('public_surface: [string] required');

  for (const [k, allowed] of Object.entries(ENUMS)) {
    if (k in p && !allowed.includes(p[k])) err(`${k}: must be one of [${allowed.join(' | ')}], got ${JSON.stringify(p[k])}`);
  }

  if (!isNonEmptyStr(p.why_this_matters)) err('why_this_matters: non-empty string required');

  if (!isMap(p.plan)) err('plan: map {transform_class, instruction} required');
  else {
    for (const k of Object.keys(p.plan)) if (!['transform_class', 'instruction'].includes(k)) err(`plan: unknown key ${k}`);
    if (!isNonEmptyStr(p.plan.transform_class)) err('plan.transform_class: non-empty string required');
    if (!isNonEmptyStr(p.plan.instruction)) err('plan.instruction: non-empty string required');
  }

  if ('routing_class' in p) { // optional L1 routing pin: a CLASS, never a model
    if (!isNonEmptyStr(p.routing_class) || !ROUTING_CLASS_NAMES.includes(p.routing_class)) {
      const looksLikeModel = isStr(p.routing_class) && MODEL_PIN_RE.test(p.routing_class.trim());
      err(`routing_class: must be one of [${ROUTING_CLASS_NAMES.join(' | ')}], got ${JSON.stringify(p.routing_class)}` +
        (looksLikeModel ? ' — packets pin a CLASS, never a specific model (tier choice stays out of maker hands; routing.json owns the class→tier table)' : ''));
    }
  }
  if ('rung_timeout_s' in p && !validTimeoutS(p.rung_timeout_s)) {
    err('rung_timeout_s: positive integer seconds required when present');
  }
  if ('certified_equivalence_rung' in p && !isNonEmptyStr(p.certified_equivalence_rung)) {
    err('certified_equivalence_rung: non-empty rung name required when present');
  }

  if ('priority' in p) { // optional ranking PRIOR (ordering only, never a quality claim)
    if (!isMap(p.priority)) err('priority: map {score, computed, inputs} required when present');
    else {
      for (const k of Object.keys(p.priority)) if (!['score', 'computed', 'inputs'].includes(k)) err(`priority: unknown key ${k}`);
      if (typeof p.priority.score !== 'number' || !Number.isFinite(p.priority.score)) err('priority.score: finite number required');
      if (!isStr(p.priority.computed) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(p.priority.computed)) err(`priority.computed: iso-timestamp required, got ${JSON.stringify(p.priority.computed)}`);
      if (!isMap(p.priority.inputs)) err('priority.inputs: map {mass, churn} required');
      else {
        for (const k of Object.keys(p.priority.inputs)) if (!['mass', 'churn'].includes(k)) err(`priority.inputs: unknown key ${k}`);
        if (!isInt(p.priority.inputs.mass)) err('priority.inputs.mass: int required');
        if (!isInt(p.priority.inputs.churn)) err('priority.inputs.churn: int required');
      }
    }
  }

  if ('resets' in p) { // optional (added engine-iteration-1, additive) — owner reopenings of a terminal packet
    if (!Array.isArray(p.resets)) err('resets: array required when present');
    else for (const [i, r] of p.resets.entries()) {
      if (!isMap(r) || Object.keys(r).sort().join(',') !== 'at,from_status,reason') {
        err(`resets[${i}]: map with exactly {at, from_status, reason} required`);
        continue;
      }
      if (!isStr(r.at) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(r.at)) err(`resets[${i}].at: iso-timestamp required`);
      if (!ENUMS.status.includes(r.from_status)) err(`resets[${i}].from_status: must be one of [${ENUMS.status.join(' | ')}], got ${JSON.stringify(r.from_status)}`);
      if (!isNonEmptyStr(r.reason)) err(`resets[${i}].reason: non-empty string required`);
    }
  }

  if (!isMap(p.risk)) err('risk: map required');
  else {
    const RISK = {
      blast_radius: ['local', 'subsystem', 'cross-cutting'],
      reversibility: ['trivial', 'branch-revert', 'hard'],
      silent_wrongness_cost: ['low', 'medium', 'high'],
    };
    for (const k of Object.keys(p.risk)) if (!(k in RISK) && k !== 'property_at_risk') err(`risk: unknown key ${k}`);
    for (const [k, allowed] of Object.entries(RISK)) {
      if (!allowed.includes(p.risk[k])) err(`risk.${k}: must be one of [${allowed.join(' | ')}], got ${JSON.stringify(p.risk[k])}`);
    }
    if (!('property_at_risk' in p.risk) || !isStrOrNull(p.risk.property_at_risk)) err('risk.property_at_risk: string|null required');
  }

  if (!Array.isArray(p.authoring_evidence)) err('authoring_evidence: array required');
  else for (const [i, a] of p.authoring_evidence.entries()) {
    if (!isMap(a) || Object.keys(a).sort().join(',') !== 'detail,kind,result' ||
      !isNonEmptyStr(a.kind) || !isNonEmptyStr(a.detail) || !isNonEmptyStr(a.result)) {
      err(`authoring_evidence[${i}]: {kind, detail, result} (all non-empty strings) required`);
    }
  }

  if (!Array.isArray(p.evidence_required) || !p.evidence_required.length) err('evidence_required: non-empty array required');
  else for (const [i, e] of p.evidence_required.entries()) {
    const keys = isMap(e) ? Object.keys(e).sort().join(',') : '';
    const allowed = ['command', 'expect', 'expect_check', 'rung', 'timeout_s'];
    if (!isMap(e) || Object.keys(e).some((k) => !allowed.includes(k)) ||
      !isNonEmptyStr(e.rung) || !isNonEmptyStr(e.command) || !isNonEmptyStr(e.expect)) {
      err(`evidence_required[${i}]: {rung, command, expect} (all non-empty strings — LITERAL runnable command, not prose) + optional expect_check required`);
      continue;
    }
    if ('timeout_s' in e && !validTimeoutS(e.timeout_s)) {
      err(`evidence_required[${i}].timeout_s: positive integer seconds required when present`);
    }
    if ('expect_check' in e) {
      const c = e.expect_check;
      if (!isMap(c) || Object.keys(c).sort().join(',') !== 'type,value') {
        err(`evidence_required[${i}].expect_check: map with exactly {type, value} required`);
      } else if (!EXPECT_CHECK_TYPES.includes(c.type)) {
        err(`evidence_required[${i}].expect_check.type: must be one of [${EXPECT_CHECK_TYPES.join(' | ')}], got ${JSON.stringify(c.type)}`);
      } else if (c.type === 'exit_code' && !isInt(c.value)) {
        err(`evidence_required[${i}].expect_check.value: int required for exit_code`);
      } else if ((c.type === 'scope_fn_lt' || c.type === 'file_excess_lt') && !isInt(c.value)) {
        err(`evidence_required[${i}].expect_check.value: int required for ${c.type}`);
      } else if ((c.type === 'stdout_includes' || c.type === 'stdout_regex' || c.type === 'failing_test_named') && !isNonEmptyStr(c.value)) {
        err(`evidence_required[${i}].expect_check.value: non-empty string required for ${c.type}`);
      } else if (c.type === 'stdout_regex') {
        try { new RegExp(c.value); } catch (ex) { err(`evidence_required[${i}].expect_check.value: invalid regex (${ex.message})`); }
      }
    }
    // ---- portability lint (live pilot finding, warn-only) ----
    // Absolute paths bake the AUTHORING worktree into the rung: executed on a different
    // --repo, the gate measures the wrong tree (two vacuously-green lands shipped this
    // way before the runtime rewrite closed it). The engine rewrites/refuses at
    // execution time; this lint pushes authors to the portable form up front. System
    // prefixes are exempt; only >=2-segment paths flag (single-segment `/x` fixtures
    // and sed/regex literals stay quiet).
    const absTokens = (e.command.match(/(?<=^|[\s"'=(:])\/(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+/g) ?? [])
      .filter((t) => !['/dev/', '/tmp/', '/proc/', '/sys/', '/usr/', '/bin/', '/sbin/', '/lib/', '/lib64/', '/opt/', '/etc/', '/var/'].some((a) => t.startsWith(a)));
    if (absTokens.length) {
      warn(`evidence_required[${i}] (${e.rung}): absolute path(s) in the rung command (${absTokens.slice(0, 3).join(', ')}) — authored-worktree paths break portability across worktrees (the engine rewrites or fail-closed-refuses them at run time). Author portably instead: paths relative to the repo root, or $REPO_ROOT / $GIT_ROOT / $HONE_ROOT (exported to every rung shell)`);
    }
  }
  if (isNonEmptyStr(p.certified_equivalence_rung) && Array.isArray(p.evidence_required)) {
    const names = p.evidence_required
      .filter((e) => isMap(e) && isNonEmptyStr(e.rung))
      .map((e) => e.rung);
    if (!names.includes(p.certified_equivalence_rung)) {
      err(`certified_equivalence_rung: named rung '${p.certified_equivalence_rung}' does not exist in evidence_required`);
    }
  }
  if (!Array.isArray(p.not_allowed) || !p.not_allowed.every(isNonEmptyStr)) err('not_allowed: [string] required');

  if (!isStrOrNull(p.maker_provider)) err('maker_provider: string|null required');
  if (!isStrOrNull(p.judge_provider)) err('judge_provider: string|null required');
  if (p.maker_provider !== null && p.judge_provider !== null && p.maker_provider === p.judge_provider) {
    err(`maker_provider and judge_provider MUST differ (maker ≠ judge is structural), both are ${JSON.stringify(p.maker_provider)}`);
  }
  if (!isNonEmptyStr(p.batch_key)) err('batch_key: non-empty string required');
  if (!Array.isArray(p.touchset) || !p.touchset.length || !p.touchset.every(isNonEmptyStr)) err('touchset: non-empty [string] required');

  // ---- generate_evidence guard lint (run-1 $0-block class) ----
  // A generate_evidence packet's touchset IS the oracle file(s) it will create. A rung
  // command that references one of them WITHOUT an existence guard executes a file that
  // does not exist at GREEN BASELINE → red baseline → blocked for $0 before any work
  // (run 1 blocked t1b-streaming-input-telemetry-evidence-0012,
  // rt-verdict-stream-rollups-oracle-0004 and df-evidence-package-schema-merge-oracle-0004
  // exactly this way). The hm- packets show the correct pattern:
  //   sh -c 'if [ -f <new-file> ]; then <cmd with new file>; else <fallback>; fi'
  if (p.action === 'generate_evidence' && Array.isArray(p.evidence_required) && Array.isArray(p.touchset)) {
    for (const [i, e] of p.evidence_required.entries()) {
      if (!isMap(e) || !isNonEmptyStr(e.command) || EXISTENCE_GUARD_RE.test(e.command)) continue;
      const hits = p.touchset.filter((t) => isNonEmptyStr(t) &&
        (e.command.includes(t) || e.command.includes(basename(t))));
      if (hits.length) {
        err(`evidence_required[${i}] (rung '${e.rung}'): command references touchset file(s) [${hits.join(', ')}] with no existence guard — generate_evidence packets CREATE their touchset files, so this rung executes a not-yet-existing file at GREEN BASELINE and blocks for $0 (the run-1 red-baseline class). Guard it like the hm- packets: sh -c 'if [ -f <file> ]; then <command>; else <fallback without the new file>; fi'`);
      }
    }
  }

  // ---- touchset path lint (best-effort; only when ctx.repoDir is known) ----
  // Mirrors work.mjs normalizeTouchEntry: an entry existing under --repo or the git root
  // is fine; a NOT-YET-EXISTING entry that starts with the repo dir's basename is the
  // duplicated-prefix corruption (rt-verdict-stream-rollups-oracle-0004: touchset
  // 'reference-implementation/test/…' while --repo already ends in reference-implementation
  // → work normalizes it to reference-implementation/reference-implementation/… →
  // guaranteed false touchset violation reverting honest maker work).
  if (repoDir && Array.isArray(p.touchset)) {
    const repoAbs = resolve(repoDir);
    const repoBase = basename(repoAbs);
    const gitRoot = findGitRoot(repoAbs);
    for (const [i, t] of p.touchset.entries()) {
      if (!isNonEmptyStr(t)) continue;
      if (isAbsolute(t)) { err(`touchset[${i}]: '${t}' is absolute — entries must be --repo-relative (or git-root-relative)`); continue; }
      if (existsSync(join(repoAbs, t))) continue; // --repo-relative, exists
      if (gitRoot && gitRoot !== repoAbs && existsSync(join(gitRoot, t))) continue; // git-root-relative, exists
      // exists nowhere: either a file the packet will create (fine) or a corrupted path
      if (t.startsWith(repoBase + '/')) {
        err(`touchset[${i}]: '${t}' carries a duplicated repo-dir prefix ('${repoBase}/') and exists at neither --repo nor the git root — hone work would normalize it to '${repoBase}/${t}' and revert honest maker work as a false touchset violation. Corrected path: '${t.slice(repoBase.length + 1)}'`);
        continue;
      }
      const parent = dirname(t);
      if (parent !== '.' && !existsSync(join(repoAbs, parent)) && !(gitRoot && existsSync(join(gitRoot, parent)))) {
        err(`touchset[${i}]: '${t}' resolves under neither --repo (${repoAbs}) nor the git root${gitRoot ? ` (${gitRoot})` : ''} — not even its parent directory exists; fix the path before this packet is worked`);
      }
    }
  }

  // ---- shared-DB rung lint (engine-iteration-4; only when ctx.repoDir is known) ----
  // A `node --test` rung pointed at the SHARED Postgres DB (…/pdpp) is the run-2/3
  // hang-and-redden class: dirty shared state fails unrelated tests (t1b-0012's setup
  // 409s) and concurrent schema bootstrap races block packets. spine-0003's repaired
  // rung is the exemplar: per-file ephemeral DB + --test-force-exit. The force-exit
  // half is best-effort (server-ish tests keep the runner alive — spine-0003's 2700s
  // idle-hang): WARN, never reject.
  if (repoDir && Array.isArray(p.evidence_required)) {
    const SHARED_DB_RE = /PDPP_TEST_POSTGRES_URL=postgres:\/\/[^ ]*\/pdpp\b/;
    const NODE_TEST_RE = /\bnode\s+(?:[^|;&\n]*\s)?--test\b/;
    for (const [i, e] of p.evidence_required.entries()) {
      if (!isMap(e) || !isNonEmptyStr(e.command)) continue;
      const cmd = e.command;
      if (NODE_TEST_RE.test(cmd) && SHARED_DB_RE.test(cmd)) {
        err(`evidence_required[${i}] (rung '${e.rung}'): command points node --test at the SHARED Postgres DB (PDPP_TEST_POSTGRES_URL=…/pdpp) — shared/dirty-DB state reddens unrelated tests and concurrent schema-bootstrap races block packets (the run-2/3 class). Use the spine-0003 repaired pattern (per-file ephemeral DB + --test-force-exit): db=pdpp_hone_<packet>_<file>; dropdb --if-exists $db; createdb $db; PDPP_TEST_POSTGRES_URL=postgres://…/$db node --test --test-force-exit <file>; dropdb --if-exists $db`);
        continue;
      }
      if (NODE_TEST_RE.test(cmd) && /PDPP_TEST_POSTGRES_URL=/.test(cmd) && !/--test-force-exit\b/.test(cmd)) {
        warn(`evidence_required[${i}] (rung '${e.rung}'): DB-backed node --test without --test-force-exit — a server-ish test can keep the runner alive after tests finish (spine-0003's 2700s idle-hang); add --test-force-exit (best-effort lint, not a rejection)`);
      }
    }
  }

  // ---- restore-masks-rc lint (engine-iteration-5; only when ctx.repoDir is known) ----
  // Run-3/5 finding: mutate→test→restore rungs authored as
  //   … && sed -i '<mutation>' <file> && node --test <test>; git checkout -- <file>
  // end with the restore, so the rung's recorded exit code is ALWAYS the checkout's —
  // the seeded test result is invisible to the exit code, mutation rungs can NEVER fail
  // by exit code, and the receipt PASS column lies (judges compensated by reading output).
  // The engine cannot infer the inner rc after the fact, so this is authoring truth:
  // WARN (never reject) with the canonical rc-capture shape —
  //   rc=0; <test cmd> || rc=1; git checkout -- <file>; exit $rc
  // (pair with expect_check {type: exit_code, value: 1} when the seeded run is EXPECTED red).
  if (repoDir && Array.isArray(p.evidence_required)) {
    const TEST_INVOCATION_RE = /\bnode\s+(?:[^|;&\n]*\s)?--test\b|\bnpm\s+(?:run\s+)?test\b|\bnpx\s+(?:vitest|jest|mocha|tap)\b|\byarn\s+test\b/;
    const RC_CAPTURE_RE = /\brc=|\$\?/;
    for (const [i, e] of p.evidence_required.entries()) {
      if (!isMap(e) || !isNonEmptyStr(e.command)) continue;
      const m = e.command.match(/;\s*git checkout\b/);
      if (!m) continue;
      if (TEST_INVOCATION_RE.test(e.command.slice(0, m.index)) && !RC_CAPTURE_RE.test(e.command)) {
        warn(`evidence_required[${i}] (rung '${e.rung}'): test invocation followed by '; git checkout' without rc capture — the rung's exit code is ALWAYS the checkout's, so this mutate→test→restore rung can NEVER fail by exit code and its PASS column lies (the run-5 red-then-green class; judges compensate by reading output, the exit code does not). Capture before restoring: rc=0; <test cmd> || rc=1; git checkout -- <file>; exit $rc — and pair with expect_check {type: exit_code, value: 1} when the seeded run is EXPECTED red (best-effort lint, not a rejection)`);
      }
    }
  }

  if (!isMap(p.estimates)) err('estimates: map {tokens, evidence_cost} required');
  else {
    for (const k of Object.keys(p.estimates)) if (!['tokens', 'evidence_cost'].includes(k)) err(`estimates: unknown key ${k}`);
    if (!isInt(p.estimates.tokens)) err('estimates.tokens: int required');
    if (!['low', 'medium', 'high'].includes(p.estimates.evidence_cost)) err('estimates.evidence_cost: low|medium|high required');
  }
  if (!Array.isArray(p.depends_on) || !p.depends_on.every(isNonEmptyStr)) err('depends_on: [candidate_id] required');
  if (!Array.isArray(p.unlocks) || !p.unlocks.every(isNonEmptyStr)) err('unlocks: [candidate_id] required');

  if (!isMap(p.outcome)) err('outcome: map required');
  else {
    const OUT_KEYS = ['commit', 'skip_reason', 'blocked_on', 'judge_verdict', 'evidence_receipts', 'tokens_actual', 'lesson', 'review_status'];
    for (const k of Object.keys(p.outcome)) if (!OUT_KEYS.includes(k)) err(`outcome: unknown key ${k}`);
    for (const k of OUT_KEYS.filter((k) => k !== 'review_status')) if (!(k in p.outcome)) err(`outcome: missing key ${k}`);
    for (const k of ['commit', 'skip_reason', 'blocked_on', 'judge_verdict', 'lesson']) {
      if (k in p.outcome && !isStrOrNull(p.outcome[k])) err(`outcome.${k}: string|null required`);
    }
    if ('review_status' in p.outcome && p.outcome.review_status !== null && !ENUMS.review_status.includes(p.outcome.review_status)) {
      err(`outcome.review_status: must be one of [${ENUMS.review_status.join(' | ')}]|null, got ${JSON.stringify(p.outcome.review_status)}`);
    }
    if ('evidence_receipts' in p.outcome && (!Array.isArray(p.outcome.evidence_receipts) || !p.outcome.evidence_receipts.every(isNonEmptyStr))) {
      err('outcome.evidence_receipts: [string] required');
    }
    if ('tokens_actual' in p.outcome && p.outcome.tokens_actual !== null && !isInt(p.outcome.tokens_actual)) err('outcome.tokens_actual: int|null required');
    if (p.status === 'skipped' && !isNonEmptyStr(p.outcome.skip_reason)) err('status=skipped REQUIRES outcome.skip_reason');
    if (p.status === 'blocked' && !isNonEmptyStr(p.outcome.blocked_on)) err('status=blocked REQUIRES outcome.blocked_on');
  }

  return errs;
}

/** crash loudly on a malformed packet (SPEC acceptance test #4). */
export function assertValidPacket(p, context = '', ctx = {}) {
  const errs = validatePacket(p, ctx);
  if (errs.length) {
    throw new Error(`MALFORMED PACKET${context ? ` (${context})` : ''} — refusing to emit:\n  - ${errs.join('\n  - ')}`);
  }
}
