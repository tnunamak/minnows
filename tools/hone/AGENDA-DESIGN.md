# hone agenda — judgment chooses, machinery proves (v2, PRESSURE-TESTED 2026-07-02)

Verdict on v1 draft (b317541): **BUILD WITH AMENDMENTS** — reached independently by a corpus-grounded
adversarial review and codex (gpt-5.5). All seven required amendments are incorporated below. The core move
survived the sharpest attack: the agenda-setter is NOT the "agent-derived budget" the ungameable-budget
research kills — the doctrine (rubric, budget, named targets) stays human-fixed and repo-committed; the
agenda SEQUENCES work under that gate. The corpus itself predicted the v1-planner failure this fixes: "the
weakness of the discovery tool becomes the budget's blind spot."

## The defect this fixes (measured)

The v1 planner is deterministic (mass×churn over lint flags), so the backlog became what the sensors could
see: small extractions. It missed the staff-engineer targets sitting named in the corpus (storage braid, auth
split, cc-194 dispatch hairball, prevention ratchet, skip audit). One strong-model call with sensor context
produced the correct list in one shot. Root cause: two correct anti-Goodhart rules (deterministic RISK ROUTER;
human-fixed BUDGET) were over-generalized into "selection must be mechanical" — which hard-coded the
streetlight bias it was meant to prevent. The expert v1 directive licensed the fix all along: "use strong
models for selection, concept judgment, high-risk review, and report audit"
(corpus: RAW-expert-v1-engine-directive.md §7 — captured verbatim after the pressure-test flagged it uncited).

## The principle

> **Intelligence at the front, mechanism at the back. Judgment for choosing; determinism for proving.**
> Sensors stay first-class (owner's tempering note: the mechanical instruments provided most of the valuable
> input) — collectors feed the chooser; they stop being the chooser.

## The honest downside model (v1 draft was wrong here)

A bad agenda does NOT merely waste tokens. **Omission/starvation is the primary harm class**: the gates only
see chosen work, so an agenda that starves what the owner values most leaves the repo worse without landing a
single bad diff (this exact harm — allocation drift with zero bad code — was the motivating failure). Every
control below exists for that harm.

## The design

### 1. `hone agenda --repo PATH` — selection ONLY (amendment 1: selection ≠ packetization)
One strong-model call, run on FIXED, VERSIONED, MECHANICAL triggers (never agent-discretionary — re-rolling
until an agent likes the agenda is agenda-shopping): batch completion · inventory-delta threshold · any
doctrine commit · campaign completion/stall · cost-overrun beyond band · aged NOT-chosen threshold.

**Inputs:** quality/inventory/*.json (sensors) · B-contract inventory + ratification queue · test signals
(mutation scores, skips, recorded gaps) · packet-pool state + cost-ledger actuals · the DOCTRINE (fixed) ·
prior AGENDA + selection ledger.

**Output — `quality/AGENDA.md` + `.json`:** ranked items where each = what · why-now with TYPED, CHECKABLE
evidence (sensor citation `file:metric`, corpus entry, B-inventory row, incident, recorded test gap — never
bare "judgment") · workflow class · acceptance criteria · est cost. Plus: campaign entries (named target +
acceptance criteria — NOT packet specs); NOT-chosen with reasons (persisted, AGED with counters across
agendas); deltas from prior agenda; a HUMAN-DECISIONS-NEEDED section.

The agenda does NOT author packets. A SEPARATE packet-authoring step (worker or later `hone decompose`)
produces packet specs from agenda items; the deterministic risk router runs on the final packets as always
(packet slicing is indirect tier choice — "the maker cannot choose the tier" extends to the chooser). A
deterministic decomposition-coverage check verifies a campaign's packet graph actually reaches its named
target (no endless prep-work).

### 2. Deterministic verification of the agenda's own claims (amendment 2)
A script re-derives every cited `file:metric` against quality/inventory/*.json. Items whose citations fail to
reproduce are marked UNVERIFIED and CANNOT outrank verified items. The budget-composition line is COMPUTED by
the report compiler from AGENDA.json + the cost ledger — never asserted by the model ("the report is compiled
from the ledger, not written as prose").

### 3. Fail-loud thresholds, not alarm-churn and not silence (amendment 3)
Default: one passive report line (realized spend vs agenda allocation). BUT threshold-crossing divergence
FAILS LOUD as a flag requiring owner acknowledgment: a doctrine-named target starved N consecutive batches
(default 3), or class allocation outside the doctrine band M consecutive batches (default 3). (The corpus
rule stands: "divergence is a flag, not a re-weighting opportunity." The v1 draft's "never an alarm loop"
deleted a corpus finding; the killed thing was the CONTINUOUS drift alarm, not fail-loud itself.)

### 4. Blind challenger on fixed triggers (amendment 4 — challenges, never certifies)
Every Nth agenda (default 4th) or on any doctrine-target demotion or threshold divergence: a DIFFERENT model
family produces a blind agenda (pulls its own inputs from ledgers+inventory; never handed the incumbent
agenda — the "please review my summary" packet is the rejected weak form). Diff the two; reconciliation
required only on major rank divergence, doctrine-target demotion, or repeated omission. Event-triggered and
rare — this is a challenger, not a second planner in the loop.

### 5. Non-starvation machinery (amendment 5)
- NOT-chosen entries persist and age with counters; aged items trigger the agenda re-run trigger.
- Doctrine-target DEMOTION is a first-class ESCALATION: surfaced in HUMAN-DECISIONS-NEEDED, logged, counted
  as divergence, and carries an EXPIRY after which the target auto-repromotes ("a debt item, not a
  disappearance"). Doctrine names targets, not timing — but timing debt is visible and bounded.
- Deterministic floor in `hone run` (outside the chooser's authority): negative-control class + seeded traps
  are non-droppable; in-flight campaigns finish before agenda thrash can abandon them; an aged-omission
  minimum share per batch.

### 6. Selection ledger (amendment 6 — meta-evaluate the chooser)
Per agenda item: predicted gain/cost/class vs realized outcome, appended per batch. The judge literature is
unambiguous that a single strong-model judgment reused without meta-evaluation is untrustworthy (JudgeBench:
"many strong models only slightly better than random"); the one good one-shot agenda is n=1. This ledger is
the design-gate-ledger discipline applied to selection: it scores the CHOOSER, not the code.

### 7. The deterministic rank survives (amendment 7)
The churn×complexity formula remains: (a) the default order WITHIN a class of comparable packets, and (b) the
challenger baseline — the diff between formula-rank and agenda-rank is a free streetlight-bias sensor,
rendered in the report.

## The judgment-vs-mechanical line (from the experts' own words; condensed)

| Decision | Who | Basis |
|---|---|---|
| Doctrine: objective, budget, named targets | HUMAN only, repo-committed | ungameable (A); human_ratifies_budget |
| Work selection / campaign composition | JUDGMENT — challenged, floored, meta-evaluated | expert v1 directive §7; orchestrator-audit (sequencing challenged BEFORE work) |
| Rank within a class | MECHANICAL default; judgment overrides only w/ typed evidence | discovering-entry Stage 1 |
| Proof-class assignment (risk router) | MECHANICAL, hard rule | "router is deterministic by default" (expert6 r2 §7) |
| Tier choice / lowering | FORBIDDEN to any agent | "maker cannot choose the tier; orchestrator cannot lower it" |
| Packet authoring | Judgment, SEPARATE call; router re-runs after | ROUTE/PROVE/LAND/AUDIT separation |
| Change acceptance | MECHANICAL oracle + different-model judge | SLVPQ GATE; R3 |
| Budget accounting / tallies | MECHANICAL (report compiler over ledgers) | prose must not outrun evidence |
| Doctrine-target demotion | Judgment PROPOSES; human decides (escalation+expiry) | "divergence is a flag" |
| Calibration channel (controls/traps/random escalation) | MECHANICAL, outside chooser authority | "seeded traps exist" (expert6 r2) |
| Agenda re-run trigger | MECHANICAL, versioned | Matton selection-pressure |
| Meta-eval of chooser & judge | MECHANICAL ledger + human sample | expert6 r2 recipe |

## Build scope (v1 of agenda; bounded — no cathedral)
`hone agenda` (context assembly + one strong call + artifact emit + citation-verifier) · AGENDA-consumption in
`run` (agenda order + the deterministic floor) · NOT-chosen persistence/aging · selection-ledger logging ·
threshold flags in report · challenger as a manually-invoked `hone agenda --challenge` (automated triggers
documented, wired later). Packet-authoring stays a worker task fed by agenda items for now.
