# AA Index row changes, 2026-10-28

Old columns are the previous current chart's v4.3.2 observations; new columns are the v5.0 snapshot. This is a change of measurement regime, not a claim that a model improved or regressed. Blank new cells mean missing v5 evidence, not a zero score or a rejected routing candidate. The last column preserves earlier partial-v5 observations where present; those source bytes are unchanged.

Scores, USD per AA Index task and global frontier membership are listed for every setting added, removed from current chart coverage or refreshed. Per-evaluation values and their separate comparability tags are in the [full snapshot](performance/artificial-analysis-intelligence-index-v5-full-2026-10-28.json). Historical v4 rows remain in the pack.

| model | effort | status | v4.3.2 score | v4.3.2 $/task | v4 frontier | v5.0 score | v5.0 $/task | v5 frontier | Δ score | partial v5 score / $ |
|---|---|---|---|---|---|---|---|---|---|---|
| claude-fable-5-1 | high | both | 51.15 | 3.9125 | no | 47.02 | 7.5608 | no | -4.13 |  |
| claude-fable-5-1 | low | both | 46.82 | 2.371 | no | 38.48 | 4.4867 | no | -8.33 |  |
| claude-fable-5-1 | max | both | 53.35 | 7.6297 | no | 49.01 | 13.0393 | no | -4.34 |  |
| claude-fable-5-1 | medium | both | 48.92 | 2.9826 | no | 42.97 | 5.5805 | no | -5.95 |  |
| claude-fable-5-1 | xhigh | both | 53.2 | 5.9783 | no | 49.67 | 10.8855 | no | -3.53 |  |
| claude-haiku-4-5 | Reasoning | dropped_in_v5 | 16.88 | 0.2768 | no |  |  |  |  |  |
| claude-haiku-5-5 | high | both | 37.82 | 0.0794 | no | 29.62 | 0.1586 | yes | -8.2 |  |
| claude-haiku-5-5 | low | both | 29.45 | 0.0245 | no | 18.62 | 0.0438 | yes | -10.83 |  |
| claude-haiku-5-5 | max | both | 43.4 | 0.2128 | no | 36.23 | 0.411 | no | -7.16 |  |
| claude-haiku-5-5 | medium | both | 34.46 | 0.047 | no | 24.55 | 0.0918 | no | -9.91 |  |
| claude-haiku-5-5 | xhigh | both | 41.25 | 0.1238 | yes | 34.34 | 0.2484 | no | -6.91 |  |
| claude-opus-5-5 | high | both | 53.58 | 1.8225 | yes | 51.69 | 3.4647 | no | -1.9 |  |
| claude-opus-5-5 | low | both | 42.31 | 0.5512 | no | 34.5 | 0.9755 | no | -7.8 |  |
| claude-opus-5-5 | max | both | 57.62 | 5.982 | yes | 58.41 | 10.0364 | yes | 0.78 |  |
| claude-opus-5-5 | medium | both | 51.24 | 1.336 | no | 48.06 | 2.4511 | no | -3.19 |  |
| claude-opus-5-5 | xhigh | both | 55.99 | 3.4591 | yes | 56.99 | 6.0472 | yes | 1.01 |  |
| claude-sonnet-5-5 | high | both | 46.75 | 0.8844 | no | 41.12 | 1.6361 | no | -5.64 |  |
| claude-sonnet-5-5 | low | both | 35.87 | 0.3452 | no | 25.3 | 0.5113 | no | -10.57 |  |
| claude-sonnet-5-5 | max | both | 56.0 | 5.4609 | yes | 54.24 | 10.0206 | no | -1.76 |  |
| claude-sonnet-5-5 | medium | both | 40.84 | 0.4833 | no | 30.32 | 0.7807 | no | -10.52 |  |
| claude-sonnet-5-5 | xhigh | both | 51.9 | 2.012 | no | 49.57 | 4.092 | no | -2.32 |  |
| deepseek-v4-1-flash | max | both | 39.46 | 0.2652 | no | 32.49 | 0.6021 | no | -6.97 |  |
| deepseek-v4-pro-0813 | max | both | 36.0 | 0.674 | no | 23.06 | 1.7691 | no | -12.93 |  |
| gemini-3.1-pro-preview | unknown | dropped_in_v5 | 29.72 | 1.2986 | no |  |  |  |  |  |
| gemini-3.5-flash-lite | high | added_in_v5 |  |  |  | 11.66 | 0.6929 | no |  |  |
| gemini-3.5-flash-lite | unknown | dropped_in_v5 | 22.17 | 0.1927 | no |  |  |  |  |  |
| gemini-3.8-flash | high | both | 40.93 | 1.2428 | no | 30.55 | 7.766 | no | -10.38 |  |
| gemini-3.8-flash | medium | both | 39.77 | 0.931 | no | 29.98 | 6.2586 | no | -9.8 |  |
| gemini-4-argon | high | both | 52.56 | 1.9903 | no | 50.41 | 7.6827 | no | -2.15 |  |
| glm-5.3 | low | both | 34.3 | 0.8522 | no | 23.06 | 1.5749 | no | -11.24 |  |
| glm-5.3 | max | both | 44.78 | 2.0056 | no | 31.65 | 5.0093 | no | -13.12 |  |
| glm-5.3-flash | max | dropped_in_v5 | 41.81 | 0.2533 | no |  |  |  |  |  |
| glm-5.3-flash | unknown | dropped_in_v5 | 41.81 | 0.2533 | no |  |  |  |  |  |
| gpt-5.6-luna | max | dropped_in_v5 | 37.32 | 0.1783 | no |  |  |  |  |  |
| gpt-5.6-terra | high | dropped_in_v5 | 34.24 | 0.3379 | no |  |  |  |  |  |
| gpt-5.6-terra | low | dropped_in_v5 | 27.5 | 0.1445 | no |  |  |  |  |  |
| gpt-5.6-terra | max | dropped_in_v5 | 42.08 | 1.3987 | no |  |  |  |  |  |
| gpt-5.6-terra | medium | dropped_in_v5 | 30.09 | 0.1834 | no |  |  |  |  |  |
| gpt-5.6-terra | xhigh | dropped_in_v5 | 37.95 | 0.6318 | no |  |  |  |  |  |
| gpt-6-astra | high | both | 50.92 | 1.7253 | no | 54.85 | 3.2386 | yes | 3.93 |  |
| gpt-6-astra | low | both | 45.78 | 0.8175 | no | 43.03 | 1.4237 | no | -2.75 |  |
| gpt-6-astra | max | both | 52.67 | 3.2575 | no | 56.85 | 6.2447 | no | 4.18 |  |
| gpt-6-astra | medium | both | 49.57 | 1.5406 | no | 51.45 | 2.7324 | no | 1.88 |  |
| gpt-6-astra | xhigh | both | 52.39 | 2.3088 | no | 55.26 | 3.6589 | yes | 2.87 |  |
| gpt-6-luna | Non-reasoning | dropped_in_v5 | 18.47 | 0.0113 | no |  |  |  |  |  |
| gpt-6-luna | high | both | 32.93 | 0.029 | yes | 21.38 | 0.0729 | no | -11.55 |  |
| gpt-6-luna | low | both | 21.53 | 0.0045 | yes | 9.67 | 0.0051 | yes | -11.86 |  |
| gpt-6-luna | max | both | 38.12 | 0.0678 | yes | 27.76 | 0.152 | no | -10.37 |  |
| gpt-6-luna | medium | both | 29.93 | 0.0175 | yes | 18.3 | 0.0422 | yes | -11.62 |  |
| gpt-6-luna | none | added_in_v5 |  |  |  | 9.39 | 0.0714 | no |  |  |
| gpt-6-luna | xhigh | both | 34.56 | 0.0422 | yes | 23.3 | 0.1041 | no | -11.26 |  |
| gpt-6-sol | Non-reasoning | dropped_in_v5 | 28.51 | 0.3337 | no |  |  |  |  |  |
| gpt-6-sol | high | dropped_in_v5 | 42.4 | 0.3728 | no |  |  |  |  |  |
| gpt-6-sol | low | dropped_in_v5 | 34.15 | 0.1328 | no |  |  |  |  |  |
| gpt-6-sol | max | dropped_in_v5 | 47.63 | 1.0447 | no |  |  |  |  |  |
| gpt-6-sol | medium | dropped_in_v5 | 39.81 | 0.2473 | no |  |  |  |  |  |
| gpt-6-sol | xhigh | dropped_in_v5 | 44.24 | 0.5208 | no |  |  |  |  |  |
| gpt-6.1-sol | high | both | 50.24 | 0.3191 | yes | 50.23 | 0.591 | yes | -0.01 | 50 / 0 |
| gpt-6.1-sol | low | both | 42.08 | 0.1308 | yes | 34.5 | 0.1615 | yes | -7.58 | 35 / 0 |
| gpt-6.1-sol | max | both | 51.83 | 0.7242 | yes | 54.51 | 1.2721 | yes | 2.68 | 55 / 1.27 |
| gpt-6.1-sol | medium | both | 47.78 | 0.2137 | yes | 46.15 | 0.3594 | yes | -1.64 | 46 / 0 |
| gpt-6.1-sol | xhigh | both | 51.04 | 0.3929 | yes | 52.31 | 0.764 | yes | 1.27 | 52 / 0 |
| gpt-6.1-terra | high | added_in_v5 |  |  |  | 42.33 | 0.2593 | yes |  | 42 / 0 |
| gpt-6.1-terra | low | added_in_v5 |  |  |  | 29.22 | 0.0713 | yes |  | 29 / 0 |
| gpt-6.1-terra | max | added_in_v5 |  |  |  | 50.98 | 0.9611 | no |  | 51 / 0.96 |
| gpt-6.1-terra | medium | added_in_v5 |  |  |  | 38.12 | 0.1688 | yes |  | 38 / 0 |
| gpt-6.1-terra | none | added_in_v5 |  |  |  | 30.42 | 0.2259 | no |  | 30 / 0 |
| gpt-6.1-terra | xhigh | added_in_v5 |  |  |  | 44.95 | 0.4007 | no |  | 45 / 0 |
| grok-4.7 | high | both | 46.33 | 2.7261 | no | 35.01 | 6.1836 | no | -11.32 |  |
| grok-4.7 | low | both | 42.22 | 1.248 | no | 30.88 | 2.9222 | no | -11.33 |  |
| grok-4.7 | xhigh | both | 46.45 | 3.7383 | no | 34.34 | 7.5118 | no | -12.11 |  |
| kimi-k2-7-code | unknown | dropped_in_v5 | 25.81 | 0.5412 | no |  |  |  |  |  |
| kimi-k3 | low | dropped_in_v5 | 30.07 | 1.1463 | no |  |  |  |  |  |
| kimi-k3 | max | dropped_in_v5 | 43.59 | 2.0001 | no |  |  |  |  |  |
| mimo-v2.6-flash | unknown | both | 37.88 | 0.0623 | yes | 26.73 | 0.1285 | no | -11.16 |  |
| mimo-v2.6-pro | unknown | both | 46.32 | 0.1332 | yes | 29.89 | 0.2572 | no | -16.43 |  |
| minimax-m3 | unknown | both | 29.22 | 0.5076 | no | 18.73 | 1.2851 | no | -10.49 |  |
| mistral-large-3 | unknown | dropped_in_v5 | 9.27 | 0.0311 | no |  |  |  |  |  |
| mistral-large-4 | unknown | both | 38.38 | 1.1318 | no | 25.29 | 5.537 | no | -13.09 |  |
| mistral-medium-3-5 | unknown | dropped_in_v5 | 14.19 | 0.502 | no |  |  |  |  |  |
| mistral-small-4 | unknown | dropped_in_v5 | 11.27 | 0.015 | no |  |  |  |  |  |
| muse-spark-1-3 | max | both | 48.09 | 1.6049 | no | 36.42 | 2.3858 | no | -11.68 |  |
| muse-spark-1-3 | xhigh | dropped_in_v5 | 45.07 | 1.3678 | no |  |  |  |  |  |
| muse-wonder-1-0 | max | both | 52.39 | 4.5157 | no | 43.32 | 6.952 | no | -9.07 |  |
| qwen3.8-2.4t-a95b | unknown | dropped_in_v5 | 39.89 | 2.1559 | no |  |  |  |  |  |
| qwen3.8-27b | low | dropped_in_v5 | 26.2 | 1.0482 | no |  |  |  |  |  |
| qwen3.8-27b | medium | dropped_in_v5 | 27.55 | 1.1331 | no |  |  |  |  |  |
| qwen3.8-27b | xhigh | both | 33.7 | 1.0073 | no | 20.11 | 3.5295 | no | -13.59 |  |
| qwen3.8-max | unknown | dropped_in_v5 | 45.42 | 5.4085 | no |  |  |  |  |  |
| step-5-preview | unknown | both | 43.73 | 0.7175 | no | 28.53 | 2.3453 | no | -15.21 |  |
