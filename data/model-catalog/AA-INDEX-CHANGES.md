# AA Index row changes, 2026-10-28

Old columns are the previous chart's v4.3.2 observations (plus retained unpriced scores where available); new columns are the v5.0 snapshot. This is a change of measurement regime, not a claim that a model improved or regressed. Blank new cells mean missing v5 evidence, not a zero score or a rejected routing candidate. The last column preserves earlier partial-v5 JSON row values, which stored cost only at max; #137’s non-max costs remain in its unchanged prose notes. A dash is unknown, not zero; those source bytes are unchanged.

Scores, USD per AA Index task and global frontier membership are listed for every setting added, removed from current chart coverage or refreshed. Per-evaluation values and their separate comparability tags are in the [full snapshot](performance/artificial-analysis-intelligence-index-v5-full-2026-10-28.json). Historical v4 rows remain in the pack.

| model | effort | v4.3.2 score | v4.3.2 $/task | v4 frontier | v5.0 score | v5.0 $/task | v5 frontier | partial v5 score / $ |
|---|---|---|---|---|---|---|---|---|
| claude-fable-5-1 | high | 51.15 | 3.9125 | no | 47.02 | 7.5608 | no | — |
| claude-fable-5-1 | low | 46.82 | 2.371 | no | 38.48 | 4.4867 | no | — |
| claude-fable-5-1 | max | 53.35 | 7.6297 | no | 49.01 | 13.0393 | no | — |
| claude-fable-5-1 | medium | 48.92 | 2.9826 | no | 42.97 | 5.5805 | no | — |
| claude-fable-5-1 | xhigh | 53.2 | 5.9783 | no | 49.67 | 10.8855 | no | — |
| claude-haiku-4-5 | Reasoning | 16.88 | 0.2768 | no | — | — | — | — |
| claude-haiku-5-5 | high | 37.82 | 0.0794 | no | 29.62 | 0.1586 | yes | — |
| claude-haiku-5-5 | low | 29.45 | 0.0245 | no | 18.62 | 0.0438 | yes | — |
| claude-haiku-5-5 | max | 43.4 | 0.2128 | no | 36.23 | 0.411 | no | — |
| claude-haiku-5-5 | medium | 34.46 | 0.047 | no | 24.55 | 0.0918 | no | — |
| claude-haiku-5-5 | xhigh | 41.25 | 0.1238 | yes | 34.34 | 0.2484 | no | — |
| claude-opus-5-5 | high | 53.58 | 1.8225 | yes | 51.69 | 3.4647 | no | — |
| claude-opus-5-5 | low | 42.31 | 0.5512 | no | 34.5 | 0.9755 | no | — |
| claude-opus-5-5 | max | 57.62 | 5.982 | yes | 58.41 | 10.0364 | yes | — |
| claude-opus-5-5 | medium | 51.24 | 1.336 | no | 48.06 | 2.4511 | no | — |
| claude-opus-5-5 | xhigh | 55.99 | 3.4591 | yes | 56.99 | 6.0472 | yes | — |
| claude-sonnet-5-5 | high | 46.75 | 0.8844 | no | 41.12 | 1.6361 | no | — |
| claude-sonnet-5-5 | low | 35.87 | 0.3452 | no | 25.3 | 0.5113 | no | — |
| claude-sonnet-5-5 | max | 56.0 | 5.4609 | yes | 54.24 | 10.0206 | no | — |
| claude-sonnet-5-5 | medium | 40.84 | 0.4833 | no | 30.32 | 0.7807 | no | — |
| claude-sonnet-5-5 | xhigh | 51.9 | 2.012 | no | 49.57 | 4.092 | no | — |
| deepseek-v4-1-flash | max | 39.46 | 0.2652 | no | 32.49 | 0.6021 | no | — |
| deepseek-v4-pro-0813 | max | 36.0 | 0.674 | no | 23.06 | 1.7691 | no | — |
| gemini-3.1-pro-preview | unknown | 29.72 | 1.2986 | no | — | — | — | — |
| gemini-3.5-flash-lite | high | — | — | — | 11.66 | 0.6929 | no | — |
| gemini-3.5-flash-lite | unknown | 22.17 | 0.1927 | no | — | — | — | — |
| gemini-3.8-flash | high | 40.93 | 1.2428 | no | 30.55 | 7.766 | no | — |
| gemini-3.8-flash | low | 33.45 | — | unpriced | 22.35 | — | unpriced | — |
| gemini-3.8-flash | medium | 39.77 | 0.931 | no | 29.98 | 6.2586 | no | — |
| gemini-4-argon | high | 52.56 | 1.9903 | no | 50.41 | 7.6827 | no | — |
| glm-5.3 | low | 34.3 | 0.8522 | no | 23.06 | 1.5749 | no | — |
| glm-5.3 | max | 44.78 | 2.0056 | no | 31.65 | 5.0093 | no | — |
| glm-5.3-flash | max | 41.81 | 0.2533 | no | — | — | — | — |
| glm-5.3-flash | unknown | 41.81 | 0.2533 | no | — | — | — | — |
| gpt-5.6-luna | max | 37.32 | 0.1783 | no | — | — | — | — |
| gpt-5.6-terra | high | 34.24 | 0.3379 | no | — | — | — | — |
| gpt-5.6-terra | low | 27.5 | 0.1445 | no | — | — | — | — |
| gpt-5.6-terra | max | 42.08 | 1.3987 | no | — | — | — | — |
| gpt-5.6-terra | medium | 30.09 | 0.1834 | no | — | — | — | — |
| gpt-5.6-terra | xhigh | 37.95 | 0.6318 | no | — | — | — | — |
| gpt-6-astra | high | 50.92 | 1.7253 | no | 54.85 | 3.2386 | yes | — |
| gpt-6-astra | low | 45.78 | 0.8175 | no | 43.03 | 1.4237 | no | — |
| gpt-6-astra | max | 52.67 | 3.2575 | no | 56.85 | 6.2447 | no | — |
| gpt-6-astra | medium | 49.57 | 1.5406 | no | 51.45 | 2.7324 | no | — |
| gpt-6-astra | xhigh | 52.39 | 2.3088 | no | 55.26 | 3.6589 | yes | — |
| gpt-6-luna | high | 32.93 | 0.029 | yes | 21.38 | 0.0729 | no | — |
| gpt-6-luna | low | 21.53 | 0.0045 | yes | 9.67 | 0.0051 | yes | — |
| gpt-6-luna | max | 38.12 | 0.0678 | yes | 27.76 | 0.152 | no | — |
| gpt-6-luna | medium | 29.93 | 0.0175 | yes | 18.3 | 0.0422 | yes | — |
| gpt-6-luna | none | 18.47 | 0.0113 | no | 9.39 | 0.0714 | no | — |
| gpt-6-luna | xhigh | 34.56 | 0.0422 | yes | 23.3 | 0.1041 | no | — |
| gpt-6-sol | high | 42.4 | 0.3728 | no | — | — | — | — |
| gpt-6-sol | low | 34.15 | 0.1328 | no | — | — | — | — |
| gpt-6-sol | max | 47.63 | 1.0447 | no | — | — | — | — |
| gpt-6-sol | medium | 39.81 | 0.2473 | no | — | — | — | — |
| gpt-6-sol | none | 28.51 | 0.3337 | no | — | — | — | — |
| gpt-6-sol | xhigh | 44.24 | 0.5208 | no | — | — | — | — |
| gpt-6.1-sol | high | 50.24 | 0.3191 | yes | 50.23 | 0.591 | yes | 50 / — |
| gpt-6.1-sol | low | 42.08 | 0.1308 | yes | 34.5 | 0.1615 | yes | 35 / — |
| gpt-6.1-sol | max | 51.83 | 0.7242 | yes | 54.51 | 1.2721 | yes | 55 / 1.27 |
| gpt-6.1-sol | medium | 47.78 | 0.2137 | yes | 46.15 | 0.3594 | yes | 46 / — |
| gpt-6.1-sol | xhigh | 51.04 | 0.3929 | yes | 52.31 | 0.764 | yes | 52 / — |
| gpt-6.1-terra | high | — | — | — | 42.33 | 0.2593 | yes | 42 / — |
| gpt-6.1-terra | low | — | — | — | 29.22 | 0.0713 | yes | 29 / — |
| gpt-6.1-terra | max | — | — | — | 50.98 | 0.9611 | no | 51 / 0.96 |
| gpt-6.1-terra | medium | — | — | — | 38.12 | 0.1688 | yes | 38 / — |
| gpt-6.1-terra | none | — | — | — | 30.42 | 0.2259 | no | 30 / — |
| gpt-6.1-terra | xhigh | — | — | — | 44.95 | 0.4007 | no | 45 / — |
| grok-4.7 | high | 46.33 | 2.7261 | no | 35.01 | 6.1836 | no | — |
| grok-4.7 | low | 42.22 | 1.248 | no | 30.88 | 2.9222 | no | — |
| grok-4.7 | xhigh | 46.45 | 3.7383 | no | 34.34 | 7.5118 | no | — |
| kimi-k2-7-code | unknown | 25.81 | 0.5412 | no | — | — | — | — |
| kimi-k3 | low | 30.07 | 1.1463 | no | — | — | — | — |
| kimi-k3 | max | 43.59 | 2.0001 | no | — | — | — | — |
| mimo-v2.6-flash | unknown | 37.88 | 0.0623 | yes | 26.73 | 0.1285 | no | — |
| mimo-v2.6-pro | unknown | 46.32 | 0.1332 | yes | 29.89 | 0.2572 | no | — |
| minimax-m3 | unknown | 29.22 | 0.5076 | no | 18.73 | 1.2851 | no | — |
| mistral-large-3 | unknown | 9.27 | 0.0311 | no | — | — | — | — |
| mistral-large-4 | unknown | 38.38 | 1.1318 | no | 25.29 | 5.537 | no | — |
| mistral-medium-3-5 | unknown | 14.19 | 0.502 | no | — | — | — | — |
| mistral-small-4 | unknown | 11.27 | 0.015 | no | — | — | — | — |
| muse-spark-1-3 | max | 48.09 | 1.6049 | no | 36.42 | 2.3858 | no | — |
| muse-spark-1-3 | xhigh | 45.07 | 1.3678 | no | — | — | — | — |
| muse-wonder-1-0 | max | 52.39 | 4.5157 | no | 43.32 | 6.952 | no | — |
| muse-wonder-full-1-0 | max | 55.8 | — | unpriced | 49.73 | — | unpriced | — |
| qwen3.8-2.4t-a95b | unknown | 39.89 | 2.1559 | no | — | — | — | — |
| qwen3.8-27b | low | 26.2 | 1.0482 | no | — | — | — | — |
| qwen3.8-27b | medium | 27.55 | 1.1331 | no | — | — | — | — |
| qwen3.8-27b | xhigh | 33.7 | 1.0073 | no | 20.11 | 3.5295 | no | — |
| qwen3.8-max | unknown | 45.42 | 5.4085 | no | — | — | — | — |
| step-5-preview | unknown | 43.73 | 0.7175 | no | 28.53 | 2.3453 | no | — |
