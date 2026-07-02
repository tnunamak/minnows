// claude verdict provider — subprocess adapter over `claude -p` (print mode).
//
// Invocation (kept minimal; verified against claude 2.1.198):
//   claude -p --model <model> --output-format json --no-session-persistence
// with the prompt on stdin. The JSON envelope gives us the reply text
// (`result`), real dollar cost (`total_cost_usd`), token usage, and duration.
//
// The call runs from a fresh empty temp cwd so the judge never inherits a
// repo's CLAUDE.md/hooks context — the packet + diff + evidence in the prompt
// are the ONLY case-specific context (fresh-context independence by design).

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createProvider, runCli, DEFAULT_TIMEOUT_MS } from "./provider.mjs";

const MODEL = process.env.HONE_CLAUDE_MODEL || "sonnet";
// effort is FIRST-CLASS and always explicit — never the CLI's silent default (L1
// amendment: every invocation is intentional about provider, model, AND effort).
// Judge posture defaults high (judge tier >= maker per the routing doctrine).
const EFFORT = process.env.HONE_CLAUDE_JUDGE_EFFORT || "high";

async function exec(prompt, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const cwd = mkdtempSync(join(tmpdir(), "hone-claude-"));
  const args = ["-p", "--model", MODEL, "--effort", EFFORT, "--output-format", "json", "--no-session-persistence"];
  const { stdout, durationMs } = await runCli("claude", args, { input: prompt, timeoutMs, cwd });

  let envelope;
  try {
    envelope = JSON.parse(stdout);
  } catch {
    throw Object.assign(new Error(`claude -p emitted non-JSON envelope: ${stdout.slice(0, 500)}`), {
      kind: "bad-envelope", durationMs,
    });
  }
  if (envelope.is_error) {
    throw Object.assign(new Error(`claude -p returned is_error: ${String(envelope.result).slice(0, 500)}`), {
      kind: "provider-error", durationMs,
    });
  }
  return {
    text: envelope.result ?? "",
    meta: {
      provider: "claude",
      model: MODEL,
      durationMs,
      costUsd: envelope.total_cost_usd ?? null,
      tokens: envelope.usage
        ? {
            input: envelope.usage.input_tokens ?? null,
            output: envelope.usage.output_tokens ?? null,
            cacheCreation: envelope.usage.cache_creation_input_tokens ?? null,
            cacheRead: envelope.usage.cache_read_input_tokens ?? null,
          }
        : null,
    },
  };
}

export default createProvider({ name: "claude", exec });
