# Data pack: `local-evals` (scaffold)

**Local, fixed-harness** success and cost evidence for `model-choice-policy` operating points.

This is the only path that upgrades `evidence_confidence: medium` to something earned on
Tim's actual waspflow / Claude Code / Codex / Grok surfaces.

## Status

**v0.0.1 scaffold** — no result rows yet. Do not invent P(success).

## Protocol (when running)

1. Pick an op (`implement.standard`, `review.audit`, …) and a model×effort cell.
2. Spawn a waspflow lane with that exact op/model/effort (or explicit flags).
3. Use a **fixed task** from `tasks/` with a deterministic oracle (tests pass, report exists, …).
4. Emit a performance-shaped score row into `results/`:

```json
{
  "model": "claude-sonnet-5",
  "metric": "local-implement.standard-oracle-v1",
  "score": 1.0,
  "unit": "pass_rate",
  "effort": "medium",
  "harness": "waspflow+claude_code",
  "task_family": "implementation",
  "source_type": "local_eval",
  "evidence_grade": "A",
  "observed_at": "2026-07-09",
  "cost": { "value": 0.42, "unit": "usd_per_task", "basis": "api_usd" }
}
```

5. Never judge a model with itself when an LLM judge is required.

## Get

Not released until first real results land. Pin catalog/policy tags in `pack.json` related.
