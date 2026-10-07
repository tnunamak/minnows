#!/usr/bin/env python3
"""Render the model-catalog README charts from the pack's own data.

  scripts/render_model_catalog_charts.py          # write charts/*.svg and the README block
  scripts/render_model_catalog_charts.py --png    # also write charts/social-card.png (needs Chrome)
  scripts/render_model_catalog_charts.py --check  # exit 1 if any of them is out of date

The script knows no provider, model, tier, effort level or benchmark. It reads:

  guide.json          which quota pools and tiers to show, task-family grouping, noise floor,
                      and (`hero`) the metric, comparability rule and vendor colours of the
                      cost-vs-intelligence chart
  models.json         the current GA model in each tier (newest `released`), display names
  capabilities/       which effort levels each model offers, and their order
  pricing/            USD list price per token (kind == api_usd)
  metrics.json        metric names and direction
  performance/        every score row that has an effort level and a USD cost per task

The cost-vs-intelligence chart (`build_hero`) plots one metric against cost per task for
every vendor. guide.json `hero.notes` states the rule that decides which rows may share it.

For each pool and task family, each (model, effort) setting gets one verdict, computed
only inside one comparability group (one benchmark, one source, one snapshot) at a time:

  try           no benchmark shows a cheaper setting in the same pool that scores within
                the noise floor, and at least one benchmark compares it with another model
  self_only     as `try`, but only ever compared with its own effort levels
  cheaper       some cheaper setting scores as well in every benchmark both appear in
  lower_effort  as `cheaper`, but only lower efforts of this same model do it
  mixed         ruled out by some benchmarks, not others
  no_data       no benchmark in this family measures it
  not_offered   the model does not offer this effort level

Stdlib only; the SVG is written by hand so layout is exact and output is deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "data" / "model-catalog"
README = PACK / "README.md"
CHARTS = PACK / "charts"
BEGIN, END = "<!-- option-map:begin -->", "<!-- option-map:end -->"
CARD = CHARTS / "social-card.png"  # link-preview image, written only with --png
CARD_SIZE = (1200, 630)
CARD_KEY = b"svg-sha256"  # PNG tEXt key: hash of the SVG the PNG was made from, so --check needs no Chrome


# ---------------------------------------------------------------- pack data


def load(rel: str):
    return json.loads((PACK / rel).read_text())


@dataclass
class Pack:
    guide: dict
    models: dict
    metrics: dict
    rows: list
    usd_prices: dict
    surfaces: list
    hero_rows: list = field(default_factory=list)  # every score row of guide hero.metric_id

    @classmethod
    def read(cls) -> "Pack":
        guide = load("guide.json")
        cost = guide["cost"]
        hero_metric = (guide.get("hero") or {}).get("metric_id")
        rows, hero_rows = [], []
        for f in sorted((PACK / "performance").glob("*.json")):
            for r in load(f"performance/{f.name}").get("scores") or []:
                if r.get("effort") and (r.get("cost") or {}).get("unit") == cost["score_cost_unit"]:
                    rows.append({**r, "_file": f"performance/{f.name}"})
                if hero_metric and r.get("metric_id") == hero_metric:
                    hero_rows.append({**r, "_file": f"performance/{f.name}"})
        prices = {}
        for f in sorted((PACK / "pricing").glob("*.json")):
            doc = load(f"pricing/{f.name}")
            if doc.get("kind") == cost["price_kind"]:
                for mid, p in (doc.get("models") or {}).items():
                    if "fresh_input_per_m" in p:
                        prices.setdefault(mid, (float(p["fresh_input_per_m"]), float(p["output_per_m"])))
        surfaces = []
        for f in sorted((PACK / "capabilities").glob("*.json")):
            surfaces += load(f"capabilities/{f.name}").get("surfaces", [])
        return cls(
            guide=guide,
            models={m["id"]: m for m in load("models.json")["models"]},
            metrics={m["id"]: m for m in load("metrics.json")["metrics"]},
            rows=rows,
            usd_prices=prices,
            surfaces=surfaces,
            hero_rows=hero_rows,
        )

    def effort_order(self) -> list[str]:
        """Merge every surface's ordered valid_efforts list into one order."""
        after = defaultdict(set)
        seen: list[str] = []
        for s in self.surfaces:
            levels = s.get("valid_efforts") or []
            for i, e in enumerate(levels):
                if e not in seen:
                    seen.append(e)
                after[e].update(levels[i + 1 :])
        order: list[str] = []
        pending = list(seen)
        while pending:
            # next = a level no other pending level must come before
            nxt = next(e for e in pending if not any(e in after[o] and o != e for o in pending))
            order.append(nxt)
            pending.remove(nxt)
        return order

    def current_model(self, pool: dict, tier: str) -> dict:
        cands = [
            m for m in self.models.values()
            if m.get("provider") in pool["providers"] and m.get("tier") == tier
            and m.get("status") == "ga" and m.get("access") != "restricted"
        ]
        if not cands:
            raise SystemExit(f"guide.json: no GA model in tier {tier!r} for pool {pool['id']!r}")
        undated = [m["id"] for m in cands if not m.get("released")]
        if undated and len(cands) > 1:
            raise SystemExit(f"models.json: cannot pick the newest {tier!r} model; no `released` on {undated}")
        return max(cands, key=lambda m: m.get("released") or "")

    def offered_efforts(self, pool: dict, model: dict) -> list[str] | None:
        if model.get("effort_parameter") is False:
            return []
        for surface in pool["surfaces"]:
            for s in self.surfaces:
                if s.get("model") == model["id"] and s.get("surface") == surface:
                    return s.get("valid_efforts") or []
        return None  # unknown

    def noise_floor(self, metric_id: str, unit: str | None) -> float | None:
        nf = self.guide["noise_floor"]
        return nf["by_metric"].get(metric_id, nf["by_unit"].get(unit or ""))


def display_name(model: dict) -> str:
    return model.get("name") or model["id"]


# ---------------------------------------------------------------- verdicts


@dataclass
class Group:
    family: str
    metric_id: str
    name: str
    file: str
    source_type: str
    grade: str
    models: list = field(default_factory=list)
    opts: list = field(default_factory=list)  # (model, effort, score, cost) for usable settings
    floor: float = 0.0
    higher: bool = True
    unit: str = ""


@dataclass
class Cell:
    status: str = ""
    # alternative (model, effort) -> one bool per shared benchmark: "cheaper there and scores as well"
    vs: dict = field(default_factory=lambda: defaultdict(list))
    groups: set = field(default_factory=set)

    def rulers(self) -> list[tuple]:
        """Alternatives that beat this setting in every benchmark both appear in."""
        return [y for y, hits in self.vs.items() if all(hits)]

    def best_split(self) -> tuple | None:
        split = [(sum(h), len(h), y) for y, h in self.vs.items() if any(h) and not all(h)]
        return max(split) if split else None


def verdict(cell: Cell, model: str, effort: str, rank: dict) -> str:
    rulers = cell.rulers()
    if rulers:
        if any(y[0] != model for y in rulers):
            return "cheaper"
        return "lower_effort" if any(rank[y[1]] < rank[effort] for y in rulers) else "cheaper"
    if cell.best_split():
        return "mixed"
    return "try" if any(y[0] != model for y in cell.vs) else "self_only"


def build(pack: Pack) -> dict:
    fam_of = {raw: f["id"] for f in pack.guide["task_families"] for raw in f["includes"]}
    grouped = defaultdict(list)
    for r in pack.rows:
        fam = fam_of.get(r.get("task_family"))
        if fam:
            grouped[(fam, r["metric_id"], r.get("comparability_group"), r.get("snapshot_id"))].append(r)

    rank = {e: i for i, e in enumerate(pack.effort_order())}
    out = {"pools": []}
    for pool in pack.guide["pools"]:
        tiers = []
        for t in pool["tiers"]:
            m = pack.current_model(pool, t["tier"])
            tiers.append({**t, "model": m, "efforts": pack.offered_efforts(pool, m),
                          "price": pack.usd_prices.get(m["id"])})
        current = {t["model"]["id"] for t in tiers}
        offered_by = {t["model"]["id"]: t["efforts"] for t in tiers}

        def usable(model_id, effort):
            offered = offered_by.get(model_id)
            return model_id in current and (offered is None or effort in offered)
        families = []
        for fam in pack.guide["task_families"]:
            cells = {}
            groups = []
            skipped = set()
            for (fid, metric_id, *_), rs in sorted(grouped.items(), key=lambda kv: str(kv[0])):
                if fid != fam["id"]:
                    continue
                opts = [(r["model"], r["effort"], r["score"], r["cost"]["value"]) for r in rs if usable(r["model"], r["effort"])]
                if len(opts) < 2:
                    continue
                floor = pack.noise_floor(metric_id, rs[0].get("unit"))
                if floor is None:
                    skipped.add(metric_id)
                    continue
                higher = pack.metrics.get(metric_id, {}).get("direction", "higher_better") != "lower_better"
                g = Group(fam["id"], metric_id, pack.metrics.get(metric_id, {}).get("name", metric_id),
                          rs[0]["_file"], rs[0].get("source_type", "?"), rs[0].get("evidence_grade", "?"),
                          sorted({o[0] for o in opts}), opts, floor, higher, rs[0].get("unit") or "")
                groups.append(g)
                for m, e, sc, c in opts:
                    cell = cells.setdefault((m, e), Cell())
                    cell.groups.add(len(groups))
                    for m2, e2, sc2, c2 in opts:
                        if (m2, e2) != (m, e):
                            as_good = sc2 > sc - floor if higher else sc2 < sc + floor
                            cell.vs[(m2, e2)].append(c2 < c and as_good)
            for t in tiers:
                mid = t["model"]["id"]
                offered = t["efforts"]
                for e in pack.effort_order():
                    cell = cells.setdefault((mid, e), Cell())
                    if not cell.vs:
                        cell.status = "not_offered" if offered is not None and e not in offered else "no_data"
                    else:
                        cell.status = verdict(cell, mid, e, rank)
            families.append({**fam, "groups": groups, "cells": cells, "skipped": sorted(skipped),
                             "benchmarks": len({g.metric_id for g in groups})})
        out["pools"].append({**pool, "tiers": tiers, "families": families})
    measured = {e for p in out["pools"] for f in p["families"] for (m, e), c in f["cells"].items()
                if c.status not in ("no_data", "not_offered")}
    out["efforts"] = [e for e in pack.effort_order() if e in measured]
    # offered efforts no benchmark measures: named in the footer so they do not vanish silently
    unmeasured = defaultdict(list)
    for p in out["pools"]:
        for t in p["tiers"]:
            for e in t["efforts"] or []:
                if e not in measured:
                    unmeasured[e].append(display_name(t["model"]))
    out["unmeasured"] = [(e, unmeasured[e]) for e in pack.effort_order() if e in unmeasured]
    out["cost"] = pack.guide["cost"]
    out["rank"] = rank
    out["effort_labels"] = pack.guide.get("effort_labels", {})
    out["score_display"] = pack.guide.get("score_display", {"by_unit": {}, "by_metric": {}})
    out["tie_rule"] = pack.guide["noise_floor"]["label"]
    return out


# ---------------------------------------------------------------- SVG

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
THEMES = {
    "light": {"bg": "#ffffff", "ink": "#1f2328", "ink2": "#59636e", "muted": "#8c959f",
              "rule": "#d1d9e0", "faint": "#c8d1da", "band": "#f6f8fa", "grid": "#eef1f4", "track": "#e3e7eb", "soft": 0.4,
              "slots": {1: "#2a78d6", 2: "#eb6834"}},
    "dark": {"bg": "#0d1117", "ink": "#f0f6fc", "ink2": "#9198a1", "muted": "#656c76",
             "rule": "#30363d", "faint": "#3d444d", "band": "#151b23", "grid": "#1c222a", "track": "#2a3038", "soft": 0.6,
             "slots": {1: "#3987e5", 2: "#d95926"}},
}
LEGEND = [
    ("Keep", [("try", "Worth trying"), ("mixed", "Mixed evidence"),
              ("self_only", "Not yet compared with other models")]),
    ("Skip", [("cheaper", "A cheaper setting does as well"), ("lower_effort", "A lower effort does as well")]),
    ("Unknown", [("no_data", "Not measured")]),
]


def text_width(s: str, size: float, bold: bool = False) -> float:
    return len(s) * size * (0.58 if bold else 0.54)


class Svg:
    def __init__(self, t: dict):
        self.t = t
        self.parts: list[str] = []

    def text(self, x, y, s, size: float = 12, color="ink", weight=400, anchor="start", halo=False):
        ring = f' stroke="{self.t["bg"]}" stroke-width="4" stroke-linejoin="round" paint-order="stroke"' if halo else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
            f'fill="{self.t.get(color, color)}" text-anchor="{anchor}"{ring}>{html.escape(s)}</text>'
        )

    def line(self, x1, y1, x2, y2, color="rule", width: float = 1):
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{self.t.get(color, color)}" stroke-width="{width}" stroke-linecap="round"/>'
        )

    def band(self, x, y, w, h):
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{self.t["band"]}"/>')

    def glyph(self, status: str, x: float, y: float, accent: str):
        t, p = self.t, self.parts
        if status == "try":
            p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6.5" fill="{accent}"/>')
        elif status == "mixed":
            p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.75" fill="none" stroke="{accent}" stroke-width="1.5"/>')
            p.append(f'<path d="M{x:.1f},{y - 5.75:.1f} A5.75,5.75 0 0,0 {x:.1f},{y + 5.75:.1f} Z" fill="{accent}"/>')
        elif status == "self_only":
            p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.75" fill="none" stroke="{accent}" stroke-width="1.5"/>')
        elif status == "cheaper":
            d = 3.8
            self.line(x - d, y - d, x + d, y + d, "muted", 1.6)
            self.line(x - d, y + d, x + d, y - d, "muted", 1.6)
        elif status == "lower_effort":  # arrow pointing left, toward the cheaper effort
            self.line(x - 5, y, x + 5, y, "muted", 1.6)
            self.line(x - 5, y, x - 1.5, y - 3.5, "muted", 1.6)
            self.line(x - 5, y, x - 1.5, y + 3.5, "muted", 1.6)
        elif status == "no_data":
            p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{t["muted"]}"/>')

    def render(self, width: float, height: float, title: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}" role="img">'
            f"<title>{html.escape(title)}</title>"
            f'<rect width="100%" height="100%" fill="{self.t["bg"]}"/>' + "".join(self.parts) + "</svg>\n"
        )


def price_label(price) -> str:
    if not price:
        return "price n/a"

    def f(v):
        return f"${v:g}" if v >= 1 else f"${v:.2f}".rstrip("0").rstrip(".")
    return f"{f(price[0])} / {f(price[1])}"


SEGMENT = {  # effort bar segment style per verdict: (fill, stroke)
    "try": ("accent", None),
    "mixed": ("accent_soft", None),
    "self_only": (None, "accent"),
    "cheaper": ("track", None),
    "lower_effort": ("track", None),
}
KEY = [("try", "Worth trying"), ("mixed", "Mixed evidence"), ("self_only", "Not compared with other models"),
       ("cheaper", "Ruled out"), ("no_data", "Not measured")]


def effort_range(efforts: list[str], kept: list[str], short: dict) -> str:
    """'low – high' for a consecutive run, else a comma list."""
    idx = [efforts.index(e) for e in kept]
    names = [short.get(e, e) for e in kept]
    if len(idx) > 1 and idx == list(range(idx[0], idx[-1] + 1)):
        return f"{names[0]} – {names[-1]}"
    return ", ".join(names)


def cell_summary(cells: dict, tier: dict, efforts: list[str], short: dict, tiers_by_model: dict) -> tuple[str, str]:
    """(text, tone) for the line under an effort bar."""
    mid = tier["model"]["id"]
    row = {e: cells[(mid, e)] for e in efforts}
    kept = [e for e in efforts if row[e].status in ("try", "mixed", "self_only")]
    if kept:
        text = effort_range(efforts, kept, short)
        if all(row[e].status == "self_only" for e in kept):
            text += ", not compared"
        return text, "ink"
    judged = [c for c in row.values() if c.status in ("cheaper", "lower_effort")]
    if not judged:
        return "No data", "muted"
    others = Counter(y[0] for c in judged for y in c.rulers() if y[0] != mid)
    if others:
        return f"Skip: {tiers_by_model[others.most_common(1)[0][0]]} does as well", "ink2"
    return "Skip", "ink2"


def render_svg(data: dict, theme: str) -> str:
    t = THEMES[theme]
    s = Svg(t)
    efforts, short = data["efforts"], data["effort_labels"]
    families = data["pools"][0]["families"]
    pad, label_w, width = 32, 300, 920
    col_w = (width - 2 * pad - label_w) / len(families)
    seg_w, seg_h, seg_gap = 24, 8, 4
    row_h = 62
    tiers_by_model = {tr["model"]["id"]: tr["label"] for p in data["pools"] for tr in p["tiers"]}

    def segment(x, y, status, accent):
        fill, stroke = SEGMENT.get(status, (None, None))
        colors = {"accent": accent, "accent_soft": accent, "track": t["track"]}
        if status == "no_data":
            s.line(x + 1, y + seg_h / 2, x + seg_w - 1, y + seg_h / 2, "track", 1.5)
            return
        f = colors.get(fill, "none")
        st = f' stroke="{colors[stroke]}" stroke-width="1.4"' if stroke else ""
        if fill == "accent_soft":
            st += f' fill-opacity="{t["soft"]}"'
        inset = 0.7 if stroke else 0
        s.parts.append(f'<rect x="{x + inset:.1f}" y="{y + inset:.1f}" width="{seg_w - 2 * inset:.1f}" '
                       f'height="{seg_h - 2 * inset:.1f}" rx="2" fill="{f}"{st}/>')

    y = 44
    s.text(pad, y, "Which settings are worth trying", 21, weight=600)
    y += 26
    s.text(pad, y, "What public benchmarks say, per quota pool. Each bar runs from the lowest to the highest effort level.", 13.5, "ink2")

    # key
    y += 30
    x = pad
    accent0 = t["slots"].get(data["pools"][0].get("color_slot"), t["ink2"])
    for status, label in KEY:
        segment(x, y - 8, status, accent0)
        s.text(x + seg_w + 8, y, label, 12, "ink2")
        x += seg_w + 8 + text_width(label, 12) + 26

    # column heads
    y += 42
    bar_w = len(efforts) * (seg_w + seg_gap) - seg_gap
    for i, fam in enumerate(families):
        fx = pad + label_w + i * col_w
        s.text(fx, y, fam["label"], 13.5, weight=600)
        # effort scale: one segment per level, lowest on the left
        s.text(fx, y + 17, short.get(efforts[0], efforts[0]), 10.5, "muted")
        s.text(fx + bar_w, y + 17, short.get(efforts[-1], efforts[-1]), 10.5, "muted", anchor="end")
        s.line(fx + text_width(efforts[0], 10.5) + 6, y + 13.5, fx + bar_w - text_width(efforts[-1], 10.5) - 6, y + 13.5,
               "track", 1)
    y += 20

    for pool in data["pools"]:
        if pool.get("color_slot") not in t["slots"]:
            raise SystemExit(f"guide.json: pool {pool['id']!r} needs color_slot in {sorted(t['slots'])}")
        accent = t["slots"][pool["color_slot"]]
        y += 34
        s.parts.append(f'<circle cx="{pad + 4:.1f}" cy="{y - 5:.1f}" r="4" fill="{accent}"/>')
        s.text(pad + 16, y, pool["label"], 14, weight=600)
        s.text(pad + label_w - 20, y, ", ".join(data["cost"]["price_header"]), 11, "muted", anchor="end")
        for i, fam in enumerate(pool["families"]):
            n = fam["benchmarks"]
            s.text(pad + label_w + i * col_w, y, f"based on {n} benchmark{'s' if n != 1 else ''}", 11, "muted")
        y += 12
        s.line(pad, y, width - pad, y, "rule")
        for tier in pool["tiers"]:
            if text_width(tier["summary"], 12) > label_w - 30:
                raise SystemExit(f"guide.json: tier summary too long for the chart: {tier['summary']!r}")
            top = y
            s.text(pad, top + 25, tier["label"], 14, weight=600)
            s.text(pad + text_width(tier["label"], 14, True) + 10, top + 25, display_name(tier["model"]), 13, "ink2")
            s.text(pad, top + 44, tier["summary"], 12, "muted")
            s.text(pad + label_w - 20, top + 25, price_label(tier["price"]), 12, "ink2", anchor="end")
            for i, fam in enumerate(pool["families"]):
                fx = pad + label_w + i * col_w
                if tier["efforts"] != []:
                    for j, e in enumerate(efforts):
                        segment(fx + j * (seg_w + seg_gap), top + 15, fam["cells"][(tier["model"]["id"], e)].status, accent)
                    text, tone = cell_summary(fam["cells"], tier, efforts, short, tiers_by_model)
                    s.text(fx, top + 44, text, 12, tone)
                else:
                    s.text(fx, top + 25, "No effort setting", 12, "muted")
            y += row_h
            s.line(pad, y, width - pad, y, "rule", 0.6)

    notes = [
        data["tie_rule"] + " Settings are compared only inside one benchmark from one source.",
        data["cost"]["footer"] + " Reasons and sources: the tables under this chart.",
    ]
    if data["unmeasured"]:
        notes.append("Offered but never measured: " + "; ".join(
            f"{e} ({', '.join(models)})" for e, models in data["unmeasured"]) + ".")
    y += 20
    for note in notes:
        y += 17
        s.text(pad, y, note, 11.5, "muted")
    y += 26
    return s.render(width, y, "Which model and effort settings public benchmarks say are worth trying")


# ---------------------------------------------------------------- curves figure


def _hex_to_oklch(h: str) -> tuple[float, float, float]:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    l_ = (0.4122214708 * lin[0] + 0.5363325363 * lin[1] + 0.0514459929 * lin[2]) ** (1 / 3)
    m_ = (0.2119034982 * lin[0] + 0.6806995451 * lin[1] + 0.1073969566 * lin[2]) ** (1 / 3)
    s_ = (0.0883024619 * lin[0] + 0.2817188376 * lin[1] + 0.6299787005 * lin[2]) ** (1 / 3)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a))


def _oklch_to_hex(L: float, C: float, h: float) -> str:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_, m_, s_ = L + 0.3963377774 * a + 0.2158037573 * b, L - 0.1055613458 * a - 0.0638541728 * b, \
        L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s3,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s3,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s3)
    out = []
    for c in rgb:
        c = min(1.0, max(0.0, c))
        out.append(round(255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)))
    return "#%02x%02x%02x" % tuple(out)


def tier_colors(base: str, n: int, theme: str) -> list[str]:
    """One shade per tier from the pool's hue: cheapest tier lightest in light mode, most muted in dark."""
    _, C, h = _hex_to_oklch(base)
    lo, hi = (0.80, 0.40) if theme == "light" else (0.60, 0.88)
    steps = [lo + (hi - lo) * (i / max(1, n - 1)) for i in range(n)]
    return [_oklch_to_hex(L, min(C, 0.17), h) for L in steps]


GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def pick_group(fam: dict) -> "Group | None":
    """The benchmark that shows the most settings; ties go to better evidence, then the widest score spread."""
    def key(g):
        scores = [o[2] for o in g.opts]
        spread = (max(scores) - min(scores)) / (abs(max(scores)) or 1)
        return (len(g.opts), len(g.models), -GRADE_ORDER.get(g.grade, 9), spread, g.metric_id)
    candidates = [g for g in fam["groups"] if len({o[1] for o in g.opts}) >= 3]
    return max(candidates, key=key) if candidates else None


def panel_verdicts(g: "Group") -> dict:
    """Within one benchmark: is a cheaper setting within the noise floor?"""
    out = {}
    for m, e, sc, c in g.opts:
        out[(m, e)] = any(
            c2 < c and (sc2 > sc - g.floor if g.higher else sc2 < sc + g.floor)
            for m2, e2, sc2, c2 in g.opts if (m2, e2) != (m, e)
        )
    return out


def nice_ticks(lo: float, hi: float, n: int = 3) -> list[float]:
    span = hi - lo or abs(hi) or 1
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)
    start = math.ceil(lo / step) * step
    ticks, v = [], start
    while v <= hi + 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


def usd(v: float) -> str:
    if v >= 1:
        return f"${v:,.0f}"
    return f"${v:.2f}".rstrip("0").rstrip(".") if v >= 0.1 else f"${v:.3f}".rstrip("0").rstrip(".")


def draw_panel(s: Svg, t: dict, g: "Group", box: tuple, color_of: dict, label_of: dict, rank: dict,
               disp: dict) -> None:
    """One benchmark's effort curves: x = log cost per task, y = score."""
    px, py0, pw, ph = box
    xs = [math.log10(o[3]) for o in g.opts]
    ys = [o[2] for o in g.opts]
    x0, x1 = min(xs), max(xs)
    xpad = (x1 - x0) * 0.06 or 0.3
    x0, x1 = x0 - xpad, x1 + xpad
    y0, y1 = min(ys), max(ys)
    ypad = (y1 - y0) * 0.1 or abs(y1) * 0.05 or 1
    y0, y1 = y0 - ypad, y1 + ypad

    def X(c):
        return px + (math.log10(c) - x0) / (x1 - x0) * pw

    def Y(v):
        return py0 + ph - (v - y0) / (y1 - y0) * ph

    for v in nice_ticks(y0, y1, 4):
        if y0 <= v <= y1:
            s.line(px, Y(v), px + pw, Y(v), "grid", 1)
            lab = f"{v * 100:.0f}%" if disp.get("percent") else f"{v:g}"
            s.text(px - 8, Y(v) + 3.5, lab, 10.5, "muted", anchor="end")
    s.line(px, py0 + ph, px + pw, py0 + ph, "rule", 1)
    for v in (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300):
        if x0 <= math.log10(v) <= x1:
            s.line(X(v), py0 + ph, X(v), py0 + ph + 4, "rule", 1)
            s.text(X(v), py0 + ph + 17, usd(v), 10.5, "muted", anchor="middle")

    ruled = panel_verdicts(g)
    lines = defaultdict(list)
    for m, e, sc, c in g.opts:
        lines[m].append((rank[e], e, sc, c))
    ends = []
    order = list(color_of)
    for m in sorted(lines, key=order.index):
        pts = sorted(lines[m])
        col = color_of[m]
        path = " ".join(f"{'M' if k == 0 else 'L'}{X(c):.1f},{Y(sc):.1f}" for k, (_, _, sc, c) in enumerate(pts))
        s.parts.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2" '
                       f'stroke-linejoin="round" stroke-linecap="round"/>')
        base = min(p[0] for p in pts)
        for r, e, sc, c in pts:
            rad = 3.4 + 0.75 * (r - base)
            if ruled[(m, e)]:
                s.parts.append(f'<circle cx="{X(c):.1f}" cy="{Y(sc):.1f}" r="{rad:.1f}" fill="{t["bg"]}" '
                               f'stroke="{col}" stroke-width="1.6"/>')
            else:
                s.parts.append(f'<circle cx="{X(c):.1f}" cy="{Y(sc):.1f}" r="{rad:.1f}" fill="{col}" '
                               f'stroke="{t["bg"]}" stroke-width="1.5"/>')
        _, _, sc, c = pts[-1]
        ends.append([Y(sc), X(c) + 3.4 + 0.75 * (pts[-1][0] - base), label_of[m]])
    ends.sort()
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 13)
    for ey, ex, lab in ends:
        s.text(ex + 7, ey + 4, lab, 11.5, weight=600, halo=True)


def render_curves_svg(data: dict, theme: str) -> str:
    t = THEMES[theme]
    s = Svg(t)
    rank = data["rank"]
    display = data["score_display"]
    pools = data["pools"]
    pad, col_gap = 32, 48
    width = 1000
    col_w = (width - 2 * pad - col_gap * (len(pools) - 1)) / len(pools)
    ml, mr, ph = 46, 64, 168  # left axis, room for end labels, plot height

    y = 46
    s.text(pad, y, "Where more effort pays, and where it stops", 22, weight=600)
    y += 28
    s.text(pad, y, "Each line is one model; each dot is an effort level, larger for more effort. Up and to the left is better.", 13.5, "ink2")
    y += 20
    s.text(pad, y, "Across: API cost per task, log scale. Up: score. Filled: worth trying. Hollow: a cheaper setting scores as well.", 13.5, "ink2")

    # pool headers with their tier legend
    y += 46
    colors, labels = [], []
    legend_bottom = y
    for j, pool in enumerate(pools):
        if pool.get("color_slot") not in t["slots"]:
            raise SystemExit(f"guide.json: pool {pool['id']!r} needs color_slot in {sorted(t['slots'])}")
        shades = tier_colors(t["slots"][pool["color_slot"]], len(pool["tiers"]), theme)
        colors.append({tr["model"]["id"]: shades[i] for i, tr in enumerate(pool["tiers"])})
        labels.append({tr["model"]["id"]: tr["label"] for tr in pool["tiers"]})
        cx = pad + j * (col_w + col_gap)
        charted = {o[0] for f in pool["families"] for g in [pick_group(f)] if g for o in g.opts}
        s.text(cx, y, pool["label"], 17, weight=600)
        s.text(cx + col_w, y, "$ per M tokens, in / out", 10.5, "muted", anchor="end")
        ly = y + 8
        for i, tr in enumerate(pool["tiers"]):
            ly += 36
            s.line(cx, ly - 4.5, cx + 18, ly - 4.5, shades[i], 3)
            s.text(cx + 28, ly, tr["label"], 13.5, weight=600)
            s.text(cx + 28 + text_width(tr["label"], 13.5, True) + 8, ly, display_name(tr["model"]), 12.5, "ink2")
            s.text(cx + col_w, ly, price_label(tr["price"]), 12, "ink2", anchor="end")
            note = "" if tr["model"]["id"] in charted else " · no effort curves"
            s.text(cx + 28, ly + 15, tr["summary"] + note, 11.5, "muted")
        legend_bottom = max(legend_bottom, ly + 15)
    y = legend_bottom

    for i, fam in enumerate(pools[0]["families"]):
        y += 44
        s.line(pad, y - 26, width - pad, y - 26, "rule")
        s.text(pad, y, fam["label"], 15, weight=600)
        y += 8
        for j, pool in enumerate(pools):
            pf = pool["families"][i]
            cx = pad + j * (col_w + col_gap)
            g = pick_group(pf)
            if not g:
                s.text(cx, y + 22, "No benchmark here measures three or more effort levels.", 11.5, "muted")
                continue
            more = pf["benchmarks"] - 1
            caption = f"{g.name} · {g.source_type.replace('_', ' ')}, grade {g.grade}"
            if more > 0:
                caption += f" · {more} more in the tables"
            s.text(cx, y + 14, caption, 11, "muted")
            disp = display["by_metric"].get(g.metric_id) or display["by_unit"].get(g.unit) or {"percent": False}
            draw_panel(s, t, g, (cx + ml, y + 34, col_w - ml - mr, ph), colors[j], labels[j], rank, disp)
        y += 34 + ph + 24

    notes = [
        data["tie_rule"] + " Filled and hollow compare settings inside the benchmark shown.",
        data["cost"]["footer"] + " Every other benchmark and verdict: the tables under this chart.",
    ]
    y += 20
    for note in notes:
        y += 17
        s.text(pad, y, note, 11.5, "muted")
    y += 26
    return s.render(width, y, "Where more reasoning effort pays, per quota pool and task family")


# ---------------------------------------------------------------- cost vs intelligence (hero)


@dataclass(frozen=True)
class Point:
    model: str
    effort: str | None
    score: float
    cost: float
    observed: str
    marks: tuple  # guide hero.flags marks whose caveat pattern matches this row


def _decimals(v: float) -> int:
    return 0 if float(v).is_integer() else len(repr(float(v)).split(".")[1])


def _bridges(cand: list, joined: list, tol: dict) -> bool:
    """A group may join if it shares a setting with the joined rows and every shared setting agrees."""
    seen = defaultdict(list)
    for r in joined:
        seen[(r["model"], r.get("effort"))].append(r)
    shared = [(r, o) for r in cand for o in seen.get((r["model"], r.get("effort")), [])]
    for r, o in shared:
        if abs(r["score"] - o["score"]) > tol["score_tolerance"]:
            return False
        if r["_cost"] and o["_cost"] and abs(r["_cost"] - o["_cost"]) > tol["cost_rel_tolerance"] * min(r["_cost"], o["_cost"]):
            return False
    return bool(shared)


def join_groups(rows: list, tol: dict) -> tuple[list[str], list[str]]:
    """(joined, left out) comparability groups. The group with the most models anchors the chart."""
    by_group = defaultdict(list)
    for r in rows:
        by_group[r["_group"]].append(r)
    if not by_group:
        return [], []
    anchor = max(by_group, key=lambda g: (len({r["model"] for r in by_group[g]}), len(by_group[g]), g))
    joined, pending = [anchor], sorted(g for g in by_group if g != anchor)
    grew = True
    while grew:
        grew = False
        for g in list(pending):
            if _bridges(by_group[g], [r for j in joined for r in by_group[j]], tol):
                joined.append(g)
                pending.remove(g)
                grew = True
    return joined, pending


def pareto(points: list) -> list:
    """Settings that no other setting beats on cost without losing score; cheapest first."""
    out, best = [], -math.inf
    for p in sorted(points, key=lambda p: (p.cost, -p.score, p.model, p.effort or "")):
        if p.score > best:
            out.append(p)
            best = p.score
    return out


def build_hero(pack: Pack) -> dict:
    hero = pack.guide["hero"]
    unit = pack.guide["cost"]["score_cost_unit"]

    def cost_of(r):
        c = r.get("cost") or {}
        ok = c.get("unit") == unit and c.get("basis") == hero["cost_basis"] and (c.get("value") or 0) > 0
        return float(c["value"]) if ok else None

    rows = [{**r, "_group": r.get("comparability_group") or r.get("snapshot_id") or r["_file"], "_cost": cost_of(r)}
            for r in pack.hero_rows if isinstance(r.get("score"), (int, float))]
    if not rows:
        raise SystemExit(f"guide.json: no score rows for hero.metric_id {hero['metric_id']!r}")
    pattern = hero.get("exclude_groups_matching")
    excluded = [r for r in rows if pattern and re.search(pattern, r["_group"])]
    kept = [r for r in rows if r not in excluded]
    joined, unjoined = join_groups(kept, hero["bridge"])
    usable = [r for r in kept if r["_group"] in joined]

    best = {}
    for r in usable:
        if r["_cost"] is None:
            continue
        key = (r["model"], r.get("effort"))
        rank_key = (r.get("observed_at") or "", -GRADE_ORDER.get(r.get("evidence_grade"), 9), _decimals(r["score"]), r["_file"])
        if key not in best or rank_key > best[key][0]:
            best[key] = (rank_key, r)
    flags = hero.get("flags") or []
    points = sorted(
        (Point(r["model"], r.get("effort"), float(r["score"]), r["_cost"], r.get("observed_at") or "",
               tuple(f["mark"] for f in flags if re.search(f["caveat_matches"], r.get("caveat") or "")))
         for _, r in best.values()),
        key=lambda p: (p.model, p.effort or ""))
    if not points:
        raise SystemExit(f"guide.json: no {hero['metric_id']!r} row has a {unit} cost of basis {hero['cost_basis']!r}")
    plotted = {p.model for p in points}
    no_cost = sorted({r["model"] for r in usable} - plotted)
    left_out = sorted({r["model"] for r in excluded + [r for r in kept if r["_group"] in unjoined]} - plotted - set(no_cost))
    unpriced = {(r["model"], r.get("effort")) for r in usable if r["model"] in plotted} - set(best)
    frontier = pareto(points)

    # featured: newest model with data per (provider, tier), best other-vendor models, frontier models
    feat = hero["featured"]
    info = {m: pack.models.get(m, {"id": m}) for m in plotted}
    top = {m: max(p.score for p in points if p.model == m) for m in plotted}
    newest = {}
    for m in sorted(plotted):
        mi = info[m]
        if mi.get("provider") in feat["newest_per_tier_providers"] and mi.get("status") in feat["statuses"]:
            key, cand = (mi["provider"], mi.get("tier")), (mi.get("released") or "", m)
            newest[key] = max(newest.get(key, cand), cand)
    picks = {m for _, m in newest.values()}
    others = [m for m in plotted if info[m].get("provider") not in feat["newest_per_tier_providers"]
              and info[m].get("status") in feat["statuses"]]
    picks |= set(sorted(others, key=lambda m: (-top[m], m))[: feat["top_others"]])
    if feat["include_frontier"]:
        picks |= {p.model for p in frontier}

    rank = {e: i for i, e in enumerate(pack.effort_order())}
    return {
        "title": hero["title"],
        "metric": pack.metrics.get(hero["metric_id"], {}).get("name", hero["metric_id"]),
        "y_label": hero["y_label"],
        "credit": hero["credit"],
        "tolerance": hero["bridge"]["score_tolerance"],
        "points": points,
        "frontier": frontier,
        "featured": sorted(picks),
        "models": {m: info[m] for m in sorted(plotted)},
        "names": {m: display_name(pack.models.get(m, {"id": m})) for m in plotted | set(no_cost) | set(left_out)},
        "providers": hero["providers"],
        "fallback": hero["other_providers"],
        "flags": [(f["mark"], f["label"], f["note"]) for f in flags if any(f["mark"] in p.marks for p in points)],
        "no_cost": no_cost,
        "left_out": left_out,
        "unpriced": len(unpriced),
        "dates": (min(p.observed for p in points), max(p.observed for p in points)),
        "rank": rank,
    }


def vendor_of(view: dict, model: str) -> tuple[str, dict]:
    prov = view["models"].get(model, {}).get("provider")
    return (prov, view["providers"][prov]) if prov in view["providers"] else ("", view["fallback"])


def place_labels(desired: list[float], gap: float, top: float, bottom: float) -> list[float]:
    """1-D label layout: keep the order, put each label at least `gap` from the next and as close
    as possible to its desired y (overlapping runs merge and centre on their mean), inside [top, bottom]."""
    order = sorted(range(len(desired)), key=lambda i: (desired[i], i))
    runs: list[list] = []  # [first y, indices]
    for i in order:
        runs.append([desired[i], [i]])
        while True:
            run = runs[-1]
            n = len(run[1])
            run[0] = sum(desired[j] for j in run[1]) / n - (n - 1) * gap / 2
            run[0] = max(min(run[0], bottom - (n - 1) * gap), top)
            if len(runs) > 1 and runs[-2][0] + len(runs[-2][1]) * gap > run[0]:
                prev = runs.pop(-2)
                runs[-1] = [prev[0], prev[1] + run[1]]
                continue
            break
    out = [0.0] * len(desired)
    for y0, idx in runs:
        for k, j in enumerate(idx):
            out[j] = y0 + k * gap
    return out


def log_ticks(lo: float, hi: float) -> list[float]:
    """1-2-5 ticks for up to three decades, decades only beyond that."""
    mults = (1,) if math.log10(hi / lo) > 3 else (1, 2, 5)
    out = []
    for e in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
        for k in mults:
            v = float(f"{k}e{e}")
            if lo <= v <= hi:
                out.append(v)
    return out


def usd_tick(v: float) -> str:
    return f"${v:g}" if v >= 1 else "$" + f"{v:.4f}".rstrip("0").rstrip(".")


def usd_cost(v: float) -> str:
    """Two decimals from $0.10 up, two significant digits below: $5.98, $0.13, $0.068, $0.05."""
    return f"${v:.2f}" if v >= 0.1 else f"${float(f'{v:.2g}'):f}".rstrip("0")


def fmt_score(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"


def wrap(text: str, width: float, size: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and text_width(trial, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def hero_notes(view: dict) -> list[str]:
    d0, d1 = view["dates"]
    when = f"observed {d0}" if d0 == d1 else f"observed {d0} to {d1}"
    tol = view["tolerance"]
    notes = [(f"{view['credit']}, {when}. Each point is the latest observation of that model and "
              f"effort; snapshots share the chart only where the settings they both list agree within "
              f"{tol:g} point{'s' if tol != 1 else ''}. Cost is the API cost to run one index task.")]
    notes += [f"{mark} {note[:1].upper() + note[1:]}." for mark, _, note in view["flags"]]
    names = view["names"]
    if view["no_cost"]:
        notes.append("Not plotted, no cost per task: " + ", ".join(names[m] for m in view["no_cost"]) + ".")
    if view["left_out"]:
        notes.append("Left out, estimated or not checkable against the other snapshots: "
                     + ", ".join(names[m] for m in view["left_out"]) + ".")
    if view["unpriced"]:
        notes.append(f"{view['unpriced']} more effort settings of plotted models have a score but no cost and are not drawn.")
    return notes


def render_hero_svg(view: dict, theme: str, models: list[str], width: int = 1000, card: bool = False) -> str:
    """Cost per task (log x) against index score (y): one line per model through its efforts, labels at the right."""
    t = THEMES[theme]
    s = Svg(t)
    rank = view["rank"]
    shown = set(models)
    pts = [p for p in view["points"] if p.model in shown]
    frontier = pareto(pts)
    on_frontier = {p.model for p in frontier}
    names = view["names"]
    pad = 40 if card else 32
    fs = 1.25 if card else 1.0  # card text scale

    # header
    y = 50 * fs
    s.text(pad, y, view["title"], 22 * fs + (6 if card else 0), weight=600)
    y += 26 * fs
    sub = (f"{view['metric']} against API cost per task. One line per model joins its effort levels"
           + ("." if card else "; larger dots are higher effort."))
    for line in wrap(sub, width - 2 * pad, 13.5 * fs):
        s.text(pad, y, line, 13.5 * fs, "ink2")
        y += 19 * fs

    # key: vendors present, then the frontier, diamond and flag marks; flows onto a second row if needed
    y += 14 * fs
    size = 12.5 * fs
    items = []  # (swatch, colour, text, bold)
    for prov in list(view["providers"]) + [""]:
        if any(vendor_of(view, p.model)[0] == prov for p in pts):
            st = view["providers"][prov] if prov else view["fallback"]
            items.append(("dot", st[theme], st["label"], True))
    items.append(("dash", t["ink2"], "Pareto frontier", False))
    items.append(("diamond", t["ink2"], "model on the frontier", False))
    shown_marks = {k for p in pts for k in p.marks}
    items += [("ring", t["ink2"], f"{mark} {label}", False) for mark, label, _ in view["flags"] if mark in shown_marks]
    x = pad
    for kind, col, label, bold in items:
        w = (30 if kind == "dash" else 14) * fs + text_width(label, size, bold)
        if x + w > width - pad:
            x, y = pad, y + 24 * fs
        cy = y - 4.5 * fs
        if kind == "dot":
            s.parts.append(f'<circle cx="{x + 5 * fs:.1f}" cy="{cy:.1f}" r="{5 * fs:.1f}" fill="{col}"/>')
        elif kind == "dash":
            s.parts.append(f'<line x1="{x:.1f}" y1="{cy:.1f}" x2="{x + 24 * fs:.1f}" y2="{cy:.1f}" '
                           f'stroke="{col}" stroke-width="1.5" stroke-dasharray="5 4"/>')
        elif kind == "diamond":
            diamond(s, x + 4.5 * fs, cy, 4.2 * fs, col)
        else:
            s.parts.append(f'<circle cx="{x + 4.5 * fs:.1f}" cy="{cy:.1f}" r="{4 * fs:.1f}" fill="{t["bg"]}" '
                           f'stroke="{col}" stroke-width="1.5"/>')
        s.text(x + w - text_width(label, size, bold), y, label, size, col if kind == "dot" else "ink2",
               weight=600 if bold else 400)
        x += w + 22 * fs

    # end-of-line labels decide the gutter width
    lines = defaultdict(list)
    for p in pts:
        lines[p.model].append(p)
    for m in lines:
        lines[m].sort(key=lambda p: (rank.get(p.effort, -1), p.cost))
    label_size = 12.5 * fs
    label_of = {m: names[m] + (" " + "".join(sorted({k for p in ps for k in p.marks})) if any(p.marks for p in ps) else "")
                for m, ps in lines.items()}
    gutter = max([text_width(v, label_size, True) for v in label_of.values()] + [60]) + 38 * fs
    gap = label_size + 3.5 * fs

    # plot box
    top = y + 44 * fs
    left = pad + 34 * fs
    right = width - pad - gutter
    if card:
        bottom = CARD_SIZE[1] - 100
    else:
        bottom = top + max(420, 1.5 * len(lines) * gap)
    costs = [p.cost for p in pts] or [1.0]
    scores = [p.score for p in pts] or [0.0]
    x0, x1 = math.log10(min(costs)) - 0.12, math.log10(max(costs)) + 0.12
    ticks = nice_ticks(min(scores), max(scores), 5)
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 10
    y0, y1 = math.floor(min(scores) / step) * step, math.ceil(max(scores) / step) * step
    if y1 - max(scores) < step * 0.15:
        y1 += step / 2

    def X(c):
        return left + (math.log10(c) - x0) / (x1 - x0) * (right - left)

    def Y(v):
        return bottom - (v - y0) / (y1 - y0) * (bottom - top)

    s.text(left - 8 * fs, top - 14 * fs, view["y_label"], 11.5 * fs, "ink2")
    v = y0
    while v <= y1 + 1e-9:
        s.line(left, Y(v), right, Y(v), "grid", 1)
        s.text(left - 8 * fs, Y(v) + 4 * fs, f"{v:g}", 11 * fs, "muted", anchor="end")
        v += step
    s.line(left, bottom, right, bottom, "rule", 1)
    for c in log_ticks(10 ** x0, 10 ** x1):
        s.line(X(c), bottom, X(c), bottom + 4, "rule", 1)
        s.text(X(c), bottom + 18 * fs, usd_tick(c), 11 * fs, "muted", anchor="middle")
    s.text((left + right) / 2, bottom + 38 * fs, "Cost per task (USD, log scale)", 11.5 * fs, "ink2", anchor="middle")

    # frontier under the data
    if len(frontier) > 1:
        path = " ".join(f"{'M' if k == 0 else 'L'}{X(p.cost):.1f},{Y(p.score):.1f}" for k, p in enumerate(frontier))
        s.parts.append(f'<path d="{path}" fill="none" stroke="{t["ink2"]}" stroke-width="1.4" '
                       f'stroke-dasharray="5 4" stroke-opacity="0.8"/>')

    levels = sorted({p.effort for p in pts if p.effort is not None}, key=lambda e: rank.get(e, 0))
    lvl = {e: i for i, e in enumerate(levels)}

    def radius(p):
        return (3.0 + 0.6 * lvl[p.effort] if p.effort in lvl else 3.0 + 0.3 * len(levels)) * (1.15 if card else 1)

    ends = []
    for m in sorted(lines, key=lambda m: (-lines[m][-1].score, m)):
        col = vendor_of(view, m)[1][theme]
        ps = lines[m]
        if len(ps) > 1:
            path = " ".join(f"{'M' if k == 0 else 'L'}{X(p.cost):.1f},{Y(p.score):.1f}" for k, p in enumerate(ps))
            s.parts.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{2 * fs:.1f}" '
                           'stroke-linejoin="round" stroke-linecap="round"/>')
        for p in ps:
            if p.marks:
                s.parts.append(f'<circle cx="{X(p.cost):.1f}" cy="{Y(p.score):.1f}" r="{radius(p):.1f}" '
                               f'fill="{t["bg"]}" stroke="{col}" stroke-width="1.6"/>')
            else:
                s.parts.append(f'<circle cx="{X(p.cost):.1f}" cy="{Y(p.score):.1f}" r="{radius(p):.1f}" '
                               f'fill="{col}" stroke="{t["bg"]}" stroke-width="1.2"/>')
        end = max(ps, key=lambda p: X(p.cost))
        ends.append((m, X(end.cost) + radius(end) + 3, Y(end.score), col))

    gx = right + 30 * fs
    placed = place_labels([e[2] for e in ends], gap, top, bottom)
    for (m, ex, ey, col), ly in zip(ends, placed):
        # leader: level with the line end across the plot, then a short bend in the gutter to the label
        s.parts.append(f'<path d="M{ex:.1f},{ey:.1f} L{right + 4:.1f},{ey:.1f} L{gx - 6:.1f},{ly:.1f}" fill="none" '
                       f'stroke="{col}" stroke-width="0.8" stroke-opacity="0.45" stroke-linejoin="round"/>')
        if m in on_frontier:
            diamond(s, gx + 3 * fs, ly, 4 * fs, t["ink2"])
        s.text(gx + 13 * fs, ly + label_size * 0.35, label_of[m], label_size, col, weight=600)

    y = bottom + 38 * fs
    if card:
        d0, d1 = view["dates"]
        s.text(pad, CARD_SIZE[1] - 18, f"{view['credit']} · observed {d0} to {d1}", 14, "muted")
        return s.render(width, CARD_SIZE[1], f"{view['title']}: {view['metric']} against API cost per task")
    y += 10
    for note in hero_notes(view):
        for line in wrap(note, width - 2 * pad, 11.5):
            y += 17
            s.text(pad, y, line, 11.5, "muted")
    y += 26
    return s.render(width, y, f"{view['title']}: {view['metric']} against API cost per task, one line per model")


def diamond(s: Svg, x: float, y: float, r: float, color: str) -> None:
    s.parts.append(f'<path d="M{x:.1f},{y - r:.1f} L{x + r:.1f},{y:.1f} L{x:.1f},{y + r:.1f} L{x - r:.1f},{y:.1f} Z" '
                   f'fill="{color}"/>')


def frontier_summary(view: dict) -> str:
    """'A low to high (42–52, $0.13–$0.72), B ...' for the settings on the frontier, cheapest first."""
    names, out, runs = view["names"], [], []
    for p in view["frontier"]:
        if runs and runs[-1][0] == p.model:
            runs[-1][1].append(p)
        else:
            runs.append((p.model, [p]))
    for m, ps in runs:
        eff = [p.effort for p in ps if p.effort]
        what = names[m] + (f" {eff[0]}" if len(eff) == 1 else f" {eff[0]} to {eff[-1]}" if eff else "")
        if len(ps) == 1:
            out.append(f"{what} ({fmt_score(ps[0].score)} at {usd_cost(ps[0].cost)})")
        else:
            out.append(f"{what} ({fmt_score(ps[0].score)}–{fmt_score(ps[-1].score)} at "
                       f"{usd_cost(ps[0].cost)}–{usd_cost(ps[-1].cost)})")
    return ", ".join(out)


def hero_block(view: dict) -> list[str]:
    names, rank = view["names"], view["rank"]
    pts = view["points"]
    featured_providers = {vendor_of(view, m)[0] for m in view["featured"]}
    vendors = [st["label"] for prov, st in [*view["providers"].items(), ("", view["fallback"])] if prov in featured_providers]
    lo, hi = min(p.cost for p in pts), max(p.cost for p in pts)
    alt = (f"Line chart, {view['title'].lower()}. Across: API cost per task in US dollars, log scale, "
           f"{usd_cost(lo)} to {usd_cost(hi)}. Up: {view['metric']}. One line per model joins its effort levels, "
           f"coloured by vendor ({', '.join(vendors)}); {len(view['featured'])} models shown. "
           f"A dashed Pareto frontier runs through {frontier_summary(view)}. The table below lists every point.")
    alt_all = (f"The same chart with all {len(view['models'])} models that have a cost per task. "
               "The table below lists every point.")
    best = max(pts, key=lambda p: (p.score, -p.cost))
    reading = (f"**Reading the chart.** Up is smarter, left is cheaper. The frontier (dashed) holds the settings "
               f"that no other setting beats on both cost and score: {frontier_summary(view)}. The top score is "
               f"{names[best.model]}{' ' + best.effort if best.effort else ''} at {fmt_score(best.score)} "
               f"for {usd_cost(best.cost)} per task. " + " ".join(hero_notes(view)))
    on = {(p.model, p.effort) for p in view["frontier"]}
    top = {m: max(p.score for p in pts if p.model == m) for m in view["models"]}
    rows = sorted(pts, key=lambda p: (-top[p.model], names[p.model], rank.get(p.effort, -1)))
    flag_note = {mark: label for mark, label, _ in view["flags"]}
    out = [
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="charts/cost-vs-intelligence-dark.svg">',
        f'  <img src="charts/cost-vs-intelligence-light.svg" alt="{html.escape(alt)}">',
        "</picture>",
        "",
        reading,
        "",
        f"<details><summary><b>All {len(view['models'])} models</b> on the same axes</summary>",
        "",
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="charts/cost-vs-intelligence-all-dark.svg">',
        f'  <img src="charts/cost-vs-intelligence-all-light.svg" alt="{html.escape(alt_all)}">',
        "</picture>",
        "",
        "</details>",
        "",
        f"<details><summary><b>Data behind the chart</b>: {len(pts)} settings, one row per model and effort</summary>",
        "",
        f"| Model | Effort | {view['y_label']} | $/task | Observed | Note |",
        "|---|---|---|---|---|---|",
    ]
    for p in rows:
        note = ["on the frontier"] if (p.model, p.effort) in on else []
        note += [f"{k} {flag_note[k]}" for k in p.marks if k in flag_note]
        out.append(f"| {names[p.model]} | {p.effort or '–'} | {fmt_score(p.score)} | {usd_cost(p.cost)} | "
                   f"{p.observed} | {'; '.join(note)} |")
    out += ["", "–: the model has no effort setting, or the source does not name one.", "", "</details>", ""]
    return out


# ---------------------------------------------------------------- social card PNG



def png_text(png: bytes, key: bytes) -> str | None:
    pos = 8
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + n]
        if kind == b"tEXt" and body.startswith(key + b"\0"):
            return body[len(key) + 1:].decode("latin-1")
        pos += 12 + n
    return None


def with_png_text(png: bytes, key: bytes, value: str) -> bytes:
    """Insert a tEXt chunk before IEND (the last 12 bytes) so --check can tell which SVG made the PNG."""
    body = key + b"\0" + value.encode("latin-1")
    chunk = struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body))
    return png[:-12] + chunk + png[-12:]


def write_card(svg: str) -> str:
    chrome = next((shutil.which(b) for b in ("google-chrome", "chromium", "chromium-browser", "chrome") if shutil.which(b)), None)
    if not chrome:
        return "skipped charts/social-card.png: no Chrome or Chromium on PATH"
    w, h = CARD_SIZE
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "card.svg", Path(tmp) / "card.png"
        src.write_text(svg)
        subprocess.run([chrome, "--headless=new", "--no-sandbox", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--window-size={w},{h}", f"--screenshot={out}", src.as_uri()],
                       check=True, capture_output=True, timeout=120)
        png = out.read_bytes()
    if struct.unpack(">II", png[16:24]) != (w, h):
        raise SystemExit(f"Chrome wrote a {struct.unpack('>II', png[16:24])} screenshot, expected {(w, h)}")
    CARD.write_bytes(with_png_text(png, CARD_KEY, hashlib.sha256(svg.encode()).hexdigest()))
    return str(CARD.relative_to(REPO))


# ---------------------------------------------------------------- README block

MARK = {"try": "●", "self_only": "○", "mixed": "◐", "cheaper": "✕", "lower_effort": "←", "no_data": "·",
        "not_offered": "n/a"}


def cell_text(cell: Cell, names: dict, model: str, effort: str, rank: dict) -> str:
    def name(y):
        return y[1] if y[0] == model else f"{names[y[0]]} {y[1]}"
    if cell.status in ("cheaper", "lower_effort"):
        rulers = cell.rulers()
        lower = [y for y in rulers if y[0] == model and rank[y[1]] < rank[effort]]
        others = [y for y in rulers if y[0] != model]
        pick = (lower or rulers)[0] if cell.status == "lower_effort" else (others or rulers)[0]
        n = len(cell.vs[pick])
        return f"{MARK[cell.status]} {name(pick)} ({n} of {n})"
    if cell.status == "mixed":
        k, n, y = cell.best_split()
        return f"◐ {name(y)} ({k} of {n})"
    return MARK[cell.status]


def readme_block(data: dict, hero: dict | None = None) -> str:
    names = {t["model"]["id"]: display_name(t["model"]) for p in data["pools"] for t in p["tiers"]}
    efforts = data["efforts"]
    out = [
        BEGIN,
        "<!-- Generated by scripts/render_model_catalog_charts.py. Edit guide.json or the data, not this block. -->",
        "",
        *(hero_block(hero) if hero else []),
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="charts/effort-curves-dark.svg">',
        '  <img src="charts/effort-curves-light.svg" alt="Effort curves for each quota pool and task family. Each line is '
        "a model and each dot an effort level; x is API cost per task on a log scale and y is the benchmark score. "
        'Filled dots are worth trying; hollow dots have a cheaper setting that scores as well.">',
        "</picture>",
        "",
        "<details><summary><b>Verdicts across all benchmarks</b>, not only the one charted above</summary>",
        "",
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="charts/option-map-dark.svg">',
        '  <img src="charts/option-map-light.svg" alt="A table for each quota pool. Rows are models, one per capability tier, '
        "least capable first; columns are task families. Each cell has a bar with one segment per effort level, "
        "marked worth trying, mixed evidence, not compared, ruled out or not measured, and a short label naming the "
        'effort levels worth trying or the cheaper tier that does as well. The tables below give the reason for each segment.">',
        "</picture>",
        "",
    ]
    for pool in data["pools"]:
        out += [f"<details><summary><b>{pool['label']}</b>: the reason behind each cell</summary>", ""]
        out.append("| Task | Model | " + " | ".join(efforts) + " |")
        out.append("|---|---|" + "---|" * len(efforts))
        for fam in pool["families"]:
            for tier in pool["tiers"]:
                mid = tier["model"]["id"]
                row = [cell_text(fam["cells"][(mid, e)], names, mid, e, data["rank"]) for e in efforts]
                out.append(f"| {fam['label']} | {names[mid]} | " + " | ".join(row) + " |")
        out += ["", "✕ *model effort (n of n)*: that cheaper setting scores as well on every benchmark they share. "
                "← *effort*: the same, for a lower effort of this model. ◐ *(k of n)*: cheaper and as good on k of the n "
                "benchmarks they share. ○: not yet compared with other models.", ""]
        out.append("| Task | Benchmark | Source | Grade | Models compared |")
        out.append("|---|---|---|---|---|")
        for fam in pool["families"]:
            for g in fam["groups"]:
                models = ", ".join(names[m] for m in g.models)
                out.append(f"| {fam['label']} | {g.name} | [`{g.file}`]({g.file}) · {g.source_type.replace('_', ' ')} "
                           f"| {g.grade} | {models} |")
        out += ["", "</details>", ""]
    out += ["</details>", "", END]
    return "\n".join(out)


def replace_block(readme: str, block: str) -> str:
    pattern = re.compile(re.escape(BEGIN) + ".*?" + re.escape(END), re.S)
    if not pattern.search(readme):
        raise SystemExit(f"README.md: missing {BEGIN} ... {END} markers")
    return pattern.sub(lambda _: block, readme)


# ---------------------------------------------------------------- driver


def outputs() -> tuple[dict[Path, str], str | None]:
    """(text files, social-card SVG). The card SVG is not written; --png renders it to charts/social-card.png."""
    pack = Pack.read()
    data = build(pack)
    files = {CHARTS / f"option-map-{theme}.svg": render_svg(data, theme) for theme in THEMES}
    files.update({CHARTS / f"effort-curves-{theme}.svg": render_curves_svg(data, theme) for theme in THEMES})
    hero = build_hero(pack) if "hero" in pack.guide else None
    card = None
    if hero:
        for theme in THEMES:
            files[CHARTS / f"cost-vs-intelligence-{theme}.svg"] = render_hero_svg(hero, theme, hero["featured"])
            files[CHARTS / f"cost-vs-intelligence-all-{theme}.svg"] = render_hero_svg(hero, theme, list(hero["models"]))
        card = render_hero_svg(hero, "dark", hero["featured"], width=CARD_SIZE[0], card=True)
    files[README] = replace_block(README.read_text(), readme_block(data, hero))
    return files, card


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if generated files are stale (does not need Chrome)")
    ap.add_argument("--png", action="store_true", help="also render charts/social-card.png with headless Chrome")
    args = ap.parse_args()
    files, card = outputs()
    stale = []
    for path, content in files.items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path.relative_to(REPO)))
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(content)
            print(path.relative_to(REPO))
    if card and args.check:
        made_from = png_text(CARD.read_bytes(), CARD_KEY) if CARD.exists() else None
        if made_from != hashlib.sha256(card.encode()).hexdigest():
            stale.append(f"{CARD.relative_to(REPO)} (needs --png)")
    elif card and args.png:
        print(write_card(card))
    if stale:
        print("Generated charts are stale. Run `python3 scripts/render_model_catalog_charts.py --png` "
              "and commit the result:", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
