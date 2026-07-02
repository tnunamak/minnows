# hone agenda — judgment chooses, machinery proves (DRAFT for adversarial pressure-test, 2026-07-01)

## The defect this fixes (measured, not hypothetical)

The v1 planner is deterministic (mass×churn over lint flags), so the machine's backlog became exactly what
its sensors could see: small extractions (the low-mass tail). It missed every target a staff engineer names
in one glance at the same data: the dual-backend storage braid, the auth substrate split, the cc-194 dispatch
hairball, the anonymous-callback generation ratchet, the skipped-test audit. The owner proved the
counterfactual: ONE strong-model call with good context produced the correct priority list, ungated.
Meanwhile the proposed corrective (a budget tripwire alarming on pool drift) would churn: slow noisy signal,
alarms during legitimate transitions, react-restock loops.

Root cause: two correct anti-Goodhart rules — the RISK ROUTER must be deterministic (an LLM choosing its own
oversight = self-grading) and the BUDGET/rubric must be human-fixed (an agent authoring its own rubric games
it) — were over-generalized into "selection should be mechanical." Selection is neither self-grading (every
change still passes the gates) nor rubric-authoring (the doctrine stays fixed). The deterministic planner
didn't prevent "the agent picks work its tooling shines at" — it HARD-CODED it, because its vocabulary WAS
the tooling. The expert directive even said "use strong models for selection"; v1 didn't implement it.

## The principle

> **Intelligence at the front, mechanism at the back. Judgment for choosing (errors cheap to audit by
> reading, non-catastrophic — bounded by the change gates). Determinism for proving (errors expensive to
> find, catastrophic if silent).**

The owner's tempering note (verbatim intent): the mechanical instruments provided most of the valuable input
to the good one-shot answer. **Sensors stay first-class.** What changes is who DECIDES: collectors feed the
chooser; they stop being the chooser.

## The design

`hone agenda --repo PATH` — ONE strong-model call (agenda-setter), run RARELY (per batch, or when inventory
materially changes), never per-packet.

**Inputs (the sensor array + fixed anchors):**
- quality/inventory/*.json (tier-mass, callback smells, hotspots/churn) — the deterministic collectors
- the B-contract inventory + ratification queue (product-surface findings)
- test-suite signals (mutation scores where known, skip counts, coverage gaps recorded in packets/ledgers)
- the packet pool state + cost-ledger actuals (what's pending/landed/skipped and what things really cost)
- the DOCTRINE (fixed, repo-committed): objective, budget direction (B 40-50 / A2-high-attention 30-40 /
  T1 10-15 / T0+prevention 5-10), the named expert targets, the non-negotiables
- prior AGENDA.md (for diff/stability — the agenda explains what changed and why)

**Output — `quality/AGENDA.md` + `.json` (the artifact IS the interface):**
1. Ranked agenda (≤1 page): each item = what, why-now (MUST cite sensor evidence — file:metric, not vibes),
   expected quality gain, which workflow, est cost.
2. Budget-composition line: how the agenda allocates vs the doctrine budget (visible, not an alarm).
3. Campaign decompositions: multi-packet structural jobs (storage-unify, auth-split, handleMsg) broken into
   packet graphs with depends_on; behavior-changing campaigns marked owner_ratify at CAMPAIGN level.
4. "NOT chosen and why" — the negative selections with reasons (anti-streetlight; makes the chooser's
   judgment auditable in the same glance).
5. Deltas from previous agenda (stability signal; thrash is visible).

**Consumption:** `hone run` selects packets in AGENDA order (agenda references packet ids; missing packets
get authored — by the agenda call itself when small, by a packet-authoring worker when large). The planner's
deterministic packets remain one input pool the agenda draws from. Persisted `priority` becomes the
agenda-assigned rank, not the formula's.

## Anchors (why this doesn't reintroduce the orchestrator-Goodhart problem)

- The DOCTRINE stays human-fixed and repo-committed; the agenda-setter cannot edit it (any doctrine change =
  owner-reviewed commit). It answers TO the rubric; it does not author it.
- The artifact is legible and skimmable by the owner in ~1 minute; every item cites sensor evidence; the
  NOT-chosen section exposes streetlight bias directly.
- Selection errors are bounded: every chosen change still passes oracle + independent judge + touchset +
  honesty gates. A bad agenda wastes tokens; it cannot land bad code.
- Agenda-setter ≠ maker ≠ judge (three different calls; agenda runs at a different cadence entirely).
- The report gains ONE passive line (realized spend vs agenda allocation) — information for the owner's
  glance, NOT an alarm loop.

## What stays mechanical (unchanged)

Collectors/sensors; the per-change risk router (proof-class assignment — deterministic, ambiguity escalates);
all gates (oracle, judge, touchset, no-new-skips, honesty); ledgers; report compilation.

## Open questions for the pressure test (attack these)

1. Does the agenda-setter reintroduce "picks work its execution shines at" one level up (choosing packet
   shapes the maker lands easily, avoiding hard campaigns)? Is evidence-citation + NOT-chosen + owner-glance
   sufficient, or is a periodic independent re-derivation (second model produces a blind agenda; diff them)
   warranted — and is that worth the cost, or is it tripwire-churn reborn?
2. Cadence: is per-batch too often (thrash) or too rare (stale after big lands)? What triggers a re-run?
3. Should the agenda be able to DEMOTE doctrine-named expert targets (e.g. "storage-unify not now because X")?
   Proposed: yes with mandatory rationale in NOT-chosen — the doctrine names targets, not their timing. Sound?
4. Failure mode when sensors and judgment disagree (agenda wants something sensors score cold): allowed with
   citation of NON-sensor evidence (corpus/product), or sensor-bounded?
5. Is AGENDA-orders-run too much authority — should run keep any deterministic floor (e.g. always include
   the negative-control class, always finish in-flight campaigns first)?
