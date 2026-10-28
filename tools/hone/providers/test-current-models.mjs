import assert from 'node:assert/strict';
import { requireCurrentCodexModel } from './provider.mjs';
import { loadRegistry, loadRouting, selectAgent } from '../lib/routing.mjs';

assert.equal(requireCurrentCodexModel('gpt-6.1-sol'), 'gpt-6.1-sol');
assert.equal(requireCurrentCodexModel('gpt-6.1-terra'), 'gpt-6.1-terra');
assert.equal(requireCurrentCodexModel('gpt-6-luna'), 'gpt-6-luna');
assert.equal(requireCurrentCodexModel('gpt-6-astra'), 'gpt-6-astra');
assert.throws(() => requireCurrentCodexModel('gpt-5.6-sol'));
assert.throws(() => requireCurrentCodexModel('gpt-6.1-sol-untrusted'));
const registry = loadRegistry();
const policy = loadRouting(undefined, registry);
assert.throws(() => selectAgent('extraction', 0, null, registry, policy, { providerFilter: 'codex' }), /no eligible candidate/);
console.log('current model guard and calibration gate pass');
