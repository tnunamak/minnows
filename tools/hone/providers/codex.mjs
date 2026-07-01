// codex verdict provider — subprocess adapter over `codex exec`.
//
// Invocation (kept minimal; verified against codex-cli 0.142.5):
//   codex exec --ephemeral --skip-git-repo-check -s read-only --color never \
//              -m <model> -o <last-message-file> -
// with the prompt on stdin (`-`). `-o` writes the agent's final message to a
// file, which is far more robust than scraping it out of the event stream.
// `--ephemeral` keeps judge calls out of session history; `-s read-only`
// sandbox: a judge reviews evidence, it never executes anything mutating.
//
// Runs from a fresh empty temp cwd for fresh-context independence (no
// AGENTS.md / repo trust inherited). Cost: codex under a ChatGPT plan reports
// token count, not dollars — meta carries tokens + duration as the cost proxy.

import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createProvider, runCli, DEFAULT_TIMEOUT_MS } from "./provider.mjs";

const MODEL = process.env.HONE_CODEX_MODEL || "gpt-5.5";

function parseTokensUsed(streams) {
  // codex prints "tokens used\n<n,nnn>" at the end of its event stream.
  const m = /tokens used[^\d]*([\d,]+)/i.exec(streams);
  return m ? Number(m[1].replaceAll(",", "")) : null;
}

async function exec(prompt, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "hone-codex-"));
  const outFile = join(dir, "last-message.txt");
  const args = [
    "exec",
    "--ephemeral",
    "--skip-git-repo-check",
    "-s", "read-only",
    "--color", "never",
    "-m", MODEL,
    "-o", outFile,
    "-",
  ];
  const { stdout, stderr, durationMs } = await runCli("codex", args, { input: prompt, timeoutMs, cwd: dir });

  let text = "";
  try {
    text = readFileSync(outFile, "utf8");
  } catch {
    throw Object.assign(new Error(`codex exec produced no last-message file; stderr: ${stderr.slice(0, 500)}`), {
      kind: "no-output", durationMs,
    });
  }
  return {
    text,
    meta: {
      provider: "codex",
      model: MODEL,
      durationMs,
      costUsd: null, // ChatGPT-plan auth: no dollar figure reported
      tokens: { total: parseTokensUsed(stdout + "\n" + stderr) },
    },
  };
}

export default createProvider({ name: "codex", exec });
