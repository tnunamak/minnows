# Routing guidance (provisional)

This note says how to use `model-policy-ops` records when you choose a model. It
describes a recording procedure and its limits. It is not learned routing, and no
code here changes a policy or a default.

## Objective

The objective belongs to the owner who deploys this tool. This note does not set
one, and no code here encodes one. Write yours down before you read the audit, for
example: choose the option whose verified outcome justifies its total cost,
including review, repair and owner effort. Cost means more than tokens. Declare the
objective and any exploration budget ahead of time, so the records can test them.

## Complete procedures

Judge a *procedure*, not a single call: for example, a cheap maker plus an
independent checker. The ledger records **decisions**. A procedure with a maker and
a checker is at least two decisions, and nothing links them. Parent overhead and
nested subagent usage are unknown. So the audit cannot give the cost or quality of a
whole procedure, and it must not be read as one.

## Procedure

1. At launch, pass `--purpose` and `--proof-class` when you know them. Declare
   `exploration` before the work, not after a bad result.
2. When you integrate or discard a delegated result, run `close` with typed
   evidence. Use `unknown` rather than a guess.
3. When you touch the same work again, run `followup` with a stated scope.
4. Read the audit strata. Check close coverage first. A low coverage means the
   other numbers describe only the closed part.

## Limits

- Outcomes are caller claims. A parent that judges its own delegate leans optimistic;
  sample some closes with an independent judge.
- Evidence checks cover existence and hashes only.
- Arms are compared across tasks that were not assigned at random, so differences are
  confounded. The audit prints descriptions, not effects.
- Small strata say little. Read the counts before the rates.
- Follow-ups are sampled. Their absence is not evidence of quality.
