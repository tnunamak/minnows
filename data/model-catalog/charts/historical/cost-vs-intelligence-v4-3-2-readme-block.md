<picture>
  <source media="(prefers-color-scheme: dark)" srcset="charts/cost-vs-intelligence-dark.svg">
  <img src="charts/cost-vs-intelligence-light.svg" alt="Line chart, cost vs intelligence. Across: API cost per task in US dollars, log scale, $0.0045 to $7.63. Up: Artificial Analysis Intelligence Index v4.3.2. One line per model joins its effort levels, coloured by vendor (Anthropic, OpenAI, Google, xAI, Other vendors); 16 models shown. A dashed Pareto frontier runs through GPT-6 Luna low to xhigh (21.5–34.6 at $0.0045–$0.042), MiMo-V2.6-Flash (37.9 at $0.062), GPT-6 Luna max (38.1 at $0.068), Claude Haiku 5.5 xhigh (41.2 at $0.12), GPT-6.1 Sol low (42.1 at $0.13), MiMo-V2.6-Pro (46.3 at $0.13), GPT-6.1 Sol medium to max (47.8–51.8 at $0.21–$0.72), Claude Opus 5.5 high to xhigh (53.6–56.0 at $1.82–$3.46), Claude Sonnet 5.5 max (56.0 at $5.46), Claude Opus 5.5 max (57.6 at $5.98). The table below lists every point.">
</picture>

**Reading the chart.** Up is smarter, left is cheaper. The frontier (dashed) holds the settings that no other setting beats on both cost and score: GPT-6 Luna low to xhigh (21.5–34.6 at $0.0045–$0.042), MiMo-V2.6-Flash (37.9 at $0.062), GPT-6 Luna max (38.1 at $0.068), Claude Haiku 5.5 xhigh (41.2 at $0.12), GPT-6.1 Sol low (42.1 at $0.13), MiMo-V2.6-Pro (46.3 at $0.13), GPT-6.1 Sol medium to max (47.8–51.8 at $0.21–$0.72), Claude Opus 5.5 high to xhigh (53.6–56.0 at $1.82–$3.46), Claude Sonnet 5.5 max (56.0 at $5.46), Claude Opus 5.5 max (57.6 at $5.98). The top score is Claude Opus 5.5 max at 57.6 for $5.98 per task. Data from Artificial Analysis, observed 2026-10-07 to 2026-10-13. Each point is the latest observation of that model and effort; snapshots share the chart only where the settings they both list agree within 1 point. Cost is the API cost to run one index task. The source re-measures cost between snapshots: the same setting's cost moved by up to 39%, and each point uses the latest. Not plotted, no cost per task: Muse Wonder Full. Left out, estimated or not checkable against the other snapshots: Claude Opus 5, Claude Sonnet 5, GPT-5.6 Sol, Grok 4.6, MiniMax-M2.7. 2 more settings of plotted models have a score but no cost and are not drawn. Below 25 on the index, so only in the all-models chart: gemini-3.5-flash-lite.

<details><summary><b>All 35 models</b> on the same axes</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="charts/cost-vs-intelligence-all-dark.svg">
  <img src="charts/cost-vs-intelligence-all-light.svg" alt="The same chart with all 35 models that have a cost per task. The table below lists every point.">
</picture>

</details>

<details><summary><b>Data behind the chart</b>: 82 settings, one row per model and effort</summary>

| Model | Effort | Intelligence Index | $/task | Observed | Note |
|---|---|---|---|---|---|
| Claude Opus 5.5 | low | 42.3 | $0.55 | 2026-10-13 |  |
| Claude Opus 5.5 | medium | 51.2 | $1.34 | 2026-10-13 |  |
| Claude Opus 5.5 | high | 53.6 | $1.82 | 2026-10-13 | on the frontier |
| Claude Opus 5.5 | xhigh | 56.0 | $3.46 | 2026-10-13 | on the frontier |
| Claude Opus 5.5 | max | 57.6 | $5.98 | 2026-10-13 | on the frontier |
| Claude Sonnet 5.5 | low | 35.9 | $0.35 | 2026-10-13 |  |
| Claude Sonnet 5.5 | medium | 40.8 | $0.48 | 2026-10-13 |  |
| Claude Sonnet 5.5 | high | 46.8 | $0.88 | 2026-10-13 |  |
| Claude Sonnet 5.5 | xhigh | 51.9 | $2.01 | 2026-10-13 |  |
| Claude Sonnet 5.5 | max | 56.0 | $5.46 | 2026-10-13 | on the frontier |
| Claude Fable 5.1 | low | 46.8 | $2.37 | 2026-10-13 |  |
| Claude Fable 5.1 | medium | 48.9 | $2.98 | 2026-10-13 |  |
| Claude Fable 5.1 | high | 51.2 | $3.91 | 2026-10-13 |  |
| Claude Fable 5.1 | xhigh | 53.2 | $5.98 | 2026-10-13 |  |
| Claude Fable 5.1 | max | 53.4 | $7.63 | 2026-10-13 |  |
| GPT-6 Astra | low | 45.8 | $0.82 | 2026-10-13 |  |
| GPT-6 Astra | medium | 49.6 | $1.54 | 2026-10-13 |  |
| GPT-6 Astra | high | 50.9 | $1.73 | 2026-10-13 |  |
| GPT-6 Astra | xhigh | 52.4 | $2.31 | 2026-10-13 |  |
| GPT-6 Astra | max | 52.7 | $3.26 | 2026-10-13 |  |
| Gemini 4 Argon | high | 52.6 | $1.99 | 2026-10-13 |  |
| Muse Wonder | max | 52.4 | $4.52 | 2026-10-13 |  |
| GPT-6.1 Sol | low | 42.1 | $0.13 | 2026-10-13 | on the frontier |
| GPT-6.1 Sol | medium | 47.8 | $0.21 | 2026-10-13 | on the frontier |
| GPT-6.1 Sol | high | 50.2 | $0.32 | 2026-10-13 | on the frontier |
| GPT-6.1 Sol | xhigh | 51.0 | $0.39 | 2026-10-13 | on the frontier |
| GPT-6.1 Sol | max | 51.8 | $0.72 | 2026-10-13 | on the frontier |
| Muse Spark 1.3 | xhigh | 45.1 | $1.37 | 2026-10-13 |  |
| Muse Spark 1.3 | max | 48.1 | $1.60 | 2026-10-13 |  |
| GPT-6 Sol | Non-reasoning | 28.5 | $0.33 | 2026-10-07 |  |
| GPT-6 Sol | low | 34.2 | $0.13 | 2026-10-07 |  |
| GPT-6 Sol | medium | 39.8 | $0.25 | 2026-10-07 |  |
| GPT-6 Sol | high | 42.4 | $0.37 | 2026-10-07 |  |
| GPT-6 Sol | xhigh | 44.2 | $0.52 | 2026-10-07 |  |
| GPT-6 Sol | max | 47.6 | $1.04 | 2026-10-07 |  |
| Grok 4.7 | low | 42.2 | $1.25 | 2026-10-07 |  |
| Grok 4.7 | high | 46.3 | $2.73 | 2026-10-07 |  |
| Grok 4.7 | xhigh | 46.4 | $3.74 | 2026-10-07 |  |
| MiMo-V2.6-Pro | – | 46.3 | $0.13 | 2026-10-07 | on the frontier |
| Qwen3.8-Max | – | 45.4 | $5.41 | 2026-10-07 |  |
| GLM-5.3 | low | 34.3 | $0.85 | 2026-10-07 |  |
| GLM-5.3 | max | 44.8 | $2.01 | 2026-10-07 |  |
| step-5-preview | – | 43.7 | $0.72 | 2026-10-07 |  |
| Kimi K3 | low | 30.1 | $1.15 | 2026-10-07 |  |
| Kimi K3 | max | 43.6 | $2.00 | 2026-10-07 |  |
| Claude Haiku 5.5 | low | 29.5 | $0.025 | 2026-10-13 |  |
| Claude Haiku 5.5 | medium | 34.5 | $0.047 | 2026-10-13 |  |
| Claude Haiku 5.5 | high | 37.8 | $0.079 | 2026-10-13 |  |
| Claude Haiku 5.5 | xhigh | 41.2 | $0.12 | 2026-10-13 | on the frontier |
| Claude Haiku 5.5 | max | 43.4 | $0.21 | 2026-10-13 |  |
| GPT-5.6 Terra | low | 27.5 | $0.14 | 2026-10-07 |  |
| GPT-5.6 Terra | medium | 30.1 | $0.18 | 2026-10-07 |  |
| GPT-5.6 Terra | high | 34.2 | $0.34 | 2026-10-07 |  |
| GPT-5.6 Terra | xhigh | 38.0 | $0.63 | 2026-10-07 |  |
| GPT-5.6 Terra | max | 42.1 | $1.40 | 2026-10-07 |  |
| GLM-5.3-Flash | – | 41.8 | $0.25 | 2026-10-07 |  |
| GLM-5.3-Flash | max | 41.8 | $0.25 | 2026-10-07 |  |
| Gemini 3.8 Flash | medium | 39.8 | $0.93 | 2026-10-07 |  |
| Gemini 3.8 Flash | high | 40.9 | $1.24 | 2026-10-07 |  |
| qwen3.8-2.4t-a95b | – | 39.9 | $2.16 | 2026-10-07 |  |
| DeepSeek-V4.1-Flash | max | 39.5 | $0.27 | 2026-10-07 |  |
| mistral-large-4 | – | 38.4 | $1.13 | 2026-10-07 |  |
| GPT-6 Luna | Non-reasoning | 18.5 | $0.011 | 2026-10-07 |  |
| GPT-6 Luna | low | 21.5 | $0.0045 | 2026-10-07 | on the frontier |
| GPT-6 Luna | medium | 29.9 | $0.018 | 2026-10-07 | on the frontier |
| GPT-6 Luna | high | 32.9 | $0.029 | 2026-10-07 | on the frontier |
| GPT-6 Luna | xhigh | 34.6 | $0.042 | 2026-10-07 | on the frontier |
| GPT-6 Luna | max | 38.1 | $0.068 | 2026-10-07 | on the frontier |
| MiMo-V2.6-Flash | – | 37.9 | $0.062 | 2026-10-07 | on the frontier |
| GPT-5.6 Luna | max | 37.3 | $0.18 | 2026-10-07 |  |
| deepseek-v4-pro-0813 | max | 36.0 | $0.67 | 2026-10-07 |  |
| qwen3.8-27b | low | 26.2 | $1.05 | 2026-10-07 |  |
| qwen3.8-27b | medium | 27.6 | $1.13 | 2026-10-07 |  |
| qwen3.8-27b | xhigh | 33.7 | $1.01 | 2026-10-07 |  |
| Gemini 3.1 Pro Preview | – | 29.7 | $1.30 | 2026-10-07 |  |
| MiniMax-M3 | – | 29.2 | $0.51 | 2026-10-07 |  |
| kimi-k2-7-code | – | 25.8 | $0.54 | 2026-10-07 |  |
| gemini-3.5-flash-lite | – | 22.2 | $0.19 | 2026-10-07 |  |
| Claude Haiku 4.5 | Reasoning | 16.9 | $0.28 | 2026-10-07 |  |
| mistral-medium-3-5 | – | 14.2 | $0.50 | 2026-10-07 |  |
| Mistral Small 4 | – | 11.3 | $0.015 | 2026-10-07 |  |
| Mistral Large 3 | – | 9.3 | $0.031 | 2026-10-07 |  |

–: the model has no effort setting, or the source does not name one.

</details>

