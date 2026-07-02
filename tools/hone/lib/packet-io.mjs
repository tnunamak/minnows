// packet-io.mjs — load/write one candidate packet, shared by `work` and `reset`.
//
// Both directions are fail-closed: loads resolve by filename OR embedded candidate_id
// (hand-authored packets may not follow the id-as-filename convention); writes are
// schema-validated AND YAML round-trip-verified so a malformed packet can never corrupt
// the packet stream (the stream IS the engine's memory).
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
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

export function writePacket(path, packet) {
  assertValidPacket(packet, packet.candidate_id);
  const yaml = stringifyYaml(packet);
  const back = parseYaml(yaml);
  if (!deepEqual(packet, back)) {
    throw new Error(`YAML round-trip mismatch for ${packet.candidate_id} — refusing to corrupt the packet stream`);
  }
  writeFileSync(path, yaml);
}
