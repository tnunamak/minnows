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
    return { profile: deepMerge(def, over), source: repoProfilePath };
  }
  return { profile: def, source: `${defPath} (no quality/hone.yaml in repo — generic defaults)` };
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
