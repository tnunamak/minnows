// hone verdict-provider interface (SPEC.md "Verdict providers").
//
// A provider adapter supplies ONE thing: `exec(prompt, {timeoutMs}) -> {text, meta}`,
// a fresh-context subprocess call to an LLM CLI. This module turns that into the
// two judgment operations the engine needs:
//
//   judge({diff, evidence, packet})   -> {verdict, reasoning, confidence, raw}
//   propose({packet, context})        -> {design, raw}
//
// Non-negotiable #1 (maker != judge) is enforced by `work`, not here — but this
// layer is what makes an *independent* judge possible: every call is a fresh
// subprocess with only the packet + diff + evidence, no maker context.
//
// Fail-CLOSED everywhere: timeout, subprocess error, or unparseable output can
// never produce PASS. The terminal fallback verdict is REVISE.

import { spawn } from "node:child_process";

export const VERDICTS = Object.freeze(["PASS", "REVISE", "REJECT"]);
export const DEFAULT_TIMEOUT_MS = Number(process.env.HONE_JUDGE_TIMEOUT_MS ?? 5 * 60 * 1000);

// ---------------------------------------------------------------------------
// Subprocess runner (shared by all adapters): 5-min default timeout, process-
// group SIGKILL on timeout so CLI helper processes die with the parent.
// ---------------------------------------------------------------------------
export function runCli(cmd, args, { input = null, timeoutMs = DEFAULT_TIMEOUT_MS, cwd, env } = {}) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    const child = spawn(cmd, args, {
      cwd,
      env: env ?? process.env,
      stdio: ["pipe", "pipe", "pipe"],
      detached: true, // own process group, so we can kill the whole tree
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      try {
        process.kill(-child.pid, "SIGKILL"); // whole process group
      } catch {
        try { child.kill("SIGKILL"); } catch { /* already gone */ }
      }
    }, timeoutMs);

    child.stdout.on("data", (d) => { stdout += d; });
    child.stderr.on("data", (d) => { stderr += d; });

    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(Object.assign(new Error(`spawn ${cmd} failed: ${err.message}`), { kind: "spawn-error" }));
    });

    child.on("close", (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const durationMs = Date.now() - startedAt;
      if (timedOut) {
        reject(Object.assign(new Error(`${cmd} timed out after ${timeoutMs}ms (killed)`), {
          kind: "timeout", durationMs, stdout, stderr,
        }));
        return;
      }
      if (code !== 0) {
        reject(Object.assign(new Error(`${cmd} exited ${code ?? `signal ${signal}`}: ${stderr.slice(0, 2000)}`), {
          kind: "nonzero-exit", code, durationMs, stdout, stderr,
        }));
        return;
      }
      resolve({ stdout, stderr, durationMs, code });
    });

    if (input != null) child.stdin.write(input);
    child.stdin.end();
  });
}

// ---------------------------------------------------------------------------
// Structured output: the model must emit a fenced JSON block; we parse the
// LAST parseable one (final answer wins over any JSON quoted mid-analysis).
// ---------------------------------------------------------------------------
export function extractFencedJson(text) {
  if (typeof text !== "string" || text.length === 0) return null;
  const fences = [...text.matchAll(/```(?:json)?\s*\n([\s\S]*?)```/g)].map((m) => m[1]);
  for (let i = fences.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(fences[i]);
      if (parsed && typeof parsed === "object") return parsed;
    } catch { /* try earlier fence */ }
  }
  // Tolerate a bare-JSON reply (some models drop the fence under "ONLY JSON" retry).
  try {
    const parsed = JSON.parse(text.trim());
    if (parsed && typeof parsed === "object") return parsed;
  } catch { /* fall through */ }
  return null;
}

function normalizeVerdict(value) {
  if (typeof value !== "string") return null;
  const v = value.trim().toUpperCase();
  return VERDICTS.includes(v) ? v : null;
}

function normalizeConfidence(value) {
  const n = typeof value === "string" ? Number(value) : value;
  if (typeof n !== "number" || Number.isNaN(n)) return null;
  return Math.min(1, Math.max(0, n));
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------
const JUDGE_ROLE = `You are the INDEPENDENT JUDGE in a repo-quality engine. A separate agent (the maker) produced the diff below to execute the work packet. You did not write this change; your job is to certify it or refuse it. Makers overclaim — be adversarial.

Rules of judgment:
- The packet is the contract: its action, not_allowed, and evidence_required bind the maker.
- Evidence policy: you review evidence, you never replace it. Judge only from what is in front of you; do not assume unstated tests or checks exist. If the supplied evidence does not actually cover the property at risk for this change class, that alone justifies REVISE or REJECT.
- Behavior preservation: for preserve_refactor packets, ANY observable behavior change (including boundary/edge conditions and operator changes) means REJECT, unless the packet explicitly allows it.
- Reject relocation: moving a code blob behind a new function name WITHOUT making captured context explicit or reducing the enclosing function's real complexity is not decomplecting, even when types and tests are green.
- Compare the removed code to the added code line by line before trusting any summary, commit message, or comment inside the diff.

Verdict semantics:
- PASS: the diff does what the packet says, violates nothing in not_allowed, and the evidence is sufficient for the change class.
- REVISE: fixable defects, insufficient evidence, or quality below the packet's bar.
- REJECT: behavior change where preservation is required, a not_allowed violation, or relocation dressed as refactoring.`;

const JUDGE_OUTPUT = `First give your analysis (brief). Then end your reply with EXACTLY ONE fenced JSON block of this shape:

\`\`\`json
{"verdict": "PASS" | "REVISE" | "REJECT", "reasoning": "<2-5 sentences, the load-bearing reason>", "confidence": <number 0.0-1.0>}
\`\`\``;

const STRICT_RETRY_SUFFIX = `

IMPORTANT: your previous reply could not be parsed. This time respond with ONLY the single fenced \`\`\`json block described above — no prose before or after it.`;

export function buildJudgePrompt({ diff, evidence, packet }, { strict = false } = {}) {
  const parts = [
    JUDGE_ROLE,
    "== WORK PACKET ==",
    typeof packet === "string" ? packet : JSON.stringify(packet, null, 2),
    "== EVIDENCE RECEIPTS (everything the maker ran; nothing else was run) ==",
    evidence || "(none supplied)",
    "== DIFF UNDER JUDGMENT ==",
    diff,
    JUDGE_OUTPUT,
  ];
  let prompt = parts.join("\n\n");
  if (strict) prompt += STRICT_RETRY_SUFFIX;
  return prompt;
}

export function buildProposePrompt({ packet, context }, { strict = false } = {}) {
  const parts = [
    `You are the DESIGN PROPOSER in a repo-quality engine. Given the work packet and context below, propose the seam/design for the change: what to extract or restructure, what becomes an explicit parameter, what the resulting interfaces look like, and what evidence would prove preservation. Propose only; do not write the full diff.`,
    "== WORK PACKET ==",
    typeof packet === "string" ? packet : JSON.stringify(packet, null, 2),
    "== CONTEXT ==",
    context || "(none supplied)",
    `End your reply with EXACTLY ONE fenced JSON block:

\`\`\`json
{"design": "<the proposed design, concrete and self-sufficient>", "rationale": "<why this seam>"}
\`\`\``,
  ];
  let prompt = parts.join("\n\n");
  if (strict) prompt += STRICT_RETRY_SUFFIX;
  return prompt;
}

// ---------------------------------------------------------------------------
// The provider factory. `adapter` = { name, exec(prompt, {timeoutMs}) -> {text, meta} }.
// Retry policy (documented in README): at most ONE retry total per operation —
//   - subprocess error/timeout  -> one retry with the same prompt
//   - unparseable/invalid reply -> one retry with the strict-output prompt
// then fail closed (judge -> REVISE "unparseable"; propose -> design: null).
// ---------------------------------------------------------------------------
export function createProvider(adapter) {
  const { name, exec } = adapter;
  if (typeof name !== "string" || typeof exec !== "function") {
    throw new Error("createProvider requires { name, exec }");
  }

  async function attempt(prompt, timeoutMs, attempts) {
    const record = { prompt_bytes: Buffer.byteLength(prompt), text: null, meta: null, error: null };
    attempts.push(record);
    try {
      const { text, meta } = await exec(prompt, { timeoutMs });
      record.text = text;
      record.meta = meta;
      return { text, meta };
    } catch (err) {
      record.error = { kind: err.kind ?? "error", message: err.message, durationMs: err.durationMs ?? null };
      return { error: err };
    }
  }

  async function structuredCall(buildPrompt, args, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
    const attempts = [];
    let strict = false;
    for (let call = 0; call < 2; call++) {
      const res = await attempt(buildPrompt(args, { strict }), timeoutMs, attempts);
      if (res.error) { strict = false; continue; } // transient failure: retry same prompt once
      const parsed = extractFencedJson(res.text);
      if (parsed) return { parsed, attempts };
      strict = true; // parse failure: retry once with strict output instruction
    }
    return { parsed: null, attempts };
  }

  return {
    name,

    async judge({ diff, evidence, packet }, opts = {}) {
      if (!diff) throw new Error("judge: diff is required");
      const { parsed, attempts } = await structuredCall(buildJudgePrompt, { diff, evidence, packet }, opts);
      const raw = { provider: name, attempts };

      const verdict = parsed ? normalizeVerdict(parsed.verdict) : null;
      if (!verdict) {
        // Fail CLOSED: never PASS on garbage.
        return { verdict: "REVISE", reasoning: "unparseable", confidence: null, raw };
      }
      return {
        verdict,
        reasoning: typeof parsed.reasoning === "string" ? parsed.reasoning : "",
        confidence: normalizeConfidence(parsed.confidence),
        raw,
      };
    },

    async propose({ packet, context }, opts = {}) {
      const { parsed, attempts } = await structuredCall(buildProposePrompt, { packet, context }, opts);
      const raw = { provider: name, attempts };
      if (!parsed || typeof parsed.design !== "string" || parsed.design.length === 0) {
        // Fail CLOSED: a null design is never actionable.
        return { design: null, rationale: null, error: "unparseable", raw };
      }
      return {
        design: parsed.design,
        rationale: typeof parsed.rationale === "string" ? parsed.rationale : null,
        raw,
      };
    },
  };
}
