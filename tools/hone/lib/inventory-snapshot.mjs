import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const SENSOR_FILES = ['tier-mass.json', 'hotspots.json', 'callback-smells.json', 'test-signals.json'];

/** Refuse stale, partial, or mixed inventory before any consumer spends or writes. */
export function loadVerifiedInventorySnapshot(repoRoot, currentSha) {
  const dir = join(repoRoot, 'quality', 'inventory');
  const read = (name) => {
    const path = join(dir, name);
    if (!existsSync(path)) throw new Error(`missing quality/inventory/${name} — run hone inventory first`);
    return JSON.parse(readFileSync(path, 'utf8'));
  };
  const meta = read('meta.json');
  if (meta.repo_sha !== currentSha) {
    throw new Error(`stale inventory repo_sha ${String(meta.repo_sha).slice(0, 12)} != HEAD ${String(currentSha).slice(0, 12)} — re-run inventory`);
  }
  const sensors = Object.fromEntries(SENSOR_FILES.map((name) => [name, read(name)]));
  for (const [name, doc] of Object.entries(sensors)) {
    if (doc.generated_from?.repo_sha !== meta.repo_sha) {
      throw new Error(`mixed inventory snapshot: ${name} repo_sha ${doc.generated_from?.repo_sha ?? '(missing)'} != meta ${meta.repo_sha}`);
    }
  }
  return { meta, sensors };
}
