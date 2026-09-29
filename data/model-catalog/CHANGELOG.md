# Model catalog changelog

## v0.5.9 — 2026-09-28

- Added Claude Sonnet 5.5 to the model registry and recorded launch-day Artificial Analysis Intelligence Index scores and per-task costs for low through max effort.
- Added Artificial Analysis max-effort Terminal-Bench 4.0 and Vals AI Terminal-Bench 4.0 / Terminal-Bench 2.1 observations with same-board peer rows where directly surfaced.
- Added provisional launch-day coverage checks for Terminal-Bench, ARC Prize, DeepSWE, SEAL/SWE-bench, Arena, Steel BrowseComp, Epoch AI, and OpenRouter.
- Left per-evaluation AA chart extraction and complete requested peer rosters under explicit missing notes rather than inferring values.


## v0.5.8 — 2026-09-23

- Added source-derived snapshot IDs to third-party board and vendor-table score rows and validated snapshot/group consistency across files.
- Normalized the Z.AI GLM-5.3-Flash launch-table rival rows into their shared snapshot groups.
- Recorded effort from vals.ai Terminal-Bench 4.0 and SWE-bench payload fields; marked rows with no stated effort as `vals default`.
- Added Gemini 3.8 Flash model-card rivals at the card's published precision and recorded effort as unattributed where the card does not state it.
- Separated AA's estimated Haiku 4.5 index row from the measured group.
- Added documented Qwen capability tiers and recorded the local Qwen Code 0.21.1 CLI flag surface.
- Kept historical DeepSeek V4 Flash identities distinct; pinned mutable `deepseek-v4-pro` to `deepseek-v4-pro-0813`.
