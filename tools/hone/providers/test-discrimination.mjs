#!/usr/bin/env node
// Discrimination test: can the verdict providers actually judge?
//
// Three fixtures derived from REAL history (pdpp-cq-sweep cdd42d4e6, a landed
// T1b explicit-context extraction that a real codex review PASSed):
//   good           the landed diff verbatim                    -> expect PASS
//   bad-behavior   same diff, hoisted predicate uses `<` where the original
//                  used `<=` (boundary behavior break, tsc-green) -> expect REJECT|REVISE
//   bad-relocation same extraction as a NESTED named function that still
//                  captures nowMs implicitly (pure relocation,
//                  tsc-green, tests-green)                      -> expect REJECT|REVISE
//
// All three share one packet (fixtures/packet.json): the T1b explicit-context
// extraction contract. Same packet, three different maker outputs — exactly
// the judge's real job.
//
// Usage:
//   node test-discrimination.mjs                 # both providers, all cases
//   node test-discrimination.mjs --provider claude --case bad-behavior
//   node test-discrimination.mjs --save          # also write results/discrimination-<ts>.json
//   node test-discrimination.mjs --self-test     # offline: fail-closed parse/error paths only
//
// Exit code 0 iff every (provider, case) verdict matches expectation.

import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createProvider, extractFencedJson } from "./provider.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, "fixtures");

const CASES = [
  { name: "good", expect: ["PASS"] },
  { name: "bad-behavior", expect: ["REJECT", "REVISE"] },
  { name: "bad-relocation", expect: ["REJECT", "REVISE"] },
];

const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? null : (args[i + 1]?.startsWith("--") ? true : args[i + 1] ?? true);
};

// ---------------------------------------------------------------------------
// Offline self-test of the fail-closed machinery (no LLM calls).
// ---------------------------------------------------------------------------
if (args.includes("--self-test")) {
  const checks = [];
  const ok = (name, cond) => checks.push({ name, pass: !!cond });

  ok("fenced json parses", extractFencedJson('x\n```json\n{"verdict":"PASS"}\n```\ny')?.verdict === "PASS");
  ok("last fence wins", extractFencedJson('```json\n{"verdict":"PASS"}\n```\n```json\n{"verdict":"REJECT"}\n```')?.verdict === "REJECT");
  ok("bare json tolerated", extractFencedJson('{"verdict":"REVISE"}')?.verdict === "REVISE");
  ok("garbage -> null", extractFencedJson("no json here") === null);

  const garbage = createProvider({ name: "garbage", exec: async () => ({ text: "not json, ever", meta: {} }) });
  const g = await garbage.judge({ diff: "d", evidence: "e", packet: {} });
  ok("unparseable -> REVISE", g.verdict === "REVISE" && g.reasoning === "unparseable");
  ok("unparseable retried once (2 attempts)", g.raw.attempts.length === 2);

  const invalid = createProvider({ name: "invalid", exec: async () => ({ text: '```json\n{"verdict":"LGTM!"}\n```', meta: {} }) });
  ok("invalid verdict -> REVISE", (await invalid.judge({ diff: "d" })).verdict === "REVISE");

  const dying = createProvider({ name: "dying", exec: async () => { throw Object.assign(new Error("boom"), { kind: "timeout" }); } });
  const d = await dying.judge({ diff: "d" });
  ok("error/timeout -> REVISE, never PASS", d.verdict === "REVISE");
  ok("error recorded in raw", d.raw.attempts.every((a) => a.error));

  const flaky = (() => {
    let n = 0;
    return createProvider({ name: "flaky", exec: async () => (++n === 1 ? Promise.reject(Object.assign(new Error("x"), { kind: "nonzero-exit" })) : { text: '```json\n{"verdict":"PASS","reasoning":"r","confidence":0.9}\n```', meta: {} }) });
  })();
  ok("single retry recovers transient error", (await flaky.judge({ diff: "d" })).verdict === "PASS");

  const p = await garbage.propose({ packet: {}, context: "c" });
  ok("propose fail-closed -> design null", p.design === null && p.error === "unparseable");

  for (const c of checks) console.log(`${c.pass ? "ok  " : "FAIL"} ${c.name}`);
  process.exit(checks.every((c) => c.pass) ? 0 : 1);
}

// ---------------------------------------------------------------------------
// The real discrimination run.
// ---------------------------------------------------------------------------
const providers = [];
const wantProvider = flag("provider");
if (!wantProvider || wantProvider === "claude") providers.push((await import("./claude.mjs")).default);
if (!wantProvider || wantProvider === "codex") providers.push((await import("./codex.mjs")).default);

const wantCase = flag("case");
const cases = CASES.filter((c) => !wantCase || c.name === wantCase).map((c) => ({
  ...c,
  diff: readFileSync(join(FIXTURES, c.name, "diff.patch"), "utf8"),
  evidence: readFileSync(join(FIXTURES, c.name, "evidence.txt"), "utf8"),
}));
const packet = JSON.parse(readFileSync(join(FIXTURES, "packet.json"), "utf8"));

console.log(`discrimination test: ${providers.map((p) => p.name).join(", ")} x ${cases.map((c) => c.name).join(", ")}\n`);

const rows = [];
await Promise.all(
  providers.map(async (provider) => {
    for (const c of cases) {
      const t0 = Date.now();
      const res = await provider.judge({ diff: c.diff, evidence: c.evidence, packet });
      const wallMs = Date.now() - t0;
      const lastMeta = [...res.raw.attempts].reverse().find((a) => a.meta)?.meta ?? null;
      const row = {
        provider: provider.name,
        model: lastMeta?.model ?? null,
        case: c.name,
        expected: c.expect.join("|"),
        verdict: res.verdict,
        ok: c.expect.includes(res.verdict),
        confidence: res.confidence,
        reasoning: res.reasoning,
        wallMs,
        attempts: res.raw.attempts.length,
        costUsd: lastMeta?.costUsd ?? null,
        tokens: lastMeta?.tokens ?? null,
        errors: res.raw.attempts.filter((a) => a.error).map((a) => a.error),
      };
      rows.push(row);
      console.log(
        `[${row.provider}/${row.case}] ${row.ok ? "OK " : "MISS"} verdict=${row.verdict} (expected ${row.expected}) ` +
          `conf=${row.confidence ?? "-"} wall=${(wallMs / 1000).toFixed(1)}s ` +
          `cost=${row.costUsd != null ? `$${row.costUsd.toFixed(3)}` : `${row.tokens?.total ?? "?"} tok`}\n` +
          `  reasoning: ${row.reasoning}\n`
      );
    }
  })
);

const misses = rows.filter((r) => !r.ok);
console.log(`\n=== matrix ===`);
for (const r of rows) console.log(`${r.provider.padEnd(7)} ${r.case.padEnd(15)} ${r.verdict.padEnd(7)} ${r.ok ? "ok" : "MISS"}`);
console.log(misses.length === 0 ? "\nDISCRIMINATION: all verdicts as expected" : `\nDISCRIMINATION FAILURES: ${misses.map((m) => `${m.provider}/${m.case}`).join(", ")}`);

if (args.includes("--save")) {
  const dir = join(HERE, "results");
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `discrimination-${new Date().toISOString().replace(/[:.]/g, "-")}.json`);
  writeFileSync(file, JSON.stringify(rows, null, 2));
  console.log(`saved: ${file}`);
}

process.exit(misses.length === 0 ? 0 : 1);
