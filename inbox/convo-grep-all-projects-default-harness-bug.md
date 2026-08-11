# Bug: `convo grep --all-projects` (no `--harness` flag) returns FEWER results than explicit `--harness claude`

**Filed:** 2026-07-29
**Kind:** bug, `convo` (bin/.local/bin/convo, dotfiles-managed)
**Origin:** real friction, dotfiles session — searching for a specific past session by
keyword ("igpu") returned 0 useful hits with the documented default invocation, and only
surfaced the real session after manually forcing `--harness claude`.

## Problem

`convo`'s own skill docs state: `--harness claude|codex|gemini|all` (aliases `cc,cx,gm`;
**default all**). In practice, omitting `--harness` entirely does NOT search all harnesses
the same way `--harness all` (or a single explicit harness) does — it returns a smaller,
different result set.

## Reproduction

```
convo grep "igpu" --all-projects --since 60d | grep -v '^convo: corrupt' | wc -l
# 102 lines — the "default" invocation, no --harness flag

convo grep "igpu" --harness claude --all-projects --since 60d | grep -v '^convo: corrupt' | wc -l
# 105 lines — MORE results from claude ALONE than the supposed "all" default

convo grep "igpu" --harness codex --all-projects --since 60d | grep -v '^convo: corrupt' | wc -l
# 0 lines
```

If default-omitted `--harness` truly meant "all" (claude + codex + gemini), the omitted-flag
result should be >= the single-harness result (105), never less. It returned 102 — meaning
some real matches present under explicit `--harness claude` are silently dropped when the
flag is omitted, even though `--all-projects` (a separate flag) was set correctly in both
invocations.

## Impact

A user searching for a real, relevant past session (a Claude Code session actually named
`CC:47fd3267-7d5`, 2026-07-26, discussing bravo's iGPU passthrough risk on PVE 9) got **zero
useful hits** on the first attempt using the tool's own documented default usage, and only
found it after manually testing `--harness claude` explicitly out of suspicion the default
was broken. This directly defeats the tool's core value proposition ("find the session that
discussed X") for anyone trusting the documented default.

## Suspected cause (not verified — read-only investigation, no source dive done)

Possibly: the default `--harness` value used internally differs from what `--harness all`
explicitly resolves to (e.g. a stale/incomplete harness list constant used only in the
no-flag code path), or the omitted-flag path applies a different/narrower matching mode
than the explicit-`all` path. Worth checking `bin/.local/bin/convo`'s argument-parsing and
default-value logic for `--harness`.

## Suggested fix / verification steps for whoever picks this up

1. Confirm `convo grep <term> --all-projects` (no `--harness`) and
   `convo grep <term> --harness all --all-projects` produce byte-identical result sets for
   several test terms — they should, per the tool's own documented default.
2. If they diverge, trace whether the no-flag code path is missing codex/gemini entirely, or
   applying some other filter (date window, dedup, path exclusion) not present in the
   explicit-all path.
3. Add a regression test asserting default-omitted `--harness` and `--harness all` are
   equivalent, so this can't silently regress again.

## Reference

Session where this was found: dotfiles Claude Code session `bdac59f6-cce0-4bbb-8c9f-e58ff30c232b`,
2026-07-29, searching for the prior PVE-9-on-bravo iGPU risk assessment. Real session
eventually found: Claude Code `47fd3267-7d5e-45af-89dd-a8d2a493d248`, 2026-07-26 — confirmed
bravo is i9-12900T (Alder Lake), not the Comet Lake generation the PVE 9.1.1 iGPU-freeze
reports concerned; iGPU passthrough verified intact post-upgrade.
