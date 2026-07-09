# Digitized charts

Numeric series extracted from official **figure images** when vendors publish
curves without tables.

## How

```bash
# Geometric (log-cost × pass-rate markers) — Sonnet 5 pilot example
./scripts/digitize_chart.py geometric \
  --image data/model-catalog/digitized/assets/anthropic-sonnet5-browsecomp-effort.png \
  --source-url 'https://cdn.sanity.io/images/4zrzovbb/website/cd0df787f39b6408dcba539fba93f817f2f3c0b4-3840x2160.png' \
  --page-url 'https://www.anthropic.com/news/claude-sonnet-5' \
  --title 'BrowseComp effort curves' \
  --x-scale log --x-min 2 --x-max 50 --y-min 60 --y-max 90 \
  --plot-box 430,430,3600,1785 \
  --series orange:claude-sonnet-5 gold:claude-opus-4-8 \
  --efforts low,medium,high,xhigh,max \
  --out /tmp/out.json

# Labeled bars/tables (paste vision/OCR numbers)
./scripts/digitize_chart.py from-labels \
  --image path/to/figure.png \
  --points points.json \
  --title '…' --out /tmp/out.json
```

## Accuracy

| Kind | Method | Typical error |
|------|--------|----------------|
| Printed table/bars | `labeled_value_read` | Transcription only |
| Filled markers on clean axes | `density_peak_marker_centroids` | ~0.2–1 pp on y for Sonnet 5 pilot; cost depends on axis box |
| Vision-only draft | dual_read comparison | Higher; not catalog grade alone |

Always store: asset URL, **sha256**, axis calibration, method, dual-read Δ if available.

## Sonnet 5 pilot (v0.4.1)

| File | Content |
|------|---------|
| `anthropic-sonnet5-browsecomp-effort.json` | Effort × cost curves (geometric) |
| `anthropic-sonnet5-osworld-effort.json` | Effort × cost curves (geometric) |
| `anthropic-sonnet5-benchmark-table.json` | Printed table |
| `anthropic-sonnet5-misaligned-behavior.json` | Printed bars |
| `anthropic-sonnet5-firefox147-exploit.json` | Printed bars |

Catalog scores: `performance/anthropic-sonnet5-digitized-2026-07.json`.
