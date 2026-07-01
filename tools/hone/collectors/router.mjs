// router.mjs — ROUTER v1: the deterministic tier classifier, factored so every collector and
// `plan` route identically.
//
// PORTED from falsify.mjs route() / tier-mass-report.mjs routeTier() (they encode the same
// predicate; falsify adds the DELETE lane via caller counts). The router is a CONTROL component
// (maker≠judge fractal): it must NOT be an LLM with discretion. Bias is ASYMMETRIC by
// construction — every ambiguity ESCALATES (up-tiers), so v1 can be wrong-expensive but is
// engineered to avoid wrong-cheap.
//
// Tiers:
//   DELETE                  0 callers (dead, named fns only) — delete-not-refactor lane
//   T2-property             security markers in body OR transaction callback — strongest proof class
//   T2-async-order          >=2 awaits AND branching — awaited-effect ordering not mechanically decidable
//   T2-capturing-mutable    callback captures let/var bindings reassigned elsewhere — shared-cell hazard
//   T1-extractable-callback callback with zero true free vars — hoist to a named pure top-level fn
//   T1b-explicit-context    callback with immutable captures — implicit env → explicit context param
//   T1-seam                 named fn, cc >= seam_cc — seam choice is judgment, execution mechanical
//   T0                      residue — eligible to ATTEMPT a certified mechanical transform
//                           (routing eligibility, NOT proof — certification happens at work time)

/**
 * @param u  { cc, isCallback, callbackKind, secHits: string[], captures: string[],
 *             capturesMutable: string[], awaitCount, hasBranch, callers: number|null }
 *           callers is null when unknown/not-computed — DELETE only fires on a hard 0.
 * @returns { tier, why }
 */
export function routeFn(u, { seamCc = 12 } = {}) {
  const sec = u.secHits || [];
  const captures = u.captures || [];
  const capturesMut = u.capturesMutable || [];
  const awaits = u.awaitCount || 0;
  const branch = !!u.hasBranch;
  if (u.isCallback) {
    if (sec.length || u.callbackKind === 'transaction') {
      return {
        tier: 'T2-property',
        why: `callback${u.callbackKind === 'transaction' ? ' (DB txn)' : ''}${sec.length ? ' + security markers: ' + sec.slice(0, 3).join(',') : ''} → property may be nonlocal; strongest proof class`,
      };
    }
    if (awaits >= 2 && branch) return { tier: 'T2-async-order', why: `callback, ${awaits} awaits + branching → awaited-effect ordering not v1-decidable` };
    if (capturesMut.length > 0) return { tier: 'T2-capturing-mutable', why: `callback captures MUTABLE bindings [${capturesMut.join(',')}] → shared-cell hazard; extraction changes mutation semantics` };
    if (captures.length === 0) return { tier: 'T1-extractable-callback', why: `callback captures NOTHING (AST free-vars=∅) → hoist to a named pure top-level fn` };
    return { tier: 'T1b-explicit-context', why: `callback captures [${captures.slice(0, 4).join(',')}] (immutable) → turn implicit env into an explicit context param, then name it` };
  }
  if (u.callers === 0) return { tier: 'DELETE', why: 'no callers (dead) → delete-not-refactor lane' };
  if (u.secHits?.length) return { tier: 'T2-property', why: `security markers: ${sec.slice(0, 4).join(',')} → property may be nonlocal; strongest proof class` };
  if (awaits >= 2 && branch) return { tier: 'T2-async-order', why: `${awaits} awaits + branching → awaited-effect ordering not v1-decidable` };
  if (u.cc >= seamCc) return { tier: 'T1-seam', why: `cc ${u.cc} ≥ ${seamCc} → seam choice is judgment, execution mechanical` };
  return { tier: 'T0', why: `cc ${u.cc}, no security/async-order/seam signal → eligible to ATTEMPT a certified mechanical transform (routing does NOT prove Tier-0)` };
}
