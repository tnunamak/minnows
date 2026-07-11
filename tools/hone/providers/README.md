# hone verdict providers

The pluggable judgment layer of the repo-quality engine (SPEC.md "Verdict providers").
This is non-negotiable #1 — **maker ≠ judge** — as code: every judgment is a fresh-context
subprocess to an *independent* LLM CLI, carrying only the packet + diff + evidence. `work`
enforces that maker_provider ≠ judge_provider; this layer makes the independent side real.

## Interface (`provider.mjs`)

```js
import claude from "./claude.mjs";   // or codex from "./codex.mjs"

const { verdict, reasoning, confidence, raw } =
  await claude.judge({ diff, evidence, packet });   // verdict: PASS | REVISE | REJECT

const { design, rationale, raw } =
  await claude.propose({ packet, context });        // design: string | null
```

- `packet` is the candidate packet (object or string, per `../schemas/candidate-packet.yaml`).
- `evidence` is the maker's receipts, pasted not narrated. **Evidence policy:** the judge
  reviews evidence, it never replaces it — the prompt instructs the judge that insufficient
  evidence for the change class is itself grounds for REVISE/REJECT, and that nothing
  outside the receipts may be assumed to exist.
- `raw.attempts[]` records every subprocess call verbatim (reply text, meta, error).

Adapters supply one function: `exec(prompt, {timeoutMs}) → {text, meta}`. Everything else
(prompts, parsing, retry, fail-closed) lives in `provider.mjs` — a new provider is ~40 lines.

## Structured output, fail-CLOSED

The model must end its reply with one fenced ```json block:
`{"verdict": "PASS"|"REVISE"|"REJECT", "reasoning": "...", "confidence": 0.0-1.0}`.
The **last** parseable fenced block wins (bare JSON tolerated as fallback).

Failure can never produce PASS:

| failure | behavior |
|---|---|
| reply has no parseable JSON / invalid verdict | **one** retry with a strict "ONLY the fenced json block" prompt, then `judge` → `{verdict: "REVISE", reasoning: "unparseable"}`; `propose` → `{design: null, error: "unparseable"}` |
| subprocess error / nonzero exit | **one** retry with the same prompt, then fail closed as above |
| timeout (default **5 min/call**, `HONE_JUDGE_TIMEOUT_MS`) | process-group SIGKILL (the whole CLI tree dies), then same retry-once/fail-closed path |

At most one retry total per operation → max 2 subprocess calls. All paths are covered by
the offline self-test: `node test-discrimination.mjs --self-test` (no LLM calls).

## Adapters

**`claude.mjs`** — `claude -p --model <m> --output-format json --no-session-persistence`,
prompt on stdin, run from a fresh empty temp cwd (no repo CLAUDE.md/hooks leak into the
judge). The JSON envelope supplies real `costUsd` and token usage. Model:
`HONE_CLAUDE_MODEL` (default `sonnet`). Verified against claude 2.1.198.

**`codex.mjs`** — `codex exec --ephemeral --skip-git-repo-check -s read-only --color never
-m <m> -o <file> -`, prompt on stdin, fresh temp cwd. `-o` captures the final message
robustly; `-s read-only` because a judge reviews evidence, it never mutates. Model:
`HONE_CODEX_MODEL` (default `gpt-5.6-sol`; non-GPT-5.6 values are refused). ChatGPT-plan auth reports tokens, not dollars —
`meta.tokens.total` + duration are the cost proxy. Verified against codex-cli 0.142.5.

## Discrimination test (proof the judges can judge)

```
node test-discrimination.mjs [--provider claude|codex] [--case <name>] [--save]
```

Fixtures in `fixtures/` derive from REAL history (pdpp-cq-sweep `cdd42d4e6`, a landed T1b
explicit-context extraction that a real codex review PASSed). One shared packet, three
maker outputs:

| case | construction | expected |
|---|---|---|
| `good` | the landed diff, verbatim | PASS |
| `bad-behavior` | identical, except the hoisted predicate uses `<` where the original used `<=` (boundary break; tsc-green, suite plausibly green) | REJECT/REVISE |
| `bad-relocation` | predicate moved to a *nested* named function still capturing `nowMs` implicitly — pure relocation, tsc-green, tests genuinely green | REJECT/REVISE |

### Measured results (2026-07-01, first run, zero prompt iteration; `results/discrimination-2026-07-01T22-57-50-097Z.json` has verbatim verdicts)

| provider (model) | good | bad-behavior | bad-relocation | wall/call | cost/call |
|---|---|---|---|---|---|
| claude (sonnet) | PASS (0.92) | REJECT (0.95) | REJECT (0.90) | 14–16 s | $0.14–0.26 |
| codex (historical GPT-5.5, xhigh) | PASS (0.90) | REJECT (0.99) | REJECT (0.93) | 24–68 s | 15.3k–26.0k tokens (subscription) |

6/6 correct, all single-attempt, both providers cited the exact defect (the `<=`→`<`
boundary flip incl. that the supplied evidence couldn't have caught it; the still-implicit
`nowMs` capture for the relocation). Judge-prompt rules (compare removed vs added line by
line; reject relocation; evidence never assumed) come from SPEC non-negotiables, not from
tuning to these fixtures.

## Honest limitations

- **n=1 per cell.** One run per case per provider; LLM verdicts are nondeterministic.
  Re-run `test-discrimination.mjs` before trusting a provider change.
- Fixtures derive from a single real commit + one packet shape (preserve_refactor,
  certified-local-transform). No coverage yet of surface-repair, evidence-generation,
  auth/storage-risk, or `propose` discrimination.
- Evidence stubs are constructed receipts; the judge cannot re-run them (by design —
  evidence verification is the engine's job upstream, the judge only reviews).
- claude `-p` still loads user-level context (~17k input + ~30k cache-creation tokens
  baseline ≈ $0.15–0.26/call on sonnet). `--bare` would cut this but forces API-key auth.
- codex latency/cost reflect the user config's `xhigh` reasoning effort; no dollar figure
  under ChatGPT-plan auth.
- Timeout kill is process-group SIGKILL; a killed codex call may leave an `--ephemeral`
  temp dir behind (harmless, in `$TMPDIR`).
