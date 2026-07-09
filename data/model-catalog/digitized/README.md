# Digitized charts

**Not a scalable scraper.** A **case-by-case** way to get **reliable** numbers when
vendors bury curves in figures. Prefer published tables when they exist.

## Philosophy

1. **On demand** — digitize when an operating-point decision needs the series; don’t batch the whole web.
2. **Reliable over automatic** — a careful one-off with provenance beats a flaky full pipeline.
3. **Honest grade** — catalog rows must say `digitized_chart` (never pretend they’re vendor tables).
4. **Prefer labels** — if the figure prints numbers, transcribe those (`from-labels`). Only use geometry for unlabeled markers.

## Workflow (per chart)

```text
1. Download the largest PNG (not a screenshot of the page).
2. Record source_url + sha256 (asset subcommand or geometric output).
3. If numbers are printed → from-labels (vision/OCR + eyes).
4. If markers only → geometric with an explicit plot-box + axis scales.
5. Spot-check 2–3 points by eye against the image.
6. Optional: dual-read (independent vision estimate) → record median |Δ|.
7. Merge into performance/*.json with source_id + caveat.
```

## Commands

```bash
# Printed table / bars — most reliable
./scripts/digitize_chart.py from-labels \
  --image data/model-catalog/digitized/assets/anthropic-sonnet5-benchmark-table.png \
  --points /tmp/points.json \
  --page-url 'https://www.anthropic.com/news/claude-sonnet-5' \
  --title 'Sonnet 5 benchmark table' \
  --out data/model-catalog/digitized/example-labels.json

# Unlabeled markers — workable; tune --plot-box once per figure family
./scripts/digitize_chart.py geometric \
  --image data/model-catalog/digitized/assets/anthropic-sonnet5-browsecomp-effort.png \
  --source-url 'https://cdn.sanity.io/images/4zrzovbb/website/cd0df787f39b6408dcba539fba93f817f2f3c0b4-3840x2160.png' \
  --page-url 'https://www.anthropic.com/news/claude-sonnet-5' \
  --title 'BrowseComp effort curves' \
  --x-scale log --x-min 2 --x-max 50 --y-min 60 --y-max 90 \
  --plot-box 430,430,3600,1785 \
  --series orange:claude-sonnet-5 gold:claude-opus-4-8 \
  --efforts low,medium,high,xhigh,max \
  --out /tmp/browsecomp.json
```

`--plot-box` is the only awkward bit: `left,top,right,bottom` in pixels for the
data area. Set it once while looking at the image; reuse for siblings from the
same post if the layout matches.

## Accuracy expectations

| Kind | Method | Reliability |
|------|--------|-------------|
| Printed table/bars | `from-labels` | **Very high** (transcription) |
| Clean filled markers | `geometric` | **High** if axes/box checked by eye (Sonnet 5 pilot: ~0.2–1 pp on y) |
| Vision-only draft | dual_read only | Draft — not catalog grade alone |

Always keep: **asset URL, sha256, axis/box, method, caveats**.

## Sonnet 5 pilot (v0.4.1)

| File | Content |
|------|---------|
| `anthropic-sonnet5-browsecomp-effort.json` | Effort × cost (geometric) |
| `anthropic-sonnet5-osworld-effort.json` | Effort × cost (geometric) |
| `anthropic-sonnet5-benchmark-table.json` | Printed table |
| `anthropic-sonnet5-misaligned-behavior.json` | Printed bars |
| `anthropic-sonnet5-firefox147-exploit.json` | Printed bars |

Catalog: `performance/anthropic-sonnet5-digitized-2026-07.json`.
