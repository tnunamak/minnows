// util.mjs — small shared helpers for hone (stdlib-only).
import { execSync } from 'node:child_process';

/** parse argv: supports --k=v, --k v, bare --flag, and positionals in `_`. */
export function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq !== -1) out[a.slice(2, eq)] = a.slice(eq + 1);
      else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) out[a.slice(2)] = argv[++i];
      else out[a.slice(2)] = true;
    } else out._.push(a);
  }
  return out;
}

/** shell runner bound to a default cwd; on failure returns stdout+stderr (ported from the PDPP instruments). */
export function makeSh(defaultCwd) {
  return (cmd, cwd = defaultCwd) => {
    try { return execSync(cmd, { cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }); }
    catch (e) { return (e.stdout || '') + (e.stderr || ''); }
  };
}

/** deterministic djb2 hash → 8-hex string (no Math.random anywhere in hone; reproducibility is a feature). */
export function djb2(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(16).padStart(8, '0');
}

export function slug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'x';
}

export function escRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

/** which of `markers` (literal strings, case-insensitive) appear in `text`. */
export function markerHits(text, markers = []) {
  return (markers || []).filter((mk) => new RegExp(escRe(mk), 'i').test(text));
}

/** minimal glob → RegExp: `**` spans dirs, `*` within a segment, `?` one char. */
export function globToRegExp(glob) {
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') { re += glob[i + 2] === '/' ? '(?:.*/)?' : '.*'; i += glob[i + 2] === '/' ? 2 : 1; }
      else re += '[^/]*';
    } else if (c === '?') re += '[^/]';
    else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp('^' + re + '$');
}

export function matchAnyGlob(file, globs = []) {
  return (globs || []).some((g) => globToRegExp(g).test(file));
}

/** subsystem of a repo-relative file: top dir + one level of subdir (ported from tier-mass-report.mjs). */
export function subsystemOf(file) {
  const parts = file.split('/');
  if (parts.length <= 1) return parts[0] ? '(root)' : '(root)';
  return parts.length >= 3 ? `${parts[0]}/${parts[1]}` : parts[0];
}

export function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (typeof a !== 'object') return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => deepEqual(a[k], b[k]));
}

export function deepMerge(base, over) {
  if (over === undefined) return base;
  if (base === null || over === null || typeof base !== 'object' || typeof over !== 'object' ||
    Array.isArray(base) || Array.isArray(over)) return over;
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = deepMerge(base[k], v);
  return out;
}
