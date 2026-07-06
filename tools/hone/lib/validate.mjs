// validate.mjs — `hone validate`: operator-facing wrapper around the executable
// candidate-packet validator. The validator itself stays in validate-packet.mjs; this
// file only resolves packets, supplies the --repo context for repo-aware lints, and
// renders a terse per-packet report plus a summary.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { parseYaml } from './yaml.mjs';
import { validatePacket } from './validate-packet.mjs';
import { loadPacket } from './packet-io.mjs';

function packetDir(repoRoot) {
  return join(repoRoot, 'quality', 'packets');
}

function loadAllPackets(repoRoot) {
  const dir = packetDir(repoRoot);
  if (!existsSync(dir)) throw new Error(`packet directory not found: ${dir}`);
  return readdirSync(dir)
    .filter((f) => f.endsWith('.yaml'))
    .sort()
    .map((f) => {
      const path = join(dir, f);
      const rawText = readFileSync(path, 'utf8');
      try {
        const packet = parseYaml(rawText);
        return { id: packet?.candidate_id || basename(f, '.yaml'), packet, path, rawText, parseError: null };
      } catch (e) {
        return { id: basename(f, '.yaml'), packet: null, path, rawText, parseError: e };
      }
    });
}

function loadOnePacket(repoRoot, id) {
  try {
    const loaded = loadPacket(repoRoot, id);
    return [{ id: loaded.packet?.candidate_id || id, ...loaded, parseError: null }];
  } catch (e) {
    return [{ id, packet: null, path: join(packetDir(repoRoot), `${id}.yaml`), rawText: '', parseError: e }];
  }
}

export function executeValidate({ id = null, all = false, repoRoot }) {
  const packets = all ? loadAllPackets(repoRoot) : loadOnePacket(repoRoot, id);
  const results = packets.map((item) => {
    const warnings = [];
    const errors = item.parseError
      ? [`could not load packet: ${item.parseError.message}`]
      : validatePacket(item.packet, { repoDir: repoRoot, warn: (m) => warnings.push(m) });
    return { id: item.id, path: item.path, errors, warnings };
  });
  const valid = results.filter((r) => r.errors.length === 0).length;
  const invalid = results.length - valid;
  const warnings = results.reduce((n, r) => n + r.warnings.length, 0);
  return {
    exitCode: invalid ? 1 : 0,
    results,
    summary: `hone validate — ${results.length} packet(s): ${valid} valid, ${invalid} invalid, ${warnings} warning(s)`,
  };
}

export async function runValidate(flags) {
  const id = flags._?.[0];
  const all = flags.all === true || flags.all === 'true';
  if ((all && id) || (!all && (!id || typeof id !== 'string'))) {
    throw new Error('usage: hone validate <packet-id>|--all --repo PATH');
  }
  const repoRoot = resolve(flags.repo || '.');
  if (!existsSync(repoRoot)) throw new Error(`--repo path does not exist: ${repoRoot}`);
  const res = executeValidate({ id, all, repoRoot });
  for (const r of res.results) {
    process.stdout.write(`${r.errors.length ? 'FAIL' : 'PASS'} ${r.id}  ${r.path}\n`);
    for (const w of r.warnings) process.stdout.write(`  warning: ${w}\n`);
    for (const e of r.errors) process.stdout.write(`  - ${e}\n`);
  }
  process.stdout.write(`${res.summary}\n`);
  process.exitCode = res.exitCode;
}
