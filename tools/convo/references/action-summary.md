# Action-summary workflow

Use this workflow when the user asks what was done, requests an action-oriented session
summary, or asks for concise key events or epics.

## Retrieve evidence

1. Identify the physical session with `convo list` or `convo grep`.
2. Read the complete session with `convo show <id> --mode final --json`.
3. If the source reports truncation, corruption, or a compaction limitation, state that briefly.
4. Verify current state separately when it may have changed since the recorded session.

## Select content

Include only completed or materially attempted actions, such as code changed, artifacts
created, tests run, reviews completed, PRs changed, or resources cleaned up.

Exclude:

- user requests and intentions;
- general discussion, conclusions, advice, or background;
- progress commentary and repeated status checks;
- intermediate corrections unless they materially changed the resulting state;
- evidence details that are not needed to identify the action or its current state.

## Format

- Group actions into the fewest genuine workstreams; do not invent an epic for every topic.
- Use no epic headings when one list is clearer.
- Write one short, single-line bullet for each key action.
- Put only one action in each bullet; merge mechanical steps that produced one outcome.
- Keep bullets near 18 words and normally return 5–12 bullets; exceed 12 only when the user
  explicitly needs more detail.
- State the current state in the relevant bullet instead of adding a separate narrative recap.
- Do not add tables, nested bullets, rollups, or explanatory prose unless requested.

## Final check

Before answering, remove every bullet that describes only what happened, what was learned,
or what was recommended rather than an action someone took. Confirm every remaining bullet
is short enough to render as one logical line.
