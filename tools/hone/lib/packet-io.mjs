// packet-io.mjs — load/write one candidate packet, shared by `work` and `reset`.
//
// Both directions are fail-closed: loads resolve by filename OR embedded candidate_id
// (hand-authored packets may not follow the id-as-filename convention); writes are
// schema-validated AND YAML round-trip-verified so a malformed packet can never corrupt
// the packet stream (the stream IS the engine's memory).
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { hostScrubber, scrubDeep } from './portable.mjs';
import { parseYaml, stringifyYaml } from './yaml.mjs';
import { assertValidPacket } from './validate-packet.mjs';
import { deepEqual } from './util.mjs';

export function loadPacket(repoRoot, id) {
  const dir = join(repoRoot, 'quality', 'packets');
  let path = join(dir, `${id}.yaml`);
  if (!existsSync(path)) {
    path = null;
    if (existsSync(dir)) {
      for (const f of readdirSync(dir)) {
        if (!f.endsWith('.yaml')) continue;
        try {
          if (parseYaml(readFileSync(join(dir, f), 'utf8'))?.candidate_id === id) { path = join(dir, f); break; }
        } catch { /* foreign yaml */ }
      }
    }
    if (!path) throw new Error(`packet not found: ${id} (looked in ${dir})`);
  }
  const rawText = readFileSync(path, 'utf8');
  return { packet: parseYaml(rawText), path, rawText };
}

// evidence_required[].command / expect_check are AUTHORED input: the executor rewrites foreign
// paths at run time (portableRungCommand) and validate-packet lints them; everything else is
// free text bound for a committed file and gets host paths scrubbed on the way out.
const AUTHORED_RUNG_FIELDS = new Set(['command', 'expect_check']);
const isAuthoredRungField = (p) => p[0] === 'evidence_required' && p.length >= 3 && AUTHORED_RUNG_FIELDS.has(p[2]);

export function writePacket(path, packet) {
  // packets live at <repo>/quality/packets/<id>.yaml
  packet = scrubDeep(packet, hostScrubber(dirname(dirname(dirname(path)))), isAuthoredRungField);
  assertValidPacket(packet, packet.candidate_id);
  const yaml = stringifyYaml(packet);
  const back = parseYaml(yaml);
  if (!deepEqual(packet, back)) {
    throw new Error(`YAML round-trip mismatch for ${packet.candidate_id} — refusing to corrupt the packet stream`);
  }
  writeFileSync(path, yaml);
}
