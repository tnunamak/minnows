// Execute the exact private adapter bodies with inert filesystem/MCP/CLI doubles.
// No exported production test hooks and no real model processes are needed.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import { requireCurrentCodexModel } from './provider.mjs';

const current = ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna', 'gpt-6-astra'];
const retired = ['gpt-5-sol', 'gpt-5.1-sol', 'gpt-5.2-sol', 'gpt-5.3-sol', 'gpt-5.4-sol',
  'gpt-5.5-sol', 'gpt-5.6-sol', 'gpt-5.9-astra', 'gpt-6-sol'];
const files = {
  maker: new URL('../lib/work.mjs', import.meta.url),
  judge: new URL('./codex.mjs', import.meta.url),
  agenda: new URL('../lib/agenda.mjs', import.meta.url),
};

function adapter(kind, env = {}, failure = null, state = { calls: [], effects: 0 }) {
  const context = {
    process: { env }, join, requireCurrentCodexModel,
    MAKER_TIMEOUT_MS: 1000, AGENDA_TIMEOUT_MS: 1000, DEFAULT_TIMEOUT_MS: 1000,
    tmpdir: () => '/inert',
    mkdtempSync: () => { state.effects++; return '/inert/adapter'; },
    readFileSync: () => { state.effects++; return 'inert reply'; },
    codexNoMcpArgs: async () => { state.effects++; return ['-c', 'mcp_servers.inert.enabled=false']; },
    runCli: async (cmd, args, opts) => {
      state.effects++;
      state.calls.push({ cmd, args: Array.from(args), opts });
      if (failure) throw failure;
      return { stdout: 'tokens used\n42', stderr: '', durationMs: 1 };
    },
    createProvider: (opts) => opts,
  };
  const source = readFileSync(files[kind], 'utf8');
  let run;
  if (kind === 'judge') {
    // Evaluate module-level model/effort selection as well as exec().
    const body = source.replace(/^import .*;\n/gm, '').replace('export default ', 'globalThis.adapter = ');
    assert.doesNotMatch(body, /^import /m);
    runInNewContext(body, context, { filename: String(files[kind]) });
    run = context.adapter.exec;
  } else {
    const name = kind === 'maker' ? 'codexMaker' : 'codexExec';
    const start = source.indexOf(`async function ${name}(`);
    const end = source.indexOf('\n}', start);
    assert.ok(start >= 0 && end > start, `missing complete ${name} adapter`);
    run = runInNewContext(`(${source.slice(start, end + 2)})`, context, { filename: String(files[kind]) });
  }
  return { run, state };
}

async function selection(kind, env = {}, opts = {}, expectedModel = 'gpt-6.1-sol', effort = null) {
  const { run, state } = adapter(kind, env);
  const result = await run('inert prompt', { cwd: '/inert/repo', ...opts });
  assert.equal(state.calls.length, 1);
  const { cmd, args } = state.calls[0];
  assert.equal(cmd, 'codex');
  assert.equal(args[args.indexOf('-m') + 1], expectedModel);
  assert.equal(result.meta.model, expectedModel);
  assert.equal(args[args.indexOf('-s') + 1], kind === 'maker' ? 'workspace-write' : 'read-only');
  const efforts = args.filter((arg) => arg.startsWith('model_reasoning_effort='));
  assert.deepEqual(efforts, effort === null ? [] : [`model_reasoning_effort="${effort}"`]);
}

for (const kind of Object.keys(files)) {
  test(`${kind}: actual default model and effort argv`, async () => {
    await selection(kind, {}, {}, 'gpt-6.1-sol', kind === 'agenda' ? null : 'high');
  });

  test(`${kind}: accepts every current override and rejects retired overrides before effects`, async () => {
    for (const model of current) {
      await selection(kind, { HONE_CODEX_MODEL: model }, {}, model, kind === 'agenda' ? null : 'high');
    }
    for (const model of retired) {
      const state = { calls: [], effects: 0 };
      await assert.rejects(async () => {
        const { run } = adapter(kind, { HONE_CODEX_MODEL: model }, null, state);
        return run('inert prompt');
      }, /unsupported Codex model/);
      assert.equal(state.effects, 0, model);
    }
  });

  test(`${kind}: model execution failure propagates without another model call`, async () => {
    const failure = new Error('synthetic model unavailable');
    const { run, state } = adapter(kind, {}, failure);
    await assert.rejects(() => run('inert prompt'), (error) => error === failure);
    assert.equal(state.calls.length, 1);
  });
}

test('maker: routing supplies model/effort; environment wins; retired routing is refused', async () => {
  for (const model of current) await selection('maker', {}, { model, effort: 'medium' }, model, 'medium');
  await selection('maker', { HONE_CODEX_MODEL: 'gpt-6.1-terra', HONE_CODEX_EFFORT: 'low' },
    { model: 'gpt-6-astra', effort: 'medium' }, 'gpt-6.1-terra', 'low');
  for (const model of retired) {
    const { run, state } = adapter('maker');
    await assert.rejects(() => run('inert prompt', { model }), /unsupported Codex model/);
    assert.equal(state.effects, 0);
  }
});

test('judge: explicit effort override reaches argv', async () => {
  await selection('judge', { HONE_CODEX_JUDGE_EFFORT: 'medium' }, {}, 'gpt-6.1-sol', 'medium');
});

test('agenda: dedicated model wins; effort stays inherited from CLI/profile', async () => {
  for (const model of current) {
    await selection('agenda', { HONE_AGENDA_CODEX_MODEL: model, HONE_CODEX_MODEL: 'gpt-5.6-sol',
      HONE_CODEX_EFFORT: 'low', HONE_CODEX_JUDGE_EFFORT: 'medium' }, {}, model);
  }
  for (const model of retired) {
    const { run, state } = adapter('agenda', { HONE_AGENDA_CODEX_MODEL: model, HONE_CODEX_MODEL: 'gpt-6.1-sol' });
    await assert.rejects(() => run('inert prompt'), /unsupported Codex model/);
    assert.equal(state.effects, 0);
  }
});
