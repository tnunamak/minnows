// portable.mjs — host-path scrubbing for text bound for COMMITTED quality/ artifacts.
//
// One implementation, applied at the shared writers (writeTerminal, appendClaim, writePacket,
// the agenda doc writer, the revision-note write) so no call site has to remember it.
// Leaf module: imports only util/profile, so ledger.mjs and packet-io.mjs can use it.

import { existsSync, realpathSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { escRe } from './util.mjs';
import { HONE_ROOT } from './profile.mjs';

// absolute path tokens with >= 2 segments (single-segment tokens like `cd /x` fixtures or
// sed deletions are ignored); the lookbehind keeps URL slashes (postgres://…) unmatched
export const ABS_PATH_RE = /(?<=^|[\s"'=(:])\/(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+/g;

const replaceRoots = (text, pairs, boundary = '(?![A-Za-z0-9._-])') => {
  const real = (p) => { try { return realpathSync(p); } catch { return p; } };
  return pairs
    .flatMap(([p, ph]) => [[p, ph], [real(p), ph]])
    .filter(([p]) => p && p !== '/')
    .sort((x, y) => y[0].length - x[0].length)
    .reduce((t, [p, ph]) => t.replace(new RegExp(`${escRe(p)}${boundary}`, 'g'), () => ph), String(text));
};

/**
 * receipts are committed, so host paths become the same placeholders rung shells are given:
 * the repo root -> $REPO_ROOT, the git root -> $GIT_ROOT, the engine install -> $HONE_ROOT.
 * Longest path first (the repo root usually sits inside the git root); realpath forms included.
 */
export function portableReceiptText(text, { repoRoot, gitRoot = repoRoot }) {
  return replaceRoots(text, [[repoRoot, '$REPO_ROOT'], [gitRoot, '$GIT_ROOT'], [HONE_ROOT, '$HONE_ROOT']]);
}

/**
 * free text bound for a committed artifact (blocked_on, headline, claim statement): roots become
 * placeholders, the bare home and temp dirs become $HOME / $TMPDIR, any other absolute path token
 * becomes <foreign-path>. Idempotent: already-portable text is unchanged.
 */
export function portableErrorText(text, ctx) {
  const rooted = portableReceiptText(text, ctx);
  // the bare home/temp dir itself maps to a name (a single-segment dir like /root is invisible to
  // ABS_PATH_RE); anything beneath them is a foreign path and loses its tail below
  const named = replaceRoots(rooted, [[homedir(), '$HOME'], [tmpdir(), '$TMPDIR']], '(?![A-Za-z0-9._/-])');
  return named.replace(ABS_PATH_RE, '<foreign-path>');
}

/** nearest ancestor of `from` holding a .git entry (the git root), else `from` itself. */
function findGitRoot(from) {
  for (let d = resolve(from); ; d = dirname(d)) {
    if (existsSync(join(d, '.git'))) return d;
    if (dirname(d) === d) return resolve(from);
  }
}

/** a string scrubber for committed text, for writers that only know the repo root. */
export function hostScrubber(repoRoot, gitRoot = findGitRoot(repoRoot)) {
  const ctx = { repoRoot, gitRoot };
  return (t) => (typeof t === 'string' ? portableErrorText(t, ctx) : t);
}

/**
 * scrub every string leaf of a JSON-like value (a copy; the input is untouched).
 * `skip(path)` receives the key path and returns true to leave that subtree as authored.
 */
export function scrubDeep(value, scrub, skip = () => false, path = []) {
  if (skip(path)) return value;
  if (typeof value === 'string') return scrub(value);
  if (Array.isArray(value)) return value.map((v, i) => scrubDeep(v, scrub, skip, [...path, i]));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, scrubDeep(v, scrub, skip, [...path, k])]));
  }
  return value;
}
