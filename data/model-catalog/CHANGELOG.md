## v0.5.17 — 2026-10-28

- Moved the cost-vs-intelligence chart to Artificial Analysis Intelligence Index v5.0 (`guide.json` `hero.metric_id`). AA published v5 on 2026-10-27. v5 values are not comparable with v4.3.2 values, so the v4.3.2 chart is kept under `charts/historical/` and is not the current chart. The change moves each setting's score by method, not by model performance.
- Added `performance/artificial-analysis-intelligence-index-v5-full-2026-10-28.json`. It holds 63 Index rows (score; 61 also carry AA's per-task USD) and 558 per-eval rows for catalog settings. AA's cost is a benchmark-task cost. It is not a cost per accepted harness task. The 2026-10-28 partial v5 snapshot is unchanged.
- Added five metric ids for v5 evals that had none: `automationbench-aa`, `frontiercode-2-0`, `hle-diamond`, `critpt-v2-hard`, `aa-omniscience-v1-1`.
- Recorded 52 explicit gaps in the snapshot's `missing[]` (30 no AA object for this catalog id or effort; 11 AA object exists, no Index value; 7 blank or unknown catalog effort, not mapped; 3 identity not established (different version or snapshot); 1 other). Each is listed in `AA-INDEX-SNAPSHOTS.md`.
- Gemini 4 Carbon has a v5 value in AA's roster but no catalog id, so it is recorded as a gap and not added as a setting.
- Unresolved: AA's 2026-10-26 Terra article quotes $0.40 per task for GPT-6.1 Terra at max. The 2026-10-28 roster gives $0.96 at max and $0.40 at xhigh. The stored value is the roster's $0.96 at max; the $0.40 at max is not reproduced.
- Unresolved: AA's article gives GPT-6 Astra's CritPt v2 hard subset as 72%, and the roster gives 70.5%. The roster value is stored.
- No provider token rate changed. No routing pick changed in `model-choice-policy`; that pack pins this catalog tag.

# Model catalog changelog

Newest first. The README links here; this file is the single record.

## v0.5.16 — 2026-10-28

- Added OpenAI GPT-6.1 Terra (`gpt-6.1-terra`, tier `terra`, status `ga`, released 2026-10-26 per the OpenAI API changelog). Standard API price is $1.00 input, $0.05 cached input and $5.00 output per M tokens; cache writes are $1.25 for 30 minutes and $2.50 for 12 hours. Above 272K input tokens the rates are $2.00, $0.10 and $7.50, with cache writes of $2.50 and $5.00, and the `long_prompt` tier bills the whole request. The row is in `pricing/openai-api-2026-07.json`; no rule before it matches the id. The model has `api` and `codex_cli` effort surfaces. Its API default is `medium`, and its Codex default is `low`.
- Added Artificial Analysis Intelligence Index v5.0 and Terminal-Bench 4.0 readings for GPT-6.1 Terra and GPT-6.1 Sol at each effort level (grade B), in `performance/artificial-analysis-intelligence-index-v5-2026-10-28.json`. v5.0 has its own metric id and comparability group. AA's v5 article does not say whether v5 scores are comparable with v4.x: GPT-6.1 Sol's low-effort Intelligence score is 42.1 on v4.3.2 and 35 on v5.0.
- Recorded disagreements without reconciling them. AA's 2026-10-26 Terra article gives $0.40 per task at max effort; its 2026-10-27 v5 article gives $0.96 per Index task at max, against $1.27 for Sol. DeepSeek's 2026-09-10 announcement says `deepseek-v4-pro` requests route to V4.1-Flash rates from 2026-09-14; the same day's change-log entry says V4 Pro API service continues with billing unchanged. The `deepseek-v4-pro-0813` notes state the conflict.
- Added the `DeepSeek-V4.1-Flash-1017` alias to `deepseek-v4-1-flash`. DeepSeek's 2026-10-17 update says the `deepseek-flash` id now serves that snapshot and that the calling method and pricing are unchanged, so no separate row was added.
- Set `gemini-3.5-flash` and `gemini-3.7-flash` to `historical`. Google's Gemini changelog dates both deprecations to 2026-10-08. Requests route to `gemini-3.6-flash` and `gemini-3.8-flash` respectively, and neither id is selectable in Google AI Studio.
- Did not add Gemini 4 Carbon. Google announced it on 2026-10-28 with a staged rollout to cyber defenders, then trusted partners. Its post gives no model id, API availability, context window or effort setting, and the Gemini changelog has no entry for it. Google reports 59 on AA's Intelligence Index v5.0; AA's own score is not in the text read. Add a row when Google publishes an id.
- Recorded that OpenCode's `deepseek-v4.1-flash-free` route is free "for a limited time" with no end date as of 2026-10-28. OpenCode lists `gemini-3.8-flash` at $1.50 input and $7.50 output, which matches Google's 2027 rate; Google's current rate is $0.75 and $3.75 through 2026-12-31.

## v0.5.15 — 2026-10-13

- Added Google Gemini 4 Argon (`gemini-4-argon`). Google announced it on 2026-09-30 for trusted cyber defenders and made it generally available on 2026-10-13; the Gemini API changelog calls it generally available, so the row has status `ga`. It has a 1,048,576-token context shared by input, thinking and output, and text, image, video, audio and PDF input. It gets a new tier, `argon`, because Google gives it no Pro or Flash label; the models schema and `SCHEMA.md` list the new value.
- The API price is an introductory $2.00 input, $0.10 cached input and $10.00 output per M tokens through 2026-11-30, then $4.00, $0.20 and $20.00 from 2026-12-01. The row is in `pricing/google-api-2026-07.json`, ahead of its catch-all `gemini` rule, which would otherwise price Argon like Gemini 3.1 Pro; the rule matches routed ids such as `google/gemini-4-argon` too. The row carries both rates, and its freshness check will fail after the intro ends until the row moves to the standard rate. Batch, Flex and Priority prices and the Vertex AI differences are in the file's notes.
- Thinking levels are low, medium (the default) and high. Thinking cannot be turned off, and `thinkingBudget` is deprecated for Gemini 4. Every board reads Argon at high effort; no board lists low or medium.
- Added Google's launch table (19 rows, four models) and model card, including the Frontier Safety Framework results as stated claims. Added fresh Artificial Analysis (Intelligence Index 52.56, $1.99 per task at the intro price), Artificial Analysis Coding Agents, Vals (18 boards plus seven more), Arena (Text, Text Coding, WebDev), Epoch and OpenRouter readings. Terminal-Bench, ARC Prize, Datacurve DeepSWE, SEAL, SWE-bench, METR and OSWorld do not list Argon yet (recheck 2026-10-20).
- Google and the boards disagree. Google reports DeepSWE 1.1 at 77.9%; Artificial Analysis' own run gives 78.76%. Terminal-Bench 4.0 reads 57.4% (Google), 57.07% (Artificial Analysis), 57.58% (Vals) and 56.06% (Coding Agents Index). Google says Argon is "similarly leading" on Harvey's Legal Agent Benchmark, while the Vals board has it at 19.58%, below Muse Wonder Full and Muse Spark 1.3. Artificial Analysis' launch article and its live page differ on Briefcase (1494 Elo against 1488.47). All of these are stored, not reconciled.
- Added 24 metrics (Arena WebDev, seven Epoch and seven Vals boards, and the Argon launch-table benchmarks).
- Hone is unchanged: it has only Claude and Codex provider paths.
- No routing change. Opus 5.5, GPT-6.1 Sol and the other policy models keep their operating points.

## v0.5.14 — 2026-10-13

- Added Meta Muse Wonder (`muse-wonder-1-0`, released 2026-10-12) and Muse Wonder Full (`muse-wonder-full-1-0`). Meta's developer docs call Muse Wonder a preview, so both rows have status `preview`. Muse Wonder is in the Meta Model API and Muse Code. Muse Wonder Full is only in a Muse Code subscription, so it has no API price; `models.json` gets a new `access: subscription_only` value for it.
- Muse Wonder costs $4.00 input, $0.20 cached input and $20.00 output per M tokens, with a 1M-token context. Meta publishes no cache-write price and no long-context premium. The new pricing file also holds the Muse Spark 1.3 Standard and Contributor prices, and the Muse Code plan prices in its notes ($50 Power and $200 Maximum a month; the $5 Everyday and $15 High prices come from one page).
- The API accepts efforts from minimal to max, and `none` returns an error. Meta names no default. Every published Muse Wonder score is at max effort, except one SWE-Marathon cell at xhigh. No board lists a lower effort.
- Added Meta's launch table (14 benchmarks, 5 models) and the model card's risk tables. Added Artificial Analysis rows (Intelligence Index 55.8 for Full and 52.4 for Muse Wonder, v4.3.2), Vals (Index 64.6% and 61.1%, 18 benchmarks), SWE-Marathon, the Artificial Analysis Coding Agents Index, OpenRouter and Epoch. Terminal-Bench, ARC Prize, Datacurve DeepSWE, SEAL, Arena, SWE-bench and METR do not list the models yet (recheck 2026-10-20).
- Meta's numbers and the independent boards disagree. Meta reports DeepSWE 1.1 at 79.0 (Full) and SWE-Marathon at 57.5 (Full, xhigh); the boards' own runs give 77.0 and 53.1 (max). Meta's Muse Wonder SWE-Marathon value (53.1) equals the board's value for Full. Both sets are stored, not reconciled. Artificial Analysis has no cost for Full, and Vals prices it at Muse Wonder's list rate.
- Vals now lists Claude Haiku 5.5 (Index 54.3%). Artificial Analysis re-rated its two Elo evals after adding the Muse models, so GDPval-AA peer scores moved by up to 30 Elo; the Index values did not change.
- Added 26 metrics (for example `personal-superintelligence-index`, `memorybench`, `jobbench`, `swe-marathon`, `focusbench`). Meta's AA-style Index is stored under its own id, `artificial-analysis-intelligence-index-v4-3-meta-reported`, because Meta's version label and numbers differ from Artificial Analysis'.
- Hone is unchanged: it has no Meta provider path.

## v0.5.13 — 2026-10-11

Data corrections from reviewed fix PRs (#42–#44, #47–#49, #51–#53). No new models or sources.

- Pricing lookup order: the tables use the first matching substring, so specific rows now come before shorter family rules. Fable 5.1 and Mythos 5.1 reach their own cache-read rate ($0.25 per M, not $1); Gemini 3.8, 3.7 and 3.6 Flash no longer fall back to the generic Pro row, and 3.5 Flash Lite no longer matches standard Flash; `glm-5.3-flash` no longer matches `glm-5.3`.
- The 21 vendor-table rows in the Opus 5.5 system card move from grade B to C (vendor-reported). Their caveats already said "not independent evidence".
- After deduplication, the remaining Arena overall rows and the official Terminal-Bench rows use the same snapshot identity as the retained rows, so each board stays one comparison group.
- Six deduplicated Vals Terminal-Bench 4.0 rows record effort `max` (from the board payload) instead of `unknown`. The generated README tables gain the rows this attributes.
- The performance schema no longer defines sample size and the 95% bounds twice; the effective contract is unchanged.
- The full-pack download examples in this README use the current pack tag.

## v0.5.12 — 2026-10-07

- Added Claude Haiku 5.5 (`claude-haiku-5-5`, GA 2026-10-07) as the current Haiku-tier model. Claude Haiku 4.5 stays GA: Anthropic lists it as active with no deprecation date.
- Anthropic prices Haiku 5.5 by prompt length, so pricing rows have a new optional `long_prompt` object: $0.10 / $0.50 per M tokens (input / output) up to 100K prompt tokens, and $0.50 / $2.50 above that. The new tokenizer makes the same text about 30% more tokens than on Haiku 4.5. At list price the saving is 90% below 100K tokens and 50% above; Anthropic's "around 75% less on average" cannot be reproduced from list prices because it depends on workload mix. Readers that use only the four base rate fields under-price long prompts 5x.
- Haiku 5.5 is the first Haiku with an effort setting. The default is medium in both the API and Claude Code. Recorded as separate API and Claude Code surfaces: the API can turn thinking off at high effort or below; Claude Code cannot.
- Added the launch table (29 scores), 59 exact effort-curve points from the launch charts, and 103 system-card values. An independent check against the live pages found no mismatches. OSWorld 2.1 is recorded as partial credit (72.4%) and strict pass rate (37.1%) on the 82-task offline subset. At max effort Haiku 5.5 costs more per task than Haiku 4.5 on three of four charts; at medium it costs less on all four.
- Sonnet 5.5 cache read dropped from $0.20 to $0.10 per M tokens on 2026-10-07. Anthropic's pricing table cell and the Sonnet 5.5 model page still show $0.20 (recheck 2026-10-14).
- Added Artificial Analysis rows for Haiku 5.5 at five efforts (Intelligence Index v4.3.2: 29.4 at $0.025 per task for low, up to 43.4 at $0.213 for max) with same-snapshot peers, and a coverage check of other boards. Vals AI, tbench.ai, ARC Prize, DeepSWE, SEAL, Arena, Steel, Epoch and OpenRouter token share do not list Haiku 5.5 yet (recheck 2026-10-14).
- Added preview models `mistral-large-4` and `step-5-preview` with first-party pricing, and `glm-5.3-flash` pricing. Corrected the `glm-5.3-flash` release date to 2026-08-26, the Kimi K3 API default effort to max and its cache write to $3.00, and set the Codex CLI default effort for `gpt-6.1-sol` to low (one host only).
- Re-verified pricing for OpenAI, Google, xAI, DeepSeek, Alibaba, Moonshot, Z.AI, MiniMax, Xiaomi and Mistral on 2026-10-07; most rows are unchanged.
- Added board snapshots: Vals Terminal-Bench 4.0 (re-scored 2026-10-06; every value moved up), Vals Index and Finance Agent, the Artificial Analysis leaderboard, ARC Prize, tbench.ai, Arena text and coding, and Epoch scorecards. Done: the GPT-6.1 Sol board recheck. GPT-6.1 Sol is now on Vals, tbench.ai, ARC Prize, Arena and Epoch; DeepSWE, SEAL and Steel do not list it yet.
- Gemini 4 Argon is recorded as announced for trusted cyber defenders only, with no API id or price; it is not in `models.json`.
- Added the generated charts. A new cost-vs-intelligence chart plots Artificial Analysis Intelligence Index against cost per task, one line per model across effort levels, with a data table under it. `scripts/render_model_catalog_charts.py --check` runs in CI and fails when the charts are stale. Added 18 display `name` values to `models.json`, and `guide.json` and its schema.
- Added metrics `osworld-2-1-strict`, `physicianbench` and `vals-index`. Hone gets a Haiku 5.5 entry with no calibration, so it cannot route yet.
- Known issue: Humanity's Last Exam has several metric ids for the same benchmark from different sources (for example `humanitys-last-exam-no-tools-hle` and `humanity-s-last-exam-no-tools`), so scores from different publishers do not join yet.

## v0.5.11 — 2026-09-30

- Corrected the GPT-6.1 Sol `codex_cli` surface note. codex-cli 0.159.2 lists `gpt-6.1-sol` in `codex debug models`, and a local `codex exec` run served it (the rollout records `gpt-6.1-sol`). The previous note came from codex-cli 0.156.1, which did not list it. The CLI default effort remains unverified.

## v0.5.10 — 2026-09-29

- Added OpenAI GPT-6.1 Sol (`gpt-6.1-sol`) to the GPT-6 family and Sol tier, with launch date, standard API pricing, and API/Codex capability surfaces.
- Recorded launch-post claims and selected printed system-card results using existing metric IDs. The exact launch-page HTML table and complete SVG effort curves could not be retrieved because direct HTML requests returned HTTP 403; those values remain explicitly missing.
- Updated Hone with a GPT-6.1 Sol entry and `calibration: null`, so it cannot route. GPT-6 Sol remains GA; no routing-policy changes.
- Added Xiaomi MiMo-V2.6 Pro and Flash as public API models with first-party standard USD rates, cached-input rates, API reasoning documentation, and retrieved Arena / Artificial Analysis rows.
- Recorded StepFun Step 5 Preview as an unresolved frontier gap because the first-party API example uses a placeholder model ID; recheck on or after 2026-10-13.
- Added Artificial Analysis rows for GPT-6.1 Sol at five efforts with same-snapshot comparison rows, and a board coverage file that records provisional absence (recheck 2026-10-06) for boards that do not list it yet.

## v0.5.9 — 2026-09-28

- Added source-backed Claude Sonnet 5.5 model, API pricing, ordered name patterns, and separate Claude API / Claude Code effort surfaces.
- Added the launch-post benchmark headline table, exact per-effort chart values from four SVGs, and all numeric cells from System Card Table 8.1.A.
- Reused metric IDs and isolated publisher/harness differences with distinct comparability groups; added SWE-Bench Multilingual and Multimodal metric definitions.
- Updated Hone's Claude model registry and current Sonnet 5 token pricing. The Sonnet 5.5 Hone model has no calibration and cannot route yet.

# Model catalog changelog
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

## v0.5.7 — 2026-09-23

Adds xAI Grok 4.7 and updates Google/xAI catalog metadata from current vendor sources. Adds Google and xAI `tier` values across registered models; adds Grok 4.7 API pricing and API/Grok CLI surfaces; records authenticated local `agy models` per-effort availability for Gemini 3.8/3.7/3.6 Flash and 3.1 Pro. Adds Google Gemini 3.8 Flash and 3.1 Pro model-card tables, xAI's Grok 4.7 launch table (including effort labels verbatim), and additive direct-fetch AA/vals.ai rows. Google confirms Gemini 3.8 Flash GA, while Gemini 3.1 Pro remains Preview. The public docs do not expose a Deep Think API model ID. See `data/model-catalog/performance/google-xai-board-snapshot-2026-09-23.json` for the exact board reads and remaining un-fetched coverage.

Data repair: records all 70 DeepSWE v1.1 configurations with per-row effort, pass@1, pass@4, cost, n, and confidence bounds. Adds vendor table comparison cells and their stated effort levels; merges the split vals.ai TB4 and AA v4.3.2 comparability groups; and validates one group per explicit snapshot and metric. Preserves full AA payload precision, MiniMax M3's billed discount, DeepSeek's historical V4 Flash identity, Qwen thinking-toggle surfaces, and source-backed release dates.

Open-weight and other-lab frontier:

- Add DeepSeek V4.1 Flash, V4 Pro-0813, and historical V4 Flash-0731; API pricing and exact vendor effort mappings.
- Add Kimi K3 (2.8T weights under the custom Kimi K3 license); its official announcement is dated July 22, 2026, not July 17.
- Add GLM-5.3-Flash vendor launch scores; add MiniMax M3/M2.7 and direct token pricing; record Meta Muse Spark 1.3 as preview; add Mistral Medium 3.5 and Small 4 pricing/effort facts.
- Confirm Qwen3.8-Max is stable/current and that qwen3.8-max-0902 is its updated snapshot. Mistral Large 3 is listed in the vendor pricing page’s Flagship section; Medium 3.5 is its featured frontier-class model and Small 4 is the lower-cost open-weight option.
- H3 Max is a video-generation model (priced per second), not the MiniMax text/coding flagship; use M3 for frontier coding/agent comparison. No separate version bump in this lane.
- Third-party coverage now includes same-day Artificial Analysis model-page rows, vals.ai Terminal-Bench 4.0/SWE-bench rows, a Muse Spark 1.3 LMArena Elo row, vendor launch tables, and OpenRouter prices. DeepSWE, official tbench.ai, ARC Prize, and broader LMArena candidate coverage remain open and are listed in `missing[]`.

## v0.5.6 — 2026-09-22

Adds GPT-6 Sol and GPT-6 Luna (launched 2026-09-22, 19 days after GPT-6 Astra on 2026-09-03) across the full pack: catalog/pricing/effort surfaces, the launch-post headline table, the launch-post's 11 digitized effort×cost curves, the GPT-6 Astra system card's safety/preparedness suite (incl. a Sol/Luna appendix), and third-party board coverage. Integrated from four parallel lanes into one branch, reconciled by hand.

- **Catalog core:** `models.json` entries for `gpt-6-sol`/`gpt-6-luna` (family `gpt-6`, GA). API USD pricing (`pricing/openai-api-2026-07.json`: sol $2.00/$0.20/$2.50/$10.00, luna $0.10/$0.01/$0.125/$0.50 — short-context, input/cached/cache-write/output per 1M, same 1.25×/0.1× cache-write/read ratio as gpt-6-astra); Codex credit pricing (`pricing/codex-credits-2026-07.json`: sol 50/5/0/250, luna 2.5/0.25/0/12.5 credits per 1M). GPT-5.6 Sol/Terra/Luna and gpt-6-astra rates re-verified UNCHANGED against the same pages on this date — no rate changed today except the two new additions. Fixed a pre-existing gap in `codex-credits-2026-07.json`: `gpt-6-astra` had a `models{}` row but no `match[]` rule; added alongside the new sol/luna rules.
- **New `tier` field** on the L0 model registry (`models.json`, documented in `SCHEMA.md`, new `schemas/models-v1.schema.json` — documentation contract, not enforced under `--require-jsonschema` since `models.json` is stdlib-validated): "capability tier — vendors ship concurrent tiers on separate cadences; policy picks a tier per task, then the newest GA model in that tier." Populated for every OpenAI (`astra|sol|terra|luna|pro|mini|nano|codex|base`) and Anthropic (`fable|mythos|opus|sonnet|haiku`) model — **every OpenAI/Anthropic model in the registry carries `tier`, verified after this integration's reconciliation pass.** xAI rows are left without a `tier`: no vendor-documented capability-tier naming scheme was found in this pack's xAI sources (only `grok-4.x` version numbering). GPT-5.6 models are **not** marked historical — they remain served and listed in Codex.
- `capabilities/effort-surfaces-2026-07.json`: `api` surfaces for gpt-6-sol/gpt-6-luna from their dedicated model-reference pages — `reasoning.effort` supports none/low/medium(default)/high/xhigh/max, with **no `minimal` level** (unlike gpt-5.6's api surface). `codex_cli` surfaces: **CORRECTED during integration reconciliation.** Initially recorded empty, citing a codex-cli 0.154.0 read (same day as launch) that reported both slugs absent — a genuine but short-lived rollout gap, not a capability negation. Re-checked against a same-day codex-cli 0.156.0 read on the same host (`openai-codex-debug-models-0-156-0-2026-09-22`): both ARE listed (`visibility=list`) — gpt-6-sol low/medium/high/xhigh/max/ultra, default medium; gpt-6-luna low/medium/high/xhigh/max (no ultra), default medium. Filled in; the stale "not yet listed" claims (a `missing[]` entry and this file's document-level `notes`) were removed/corrected to match.
- **Launch-post headline table** (`performance/openai-gpt-6-sol-luna-launch-2026-09.json`): the page's two `<table>` elements (API-pricing summary; AutomationBench score/cost-per-task: GPT-6 Sol xhigh 33.2%/$0.27, GPT-6 Astra low 30.3%, Claude Opus 5 max 26.9%, Claude Fable 5.1 w/ Opus 5 Fallback max 31.4%) plus vendor claims verbatim. Claude Opus 5's 26.9% score matches Anthropic's own Opus 5.5 launch table exactly — cross-vendor corroboration, kept in a separate `comparability_group`.
- **Launch-post effort×cost curves** (`performance/openai-gpt-6-sol-luna-launch-curves-2026-09.json`): all 11 `vegaLiteSpec` charts on the launch page, parsed from the Next.js hydration payload as exact data (not digitized) — 212 `scores[]` rows + 1 documented `missing[]` gap (GPT-5.6 Luna's "Unauthorized agent interaction" point, source-labeled "Not measured"). 6 new metric_ids minted for benchmarks with no prior entry (`factual-error-rate-difficult-prompts`, `coding-deception-rate`, `broken-search-tool-nondisclosure-rate`, `reviewer-bypass-attempt-rate`, `warning-circumvention-attempt-rate`, `unauthorized-agent-interaction-rate`) — none collided with an existing id. **Consistency check (item 5):** every AutomationBench row in the launch-post HTML table was cross-checked programmatically against its matching `(model, effort)` point in this curve file — **0 mismatches**; scores and absolute costs agree to rounding everywhere they overlap.
- **GPT-6 Astra system card** (`performance/openai-gpt-6-astra-system-card-2026-09.json`): 97 score rows (Astra + Sol/Luna appendix) covering safe-completion/U18/agentic-safe-completion tables, static jailbreak robustness, Gray Swan IPI Arena, HealthBench (length-adjusted), dynamic mental-health evals, misaligned-outcome rates, agent-to-agent engagement, Preparedness Framework bio/chem/cyber/self-improvement evaluations, and safety-training/refusal evals. 59 new metric_ids minted (all `vendor_table`-stewarded, cross-card metrics marked `comparable: false` with distinct `comparability_group`s since OpenAI's own text says eval datasets/graders evolve between cards). GPT-6 Sol/Luna have no standalone system card — the Astra card's Section 11 appendix (added 2026-09-22, the same day Sol/Luna launched) explicitly covers them under the same safeguard suite as GPT-5.6 Sol/Luna.
- **Third-party board coverage:** AA Intelligence Index v4.3.2 (Sol 47.53, Luna 37.26, max effort only) + per-model AA eval breakdown; ARC Prize (full Luna row across 6 effort levels + both ARC-AGI-3 harnesses; Sol confirmed absent from the board entirely, unlike same-day-launched Luna); OpenRouter tiered pricing + live throughput telemetry; explicit confirmed-absent records for vals.ai (Terminal-Bench 4.0, SWE-bench), DeepSWE, LMArena/Arena.ai, Steel BrowseComp, Terminal-Bench official, and Epoch AI (GPT-6 Astra present on every one of these boards, proving each "absent" reading is a genuine gap, not a broken fetch). 14 new SOURCES entries.
- **Metric-id reconciliation (item 1):** checked every id the charts and syscard lanes minted for semantic (not just literal-string) overlap. Found one genuine near-duplicate: `unauthorized-agent-interaction-rate` (launch-page chart — rate of *taking* the unauthorized action, conditional on finding the board) vs. `gpt6-agent-to-agent-engagement-rate` (system-card Fig 12 — rate of *engaging with/discovering* the board at all). These measure different quantities from the same experimental setup and were kept as separate ids per house convention (same id only for the same definition), but each now cross-references the other in its `notes` so the two are never conflated. No literal metric_id string collisions existed after the merge (219 metrics, 0 duplicates) — both lanes independently minted disjoint new ids.
- **Cache facts** from `better-prompt-caching-for-gpt-6.html` folded into pricing notes: GPT-6's improved caching gives "discounts of up to 90% on cached input tokens" (matches the stored 0.1× cache-read ratio) and caches eligible shared prefixes for a **30-minute reuse window by default**; reasoning effort can be changed between responses without breaking cache via `configuration_update`.
- Observed, not acted on (flagged for a future pricing-audit pass): GPT-5.6 Terra and GPT-5.6 Luna model rows are no longer present on the live `developers.openai.com/api/docs/pricing` page as of 2026-09-22 (only `gpt-5.6-sol` and `gpt-5.6-cyber` remain listed). GPT-5.6 Sol's own rate and promotional-pricing footnote ("available at least through November 21, 2026") are unchanged.
- **SOURCES.json:** 112 sources total after this integration (was 87 on main), 0 duplicates. **metrics.json:** 219 metrics (was 154), 0 duplicates. **pack.json `files[]`:** 73 entries, 0 duplicates, alphabetically ordered.

- Same-snapshot full rosters for every routing candidate (AA Intelligence Index v4.3.2 with cost per task, vals.ai Terminal-Bench 4 and SWE-bench, DeepSWE, tbench.ai, ARC Prize): `performance/*-full-roster-2026-09-22.json`. Efforts are recorded as each board states them (AA page titles and vals.ai payloads say max).
- Evidence published after first ingest: Epoch AI model pages for Sonnet 5, Haiku 4.5, Opus 5, Fable 5.1, Astra, GPT-5.6 and Grok 4.6 (`performance/epoch-ai-model-pages-2026-09.json`).
- `models.json` adds `access: restricted` (Mythos) and `effort_parameter: false` (Haiku 4.5).

## v0.5.5 — 2026-09-22

- **Added Claude Opus 5.5** (launched 2026-09-22): `models.json` entry (family `claude-opus`, GA, aliases "Claude Opus 5.5"/"Opus 5.5"); pricing row in `pricing/anthropic-api-2026-07.json` ($4/$20 input/output, cache read $0.20 at a 0.05x-base override multiplier, cache write $5 at 1.25x, fast mode $8/$40 Claude-API-first-party-only); `capabilities/effort-surfaces-2026-07.json` api surface (levels low/medium/high/xhigh/max, **default medium** — different from Opus 5's default high; thinking cannot be disabled at ANY effort level, stricter than Opus 5's xhigh/max-only restriction; forced `tool_choice` any/tool return a 400 error; computer use requires `computer_toolset_20260801` on the Claude API/Google Cloud). `claude-opus-5` is **not** marked historical — it remains served.
- **New:** `performance/anthropic-opus-5-5-launch-2026-09.json` — the launch page's headline benchmark table (Opus 5.5 / Fable 5.1 / Opus 5 / GPT-6 Astra / GPT-5.6 Sol) across Terminal-Bench 4.0, FrontierCode v1.1 (Main), CursorBench 4.0, GDPval-AA v2.1, AutomationBench, Humanity's Last Exam (with tools), Terminal-Bench-Science 0.1, OSWorld 2.0, and Chartography, plus the page's own vendor claims (cost-per-quality comparisons vs Opus 5/GPT-6 Astra/GPT-5.6 Sol, and a customer-quote claim about BigFinance Bench).
- **New:** `performance/anthropic-opus5-5-digitized-2026-09.json` — 130 score rows, the full per-effort (low/medium/high/xhigh/max) curves behind six of the launch page's charts (Terminal-Bench 4.0, FrontierCode v1.1 Main, CursorBench 4.0, GDPval-AA v2.1, AutomationBench, and the new WANDR metric), read as exact values from the page's inline-SVG chart aria-labels. Every headline-table cell with a corresponding curve point matches it exactly (0 mismatches; verified 2026-09-22).
- **New:** `performance/anthropic-opus-5-5-system-card-2026-09.json` — 88 score rows from the 230-page system card: the full Table 8.1.A capability suite (SWE-bench variants, FrontierCode, Terminal-Bench 4.0/-Science, FrontierSWE v2, CursorBench 4.0, ArXivMath, ProgramBench, HLE, OSWorld 2.0, Chartography, BenchCAD, GDPval-AA v2.1, AA-Briefcase v1.1, Toolathlon Verified, AutomationBench, HealthBench(+Pro), GMMLU/MILU, a life-sciences suite, and cyber evals), plus quantitative safety/alignment results (sandbox-escape propensity, package-registry harmful-publish propensity, prompt-injection compliance, reward-hacking rate, SHADE-Arena/LinuxArena stealth rates, verbalized-evaluation-awareness).
- **New:** third-party board coverage — `performance/artificial-analysis-intelligence-index-opus-5-5-2026-09.json` (AA Intelligence Index v4.3.2, new composite metric, Opus 5.5 #1/212), `performance/terminal-bench-4-vals-opus-5-5-2026-09.json` (vals.ai secondary Terminal-Bench 4.0 reading), and `performance/opus-5-5-board-coverage-2026-09-22.json` (an explicit CONFIRMED-ABSENT/PROVISIONAL record of every other board checked at launch — ARC Prize, LMArena, DeepSWE, SWE-bench vals.ai, BrowseComp, Epoch AI, LiveBench — so absence is recorded, not silently omitted).
- **Metric registry reconciliation:** four workers independently ingested Opus 5.5 data and initially minted different `metric_id`s for the same benchmarks. Per this pack's existing convention (one shared `metric_id` per benchmark, cross-publisher/cross-harness distinction expressed via row-level `comparable: false` + `comparability_group`, not distinct ids — see `terminal-bench-4-0`'s pre-existing mixed-provenance pattern), these were unified: the launch table's `terminal-bench-4-0`/`frontiercode-1-1-main`/`cursorbench-4-0` rows now share ids with the digitized curves and system card; `frontiercode-v1-1-main`/`-extended` (system card naming) folded into `frontiercode-1-1-main`/`-extended`; `gdpval-aa-v2-1`, `wandr`, and `chartography` definitions deduplicated to one entry each. All affected rows carry `comparable: false` and a distinct `comparability_group`.
- **Fixed (validator, no data change):** a pre-existing error on `performance/openai-gpt-5-6-launch-2026-07.json`'s three `automationbench` rows (GPT-5.6 Sol/Terra/Luna), which mixed source/harness classes under a shared metric_id without `comparable: false` — tagged, consistent with the same convention.
- **Fixed (schema):** 25 rows in the system card file used `source_type: "third_party_eval"`, not a valid enum value in `schemas/performance-v1.schema.json`; remapped to the schema's `third_party_board` (the closest existing "independent, non-vendor" value) so `--require-jsonschema` passes.
- **New SOURCES:** `anthropic-opus-5-5-2026-09-22` (launch post, merged notes covering pricing/headline-table/footnotes and the SVG-curve extraction method), `anthropic-pricing-2026-09-22`, `anthropic-models-overview-2026-09-22`, `anthropic-effort-guide-2026-09-22`, `anthropic-opus-5-5-system-card-2026-09-22`, plus 12 board-sweep sources (Artificial Analysis, vals.ai, Steel, ARC Prize, Arena, DeepSWE, Epoch AI, LiveBench, OpenRouter).
- Not recorded (no source found): a tokenizer-identity claim for Opus 5.5 vs Opus 5, and an Opus 5.5 lifecycle/retirement floor.

## v0.5.4 — 2026-09-05

- Added current, rendered original-publisher snapshots for Terminal-Bench 2.1 and Artificial Analysis Intelligence Index v4.2; preserved earlier board reads as separate historical observations.
- Rechecked the fixed ARC GPT-5.6 table and the public Scale SWE-Bench Pro table; their stored rows still match the publisher pages.
- Closed the remaining model-label, metric-id, and local-eval source-id joins without treating board labels or vendor table configurations as new comparable model runs.
- **Fixed (validator, no data change):** `scripts/validate_data_pack.py`'s `valid_until` expiry check was flagging the six intentionally-retained `gpt-5.6-{sol,terra,luna}-2026-07` pricing rows in `pricing/openai-api-2026-07.json` and `pricing/codex-credits-2026-07.json` as "expired" — these are closed historical windows (their `models.json` entries already carry `status: "historical"`, added when the current rows were introduced), not live promos that lapsed unnoticed. The check now skips the expiry error when the row's model resolves to a `status: "historical"` entry in `models.json`; it still fails loudly for any non-historical model whose `valid_until` has passed. Re-verified against live vendor pages 2026-09-09: current `gpt-5.6-sol/terra/luna` and `gpt-6-astra` rows in both files match published rates exactly (OpenAI API pricing docs and `learn.chatgpt.com/docs/pricing`); no rates changed.

## v0.5.3 — 2026-08-02

- **Investigated the community claim "Luna at max reasoning effort is the best Codex setting."** Verdict: not supported as stated — real value point, not a dominant one. Full writeup: `~/code/dotfiles/ai/research/model-routing/gpt-5-6-luna-at-max-effort-is-a-defensible-not-dominant-codex-value-pick.md`.
- **New:** `performance/deepswe-leaderboard-gpt-5-6-2026-08.json` — third-party DeepSWE leaderboard (datacurve.ai, mini-swe-agent harness, 113 tasks) reading for gpt-5.6-sol/terra/luna at effort=max, with cost/task. New metric_id `deepswe-leaderboard-datacurve` (distinct from OpenAI's own `deepswe-v1-1` vendor table) added to `metrics.json`.
- **Corrected:** `capabilities/effort-surfaces-2026-07.json` — Codex CLI's official config-reference lists no `max` value for `model_reasoning_effort` (only minimal..xhigh); `max` is a separate opt-in UI toggle per Codex's Models doc, not a config-settable value, confirmed 2026-08-02. Added `gpt-5.6-terra` and `gpt-5.6-luna` `codex_cli` surface entries (previously only `gpt-5.6-sol` had one).
- **New SOURCES:** `openai-codex-models-2026-08-02`, `deepswe-leaderboard-2026-08-02`, `majesticlabs-luna-max-2026-08-02` (secondary commentary, explicitly not cited for numeric scores).

## v0.5.2 — 2026-07-09

- **Honesty pass (Sol+Fable):** local rows reclassified harness_smoke / grade D; mixed `metric_id`s set `comparable: false` + `comparability_group`.
- Pack schema allows png/py assets; CI requires jsonschema; freshness includes boards.

## v0.5.1 — 2026-07-09

- **Local evals** mirrored: `performance/local-evals-2026-07.json` for implement.standard, fanout.explore, review.audit on the historical waspflow harness. v0.5.2 reclassified them as grade D harness smoke.
- SOURCES kind `local_eval`.

## v0.5.0 — 2026-07-09

- **`metrics.json`** metric registry (78 metrics).
- **Score enrichment:** `harness`, `source_type`, `evidence_grade`, `observed_at`, `cost{}`, `metric_id` (cost sibling rows folded into quality).
- **Pricing:** `valid_until` on Sonnet 5 intro; `family_default` on match rules; `role: reference` for Google; Terra Codex credits **verified** vs official rate card.
- **SOURCES:** `digitized_chart` kind for Sonnet 5 digitization entry.
- **FRESHNESS.md** + `scripts/check_freshness.py`; `scripts/sync_tokensmash_pricing.sh`.

## v0.4.2 — 2026-07-09

- **`models.json` L0 registry** — canonical model ids, providers, families, aliases (e.g. board naming drift → pricing ids). Validator resolves every model string in pricing/performance/capabilities against it (including `model@harness` scores).

## v0.4.1 — 2026-07-09

- **Chart digitization pipeline** (`scripts/digitize_chart.py`): density-peak marker extraction + labeled-value reads with asset hashes and dual-read error estimates.
- **Sonnet 5 pilot:** BrowseComp & OSWorld-Verified effort×cost curves; printed benchmark table; misaligned-behavior and Firefox147 bar labels → `performance/anthropic-sonnet5-digitized-2026-07.json` + `digitized/`.

## v0.4.0 — 2026-07-09

**Breadth expansion** (multi-agent research pass — only source-backed numbers):

- **32 SOURCES** (was 10): Anthropic pricing/effort/Fable/Opus, OpenAI reasoning/API, xAI reasoning, Google Gemini pricing, ARC full leaderboard, Artificial Analysis, Terminal-Bench 2.1/2.0, SWE-Bench Pro SEAL, vals.ai, LMArena, BrowseComp aggregator.
- **Pricing:** full Anthropic cache rates + Mythos; OpenAI mini/nano/pro/5.3-codex; Codex credits for GPT-5.6 Sol/Terra/Luna; Google Gemini 3.x/2.5; xAI multi-agent row.
- **Performance:** full GPT-5.5 launch table (~29 metrics); Terminal-Bench official + AA; AA Intelligence/Coding indexes + Grok 4.5; expanded ARC multi-vendor + full cost/task matrix; SWE Pro SEAL + vals Verified (clearly labeled); LMArena Elo; BrowseComp aggregator.
- **Capabilities:** 18 surfaces across Anthropic/OpenAI/xAI with valid efforts and modes (fast/ultra/multi-agent).
- **agent enum** extended: `google` | `other` for non-coding-agent pricing tables.

Still **missing** (not invented): Anthropic chart digitization, official SWE Verified full client table, Agents' Last Exam public board, GPT-5.6 on AA/Arena boards.

## v0.3.0 — 2026-07-09

- **`SOURCES.json` provenance registry** + `sources-v1` schema; validator requires resolvable `source_ids`.
- **Full GPT-5.6 GA eval tables** (`openai-gpt-5-6-launch-2026-07.json`) from [OpenAI launch post](https://openai.com/index/gpt-5-6/) — professional, coding, science, computer use, cyber, self-improvement, multimodal, academic, tool use, long context, ARC-AGI-3.
- **ARC Prize effort ladders** for Sol/Terra/Luna ARC-AGI-1/2/3 + leaderboard cost/task (`arcprize-gpt-5-6-2026-07.json`).
- GPT-5.6 Sol/Terra/Luna **API pricing** (+ cache write 1.25×) in `pricing/openai-api-2026-07.json`.
- Row-level `source_id` on scores/claims throughout.

## v0.2.0 — 2026-07-09

- Formal **JSON Schema** (Draft 2020-12) for pack envelope, index, pricing, performance.
- Unified **performance** documents (`kind: performance` with `claims` and/or `scores`).
- `schema_version: 1` + `$schema` on all payloads.
- Stdlib **validator** (`scripts/validate_data_pack.py`); release path runs it.

## v0.1.0 — 2026-07-09

- Initial pricing (Anthropic / OpenAI / Codex credits / xAI) + sparse quality notes.
