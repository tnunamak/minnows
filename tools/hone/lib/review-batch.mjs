// review-batch.mjs — clear deferred model-review debt for landed packets.
//
// `hone work --defer-judge` lands only after the deterministic gates are green, then
// records outcome.review_status=pending. This command runs the model judge later over
// those landed diffs and flips the review_status. It NEVER reverts a landed commit.

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parseYaml } from './yaml.mjs';
import { writePacket } from './packet-io.mjs';
import { appendClaim, appendCostEntry, nextClaimSeq, nextJobAttempt, claimsPath, costPath } from './ledger.mjs';
import {
  PROVIDERS,
  DEFERRED_JUDGE_PROVIDER,
  buildCurrentJudgeEvidence,
  currentJudgeEvidenceEntries,
  gitContext,
  headClip,
  normalizeTouchEntry,
  tailClip,
} from './work.mjs';

function realDeps() {
  return {
    judge: async (name) => (await import(`../providers/${name}.mjs`)).default,
    log: (s) => process.stderr.write(s + '\n'),
  };
}

export async function runReviewBatch(flags) {
  const res = await executeReviewBatch({
    repoRoot: resolve(flags.repo || '.'),
    judgeName: String(flags.judge || 'claude'),
    limit: flags.limit == null ? null : Number(flags.limit),
  }, realDeps());
  process.stdout.write(res.summary + '\n');
  process.exitCode = res.exitCode;
}

function readPackets(repoRoot) {
  const dir = join(repoRoot, 'quality', 'packets');
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith('.yaml'))
    .sort()
    .map((file) => {
      const path = join(dir, file);
      const rawText = readFileSync(path, 'utf8');
      return { path, rawText, packet: parseYaml(rawText) };
    });
}

function receiptEntriesFromPacket(packet) {
  return (packet.outcome?.evidence_receipts ?? []).map((line) => {
    const m = String(line).match(/^\[([^\]]+)\]\s+([^:]+):/);
    return {
      line,
      slice: null,
      phase: m?.[1] ?? 'post',
      rung: m?.[2] ?? 'receipt',
      pass: /\sPASS;/.test(String(line)),
    };
  });
}

function tokensFromMetas(metas) {
  let inTok = null, outTok = null, total = null, usd = null;
  const add = (cur, v) => (v == null ? cur : (cur ?? 0) + v);
  for (const m of metas) {
    if (!m) continue;
    inTok = add(inTok, m.tokens?.input ?? null);
    outTok = add(outTok, m.tokens?.output ?? null);
    total = add(total, m.tokens?.total ?? ((m.tokens?.input ?? null) != null && (m.tokens?.output ?? null) != null ? m.tokens.input + m.tokens.output : null));
    usd = add(usd, m.costUsd ?? null);
  }
  return { inTok, outTok, total, usd };
}

function appendReviewClaim(repoRoot, id, claim) {
  const seq = nextClaimSeq(repoRoot, id);
  appendClaim(repoRoot, {
    claim_id: `clm-${id}-${seq}`,
    created: new Date().toISOString(),
    candidate_id: id,
    type: claim.type,
    statement: claim.statement,
    evidence: claim.evidence ?? [],
    judge: claim.judge ?? null,
  });
}

function appendReviewCost(repoRoot, packet, judgeName, verdict, metas, startedAt) {
  const tokens = tokensFromMetas(metas);
  appendCostEntry(repoRoot, {
    job_id: `job-${packet.candidate_id}-${nextJobAttempt(repoRoot, packet.candidate_id)}`,
    created: new Date().toISOString(),
    candidate_id: packet.candidate_id,
    workflow: `${packet.action}:review-batch`,
    maker: { provider: packet.maker_provider ?? 'unknown-maker', tier: packet.maker_tier },
    judge: { provider: judgeName, tier: packet.judge_tier },
    tokens_in: tokens.inTok,
    tokens_out: tokens.outTok,
    cost_usd: tokens.usd == null ? null : Math.round(tokens.usd * 10000) / 10000,
    wall_time_s: Math.round((Date.now() - startedAt) / 100) / 10,
    landed: true,
    revision_count: 0,
    judge_result: verdict.verdict,
    outcome: 'landed',
    followup_created: [],
  });
}

function reviewStatusFor(verdict) {
  return `reviewed-${String(verdict).toLowerCase()}`;
}

export async function executeReviewBatch({ repoRoot, judgeName = 'claude', limit = null }, deps = realDeps()) {
  if (!PROVIDERS.includes(judgeName)) {
    return { exitCode: 2, summary: `hone review-batch: REFUSED\n  unknown judge provider '${judgeName}' (known: ${PROVIDERS.join(', ')})` };
  }
  if (limit != null && (!Number.isInteger(limit) || limit <= 0)) {
    return { exitCode: 2, summary: `hone review-batch: REFUSED\n  --limit must be a positive integer, got ${JSON.stringify(limit)}` };
  }

  const all = readPackets(repoRoot);
  const pending = all
    .filter(({ packet }) => packet?.status === 'landed' && packet?.outcome?.review_status === 'pending')
    .slice(0, limit ?? undefined);
  if (!pending.length) {
    return { exitCode: 0, summary: 'hone review-batch: no landed packets with review_status=pending' };
  }

  const g = gitContext(repoRoot);
  const judgeProvider = await deps.judge(judgeName);
  const rows = [];
  let pass = 0;
  let flagged = 0;

  for (const item of pending) {
    const { packet, path, rawText } = item;
    const id = packet.candidate_id;
    const startedAt = Date.now();
    const commit = packet.outcome?.commit;
    if (!commit) {
      rows.push({ id, commit: '(missing)', verdict: 'ERROR', reason: 'pending-review landed packet has no outcome.commit' });
      flagged++;
      continue;
    }
    if (packet.judge_provider !== DEFERRED_JUDGE_PROVIDER) {
      rows.push({ id, commit: commit.slice(0, 12), verdict: 'ERROR', reason: `review_status pending but judge_provider is ${JSON.stringify(packet.judge_provider)}, not '${DEFERRED_JUDGE_PROVIDER}'` });
      flagged++;
      continue;
    }
    if (packet.maker_provider === judgeName) {
      rows.push({ id, commit: commit.slice(0, 12), verdict: 'ERROR', reason: `maker_provider == judge ('${judgeName}') — rerun review-batch with a different --judge` });
      flagged++;
      continue;
    }

    const touchTop = packet.touchset.map((p) => normalizeTouchEntry(g, repoRoot, p));
    let diff = '';
    try {
      diff = g.git(['show', '--format=', '--find-renames', commit, '--', ...touchTop]);
    } catch (e) {
      rows.push({ id, commit: commit.slice(0, 12), verdict: 'ERROR', reason: e.message });
      flagged++;
      continue;
    }
    if (!diff.trim()) {
      rows.push({ id, commit: commit.slice(0, 12), verdict: 'ERROR', reason: 'landed commit diff for packet touchset is empty; leaving review_status pending' });
      flagged++;
      continue;
    }

    const entries = currentJudgeEvidenceEntries(receiptEntriesFromPacket(packet));
    const evidence = buildCurrentJudgeEvidence(entries);
    deps.log?.(`hone review-batch — ${id}: judge=${judgeName} commit=${commit.slice(0, 12)}`);
    const verdict = await judgeProvider.judge({ diff: tailClip(diff, 150000), evidence, packet: rawText });
    const metas = (verdict.raw?.attempts ?? []).map((a) => a.meta).filter(Boolean);
    const verdictLine = `${judgeName} ${verdict.verdict}${verdict.confidence != null ? ` (confidence ${verdict.confidence})` : ''}: ${verdict.reasoning}`;

    packet.outcome.review_status = reviewStatusFor(verdict.verdict);
    packet.outcome.judge_verdict = verdictLine;
    packet.judge_provider = judgeName;
    writePacket(path, packet);
    appendReviewCost(repoRoot, packet, judgeName, verdict, metas, startedAt);
    appendReviewClaim(repoRoot, id, {
      type: 'judged_design_claim',
      statement: verdict.verdict === 'PASS'
        ? `deferred-review PASS for landed commit ${commit.slice(0, 12)}: ${verdict.reasoning}`
        : `DEFERRED REVIEW ${verdict.verdict} for already-landed commit ${commit.slice(0, 12)}: ${verdict.reasoning}`,
      judge: { provider: judgeName, verdict: verdict.verdict },
    });
    if (verdict.verdict !== 'PASS') {
      const refusalVerb = verdict.verdict === 'REVISE' ? 'FLAGGED FOR REVISION' : 'REJECTED';
      appendReviewClaim(repoRoot, id, {
        type: 'remaining_work',
        statement: `LANDED COMMIT ${commit.slice(0, 12)} WAS ${refusalVerb} IN DEFERRED REVIEW; owner must decide revert-commit or re-work. hone review-batch does not auto-revert landed commits.`,
        judge: { provider: judgeName, verdict: verdict.verdict },
      });
    }

    if (verdict.verdict === 'PASS') pass++;
    else flagged++;
    rows.push({ id, commit: commit.slice(0, 12), verdict: verdict.verdict, reason: headClip(verdict.reasoning, 180) });
  }

  const loud = rows.filter((r) => r.verdict !== 'PASS')
    .map((r) => `!!! DEFERRED REVIEW FLAGGED LANDED COMMIT ${r.commit} (${r.id}): ${r.verdict} — ${r.reason}`);
  const table = rows.map((r) => `${r.verdict.padEnd(6)} ${r.commit.padEnd(12)} ${r.id} — ${r.reason}`);
  return {
    exitCode: flagged > 0 ? 2 : 0,
    summary: [
      `hone review-batch: reviewed ${rows.length}, pass ${pass}, flagged ${flagged}`,
      ...loud,
      ...table,
      `claims: ${claimsPath(repoRoot)}`,
      `cost:   ${costPath(repoRoot)}`,
      'No landed commit was auto-reverted.',
    ].join('\n'),
  };
}
