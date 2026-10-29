# Artificial Analysis Intelligence Index snapshots

This file says which AA Intelligence Index snapshot the catalog uses, why, and what did not map. Read it with the snapshot files under `performance/`.

## Which snapshot is current

The cost-vs-intelligence chart uses **Intelligence Index v5.0** (`guide.json` `hero.metric_id` = `artificial-analysis-intelligence-index-v5-0`). AA published v5 on 2026-10-27. This catalog read it on 2026-10-28.

v5.0 scores are not comparable with v4.3.2 scores. v5 has nine evals in four categories at 25% each: Knowledge Work, Coding, Science and General. The v5 article gives these weights:

| Category (25% each) | Eval weights as shares of the whole Index |
|---|---|
| Knowledge Work | AA-Briefcase 15%, AutomationBench-AA 5%, GDP.pdf 5% |
| Coding | FrontierCode 2.0 15%, Terminal-Bench 4.0 10% |
| Science | Terminal-Bench-Science 15%, CritPt v2 hard subset 10% |
| General | AA-Omniscience v1.1 15% (10% accuracy, 5% non-hallucination), HLE-Diamond 10% |

## Snapshots

| File | Metric | Regime | Retrieved | Rows | Status |
|---|---|---|---|---|---|
| `performance/artificial-analysis-intelligence-index-v5-full-2026-10-28.json` | Intelligence Index v5.0 | v5 full, from AA's public roster | 2026-10-28 | 63 Index rows (24 models), 558 per-eval rows, 52 `missing` entries | **Current chart source** |
| `performance/artificial-analysis-intelligence-index-v5-2026-10-28.json` | Intelligence Index v5.0 | v5 partial (rounded GPT-6.1 Terra and Sol rows) | 2026-10-28 | 11 Index rows, 11 Terminal-Bench 4.0 rows | Unchanged. Kept for provenance. Its Index rows are integers and agree with the full snapshot within 0.5 for all 11 shared settings. |
| `performance/artificial-analysis-intelligence-index-opus-5-5-2026-09.json` | Intelligence Index v4.3.2 | v4 | 2026-09 | 10 rows | Historical |
| `charts/historical/cost-vs-intelligence-v4-3-2-*.svg`, `...-all-v4-3-2-*.svg`, `...-readme-block.md` | Intelligence Index v4.3.2 | v4 | observed 2026-10-07 to 2026-10-13 | 82 settings, 35 models | Historical. Not the current chart. |

## Coverage change (v4.3.2 chart to v5.0 chart)

| | v4.3.2 chart | v5.0 chart |
|---|---|---|
| Settings with an Index value and cost | 82 | 61 |
| Models | 35 | 23 |
| Frontier settings | 17 | 16 |

Coverage is narrower in v5. AA's roster gives v5 values for 24 catalog models. Of these, 23 have a per-task cost. The 24th, Muse Wonder Full at max (49.73), has an Index value but no cost, so the chart lists it as "no cost" and does not plot it. Gemini 3.8 Flash at low (22.35) also has no cost in the snapshot; its other settings are plotted.

Thirteen models on the v4 chart have no v5 setting: Claude Haiku 4.5, Gemini 3.1 Pro Preview, GLM-5.3 Flash, GPT-5.6 Luna, GPT-5.6 Terra, GPT-6 Sol, Kimi K2.7 Code, Kimi K3, Mistral Large 3, Mistral Medium 3.5, Mistral Small 4, Qwen3.8 2.4T-A95B and Qwen3.8 Max. GPT-6.1 Terra is new on the v5 chart. The gap table below gives the reason for each unmapped setting.

## Frontier pairs (one regime each)

v4.3.2 frontier (17): GPT-6 Luna low, medium, high, xhigh and max; MiMo-V2.6-Flash (unknown effort); MiMo-V2.6-Pro (unknown effort); Claude Haiku 5.5 xhigh; GPT-6.1 Sol low, medium, high, xhigh and max; Claude Opus 5.5 high, xhigh and max; Claude Sonnet 5.5 max.

v5.0 frontier (16): GPT-6 Luna low and medium; Claude Haiku 5.5 low and high; GPT-6.1 Terra low, medium and high; GPT-6.1 Sol low, medium, high, xhigh and max; GPT-6 Astra high and xhigh; Claude Opus 5.5 xhigh and max.

Gained in v5: Claude Haiku 5.5 low and high; GPT-6 Astra high and xhigh; GPT-6.1 Terra low, medium and high.
Lost in v5: Claude Haiku 5.5 xhigh; Claude Opus 5.5 high; Claude Sonnet 5.5 max; GPT-6 Luna high, xhigh and max; MiMo-V2.6-Flash and MiMo-V2.6-Pro (unknown effort).

Frontier membership is computed within each regime. A v4 score and a v5 score are never compared on one frontier.

## Gaps (`missing[]`, 52 entries)

| Reason | Count | Examples |
|---|---|---|
| No AA object for this catalog id or effort | 30 | GPT-5.6 Terra and Luna settings; Claude Opus 5 max and xhigh; Claude Fable 5; Claude Sonnet 5 max |
| Blank or unknown catalog effort, not mapped | 7 | Claude Opus 5.5 and GPT-6.1 Sol blank-effort keys (AA lists them only at labelled efforts) |
| AA object exists, no Index value | 11 | GPT-6 Sol at every effort; GLM-5.3 Flash; Gemini 3.1 Pro Preview; Muse Spark 1.3 xhigh |
| Identity not established (different version or snapshot) | 3 | Kimi K3 (AA lists Kimi K3.1 Pro, weights not released); Qwen3.8 Max (AA lists two dated objects; the catalog release date matches neither) |
| Other | 1 | Gemini 4 Carbon (see below) |

Gemini 4 Carbon: the roster has an Index value (59.43, high effort, released 2026-10-28), but the catalog has no id for it, and Google describes its access as staged. It is recorded as a gap, not added as a setting.

Full list: `missing[]` in the snapshot file.

## Disagreements

- **GPT-6.1 Terra cost at max.** AA's 2026-10-26 Terra article quotes $0.40 per task at max. The 2026-10-28 roster gives $0.96 at max and $0.40 at xhigh. The stored value is the roster's $0.96 at max. The $0.40 at max is not reproduced.
- **GPT-6 Astra CritPt v2 hard subset.** The v5 article gives 72%. The roster gives 70.5%. The stored value is the roster's.

## Policy pairs (shared pack, `operating-points.json` v0.2.10)

FrontierCode 2.0 carries the existing `coding` metric tag, so advisory coding operations can consider its rows. Those rows have no per-eval cost; the aggregate Index cost is not substituted for it. The other four new metric definitions (AutomationBench-AA, HLE-Diamond, CritPt v2 hard and AA-Omniscience v1.1) remain outside automatic operation matching until their task fit is mapped explicitly. Their scores are still stored. This is an explicit advisory coverage gap, not evidence that they are irrelevant.

Each row is a configured pair or a candidate. Frontier status is on the v5 frontier above. Scores are v5.0 Index values; no pick changed.

| Op | Role | Setting | v5 score | v5 $/task | v5 frontier |
|---|---|---|---|---|---|
| recover.report | configured | Claude Haiku 5.5 medium | 24.55 | 0.0918 | no |
| recover.report | candidate | Claude Sonnet 5.5 medium | 30.32 | 0.7807 | no |
| fanout.explore | configured | GPT-6.1 Sol medium | 46.15 | 0.3594 | yes |
| fanout.explore | candidate | Claude Opus 5.5 medium | 48.06 | 2.4511 | no |
| fanout.explore | candidate | Claude Sonnet 5.5 medium | 30.32 | 0.7807 | no |
| fanout.dollar-tight | configured | GPT-6 Luna medium | 18.30 | 0.0422 | yes |
| fanout.dollar-tight | candidate | Claude Sonnet 5.5 medium | 30.32 | 0.7807 | no |
| docs.lookup | configured | Claude Haiku 5.5 medium | 24.55 | 0.0918 | no |
| docs.lookup | candidate | Claude Sonnet 5.5 medium | 30.32 | 0.7807 | no |
| implement.standard | configured | Claude Opus 5.5 medium | 48.06 | 2.4511 | no |
| implement.standard | candidate | GPT-6.1 Sol medium | 46.15 | 0.3594 | yes |
| implement.quota-tight | configured | Claude Sonnet 5.5 medium | 30.32 | 0.7807 | no |
| implement.quota-tight | candidate | GPT-6.1 Sol medium | 46.15 | 0.3594 | yes |
| implement.oracle-bounded | configured | GPT-6 Luna high | 21.38 | 0.0729 | no |
| implement.oracle-bounded | candidate | Claude Sonnet 5.5 medium | 30.32 | 0.7807 | no |
| implement.accuracy-first | configured | Claude Opus 5.5 high | 51.69 | 3.4647 | no |
| implement.accuracy-first | candidate | GPT-6.1 Sol high | 50.23 | 0.5910 | yes |
| review.audit | configured | GPT-6.1 Sol high | 50.23 | 0.5910 | yes |
| review.audit | candidate | Claude Opus 5.5 high | 51.69 | 3.4647 | no |
| review.audit | candidate | Claude Sonnet 5.5 high | 41.12 | 1.6361 | no |
| advisor.deep | configured | Claude Opus 5.5 high | 51.69 | 3.4647 | no |
| advisor.deep | candidate | GPT-6.1 Sol high | 50.23 | 0.5910 | yes |
| ui.computer-use | configured | GPT-6.1 Sol medium | 46.15 | 0.3594 | yes |
| ui.computer-use | candidate | Claude Opus 5.5 medium | 48.06 | 2.4511 | no |
| grok.explore-only | configured | Grok 4.7 medium | no v5 row | — | no |
| review.rule-check | configured | Claude Haiku 5.5 medium | 24.55 | 0.0918 | no |

Grok 4.7 has v5 rows at low (30.88), high (35.01) and xhigh (34.34) but not at medium, so the configured medium pair has no v5 score.

Settings named in the brief but outside the shared pack: GPT-6 Luna max (v5 27.76, $0.152, not on frontier) and Claude Haiku 5.5 max (v5 36.23, $0.411, not on frontier). The shared pack does not list these as defaults or automatic candidates; this is not a prohibition on explicit reasoned overrides. A local owner overlay allows both as explicit choices for `implement.oracle-bounded`. This file does not change that overlay.

## Regenerating

    uv run python scripts/render_model_catalog_charts.py          # charts and README block
    uv run python scripts/render_model_catalog_charts.py --png    # social card (needs Chrome)
    uv run python scripts/render_model_catalog_charts.py --check  # exit 1 if stale
