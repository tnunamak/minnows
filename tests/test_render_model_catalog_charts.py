"""Behavioral tests for the model-catalog README option map.

Run: uv run --with pytest pytest tests/test_render_model_catalog_charts.py
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import re
import struct
import sys
import zlib
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


# Executable names the --png step looks up on PATH; not pack entities.
BROWSER_BINARIES = {"google-chrome", "chromium", "chromium-browser", "chrome"}


def test_script_prose_names_no_vendor_tier_or_family():
    """Catch names inside sentences too (e.g. 'Skip: Opus does as well' in alt text)."""
    literals = [n.value for n in ast.walk(ast.parse(SCRIPT.read_text()))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    literals = [lit for lit in literals if lit not in BROWSER_BINARIES]
    models = json.loads((REPO / "data" / "model-catalog" / "models.json").read_text())["models"]
    generic_words = {"other"}  # provider value that is also plain English
    names = {m.get(k) for m in models for k in ("tier", "provider", "family")} - {None} - generic_words
    found = sorted({n for n in names for lit in literals if re.search(rf"\b{re.escape(n)}\b", lit, re.I)})
    assert found == []


# ---------------------------------------------------------------- cost vs intelligence (hero)

HERO = {
    "title": "Brains per buck", "metric_id": "smartness-v9", "y_label": "Smartness", "credit": "Data from Testboard",
    "cost_basis": "api_usd", "exclude_groups_matching": "guessed",
    "bridge": {"score_tolerance": 1.0, "cost_rel_tolerance": 0.1},
    "flags": [{"caveat_matches": "unreleased build", "mark": "†", "label": "unreleased", "note": "ran on an unreleased build"}],
    "featured": {"newest_per_tier_providers": ["acme"], "statuses": ["ga"], "top_others": 1, "include_frontier": True},
    "providers": {"acme": {"label": "Acme Corp", "light": "#b4532a", "dark": "#e2875f"}},
    "other_providers": {"label": "Everyone else", "light": "#1a7380", "dark": "#4fadb8"},
    "notes": "test",
}
HERO_MODELS = {
    **MODELS,
    "zen-a": {"id": "zen-a", "name": "Zen A", "provider": "zenith", "tier": "t", "status": "ga", "released": "2030-01-01"},
    "zen-b": {"id": "zen-b", "name": "Zen B", "provider": "zenith", "tier": "t", "status": "ga", "released": "2030-01-02"},
    "orphan": {"id": "orphan", "name": "Orphan", "provider": "zenith", "status": "ga"},
}


def hrow(model, effort, score, cost, group="g1", observed="2030-03-01", caveat="", grade="B"):
    r = {"model": model, "effort": effort, "score": score, "metric_id": "smartness-v9", "comparability_group": group,
         "snapshot_id": group, "observed_at": observed, "caveat": caveat, "evidence_grade": grade, "_file": f"performance/{group}.json"}
    if cost is not None:
        r["cost"] = {"value": cost, "unit": "usd_per_task", "basis": "api_usd"}
    return r


ANCHOR = [hrow("pebble-2", "gentle", 30, 0.1), hrow("pebble-2", "turbo", 40, 0.4), hrow("boulder-1", "firm", 50, 2.0),
          hrow("zen-a", None, 35, 1.0)]


def hero(rows):
    pack = rc.Pack(guide={**GUIDE, "hero": HERO}, models=HERO_MODELS, metrics={"smartness-v9": {"name": "Smartness v9"}},
                   rows=[], usd_prices={}, surfaces=SURFACES, hero_rows=rows)
    return rc.build_hero(pack)


def test_hero_joins_only_snapshot_groups_that_agree_on_a_shared_setting():
    rows = ANCHOR + [
        # agrees with the anchor on boulder firm (display rounding): joins, brings zen-b
        hrow("boulder-1", "firm", 50.4, 2.05, group="g2"), hrow("zen-b", "firm", 45, 3.0, group="g2"),
        # disagrees by 3 points on pebble-2 turbo: left out, with its model
        hrow("pebble-2", "turbo", 43, 0.4, group="g3"), hrow("pebble-1", "firm", 20, 0.2, group="g3"),
        # shares no setting, so it cannot be checked: left out
        hrow("orphan", "firm", 44, 0.3, group="g4"),
        # estimated values: excluded by the guide pattern even though it agrees
        hrow("boulder-1", "firm", 50, 2.0, group="g5-guessed"), hrow("zen-a", "firm", 10, 0.01, group="g5-guessed"),
    ]
    v = hero(rows)
    assert set(v["models"]) == {"pebble-2", "boulder-1", "zen-a", "zen-b"}
    assert v["left_out"] == ["orphan", "pebble-1"]
    assert all(not (p.model == "zen-a" and p.effort == "firm") for p in v["points"])


def test_hero_group_that_disagrees_on_cost_does_not_join():
    v = hero(ANCHOR + [hrow("boulder-1", "firm", 50, 3.0, group="g2"), hrow("zen-b", "firm", 45, 3.0, group="g2")])
    assert "zen-b" in v["left_out"]


def test_hero_plots_the_latest_observation_of_each_setting():
    rows = ANCHOR + [hrow("boulder-1", "firm", 50.6, 2.1, group="g2", observed="2030-04-01")]
    v = hero(rows)
    (p,) = [p for p in v["points"] if p.model == "boulder-1"]
    assert (p.score, p.cost, p.observed) == (50.6, 2.1, "2030-04-01")
    assert v["dates"] == ("2030-03-01", "2030-04-01")


def test_hero_names_models_and_settings_without_cost_instead_of_dropping_them():
    v = hero(ANCHOR + [hrow("pebble-1", "firm", 25, None), hrow("pebble-2", "firm", 35, None)])
    assert v["no_cost"] == ["pebble-1"]
    assert v["unpriced"] == 1
    svg = rc.render_hero_svg(v, "light", list(v["models"]))
    assert "Pebble 1" in svg and "1 more effort settings" in svg


def test_pareto_frontier_keeps_only_settings_nothing_beats_on_both_axes():
    P = rc.Point
    pts = [P("a", "x", 10, 0.1, "", ()), P("b", "x", 30, 0.5, "", ()), P("c", "x", 25, 1.0, "", ()),
           P("d", "x", 30, 0.5, "", ()), P("e", "x", 40, 0.5, "", ()), P("f", "x", 50, 4.0, "", ())]
    assert [(p.model) for p in rc.pareto(pts)] == ["a", "e", "f"]


def test_flags_mark_caveated_rows_and_featured_follows_the_guide():
    rows = ANCHOR + [hrow("zen-b", "firm", 45, 3.0, caveat="Ran on an unreleased build."),
                     hrow("pebble-1", "gentle", 20, 0.3), hrow("boulder-1", "gentle", 48, 1.5)]
    v = hero(rows)
    assert [p.marks for p in v["points"] if p.model == "zen-b"] == [("†",)]
    assert v["flags"] == [("†", "unreleased", "ran on an unreleased build")]
    # newest acme model per tier (pebble-2 over pebble-1; boulder-1) and the best other-vendor model (zen-b);
    # every frontier setting already belongs to one of them
    assert v["featured"] == ["boulder-1", "pebble-2", "zen-b"]
    svg = rc.render_hero_svg(v, "dark", v["featured"])
    assert "Zen B †" in svg and "unreleased" in svg and "Pebble 1" not in svg and "Zen A" not in svg


def test_hero_renders_invented_names_with_title_and_role():
    v = hero(ANCHOR)
    for theme in ("light", "dark"):
        svg = rc.render_hero_svg(v, theme, list(v["models"]))
        assert svg.count('role="img"') == 1 and "<title>Brains per buck" in svg
        for text in ("Acme Corp", "Everyone else", "Pebble 2", "Zen A", "Smartness v9", "Data from Testboard"):
            assert text in svg
    block = "\n".join(rc.hero_block(v))
    assert "| Pebble 2 | turbo | 40 | $0.40 | 2030-03-01 | on the frontier |" in block
    assert "| Zen A | – | 35 | $1.00 | 2030-03-01 |  |" in block


def test_label_layout_never_overlaps_and_stays_close():
    desired = [100, 101, 102, 150, 300, 301, 299, 500, 505]
    got = rc.place_labels(desired, 14, 90, 520)
    order = sorted(range(len(desired)), key=lambda i: desired[i])
    ys = [got[i] for i in order]
    assert all(b - a >= 14 - 1e-9 for a, b in itertools.pairwise(ys))  # no overlap, order kept
    assert 90 <= min(ys) and max(ys) <= 520
    assert got[3] == 150  # a label with room stays exactly on its line
    assert max(abs(g - d) for g, d in zip(got, desired)) <= 21  # a run of three moves at most one gap
    # crowded against the bottom edge: pushed up, still inside
    got = rc.place_labels([515, 516, 517, 518], 14, 0, 520)
    assert max(got) <= 520 and min(b - a for a, b in zip(sorted(got), sorted(got)[1:])) >= 14 - 1e-9


def test_vendor_colours_meet_wcag_aa_on_both_backgrounds():
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def contrast(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)
    guide = json.loads((REPO / "data" / "model-catalog" / "guide.json").read_text())["hero"]
    for style in [*guide["providers"].values(), guide["other_providers"]]:
        for theme in ("light", "dark"):
            assert contrast(style[theme], rc.THEMES[theme]["bg"]) >= 4.5, (style["label"], theme)


def test_png_source_hash_round_trips():
    ihdr = b"\0\0\0\x01\0\0\0\x01\x08\x02\0\0\0"
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    tagged = rc.with_png_text(png, rc.CARD_KEY, "abc123")
    assert rc.png_text(tagged, rc.CARD_KEY) == "abc123" and rc.png_text(png, rc.CARD_KEY) is None
    assert tagged.endswith(chunk(b"IEND", b""))


def test_generated_output_is_deterministic():
    first, card1 = rc.outputs()
    second, card2 = rc.outputs()
    assert first == second and card1 == card2
