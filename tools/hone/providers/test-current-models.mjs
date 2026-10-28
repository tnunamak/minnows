import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { requireCurrentCodexModel } from './provider.mjs';
import { loadRegistry, loadRouting, selectAgent } from '../lib/routing.mjs';

assert.equal(requireCurrentCodexModel('gpt-6.1-sol'), 'gpt-6.1-sol');
assert.equal(requireCurrentCodexModel('gpt-6.1-terra'), 'gpt-6.1-terra');
assert.equal(requireCurrentCodexModel('gpt-6-luna'), 'gpt-6-luna');
assert.equal(requireCurrentCodexModel('gpt-6-astra'), 'gpt-6-astra');
assert.throws(() => requireCurrentCodexModel('gpt-5.6-sol'));
assert.throws(() => requireCurrentCodexModel('gpt-6.1-sol-untrusted'));
const registry = loadRegistry();
const catalog = new URL('../../../data/model-catalog/', import.meta.url);
const sources = JSON.parse(readFileSync(new URL('SOURCES.json', catalog), 'utf8')).sources;
const pricing = JSON.parse(readFileSync(new URL('pricing/openai-api-2026-07.json', catalog), 'utf8')).models;
['gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra'].forEach(id => {
  assert.deepEqual(registry.models[id].pricing, { in: pricing[id].fresh_input_per_m, out: pricing[id].output_per_m, cache_read: pricing[id].cache_read_per_m });
});
Object.values(registry.models).filter(m => m.status === 'active').forEach(m => {
  const cal = m.calibration;
  assert.equal(cal.type, 'external-eval-digitization');
  const pack = JSON.parse(readFileSync(new URL('../../../' + cal.source, import.meta.url), 'utf8'));
  assert.ok(pack.scores.some(row => JSON.stringify(row) === JSON.stringify(cal.evidence[0])));
  assert.equal(cal.evidence[0].model, m.id);
  assert.equal(cal.date, cal.evidence[0].observed_at);
  assert.equal(cal.source_url, sources.find(s => s.id === cal.source_id).url);
});
const policy = loadRouting(undefined, registry);
assert.equal(selectAgent('extraction', 0, null, registry, policy, { providerFilter: 'codex' }).model, 'gpt-6.1-sol');
registry.models['gpt-6.1-sol'].calibration = null;
assert.throws(() => selectAgent('extraction', 0, null, registry, policy, { providerFilter: 'codex' }), /no eligible candidate/);
registry.models['gpt-6.1-sol'].calibration = { source: 'fixture', date: '2026-10-28' };
registry.models['gpt-6.1-sol'].status = 'retired';
assert.throws(() => selectAgent('extraction', 0, null, registry, policy, { providerFilter: 'codex' }), /status=retired/);
console.log('current model guard, calibrated routing, retirement and calibration gate pass');
