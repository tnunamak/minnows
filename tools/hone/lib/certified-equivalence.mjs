// certified-equivalence.mjs — deterministic judge bypass for review-free classes.
//
// This is intentionally narrow. A packet is review-free only when its proof class is
// mechanically certifiable AND the current green evidence includes exactly one
// deterministic equivalence rung (or the packet names one explicitly). Any ambiguity
// returns not-certified so callers take the normal model-judge path unchanged.

const CERTIFIED_PROOF_CLASSES = new Set(['exact_move', 'type_only']);
const EQUIVALENCE_RUNG_RE = /(^|[-_\s])(byte[-_\s]?identity|ast[-_\s]?equivalence|certified[-_\s]?equivalence)([-_\s]|$)/i;

export const DETERMINISTIC_PROOF_PROVIDER = 'deterministic-proof';
export const DETERMINISTIC_PROOF_TIER = 'certified';

function receiptDigestLine(line) {
  const m = String(line ?? '').match(/\bdjb2=[^\s]+.*$/);
  return m ? m[0] : String(line ?? '');
}

/**
 * @param {object} args
 * @param {object} args.packet candidate packet
 * @param {{line:string, phase:string, pass:boolean, rung:string}[]} args.entries current receipt entries
 * @returns {{certified:false, reason:string}|{certified:true, rung:string, receiptLine:string, evidenceDigest:string, verdict:{verdict:string, reasoning:string, confidence:number, provider:string, tier:string}}}
 */
export function certifiedEquivalenceVerdict({ packet, entries }) {
  if (!CERTIFIED_PROOF_CLASSES.has(packet?.proof_class)) {
    return { certified: false, reason: `proof_class '${packet?.proof_class}' is not review-free` };
  }
  if (!Array.isArray(packet.evidence_required) || !packet.evidence_required.length) {
    return { certified: false, reason: 'no evidence_required rungs' };
  }

  const rungNames = packet.evidence_required.map((r) => r?.rung).filter((r) => typeof r === 'string' && r.trim());
  let target = null;
  if (typeof packet.certified_equivalence_rung === 'string' && packet.certified_equivalence_rung.trim()) {
    target = packet.certified_equivalence_rung.trim();
  } else {
    const inferred = rungNames.filter((r) => EQUIVALENCE_RUNG_RE.test(r));
    if (inferred.length !== 1) {
      return { certified: false, reason: inferred.length ? `ambiguous equivalence rungs: ${inferred.join(', ')}` : 'no deterministic equivalence rung' };
    }
    target = inferred[0];
  }

  if (!rungNames.includes(target)) {
    return { certified: false, reason: `certified equivalence rung '${target}' is not declared in evidence_required` };
  }

  const matches = (entries ?? []).filter((e) => e?.phase !== 'baseline' && e?.rung === target);
  const green = matches.filter((e) => e.pass === true);
  if (green.length !== 1) {
    return { certified: false, reason: green.length ? `ambiguous green receipts for '${target}'` : `no green post-change receipt for '${target}'` };
  }

  const receiptLine = green[0].line;
  const evidenceDigest = receiptDigestLine(receiptLine);
  return {
    certified: true,
    rung: target,
    receiptLine,
    evidenceDigest,
    verdict: {
      verdict: 'PASS',
      reasoning: `deterministic equivalence certified by rung '${target}' (${evidenceDigest})`,
      confidence: 1,
      provider: DETERMINISTIC_PROOF_PROVIDER,
      tier: DETERMINISTIC_PROOF_TIER,
    },
  };
}

export function deterministicProofVerdictLine(cert) {
  return `${DETERMINISTIC_PROOF_PROVIDER} PASS (tier ${DETERMINISTIC_PROOF_TIER}): ${cert.verdict.reasoning}`;
}

export function deterministicProofClaims({ packet, id, cert, receiptLines, receiptsDirRel }) {
  return [
    {
      type: 'behavior_preserved',
      statement: `${packet.proof_class} packet ${id} landed without model review because deterministic equivalence rung '${cert.rung}' passed`,
      evidence: [{ command: cert.rung, output_digest: cert.receiptLine }],
    },
    {
      type: 'judged_design_claim',
      statement: `review-free certified class (tier ${DETERMINISTIC_PROOF_TIER}): ${cert.verdict.reasoning}`,
      judge: { provider: DETERMINISTIC_PROOF_PROVIDER, verdict: 'PASS' },
      evidence: [{ command: cert.rung, output_digest: cert.receiptLine || receiptLines.find((l) => l.includes(`] ${cert.rung}:`)) || `see ${receiptsDirRel}/` }],
    },
  ];
}
