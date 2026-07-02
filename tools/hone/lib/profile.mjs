// profile.mjs — load the per-repo hone profile and git facts.
//
// The profile is the REPO-INDEPENDENCE seam: every project-specific assumption the original
// PDPP instruments hardcoded (owned dirs, security/storage/public-contract marker lists,
// no-go path pattern, biome invocation, thresholds) lives in <repo>/quality/hone.yaml,
// deep-merged over profiles/default.yaml. See tools/hone/README.md for the format.
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';
import { parseYaml } from './yaml.mjs';
import { deepMerge, makeSh } from './util.mjs';

export const HONE_ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // tools/hone

export function loadProfile(repoRoot) {
  const defPath = join(HONE_ROOT, 'profiles', 'default.yaml');
  const def = parseYaml(readFileSync(defPath, 'utf8'));
  const repoProfilePath = join(repoRoot, 'quality', 'hone.yaml');
  if (existsSync(repoProfilePath)) {
    const over = parseYaml(readFileSync(repoProfilePath, 'utf8'));
    const profile = deepMerge(def, over);
    validateAgendaProjection(profile.agenda, repoProfilePath);
    return { profile, source: repoProfilePath };
  }
  validateAgendaProjection(def.agenda, defPath);
  return { profile: def, source: `${defPath} (no quality/hone.yaml in repo — generic defaults)` };
}

/**
 * `agenda:` is the machine-readable DOCTRINE PROJECTION (AGENDA-DESIGN.md): named_targets are
 * first-class chooser anchors and the report's divergence flags consume named_targets +
 * budget_bands. A malformed projection silently disables those flags, so it fails LOUD here.
 *   agenda.doctrine_path   string|null — the human-fixed doctrine document
 *   agenda.named_targets   [{id, description?, evidence_hint?, keywords?: [..]}]
 *   agenda.budget_bands    { <doctrine-class>: [min%, max%] }
 */
export function validateAgendaProjection(agenda, source) {
  if (agenda == null) return;
  const bad = (msg) => { throw new Error(`${source}: ${msg}`); };
  if (typeof agenda !== 'object' || Array.isArray(agenda)) bad('agenda: must be a map');
  const nt = agenda.named_targets;
  if (nt != null) {
    if (!Array.isArray(nt)) bad('agenda.named_targets: must be a list');
    nt.forEach((t, i) => {
      if (!t || typeof t !== 'object' || Array.isArray(t)) bad(`agenda.named_targets[${i}]: must be a map with an id`);
      if (typeof t.id !== 'string' || !t.id.trim()) bad(`agenda.named_targets[${i}].id: non-empty string required`);
      if (t.keywords != null && (!Array.isArray(t.keywords) || !t.keywords.every((k) => typeof k === 'string'))) {
        bad(`agenda.named_targets[${i}].keywords: must be a list of strings`);
      }
    });
  }
  const bb = agenda.budget_bands;
  if (bb != null) {
    if (typeof bb !== 'object' || Array.isArray(bb)) bad('agenda.budget_bands: must be a map of class → [min%, max%]');
    for (const [cls, band] of Object.entries(bb)) {
      if (!Array.isArray(band) || band.length !== 2 || !band.every((x) => Number.isFinite(x)) || band[0] > band[1]) {
        bad(`agenda.budget_bands.${cls}: must be [min%, max%] with min ≤ max`);
      }
    }
  }
}

export function gitFacts(repoRoot) {
  const run = (cmd) => {
    try { return execSync(cmd, { cwd: repoRoot, encoding: 'utf8' }).trim(); } catch { return null; }
  };
  const gitRoot = run('git rev-parse --show-toplevel') || repoRoot;
  const sha = run('git rev-parse HEAD') || 'no-git';
  const prefix = relative(gitRoot, repoRoot); // '' when repoRoot IS the git toplevel
  return { gitRoot, sha, prefix };
}

/** everything a collector needs about the target repo, resolved once. */
export function buildContext(repoArg) {
  const repoRoot = resolve(repoArg || process.cwd());
  if (!existsSync(repoRoot)) throw new Error(`--repo path does not exist: ${repoRoot}`);
  const { profile, source } = loadProfile(repoRoot);
  const git = gitFacts(repoRoot);
  return { repoRoot, profile, profileSource: source, git, sh: makeSh(repoRoot) };
}
