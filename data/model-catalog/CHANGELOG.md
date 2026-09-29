## v0.5.9 — 2026-09-28

- Added source-backed Claude Sonnet 5.5 model, API pricing, ordered name patterns, and separate Claude API / Claude Code effort surfaces.
- Added the launch-post benchmark headline table, exact per-effort chart values from four SVGs, and all numeric cells from System Card Table 8.1.A.
- Reused metric IDs and isolated publisher/harness differences with distinct comparability groups; added SWE-Bench Multilingual and Multimodal metric definitions.
- Updated Hone's Claude model registry and current Sonnet 5 token pricing. The Sonnet 5.5 Hone model has no calibration and cannot route yet.

# Model catalog changelog

## v0.5.8 — 2026-09-23

- Added source-derived snapshot IDs to third-party board and vendor-table score rows and validated snapshot/group consistency across files.
- Normalized the Z.AI GLM-5.3-Flash launch-table rival rows into their shared snapshot groups.
- Recorded effort from vals.ai Terminal-Bench 4.0 and SWE-bench payload fields; marked rows with no stated effort as `vals default`.
- Added Gemini 3.8 Flash model-card rivals at the card's published precision and recorded effort as unattributed where the card does not state it.
- Separated AA's estimated Haiku 4.5 index row from the measured group.
- Added documented Qwen capability tiers and recorded the local Qwen Code 0.21.1 CLI flag surface.
- Kept historical DeepSeek V4 Flash identities distinct; pinned mutable `deepseek-v4-pro` to `deepseek-v4-pro-0813`.
