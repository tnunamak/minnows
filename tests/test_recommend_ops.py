"""Tests for scripts/recommend_ops.py.

Run: uv run --with pytest --with jsonschema pytest tests/test_recommend_ops.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recommend_ops", REPO / "scripts" / "recommend_ops.py")
assert SPEC is not None and SPEC.loader is not None
ro = importlib.util.module_from_spec(SPEC)
sys.modules["recommend_ops"] = ro
SPEC.loader.exec_module(ro)

EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Fixture world: two vendors, cheap/strong tiers each, plus one superseded model.
MODELS = [
    {"id": "claude-cheap-2", "provider": "anthropic", "family": "claude-cheap", "status": "ga", "tier": "sonnet"},
    {"id": "claude-cheap-1", "provider": "anthropic", "family": "claude-cheap", "status": "ga", "tier": "sonnet"},
    {"id": "claude-strong-2", "provider": "anthropic", "family": "claude-strong", "status": "ga", "tier": "opus"},
    {"id": "gpt-9-small", "provider": "openai", "family": "gpt-9", "status": "ga", "tier": "luna"},
    {"id": "gpt-9-big", "provider": "openai", "family": "gpt-9", "status": "ga", "tier": "astra"},
    {"id": "grok-9", "provider": "xai", "family": "grok-9", "status": "ga"},
]
PRICES = {"claude-cheap-2": 10, "claude-cheap-1": 10, "claude-strong-2": 25, "gpt-9-small": 1, "gpt-9-big": 50}
SURFACES = [
    {"model": "claude-cheap-2", "surface": "claude_code", "valid_efforts": EFFORTS},
    {"model": "claude-cheap-1", "surface": "claude_code", "valid_efforts": EFFORTS},
    # strong claude has only an api surface -> fallback, flagged
    {"model": "claude-strong-2", "surface": "api", "valid_efforts": EFFORTS},
    # codex CLI does not offer "low" for gpt-9-small, the api does
    {"model": "gpt-9-small", "surface": "codex_cli", "valid_efforts": ["medium", "high", "xhigh"]},
    {"model": "gpt-9-small", "surface": "api", "valid_efforts": EFFORTS},
    {"model": "gpt-9-big", "surface": "codex_cli", "valid_efforts": EFFORTS},
]
METRICS = [
    {"id": "code-a", "direction": "higher_better", "steward": "third_party_board"},
    {"id": "code-b", "direction": "higher_better", "steward": "vendor_table"},
    {"id": "err-rate", "direction": "lower_better", "steward": "vendor_table"},
    {"id": "vibes", "direction": "context_dependent", "steward": "vendor_table"},
]
SOURCES = [
    {"id": "board", "publisher": "Independent Board", "kind": "third_party_eval"},
    {"id": "oai", "publisher": "OpenAI", "kind": "vendor_blog"},
    {"id": "ant", "publisher": "Anthropic", "kind": "vendor_blog"},
]


def row(model, effort, score, metric="code-a", group="g-board", cost=None, source_type="third_party_board",
        source_id="board", grade="B", unit="accuracy", observed_at="2026-09-01"):
    r = {"model": model, "effort": effort, "score": score, "metric": metric, "metric_id": metric,
         "comparability_group": group, "unit": unit, "source_type": source_type, "source_id": source_id,
         "evidence_grade": grade, "observed_at": observed_at}
    if cost is not None:
        r["cost"] = {"value": cost, "unit": "usd_per_task", "basis": "api_usd"}
    return r


OPS = ["implement.standard", "review.audit", "grok.explore-only"]
CURRENT = {
    "implement.standard": {"provider": "claude", "model": "claude-cheap-2", "effort": "medium"},
    "review.audit": {"provider": "codex", "model": "gpt-9-big", "effort": "high"},
    "grok.explore-only": {"provider": "grok", "model": "grok-9", "effort": "high"},
}
DEFAULTS = {
    "allowed_providers": ["claude", "codex"],
    "allowed_efforts": ["low", "medium", "high"],
    "cost_basis": "usd_per_task",
    "min_evidence_grade": "C",
    "provider_restrictions": {"grok": ["grok.explore-only"]},
    "source_weights": {"third_party_board": 2, "third_party_eval": 2, "vendor_table": 1, "digitized_chart": 1, "other": 1},
    "vendor_cross_vendor_factor": 0.5,
}


def req(op, metrics=("code-a",), bar=None, constraints=(), **extra):
    return {"op": op, "status": "DRAFT", "evidence_metrics": list(metrics),
            "bar": bar or {"type": "within_points_of_best", "points": 5},
            "constraints": list(constraints), "cost_basis": "usd_per_task", "rationale": "test", **extra}


def build(tmp_path: Path, rows: list[dict], reqs: list[dict], models=None, surfaces=None, defaults=None):
    cat = tmp_path / "catalog"
    for d in ("capabilities", "performance", "pricing"):
        (cat / d).mkdir(parents=True)
    (cat / "models.json").write_text(json.dumps({"models": models or MODELS}))
    (cat / "metrics.json").write_text(json.dumps({"metrics": METRICS}))
    (cat / "SOURCES.json").write_text(json.dumps({"sources": SOURCES}))
    (cat / "capabilities" / "s.json").write_text(json.dumps({"surfaces": surfaces or SURFACES}))
    (cat / "performance" / "p.json").write_text(json.dumps({"scores": rows}))
    (cat / "pricing" / "p.json").write_text(json.dumps({
        "kind": "api_usd", "models": {m: {"output_per_m": p} for m, p in PRICES.items()}}))
    pol = tmp_path / "policy"
    pol.mkdir()
    (pol / "operating-points.json").write_text(json.dumps({"operating_points": [
        {"id": op, "expands_to": CURRENT[op]} for op in OPS]}))
    (pol / "op-requirements.json").write_text(json.dumps({
        "id": "model-choice-op-requirements", "schema_version": 1, "status": "DRAFT",
        "generated_at": "2026-09-22", "catalog_ref": "data-model-catalog-v0.0.1",
        "defaults": defaults or DEFAULTS, "ops": reqs}))
    catalog = ro.Catalog.from_dir(cat)
    points = {op: {"id": op, "expands_to": CURRENT[op]} for op in OPS}
    loaded = ro.load_requirements(pol / "op-requirements.json", catalog, set(points))
    return catalog, loaded, points


def run(tmp_path, rows, reqs, **kw) -> dict[str, dict]:
    catalog, loaded, points = build(tmp_path, rows, reqs, **kw)
    return {r["op"]: r for r in ro.recommend(catalog, loaded, points)}


def group(result, gid):
    return next(g for g in result["groups"] if g["group"] == gid)


def arm(result, gid, label):
    return next(a for a in group(result, gid)["arms"] if a["arm"] == label)


# ------------------------------------------------------------ candidate arms


def test_candidates_newest_in_tier_and_cli_surface(tmp_path):
    rows = [row("claude-cheap-2", "medium", 0.5, cost=1), row("gpt-9-big", "medium", 0.6, cost=3)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    cands = set(r["candidates"])
    # superseded same-tier model is excluded, newest kept
    assert "claude/claude-cheap-1/medium" not in cands
    assert any(e["model"] == "claude-cheap-1" and "superseded" in e["reason"] for e in r["excluded_models"])
    # effort filtering by CLI surface: codex_cli offers no "low" for gpt-9-small
    assert "codex/gpt-9-small/low" not in cands
    assert {"codex/gpt-9-small/medium", "codex/gpt-9-small/high"} <= cands
    # xhigh never enters (not in allowed_efforts)
    assert not any(c.endswith("/xhigh") for c in cands)
    # api fallback when the CLI surface is missing, and it says so
    assert "claude/claude-strong-2/low" in cands
    assert any("claude-strong-2: no claude_code surface" in f for f in r["flags"])
    # no tier -> not a candidate; grok is not an allowed provider here anyway
    assert not any("grok" in c for c in cands)


def test_model_without_any_surface_is_excluded(tmp_path):
    surfaces = [s for s in SURFACES if s["model"] != "gpt-9-big"]
    rows = [row("claude-cheap-2", "medium", 0.5, cost=1), row("claude-strong-2", "medium", 0.6, cost=3)]
    r = run(tmp_path, rows, [req("implement.standard")], surfaces=surfaces)["implement.standard"]
    assert any(e["model"] == "gpt-9-big" and "no codex_cli or api" in e["reason"] for e in r["excluded_models"])


def test_grok_has_no_tier_so_no_candidates(tmp_path):
    r = run(tmp_path, [], [req("grok.explore-only", allowed_providers=["grok"])])["grok.explore-only"]
    assert r["status"] == "NO_CANDIDATES"
    assert r["excluded_models"] == [{"model": "grok-9", "reason": "no tier in models.json"}]


# ----------------------------------------------------------- group evaluation


def test_domination_removes_arm_and_cheapest_eligible_wins(tmp_path):
    rows = [
        row("claude-strong-2", "medium", 0.60, cost=2.0),   # dominated by gpt-9-big/medium
        row("gpt-9-big", "medium", 0.70, cost=1.5),
        row("gpt-9-small", "medium", 0.68, cost=0.2),       # within 5 points, cheapest
    ]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert arm(r, "g-board", "claude/claude-strong-2/medium")["dominated"] is True
    assert arm(r, "g-board", "codex/gpt-9-small/medium")["dominated"] is False
    assert group(r, "g-board")["choice"] == "codex/gpt-9-small/medium"
    assert r["status"] == "RECOMMENDED"
    assert r["recommended"] == {"provider": "codex", "model": "gpt-9-small", "effort": "medium"}


def test_lower_better_metric_prefers_lower_score(tmp_path):
    rows = [
        row("gpt-9-big", "medium", 0.05, metric="err-rate", group="g-err", cost=1.0),
        row("gpt-9-small", "medium", 0.30, metric="err-rate", group="g-err", cost=0.1),  # 25 pp worse
        row("claude-cheap-2", "medium", 0.08, metric="err-rate", group="g-err", cost=0.5),  # 3 pp worse
    ]
    r = run(tmp_path, rows, [req("implement.standard", metrics=["err-rate"])])["implement.standard"]
    g = group(r, "g-err")
    assert g["direction"] == "lower_better"
    assert abs(g["bar_raw"] - 0.10) < 1e-9           # best 0.05 + 5 pp
    assert arm(r, "g-err", "codex/gpt-9-small/medium")["eligible"] is False
    assert g["choice"] == "claude/claude-cheap-2/medium"


def test_context_dependent_metric_is_skipped(tmp_path):
    rows = [row("gpt-9-big", "medium", 1, metric="vibes", group="g-v", cost=1),
            row("gpt-9-small", "medium", 2, metric="vibes", group="g-v", cost=1)]
    r = run(tmp_path, rows, [req("implement.standard", metrics=["vibes"])])["implement.standard"]
    assert r["groups"] == []
    assert any("not rankable" in s for s in r["skipped_groups"])


@pytest.mark.parametrize("bar,expected_choice,eligible", [
    ({"type": "frontier_best"}, "codex/gpt-9-big/medium", {"codex/gpt-9-big/medium"}),
    ({"type": "within_points_of_best", "points": 3},
     "codex/gpt-9-small/medium", {"codex/gpt-9-big/medium", "codex/gpt-9-small/medium"}),
    ({"type": "within_points_of_best", "points": 20},
     "claude/claude-cheap-2/low", {"codex/gpt-9-big/medium", "codex/gpt-9-small/medium", "claude/claude-cheap-2/low"}),
])
def test_bar_types(tmp_path, bar, expected_choice, eligible):
    rows = [
        row("gpt-9-big", "medium", 0.80, cost=3.0),
        row("gpt-9-small", "medium", 0.78, cost=0.5),
        row("claude-cheap-2", "low", 0.65, cost=0.1),
    ]
    r = run(tmp_path, rows, [req("implement.standard", bar=bar)])["implement.standard"]
    g = group(r, "g-board")
    assert {a["arm"] for a in g["arms"] if a["eligible"]} == eligible
    assert g["choice"] == expected_choice


def test_effort_unattributed_rows_are_ignored(tmp_path):
    rows = [row("gpt-9-big", None, 0.9, cost=1.0), row("gpt-9-big", "unknown", 0.9, cost=1.0),
            row("gpt-9-small", "medium", 0.5, cost=0.1), row("claude-cheap-2", "medium", 0.51, cost=0.2)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert {a["arm"] for a in group(r, "g-board")["arms"]} == {"codex/gpt-9-small/medium", "claude/claude-cheap-2/medium"}


def test_single_arm_group_is_not_decisive(tmp_path):
    rows = [row("gpt-9-big", "medium", 0.9, cost=1.0)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert group(r, "g-board")["unusable"].startswith("one candidate arm only")
    assert r["status"] == "INSUFFICIENT_EVIDENCE"


def test_duplicate_rows_keep_latest_and_flag(tmp_path):
    rows = [row("gpt-9-big", "medium", 0.70, cost=1.0, observed_at="2026-09-01"),
            row("gpt-9-big", "medium", 0.72, cost=1.0, observed_at="2026-09-20"),
            row("gpt-9-small", "medium", 0.5, cost=0.1)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert arm(r, "g-board", "codex/gpt-9-big/medium")["score"] == 0.72
    assert any("duplicate row" in f for f in r["flags"])


def test_grade_floor_drops_rows(tmp_path):
    rows = [row("gpt-9-big", "medium", 0.9, cost=1.0, grade="D"), row("gpt-9-small", "medium", 0.5, cost=0.1, grade="D")]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert r["groups"] == []
    assert any("below evidence grade C" in s for s in r["skipped_groups"])


# ------------------------------------------------------------- aggregation


def test_vendor_chart_ranking_rival_is_flagged_and_downweighted(tmp_path):
    rows = [
        row("gpt-9-big", "medium", 0.8, group="g-oai", metric="code-b", cost=2.0, source_type="vendor_table", source_id="oai"),
        row("claude-cheap-2", "medium", 0.6, group="g-oai", metric="code-b", cost=1.0, source_type="vendor_table", source_id="oai"),
        row("gpt-9-big", "medium", 0.8, group="g-oai-own", metric="code-b", cost=2.0, source_type="vendor_table", source_id="oai"),
        row("gpt-9-small", "medium", 0.5, group="g-oai-own", metric="code-b", cost=0.2, source_type="vendor_table", source_id="oai"),
    ]
    r = run(tmp_path, rows, [req("implement.standard", metrics=["code-b"])])["implement.standard"]
    assert group(r, "g-oai")["vendor_chart_ranks_rival_vendor"] is True
    assert group(r, "g-oai")["weight"] == 0.5
    assert group(r, "g-oai")["publisher_vendor"] == "openai"
    assert group(r, "g-oai-own")["vendor_chart_ranks_rival_vendor"] is False
    assert group(r, "g-oai-own")["weight"] == 1
    assert r["status"] == "RECOMMENDED" and r["confidence"] == "low"


def test_confidence_high_needs_two_independent_cross_model_groups(tmp_path):
    rows = []
    for g in ("g1", "g2"):
        rows += [row("gpt-9-small", "medium", 0.79, group=g, cost=0.2),
                 row("claude-strong-2", "medium", 0.80, group=g, cost=2.0)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert r["recommended"]["model"] == "gpt-9-small"
    assert r["confidence"] == "high"
    assert len(r["deciding_groups"]) == 2


def test_disjoint_coverage_gives_insufficient_evidence_with_exact_missing(tmp_path):
    rows = [
        # group A: only OpenAI arms; small wins
        row("gpt-9-big", "medium", 0.80, group="gA", cost=2.0),
        row("gpt-9-small", "medium", 0.78, group="gA", cost=0.2),
        # group B (other metric): only Claude arms; cheap wins
        row("claude-strong-2", "medium", 0.70, group="gB", metric="code-b", cost=2.0),
        row("claude-cheap-2", "medium", 0.69, group="gB", metric="code-b", cost=0.3),
    ]
    r = run(tmp_path, rows, [req("implement.standard", metrics=["code-a", "code-b"])])["implement.standard"]
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["recommended"] is None
    missing = {(m["model"], m["effort"], m["metric"]) for m in r["missing"]}
    assert ("gpt-9-small", "medium", "code-b") in missing
    assert ("claude-cheap-2", "medium", "code-a") in missing


def test_missing_cost_is_reported(tmp_path):
    rows = [row("gpt-9-big", "medium", 0.80, cost=2.0), row("gpt-9-small", "medium", 0.79)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert {"model": "gpt-9-small", "effort": "medium", "metric": "code-a", "group": "g-board",
            "need": "cost usd_per_task"} in r["missing"]


def test_bar_failure_anywhere_blocks_recommendation(tmp_path):
    rows = [
        row("gpt-9-small", "medium", 0.78, group="g1", cost=0.2), row("gpt-9-big", "medium", 0.80, group="g1", cost=2),
        row("gpt-9-small", "medium", 0.78, group="g2", cost=0.2), row("gpt-9-big", "medium", 0.80, group="g2", cost=2),
        row("gpt-9-small", "medium", 0.50, group="g3", cost=0.2), row("gpt-9-big", "medium", 0.80, group="g3", cost=2),
    ]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert any("gpt-9-small/medium" in d and "fails bar in g3" in d for d in r["disagreements"])


def test_effort_only_groups_do_not_outvote_cross_model_losses(tmp_path):
    rows = [
        # two single-model effort charts where gpt-9-big/low is the cheapest adequate effort
        row("gpt-9-big", "low", 0.79, group="e1", cost=1.0), row("gpt-9-big", "medium", 0.80, group="e1", cost=2.0),
        row("gpt-9-big", "low", 0.79, group="e2", cost=1.0), row("gpt-9-big", "medium", 0.80, group="e2", cost=2.0),
        # one cross-model group where a cheaper model is adequate
        row("gpt-9-big", "low", 0.79, group="x", cost=1.0), row("claude-cheap-2", "low", 0.78, group="x", cost=0.3),
    ]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    # gpt-9-big/low wins 4/6 of all weight but 0/2 of the cross-model weight, so only
    # claude-cheap-2/low is self-consistent. Without the cross-model rule both would be.
    assert r["status"] == "RECOMMENDED"
    assert r["recommended"] == {"provider": "claude", "model": "claude-cheap-2", "effort": "low"}


# ------------------------------------------------------------- constraints


def test_checker_at_least_maker_and_different_vendor(tmp_path):
    rows = [
        # maker evidence: claude-cheap-2/medium is the cheapest adequate implementer
        row("claude-cheap-2", "medium", 0.70, cost=0.5),
        row("claude-strong-2", "medium", 0.72, cost=3.0),
        # checker evidence in the same group: two OpenAI arms, one below the maker
        row("gpt-9-small", "medium", 0.60, cost=0.1),
        row("gpt-9-big", "medium", 0.75, cost=2.0),
    ]
    reqs = [
        req("implement.standard"),
        req("review.audit", bar={"type": "at_least_op", "op": "implement.standard"},
            constraints=["different_vendor_from:implement.standard"]),
    ]
    out = run(tmp_path, rows, reqs)
    maker = out["implement.standard"]
    assert maker["recommended"]["model"] == "claude-cheap-2"
    checker = out["review.audit"]
    # different vendor: no Claude arm is a candidate
    assert not any(c.startswith("claude/") for c in checker["candidates"])
    assert any(e["model"] == "claude-strong-2" and "different_vendor_from" in e["reason"] for e in checker["excluded_models"])
    # checker >= maker: gpt-9-small (0.60 < 0.70) fails, gpt-9-big (0.75) passes
    g = group(checker, "g-board")
    assert abs(g["bar_raw"] - 0.70) < 1e-9
    assert arm(checker, "g-board", "codex/gpt-9-small/medium")["eligible"] is False
    assert checker["recommended"] == {"provider": "codex", "model": "gpt-9-big", "effort": "medium"}


def test_checker_group_without_maker_score_is_unusable_and_reported(tmp_path):
    rows = [
        row("claude-cheap-2", "medium", 0.70, cost=0.5), row("claude-strong-2", "medium", 0.72, cost=3.0),
        row("gpt-9-big", "medium", 0.75, group="g-oai", metric="code-b", cost=2.0),
    ]
    reqs = [
        req("implement.standard"),
        req("review.audit", metrics=["code-a", "code-b"], bar={"type": "at_least_op", "op": "implement.standard"},
            constraints=["different_vendor_from:implement.standard"]),
    ]
    checker = run(tmp_path, rows, reqs)["review.audit"]
    assert group(checker, "g-oai")["unusable"] == "maker arm claude-cheap-2@medium not in group"
    assert {"model": "claude-cheap-2", "effort": "medium", "metric": "code-b", "group": "g-oai",
            "need": "maker score"} in checker["missing"]
    assert checker["status"] == "INSUFFICIENT_EVIDENCE"


def test_different_model_family_is_narrower_than_vendor(tmp_path):
    rows = [row("claude-cheap-2", "medium", 0.70, cost=0.5), row("claude-strong-2", "medium", 0.72, cost=3.0)]
    reqs = [req("implement.standard"),
            req("review.audit", constraints=["different_model_family_from:implement.standard"])]
    checker = run(tmp_path, rows, reqs)["review.audit"]
    # family claude-cheap is excluded, claude-strong (another family, same vendor) is not
    assert not any("claude-cheap-2" in c for c in checker["candidates"])
    assert any("claude-strong-2" in c for c in checker["candidates"])


def test_constraint_falls_back_to_current_expands_to_when_maker_unresolved(tmp_path):
    reqs = [req("implement.standard"),
            req("review.audit", constraints=["different_vendor_from:implement.standard"])]
    checker = run(tmp_path, [], reqs)["review.audit"]
    assert any("implement.standard is INSUFFICIENT_EVIDENCE; constraint uses its current expands_to" in f
               for f in checker["flags"])
    assert not any(c.startswith("claude/") for c in checker["candidates"])


# ------------------------------------------------------------ requirements


@pytest.mark.parametrize("bad,message", [
    (req("implement.standard", allowed_efforts=["high", "xhigh"]), "needs an owner effort_override_reason"),
    (req("implement.standard", allowed_providers=["grok"]), "restricted to"),
    (req("implement.standard", metrics=["nope"]), "not in metrics.json"),
    (req("implement.standard", bar={"type": "at_least_op", "op": "nope"}), "unknown op"),
    (req("nope.op"), "not an operating-points.json id"),
    (req("implement.standard", cost_basis="credits"), "usd_per_task"),
])
def test_requirements_validation(tmp_path, bad, message):
    with pytest.raises(ro.RequirementsError, match=message):
        build(tmp_path, [], [bad])


def test_constraint_cycle_is_rejected(tmp_path):
    reqs = [req("implement.standard", constraints=["different_vendor_from:review.audit"]),
            req("review.audit", constraints=["different_vendor_from:implement.standard"])]
    with pytest.raises(ro.RequirementsError, match="cycle"):
        build(tmp_path, [], reqs)


def test_override_reason_allows_xhigh(tmp_path):
    ok = req("implement.standard", allowed_efforts=["high", "xhigh"], effort_override_reason="owner said so")
    build(tmp_path, [], [ok])


# ------------------------------------------------------------ real pack


def test_real_pack_requirements_match_schema_and_run():
    jsonschema = pytest.importorskip("jsonschema")
    pol = REPO / "data" / "model-choice-policy"
    doc = json.loads((pol / "op-requirements.json").read_text())
    schema = json.loads((pol / "schemas" / "op-requirements-v1.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(doc)

    catalog = ro.Catalog.from_dir(REPO / "data" / "model-catalog")
    points = {p["id"]: p for p in json.loads((pol / "operating-points.json").read_text())["operating_points"]}
    before = (pol / "operating-points.json").read_bytes()
    loaded = ro.load_requirements(pol / "op-requirements.json", catalog, set(points))
    assert set(loaded["ops"]) == set(points), "one entry per operating point"
    results = ro.recommend(catalog, loaded, points)
    assert [r["op"] for r in results] == list(points)
    assert all(r["status"] in {"RECOMMENDED", "INSUFFICIENT_EVIDENCE", "NO_CANDIDATES"} for r in results)
    for r in results:
        if r["recommended"]:
            assert r["recommended"]["effort"] not in {"xhigh", "max", "ultra"}
    assert (pol / "operating-points.json").read_bytes() == before


def test_real_pack_is_deterministic():
    pol = REPO / "data" / "model-choice-policy"
    outs = []
    for _ in range(2):
        catalog = ro.Catalog.from_dir(REPO / "data" / "model-catalog")
        points = {p["id"]: p for p in json.loads((pol / "operating-points.json").read_text())["operating_points"]}
        loaded = ro.load_requirements(pol / "op-requirements.json", catalog, set(points))
        outs.append(json.dumps(ro.recommend(catalog, loaded, points), sort_keys=True))
    assert outs[0] == outs[1]
