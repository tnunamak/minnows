#!/usr/bin/env python3
"""Digitize chart images into provenance-backed JSON for model-catalog.

Accuracy strategy (best → worst):
  1. structured table / labeled bars (OCR or vision of printed numbers)
  2. geometric: calibrate axis pixels → data units, detect markers
  3. pure vision estimate (draft only; needs dual-read)

Usage:
  # geometric scatter (log-x cost, linear-y pass rate)
  ./scripts/digitize_chart.py geometric \\
    --image tmp/charts/assets/browsecomp.png \\
    --source-url 'https://cdn.sanity.io/images/.../browsecomp.png' \\
    --page-url 'https://www.anthropic.com/news/claude-sonnet-5' \\
    --title 'BrowseComp effort curves' \\
    --x-scale log --x-min 2 --x-max 50 --y-min 60 --y-max 90 \\
    --plot-box 480,320,3400,1750 \\
    --series orange:claude-sonnet-5 gold:claude-opus-4-8 gray:claude-sonnet-4-6 \\
    --out data/model-catalog/digitized/browsecomp-sonnet5.json

  # hash + provenance wrapper only
  ./scripts/digitize_chart.py asset --url URL --out path.json

Stdlib + Pillow + numpy only (no OpenCV required).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    import numpy as np
    from PIL import Image
except ImportError as e:
    print("need Pillow and numpy:", e, file=sys.stderr)
    sys.exit(2)


# ---- color windows in RGB (tuned for Anthropic Sonnet 5 charts; override per chart) ----
SERIES_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    # name: (rgb_lo, rgb_hi) inclusive
    "orange": ((200, 70, 35), (255, 140, 90)),  # Sonnet 5
    "gold": ((210, 140, 0), (255, 190, 40)),  # Opus 4.8
    "gray": ((148, 148, 143), (178, 178, 175)),  # Sonnet 4.6 (fragile)
    "green": ((0, 120, 80), (80, 200, 140)),
    "teal": ((40, 160, 140), (120, 220, 200)),
    "brown": ((120, 60, 0), (200, 130, 60)),
}


@dataclass
class AxisCal:
    x_scale: str  # linear | log
    y_scale: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    # plot box in pixel coords: left, top, right, bottom (inclusive data area)
    left: float
    top: float
    right: float
    bottom: float

    def px_to_data(self, x_px: float, y_px: float) -> tuple[float, float]:
        # x: left→right, y: bottom→top in data space
        fx = (x_px - self.left) / (self.right - self.left)
        fy = (self.bottom - y_px) / (self.bottom - self.top)
        fx = min(1.0, max(0.0, fx))
        fy = min(1.0, max(0.0, fy))
        if self.x_scale == "log":
            x = math.exp(
                math.log(self.x_min) + fx * (math.log(self.x_max) - math.log(self.x_min))
            )
        else:
            x = self.x_min + fx * (self.x_max - self.x_min)
        if self.y_scale == "log":
            y = math.exp(
                math.log(self.y_min) + fy * (math.log(self.y_max) - math.log(self.y_min))
            )
        else:
            y = self.y_min + fy * (self.y_max - self.y_min)
        return x, y


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "minnows-digitize/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())
    return dest


def load_rgb(path: Path) -> tuple[Image.Image, np.ndarray]:
    im = Image.open(path).convert("RGBA")
    arr = np.asarray(im)
    # composite on white for detection
    rgb = arr[..., :3].astype(np.float32)
    a = arr[..., 3:4].astype(np.float32) / 255.0
    rgb = (rgb * a + 255.0 * (1.0 - a)).astype(np.uint8)
    return im, rgb


def mask_color(rgb: np.ndarray, lo: tuple[int, int, int], hi: tuple[int, int, int]) -> np.ndarray:
    m = (
        (rgb[..., 0] >= lo[0])
        & (rgb[..., 0] <= hi[0])
        & (rgb[..., 1] >= lo[1])
        & (rgb[..., 1] <= hi[1])
        & (rgb[..., 2] >= lo[2])
        & (rgb[..., 2] <= hi[2])
    )
    return m


def density_peak_centroids(
    mask: np.ndarray,
    *,
    cell: int = 18,
    min_count: int = 1400,
    merge: int = 55,
    rad: int = 28,
) -> list[tuple[float, float, int]]:
    """Find filled markers as local density peaks (integral image).

    Thin line strokes score lower than solid dots; keep high-count peaks only.
    Returns (cx, cy, window_count).
    """
    h, w = mask.shape
    ii = np.pad(mask.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def wsum(x0: int, y0: int, x1: int, y1: int) -> int:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        return int(ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0])

    peaks: list[tuple[int, int, int]] = []
    for y in range(cell // 2, h, cell):
        for x in range(cell // 2, w, cell):
            c = wsum(x - rad, y - rad, x + rad, y + rad)
            if c >= min_count:
                peaks.append((x, y, c))
    peaks.sort(key=lambda t: t[2], reverse=True)
    kept: list[tuple[float, float, int]] = []
    for x, y, _c in peaks:
        if any(math.hypot(x - kx, y - ky) < merge for kx, ky, _ in kept):
            continue
        x0, y0 = max(0, x - rad), max(0, y - rad)
        x1, y1 = min(w, x + rad), min(h, y + rad)
        ys, xs = np.where(mask[y0:y1, x0:x1])
        if len(xs) < min_count // 2:
            continue
        kept.append((float(x0 + xs.mean()), float(y0 + ys.mean()), int(len(xs))))
    return kept


def geometric_extract(
    image: Path,
    cal: AxisCal,
    series_map: dict[str, str],
    crop: tuple[int, int, int, int] | None = None,
    min_count: int = 1400,
    keep_per_series: int | None = 5,
) -> list[dict[str, Any]]:
    _, rgb = load_rgb(image)
    if crop:
        l, t, r, b = crop
        rgb = rgb[t:b, l:r]
        cal = AxisCal(
            cal.x_scale,
            cal.y_scale,
            cal.x_min,
            cal.x_max,
            cal.y_min,
            cal.y_max,
            cal.left - l,
            cal.top - t,
            cal.right - l,
            cal.bottom - t,
        )
    points: list[dict[str, Any]] = []
    l, t, r, b = int(cal.left), int(cal.top), int(cal.right), int(cal.bottom)
    for color_name, model_id in series_map.items():
        if color_name not in SERIES_COLORS:
            raise SystemExit(f"unknown series color {color_name!r}; known: {list(SERIES_COLORS)}")
        lo, hi = SERIES_COLORS[color_name]
        m = mask_color(rgb, lo, hi)
        mm = np.zeros_like(m)
        mm[t:b, l:r] = m[t:b, l:r]
        cents = density_peak_centroids(mm, min_count=min_count)
        # drop bottom-right legend-ish peaks
        filtered: list[tuple[float, float, int]] = []
        for cx, cy, n in cents:
            if cx > l + 0.85 * (r - l) and cy > t + 0.55 * (b - t):
                continue
            filtered.append((cx, cy, n))
        if keep_per_series is not None and len(filtered) > keep_per_series:
            filtered = sorted(filtered, key=lambda z: -z[2])[:keep_per_series]
        filtered.sort(key=lambda z: z[0])
        for cx, cy, n in filtered:
            x, y = cal.px_to_data(cx, cy)
            points.append(
                {
                    "model": model_id,
                    "series_color": color_name,
                    "x": round(x, 4),
                    "y": round(y, 4),
                    "pixel": {"x": round(cx, 1), "y": round(cy, 1), "n": n},
                }
            )
    points.sort(key=lambda p: (p["model"], p["x"]))
    return points


def assign_effort_by_order(points: list[dict[str, Any]], efforts: list[str]) -> list[dict[str, Any]]:
    """Label points left→right within each model with effort ladder."""
    by_model: dict[str, list[dict[str, Any]]] = {}
    for p in points:
        by_model.setdefault(p["model"], []).append(p)
    out: list[dict[str, Any]] = []
    for model, pts in by_model.items():
        pts = sorted(pts, key=lambda p: p["x"])
        for i, p in enumerate(pts):
            p = dict(p)
            if i < len(efforts):
                p["effort"] = efforts[i]
            else:
                p["effort"] = f"point_{i}"
            out.append(p)
    out.sort(key=lambda p: (p["model"], p["x"]))
    return out


def build_doc(
    *,
    image: Path,
    source_url: str | None,
    page_url: str | None,
    title: str,
    method: str,
    points: list[dict[str, Any]],
    axis: dict[str, Any],
    notes: str,
    dual_read: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    digest = sha256_file(image)
    im = Image.open(image)
    err_x = None
    err_y = None
    if dual_read:
        # match by model+effort when present
        diffs_x: list[float] = []
        diffs_y: list[float] = []
        idx = {(p.get("model"), p.get("effort")): p for p in points if "effort" in p}
        for d in dual_read:
            key = (d.get("model"), d.get("effort"))
            if key in idx and "x" in d and "y" in d:
                diffs_x.append(abs(idx[key]["x"] - d["x"]))
                diffs_y.append(abs(idx[key]["y"] - d["y"]))
        if diffs_x:
            err_x = round(float(np.median(diffs_x)), 4)
            err_y = round(float(np.median(diffs_y)), 4)

    return {
        "schema_version": 1,
        "kind": "digitized_chart",
        "title": title,
        "retrieved_at": date.today().isoformat(),
        "page_url": page_url,
        "asset": {
            "path": str(image),
            "source_url": source_url,
            "sha256": digest,
            "width": im.width,
            "height": im.height,
            "format": im.format or image.suffix.lstrip("."),
        },
        "digitization": {
            "method": method,
            "axis": axis,
            "error_estimate": {
                "x_abs_median_vs_dual_read": err_x,
                "y_abs_median_vs_dual_read": err_y,
                "notes": "error_estimate filled only when --dual-read JSON provided",
            },
            "operator": "automated",
        },
        "points": points,
        "dual_read": dual_read or [],
        "notes": notes,
        "catalog_hint": {
            "source_type": "digitized_chart",
            "unit_for_y": "pass_rate or accuracy depending on metric",
            "unit_for_x": "USD cost per task when x is cost",
        },
    }


def cmd_asset(args: argparse.Namespace) -> int:
    dest = Path(args.out)
    if args.url:
        download(args.url, dest if dest.suffix else dest / "asset.bin")
        path = dest if dest.suffix else dest / "asset.bin"
    else:
        path = Path(args.image)
    doc = {
        "schema_version": 1,
        "kind": "chart_asset",
        "retrieved_at": date.today().isoformat(),
        "page_url": args.page_url,
        "asset": {
            "path": str(path),
            "source_url": args.url or args.source_url,
            "sha256": sha256_file(path),
        },
    }
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        im = Image.open(path)
        doc["asset"]["width"] = im.width
        doc["asset"]["height"] = im.height
    out = Path(args.meta_out or (str(path) + ".meta.json"))
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps(doc, indent=2))
    return 0


def cmd_geometric(args: argparse.Namespace) -> int:
    image = Path(args.image)
    if args.url and not image.exists():
        download(args.url, image)
    box = [float(x) for x in args.plot_box.split(",")]
    if len(box) != 4:
        raise SystemExit("--plot-box needs left,top,right,bottom")
    cal = AxisCal(
        x_scale=args.x_scale,
        y_scale=args.y_scale,
        x_min=args.x_min,
        x_max=args.x_max,
        y_min=args.y_min,
        y_max=args.y_max,
        left=box[0],
        top=box[1],
        right=box[2],
        bottom=box[3],
    )
    series_map: dict[str, str] = {}
    for item in args.series:
        # color:model
        if ":" not in item:
            raise SystemExit(f"--series item must be color:model_id, got {item!r}")
        c, m = item.split(":", 1)
        series_map[c] = m

    points = geometric_extract(
        image,
        cal,
        series_map,
        min_count=args.min_count,
        keep_per_series=args.keep if args.keep > 0 else None,
    )
    efforts = [e.strip() for e in args.efforts.split(",") if e.strip()]
    if efforts:
        points = assign_effort_by_order(points, efforts)

    dual = None
    if args.dual_read:
        dual = json.loads(Path(args.dual_read).read_text())
        if isinstance(dual, dict) and "points" in dual:
            dual = dual["points"]

    doc = build_doc(
        image=image,
        source_url=args.source_url or args.url,
        page_url=args.page_url,
        title=args.title,
        method="geometric_marker_centroids",
        points=points,
        axis={
            "x": args.x_name,
            "y": args.y_name,
            "x_scale": args.x_scale,
            "y_scale": args.y_scale,
            "x_min": args.x_min,
            "x_max": args.x_max,
            "y_min": args.y_min,
            "y_max": args.y_max,
            "plot_box_px": box,
        },
        notes=args.notes
        or "Geometric digitization of marker centroids. Effort labels assigned left→right within series.",
        dual_read=dual,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out} points={len(points)}")
    for p in points:
        print(
            f"  {p['model']:20} effort={p.get('effort','?'):6} "
            f"x={p['x']:<10} y={p['y']}"
        )
    if doc["digitization"]["error_estimate"]["x_abs_median_vs_dual_read"] is not None:
        e = doc["digitization"]["error_estimate"]
        print(f"dual-read median |Δx|={e['x_abs_median_vs_dual_read']} |Δy|={e['y_abs_median_vs_dual_read']}")
    return 0


def cmd_from_labels(args: argparse.Namespace) -> int:
    """Ingest explicitly provided labeled values (from OCR/vision of printed numbers)."""
    image = Path(args.image)
    points = json.loads(Path(args.points).read_text())
    if isinstance(points, dict) and "points" in points:
        points = points["points"]
    doc = build_doc(
        image=image,
        source_url=args.source_url,
        page_url=args.page_url,
        title=args.title,
        method="labeled_value_read",
        points=points,
        axis={"notes": "values read from printed labels on chart, not geometric inversion"},
        notes=args.notes
        or "Numbers transcribed from visible chart labels (vision/OCR). Prefer over geometric when labels present.",
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out} points={len(points)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("asset", help="Download/hash an asset with provenance")
    a.add_argument("--url")
    a.add_argument("--image")
    a.add_argument("--source-url")
    a.add_argument("--page-url")
    a.add_argument("--out", required=True)
    a.add_argument("--meta-out")
    a.set_defaults(func=cmd_asset)

    g = sub.add_parser("geometric", help="Calibrated marker extraction")
    g.add_argument("--image", required=True)
    g.add_argument("--url")
    g.add_argument("--source-url")
    g.add_argument("--page-url")
    g.add_argument("--title", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--x-scale", choices=["linear", "log"], default="log")
    g.add_argument("--y-scale", choices=["linear", "log"], default="linear")
    g.add_argument("--x-min", type=float, required=True)
    g.add_argument("--x-max", type=float, required=True)
    g.add_argument("--y-min", type=float, required=True)
    g.add_argument("--y-max", type=float, required=True)
    g.add_argument("--x-name", default="cost_per_task_usd")
    g.add_argument("--y-name", default="pass_rate_pct")
    g.add_argument("--plot-box", required=True, help="left,top,right,bottom px")
    g.add_argument(
        "--series",
        nargs="+",
        required=True,
        help="color:model_id pairs (orange/gold/gray/green/teal/brown)",
    )
    g.add_argument(
        "--efforts",
        default="low,medium,high,xhigh,max",
        help="comma list assigned left→right per series",
    )
    g.add_argument("--dual-read", help="JSON file of points for error estimate")
    g.add_argument("--min-count", type=int, default=1400, help="density peak min window count")
    g.add_argument("--keep", type=int, default=5, help="max markers kept per series (densest)")
    g.add_argument("--notes", default="")
    g.set_defaults(func=cmd_geometric)

    l = sub.add_parser("from-labels", help="Ingest labeled values JSON")
    l.add_argument("--image", required=True)
    l.add_argument("--points", required=True, help="JSON list of {model,y,...} or {points:[...]}")
    l.add_argument("--source-url")
    l.add_argument("--page-url")
    l.add_argument("--title", required=True)
    l.add_argument("--out", required=True)
    l.add_argument("--notes", default="")
    l.set_defaults(func=cmd_from_labels)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
