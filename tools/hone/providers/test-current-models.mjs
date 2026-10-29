import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import * as provider from './provider.mjs';
import { requireCurrentCodexModel } from './provider.mjs';
import { loadRegistry, loadRouting, selectAgent } from '../lib/routing.mjs';

test('Codex acceptance is exactly the intended current set', () => {
  assert.deepEqual(provider.CURRENT_CODEX_MODELS, ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna', 'gpt-6-astra']);
  for (const id of provider.CURRENT_CODEX_MODELS) assert.equal(requireCurrentCodexModel(id), id);
  for (const id of ['gpt-5-sol', 'gpt-5.1-sol', 'gpt-5.2-sol', 'gpt-5.3-sol', 'gpt-5.4-sol',
    'gpt-5.5-sol', 'gpt-5.6-sol', 'gpt-5.9-astra', 'gpt-6-sol', 'gpt-6.1-sol-untrusted', '', null, undefined]) {
    assert.throws(() => requireCurrentCodexModel(id), /unsupported Codex model/, String(id));
  }
});

test('every routing class, escalation and quota path selects a current calibrated model', () => {
  const reg = loadRegistry();
  const pol = loadRouting(undefined, reg);
  const current = new Set(['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna', 'gpt-6-astra',
    'claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5']);
  const pressure = [null, { pools: { 'claude-sub': 1 } }, { pools: { 'openai-sub': 1 } },
    { pools: { 'claude-sub': 1, 'openai-sub': 1 } }];
  let decisions = 0;
  for (const [cls, config] of Object.entries(pol.classes)) {
    // Inspect all candidates as well as every step and the saturated escalation tail.
    for (const { model } of config.maker) assert.ok(current.has(reg.models[model].id), model);
    for (let attempt = 0; attempt <= config.maker.length * pol.escalation.strikes_per_step; attempt++) {
      for (const quota of pressure) {
        for (const providerFilter of [undefined, 'codex', 'claude']) {
          const chosen = selectAgent(cls, attempt, quota, reg, pol, { providerFilter });
          assert.ok(current.has(chosen.model), JSON.stringify(chosen));
          assert.equal(chosen.status, 'active');
          assert.equal(chosen.calibrated, true);
          if (providerFilter) assert.equal(chosen.provider, providerFilter);
          decisions++;
        }
      }
    }
  }
  console.log(`${decisions} current-model routing decisions checked`);
});

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
