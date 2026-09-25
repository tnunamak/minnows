"""Behavioral tests for the model-catalog README option map.

Run: uv run --with pytest pytest tests/test_render_model_catalog_charts.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "render_model_catalog_charts.py"
SPEC = importlib.util.spec_from_file_location("render_charts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
rc = importlib.util.module_from_spec(SPEC)
sys.modules["render_charts"] = rc
SPEC.loader.exec_module(rc)

# Invented names on purpose: the renderer must work for providers, tiers, efforts and
# benchmarks it has never heard of.
EFFORTS = ["gentle", "firm", "turbo"]
GUIDE = {
    "pools": [{
        "id": "acme", "label": "Acme", "providers": ["acme"], "surfaces": ["cli"], "color_slot": 1,
        "tiers": [
            {"tier": "pebble", "label": "Pebble", "summary": "small", "source_id": "s"},
            {"tier": "boulder", "label": "Boulder", "summary": "big", "source_id": "s"},
        ],
    }],
    "task_families": [{"id": "build", "label": "Building", "includes": ["building"]}],
    "noise_floor": {"label": "Ties under 3 points.", "source_ids": ["s"], "by_unit": {"accuracy": 0.03}, "by_metric": {}},
    "cost": {"score_cost_unit": "usd_per_task", "price_kind": "api_usd", "price_header": ["$/M", "in / out"],
             "footer": "Cheaper means cheaper."},
}
MODELS = {
    "pebble-2": {"id": "pebble-2", "name": "Pebble 2", "provider": "acme", "tier": "pebble", "status": "ga", "released": "2030-02-01"},
    "pebble-1": {"id": "pebble-1", "name": "Pebble 1", "provider": "acme", "tier": "pebble", "status": "ga", "released": "2030-01-01"},
    "boulder-1": {"id": "boulder-1", "name": "Boulder 1", "provider": "acme", "tier": "boulder", "status": "ga", "released": "2030-01-01"},
}
SURFACES = [{"model": m, "surface": "cli", "valid_efforts": EFFORTS} for m in MODELS]


def row(model, effort, score, cost, group="g1", metric="bench-a"):
    return {"model": model, "effort": effort, "score": score, "cost": {"value": cost, "unit": "usd_per_task"},
            "metric_id": metric, "comparability_group": group, "snapshot_id": group, "task_family": "building",
            "unit": "accuracy", "source_type": "vendor_table", "evidence_grade": "C", "_file": "performance/x.json"}


def build(rows, surfaces=SURFACES):
    pack = rc.Pack(guide=GUIDE, models=MODELS, metrics={}, rows=rows,
                   usd_prices={"pebble-2": (1.0, 2.0), "boulder-1": (5.0, 10.0)}, surfaces=surfaces)
    return rc.build(pack)


def cells(data):
    return data["pools"][0]["families"][0]["cells"]


def test_newest_ga_model_represents_its_tier():
    data = build([row("pebble-2", "gentle", 0.5, 1), row("boulder-1", "gentle", 0.6, 5)])
    assert [t["model"]["id"] for t in data["pools"][0]["tiers"]] == ["pebble-2", "boulder-1"]


def test_cheaper_model_that_scores_as_well_rules_out_the_expensive_one():
    data = build([row("pebble-2", "firm", 0.60, 1), row("boulder-1", "firm", 0.61, 5)])
    assert cells(data)[("boulder-1", "firm")].status == "cheaper"
    assert cells(data)[("pebble-2", "firm")].status == "try"


def test_tie_rule_is_strict_at_the_noise_floor():
    # exactly 3 points apart is not a tie
    data = build([row("pebble-2", "firm", 0.47, 1), row("boulder-1", "firm", 0.50, 5)])
    assert cells(data)[("boulder-1", "firm")].status == "try"


def test_a_benchmark_can_only_clear_a_setting_against_what_it_contains():
    rows = [
        row("pebble-2", "firm", 0.60, 1, group="g1"), row("boulder-1", "firm", 0.60, 5, group="g1"),
        # boulder's own launch chart has no pebble in it: it must not clear boulder
        row("boulder-1", "firm", 0.60, 5, group="g2"), row("boulder-1", "turbo", 0.70, 9, group="g2"),
    ]
    assert cells(build(rows))[("boulder-1", "firm")].status == "cheaper"


def test_disagreeing_benchmarks_give_mixed():
    rows = [
        row("pebble-2", "firm", 0.60, 1, group="g1"), row("boulder-1", "firm", 0.61, 5, group="g1"),
        row("pebble-2", "firm", 0.40, 1, group="g2"), row("boulder-1", "firm", 0.70, 5, group="g2"),
    ]
    assert cells(build(rows))[("boulder-1", "firm")].status == "mixed"


def test_lower_effort_of_same_model_is_named_and_self_only_is_flagged():
    rows = [row("boulder-1", "gentle", 0.70, 3), row("boulder-1", "turbo", 0.71, 9)]
    c = cells(build(rows))
    assert c[("boulder-1", "turbo")].status == "lower_effort"
    assert c[("boulder-1", "gentle")].status == "self_only"


def test_cheaper_higher_effort_is_not_called_a_lower_effort():
    rows = [row("boulder-1", "firm", 0.60, 5), row("boulder-1", "turbo", 0.70, 4)]
    assert cells(build(rows))[("boulder-1", "firm")].status == "cheaper"


def test_settings_the_pool_does_not_offer_neither_show_nor_rule_others_out():
    surfaces = [{"model": "pebble-2", "surface": "cli", "valid_efforts": ["gentle"]},
                {"model": "boulder-1", "surface": "cli", "valid_efforts": EFFORTS}]
    rows = [row("pebble-2", "firm", 0.9, 1), row("boulder-1", "firm", 0.6, 5), row("boulder-1", "gentle", 0.5, 3)]
    c = cells(build(rows, surfaces))
    assert c[("pebble-2", "firm")].status == "not_offered"
    # pebble's cheaper, better "firm" is not offered, so it cannot rule boulder out
    assert c[("boulder-1", "firm")].status == "self_only"


def test_invented_names_render_and_unmeasured_efforts_are_named():
    data = build([row("pebble-2", "gentle", 0.5, 1), row("boulder-1", "firm", 0.6, 5)])
    svg = rc.render_svg(data, "light")
    for text in ("Acme", "Pebble 2", "Boulder 1", "Building", "gentle", "firm"):
        assert text in svg
    assert ("turbo", ["Pebble 2", "Boulder 1"]) in data["unmeasured"]
    assert "turbo" in svg  # named in the footer, not silently dropped


def test_script_hardcodes_no_pack_entity():
    literals = [n.value for n in ast.walk(ast.parse(SCRIPT.read_text()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    catalog = REPO / "data" / "model-catalog"
    names = set()
    for m in json.loads((catalog / "models.json").read_text())["models"]:
        names |= {m["id"], m.get("provider", ""), m.get("tier", "")}
    names |= {m["id"] for m in json.loads((catalog / "metrics.json").read_text())["metrics"]}
    for s in json.loads((catalog / "capabilities" / "effort-surfaces-2026-07.json").read_text())["surfaces"]:
        names |= set(s.get("valid_efforts") or [])
    # SVG keywords that happen to equal a pack value (effort level "none" vs fill="none")
    svg_keywords = {"none"}
    names = {n for n in names if n and n not in svg_keywords}
    # a common word (low, max, other) counts only as a whole literal; a distinctive id also inside one
    found = sorted({n for n in names for lit in literals
                    if lit == n or (re.search(r"[-\d]", n) and re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", lit, re.I))})
    assert found == []


def test_script_prose_names_no_vendor_tier_or_family():
    """Catch names inside sentences too (e.g. 'Skip: Opus does as well' in alt text)."""
    literals = [n.value for n in ast.walk(ast.parse(SCRIPT.read_text()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    models = json.loads((REPO / "data" / "model-catalog" / "models.json").read_text())["models"]
    generic_words = {"other"}  # provider value that is also plain English
    names = {m.get(k) for m in models for k in ("tier", "provider", "family")} - {None} - generic_words
    found = sorted({n for n in names for lit in literals if re.search(rf"\b{re.escape(n)}\b", lit, re.I)})
    assert found == []
