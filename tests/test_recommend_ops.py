"""Behavioral tests for the expected-cost operating-point recommender.

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
MODELS = [
    {"id": "claude-cheap-2", "provider": "anthropic", "family": "claude-cheap", "status": "ga", "tier": "sonnet"},
    {"id": "claude-cheap-1", "provider": "anthropic", "family": "claude-cheap", "status": "ga", "tier": "sonnet"},
    {"id": "claude-strong-2", "provider": "anthropic", "family": "claude-strong", "status": "ga", "tier": "opus"},
    {"id": "gpt-9-small", "provider": "openai", "family": "gpt-9-small", "status": "ga", "tier": "luna"},
    {"id": "gpt-9-big", "provider": "openai", "family": "gpt-9-big", "status": "ga", "tier": "astra"},
    {"id": "grok-9", "provider": "xai", "family": "grok-9", "status": "ga"},
]
SURFACES = [
    {"model": "claude-cheap-2", "surface": "claude_code", "valid_efforts": EFFORTS},
    {"model": "claude-cheap-1", "surface": "claude_code", "valid_efforts": EFFORTS},
    {"model": "claude-strong-2", "surface": "api", "valid_efforts": EFFORTS},
    {"model": "gpt-9-small", "surface": "codex_cli", "valid_efforts": ["medium", "high", "xhigh"]},
    {"model": "gpt-9-big", "surface": "codex_cli", "valid_efforts": EFFORTS},
]
METRICS = [
    {"id": "code-a", "direction": "higher_better", "task_family": "coding"},
    {"id": "code-b", "direction": "higher_better", "task_family": "coding"},
    {"id": "err-rate", "direction": "lower_better", "task_family": "knowledge/factuality"},
    {"id": "index", "direction": "higher_better", "task_family": "coding"},
    {"id": "browse", "direction": "higher_better", "task_family": "research/browsing"},
    {"id": "unrelated", "direction": "higher_better", "task_family": "reasoning"},
]
PRICES = {"claude-cheap-2": 10, "claude-strong-2": 25, "gpt-9-small": 1, "gpt-9-big": 50}
CURRENT = {
    "implement.standard": {"provider": "claude", "model": "claude-cheap-2", "effort": "medium"},
    "review.audit": {"provider": "codex", "model": "gpt-9-big", "effort": "high"},
    "grok.explore-only": {"provider": "grok", "model": "grok-9", "effort": "high"},
    "docs.lookup": {"provider": "claude", "model": "claude-cheap-2", "effort": "medium"},
}
DEFAULTS = {
    "allowed_providers": ["claude", "codex"], "allowed_efforts": ["low", "medium", "high"],
    "cost_basis": "usd_per_task", "min_evidence_grade": "C",
    "source_weights": {"third_party_board": 2, "third_party_eval": 2, "vendor_table": 1, "other": 1},
    "vendor_cross_vendor_factor": 0.5,
}
FAMILIES = {"implement.standard": ["coding"], "review.audit": ["coding"],
            "grok.explore-only": ["research/browsing"], "docs.lookup": ["knowledge/factuality"]}


def row(model, effort, score, metric="code-a", group="g-board", cost=None, unit="accuracy",
        source_type="third_party_board", source_id="board", grade="B", observed_at="2026-09-01"):
    r = {"model": model, "effort": effort, "score": score, "metric_id": metric,
         "comparability_group": group, "unit": unit, "source_type": source_type,
         "source_id": source_id, "evidence_grade": grade, "observed_at": observed_at}
    if cost is not None:
        r["cost"] = {"value": cost, "unit": "usd_per_task", "basis": "api_usd"}
    return r


def req(op, **extra):
    return {"op": op, "status": "DRAFT", "constraints": [], "cost_basis": "usd_per_task",
            "rationale": "test", **({"silent_failure_cost_usd": 100} if op == "review.audit" else {}), **extra}


def build(tmp_path, rows, reqs, *, models=None, surfaces=None, defaults=None, metrics=None, families=None):
    cat = tmp_path / "catalog"
    for d in ("capabilities", "performance", "pricing"):
        (cat / d).mkdir(parents=True)
    (cat / "models.json").write_text(json.dumps({"models": MODELS if models is None else models}))
    (cat / "metrics.json").write_text(json.dumps({"metrics": METRICS if metrics is None else metrics}))
    (cat / "SOURCES.json").write_text(json.dumps({"sources": [
        {"id": "board", "publisher": "Independent Board"}, {"id": "oai", "publisher": "OpenAI"}]}))
    (cat / "capabilities" / "s.json").write_text(json.dumps({"surfaces": SURFACES if surfaces is None else surfaces}))
    (cat / "performance" / "p.json").write_text(json.dumps({"scores": rows}))
    (cat / "pricing" / "p.json").write_text(json.dumps({"kind": "api_usd", "models": {
        m: {"output_per_m": p} for m, p in PRICES.items()}}))
    pol = tmp_path / "policy"
    pol.mkdir()
    (pol / "operating-points.json").write_text(json.dumps({"operating_points": [
        {"id": op, "expands_to": CURRENT[op]} for op in CURRENT]}))
    (pol / "op-requirements.json").write_text(json.dumps({
        "defaults": DEFAULTS if defaults is None else defaults,
        "task_families": FAMILIES if families is None else families, "ops": reqs}))
    catalog = ro.Catalog.from_dir(cat)
    points = {op: {"id": op, "expands_to": CURRENT[op]} for op in CURRENT}
    loaded = ro.load_requirements(pol / "op-requirements.json", catalog, set(points))
    return catalog, loaded, points


def run(tmp_path, rows, reqs, **kw):
    catalog, loaded, points = build(tmp_path, rows, reqs, **kw)
    return {r["op"]: r for r in ro.recommend(catalog, loaded, points)}


def group(result, gid):
    return next(g for g in result["groups"] if g["group"] == gid)


def arm(result, gid, label):
    return next(a for a in group(result, gid)["arms"] if a["arm"] == label)


def test_verified_expected_cost_prefers_cheap_lower_probability(tmp_path):
    rows = [row("gpt-9-small", "medium", .4, cost=.2), row("gpt-9-big", "medium", .9, cost=2)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert group(r, "g-board")["choice"] == "codex/gpt-9-small/medium"
    assert arm(r, "g-board", "codex/gpt-9-small/medium")["expected_cost_usd"] == pytest.approx(.5)
    assert r["recommended"]["model"] == "gpt-9-small"


def test_verified_expected_cost_prefers_expensive_high_probability(tmp_path):
    rows = [row("gpt-9-small", "medium", .1, cost=.3), row("gpt-9-big", "medium", .9, cost=1)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert group(r, "g-board")["choice"] == "codex/gpt-9-big/medium"
    assert arm(r, "g-board", "codex/gpt-9-big/medium")["expected_cost_usd"] == pytest.approx(1/.9)


def test_lower_better_error_rate_converts_to_success_probability(tmp_path):
    rows = [row("gpt-9-small", "medium", .5, metric="err-rate", cost=.3, unit="error_rate"),
            row("gpt-9-big", "medium", .1, metric="err-rate", cost=.4, unit="error_rate")]
    r = run(tmp_path, rows, [req("docs.lookup")])["docs.lookup"]
    assert arm(r, "g-board", "codex/gpt-9-big/medium")["p"] == pytest.approx(.9)
    assert group(r, "g-board")["choice"] == "codex/gpt-9-big/medium"


def test_judged_cost_changes_choice_and_maker_p_is_visible(tmp_path):
    rows = [row("claude-cheap-2", "medium", .8, cost=.05),
            row("gpt-9-small", "medium", .6, cost=.1), row("gpt-9-big", "medium", .9, cost=3)]
    reqs = [req("implement.standard"), req("review.audit", constraints=["different_model_family_from:implement.standard"])]
    out = run(tmp_path, rows, reqs)
    assert out["implement.standard"]["recommended"]["model"] == "claude-cheap-2"
    checker = out["review.audit"]
    assert checker["recommended"]["model"] == "gpt-9-big"
    assert group(checker, "g-board")["maker_p"] == pytest.approx(.8)
    assert group(checker, "g-board")["checker_p"] == pytest.approx(.9)
    assert arm(checker, "g-board", "codex/gpt-9-big/medium")["expected_cost_usd"] == pytest.approx(13)
    assert "claude/claude-cheap-2/medium" not in checker["candidates"]


def test_task_family_derives_metrics_without_id_list(tmp_path):
    rows = [row("gpt-9-small", "medium", .5, cost=.1), row("gpt-9-big", "medium", .6, cost=.2),
            row("gpt-9-small", "medium", .9, metric="unrelated", group="other", cost=.1)]
    catalog, loaded, points = build(tmp_path, rows, [req("implement.standard")])
    assert set(loaded["ops"]["implement.standard"]["evidence_metrics"]) == {"code-a", "code-b", "index"}
    r = ro.recommend(catalog, loaded, points)[0]
    assert {g["metric_id"] for g in r["groups"]} == {"code-a"}


def test_index_and_elo_are_ceiling_screens_only(tmp_path):
    rows = [row("gpt-9-small", "medium", .7, cost=.2), row("gpt-9-big", "medium", .8, cost=1),
            row("gpt-9-small", "medium", 100, metric="index", group="g-index", cost=.01, unit="elo"),
            row("gpt-9-big", "medium", 90, metric="index", group="g-index", cost=2, unit="elo")]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert group(r, "g-index")["choice"] is None
    assert all(a["expected_cost_usd"] is None for a in group(r, "g-index")["arms"])
    assert group(r, "g-board")["choice"] == "codex/gpt-9-small/medium"


def test_non_success_metric_screens_unmeasured_effort(tmp_path):
    rows = [row("gpt-9-big", "medium", 100, metric="index", group="g-index", unit="elo"),
            row("gpt-9-small", "max", 80, metric="index", group="g-index", unit="elo"),
            row("claude-cheap-2", "medium", 90, metric="index", group="g-index", unit="elo")]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert any(e["model"] == "gpt-9-small" and "ceiling" in e["reason"] for e in r["excluded_models"])
    assert group(r, "g-index")["choice"] is None


def test_zero_success_has_infinite_expected_cost_and_valid_json(tmp_path):
    rows = [row("gpt-9-small", "medium", 0, cost=.01), row("gpt-9-big", "medium", .5, cost=1)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert arm(r, "g-board", "codex/gpt-9-small/medium")["expected_cost_usd"] == "Infinity"
    assert group(r, "g-board")["choice"] == "codex/gpt-9-big/medium"
    assert '"Infinity"' in json.dumps(r, allow_nan=False)


def test_candidate_filters_and_cli_effort(tmp_path):
    models = MODELS + [{"id": "claude-secret-1", "provider": "anthropic", "family": "secret", "tier": "mythos", "status": "ga", "access": "restricted"}]
    r = run(tmp_path, [], [req("implement.standard")], models=models)["implement.standard"]
    assert "claude/claude-cheap-1/medium" not in r["candidates"]
    assert "codex/gpt-9-small/low" not in r["candidates"]
    assert not any(x.endswith("/xhigh") for x in r["candidates"])
    assert not any("secret" in x for x in r["candidates"])
    assert any("claude-strong-2: no claude_code surface" in f for f in r["flags"])


def test_effortless_model_uses_default_arm(tmp_path):
    models = MODELS + [{"id": "claude-tiny-4", "provider": "anthropic", "family": "tiny", "tier": "haiku",
                        "status": "ga", "effort_parameter": False}]
    rows = [row("claude-cheap-2", "medium", .5, cost=1), row("claude-tiny-4", None, .49, cost=.1)]
    r = run(tmp_path, rows, [req("implement.standard")], models=models)["implement.standard"]
    assert "claude/claude-tiny-4/default" in r["candidates"]
    assert r["recommended"]["model"] == "claude-tiny-4"


def test_all_six_lanes_can_generate_candidates(tmp_path):
    models = MODELS + [
        {"id": "gemini-3.8-flash", "provider": "google", "family": "gemini-3.8", "tier": "flash", "status": "ga"},
        {"id": "qwen3.8-flash", "provider": "alibaba", "family": "qwen3.8", "tier": "flash", "status": "ga"},
        {"id": "deepseek-v4-flash", "provider": "deepseek", "family": "deepseek-v4", "tier": "flash", "status": "ga"},
        {"id": "claude-sonnet-4-6", "provider": "anthropic", "family": "claude-sonnet", "tier": "cross", "status": "ga"},
    ]
    models[5] = {**models[5], "tier": "base"}
    surfaces = SURFACES + [
        {"model": "gemini-3.8-flash", "surface": "api", "valid_efforts": EFFORTS},
        {"model": "qwen3.8-flash", "surface": "qwen_code", "valid_efforts": EFFORTS},
        {"model": "deepseek-v4-flash", "surface": "api", "valid_efforts": EFFORTS},
        {"model": "claude-sonnet-4-6", "surface": "api", "valid_efforts": EFFORTS},
        {"model": "grok-9", "surface": "grok_cli", "valid_efforts": EFFORTS},
    ]
    defaults = {**DEFAULTS, "allowed_providers": list(ro.LANES)}
    r = run(tmp_path, [], [req("implement.standard")], models=models, surfaces=surfaces, defaults=defaults)["implement.standard"]
    assert {x.split("/")[0] for x in r["candidates"]} == set(ro.LANES)
    assert "antigravity/gemini-3.8-flash-low/low" in r["candidates"]
    assert "antigravity/gemini-3.8-flash-medium/medium" in r["candidates"]
    assert "antigravity/claude-sonnet-4-6/medium" in r["candidates"]
    assert "claude/claude-sonnet-4-6/medium" in r["candidates"]
    assert any("deepseek/deepseek-v4-flash" in f for f in r["flags"])


def test_same_model_two_lanes_share_evidence(tmp_path):
    models = MODELS + [{"id": "claude-sonnet-4-6", "provider": "anthropic", "family": "cross", "tier": "cross", "status": "ga"}]
    surfaces = SURFACES + [{"model": "claude-sonnet-4-6", "surface": "api", "valid_efforts": EFFORTS}]
    rows = [row("claude-sonnet-4-6", "medium", .8, cost=.2), row("gpt-9-big", "medium", .7, cost=1)]
    defaults = {**DEFAULTS, "allowed_providers": ["claude", "codex", "antigravity"]}
    r = run(tmp_path, rows, [req("implement.standard")], models=models, surfaces=surfaces, defaults=defaults)["implement.standard"]
    assert arm(r, "g-board", "claude/claude-sonnet-4-6/medium")["p"] == pytest.approx(.8)
    assert arm(r, "g-board", "antigravity/claude-sonnet-4-6/medium")["p"] == pytest.approx(.8)
    assert r["status"] == "RECOMMENDED"


def test_newest_tier_is_scoped_to_reachable_lane(tmp_path):
    models = MODELS + [
        {"id": "claude-sonnet-4-6", "provider": "anthropic", "family": "claude-sonnet", "tier": "sonnet", "status": "ga"},
        {"id": "claude-sonnet-5", "provider": "anthropic", "family": "claude-sonnet", "tier": "sonnet", "status": "ga"},
    ]
    surfaces = SURFACES + [
        {"model": "claude-sonnet-4-6", "surface": "api", "valid_efforts": EFFORTS},
        {"model": "claude-sonnet-5", "surface": "claude_code", "valid_efforts": EFFORTS},
    ]
    defaults = {**DEFAULTS, "allowed_providers": ["claude", "antigravity"]}
    r = run(tmp_path, [], [req("implement.standard")], models=models, surfaces=surfaces, defaults=defaults)["implement.standard"]
    assert "claude/claude-sonnet-5/medium" in r["candidates"]
    assert "antigravity/claude-sonnet-4-6/medium" in r["candidates"]
    assert "claude/claude-sonnet-4-6/medium" not in r["candidates"]


def test_antigravity_recommendation_uses_dispatch_model_id(tmp_path):
    models = MODELS + [{"id": "gemini-3.8-flash", "provider": "google", "family": "gemini-3.8",
                        "tier": "flash", "status": "ga"}]
    surfaces = SURFACES + [{"model": "gemini-3.8-flash", "surface": "api", "valid_efforts": EFFORTS}]
    defaults = {**DEFAULTS, "allowed_providers": ["antigravity", "codex"]}
    rows = [row("gemini-3.8-flash", "medium", .8, cost=.2), row("gpt-9-big", "medium", .7, cost=1)]
    r = run(tmp_path, rows, [req("implement.standard")], models=models, surfaces=surfaces, defaults=defaults)["implement.standard"]
    assert r["recommended"] == {"provider": "antigravity", "model": "gemini-3.8-flash-medium",
                                "effort": "medium", "catalog_model": "gemini-3.8-flash"}


def test_different_family_allows_same_vendor_checker(tmp_path):
    rows = [row("claude-cheap-2", "medium", .8, cost=.05), row("claude-strong-2", "medium", .9, cost=.5),
            row("gpt-9-big", "medium", .6, cost=2)]
    reqs = [req("implement.standard"), req("review.audit", constraints=["different_model_family_from:implement.standard"])]
    checker = run(tmp_path, rows, reqs)["review.audit"]
    assert "claude/claude-strong-2/medium" in checker["candidates"]
    assert "claude/claude-cheap-2/medium" not in checker["candidates"]


def test_missing_cost_is_exact(tmp_path):
    rows = [row("gpt-9-big", "medium", .8, cost=2), row("gpt-9-small", "medium", .7)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert {"model": "gpt-9-small", "effort": "medium", "metric": "code-a", "group": "g-board",
            "need": "cost usd_per_task"} in r["missing"]


def test_disjoint_groups_report_cross_coverage(tmp_path):
    rows = [row("gpt-9-big", "medium", .8, group="gA", cost=2),
            row("gpt-9-small", "medium", .7, group="gA", cost=.2),
            row("claude-strong-2", "medium", .8, metric="code-b", group="gB", cost=2),
            row("claude-cheap-2", "medium", .7, metric="code-b", group="gB", cost=.2)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert {(m["model"], m["metric"]) for m in r["missing"]} >= {("gpt-9-small", "code-b"), ("claude-cheap-2", "code-a")}


def test_vendor_group_is_downweighted(tmp_path):
    rows = [row("gpt-9-big", "medium", .8, cost=2, source_type="vendor_table", source_id="oai"),
            row("claude-cheap-2", "medium", .7, cost=.2, source_type="vendor_table", source_id="oai")]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert group(r, "g-board")["weight"] == .5
    assert group(r, "g-board")["vendor_chart_ranks_rival_vendor"] is True


def test_vendor_ceiling_only_flags(tmp_path):
    rows = [row("gpt-9-big", "medium", .7, cost=1, source_type="vendor_table", source_id="oai"),
            row("claude-strong-2", "medium", .69, cost=2, source_type="vendor_table", source_id="oai"),
            row("claude-cheap-2", "max", .4, cost=5, source_type="vendor_table", source_id="oai")]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert any(c.startswith("claude/claude-cheap-2/") for c in r["candidates"])
    assert any("ceiling (vendor group, not excluding)" in f for f in r["flags"])


def test_effort_only_groups_do_not_outvote_cross_model_result(tmp_path):
    rows = []
    for gid in ("effort-1", "effort-2"):
        rows += [row("gpt-9-big", "low", .8, group=gid, cost=1),
                 row("gpt-9-big", "medium", .9, group=gid, cost=2)]
    rows += [row("gpt-9-big", "low", .8, group="cross", cost=1),
             row("claude-cheap-2", "low", .8, group="cross", cost=.3)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert r["recommended"]["model"] == "claude-cheap-2"


def test_grade_and_unattributed_effort_are_ignored(tmp_path):
    rows = [row("gpt-9-big", None, .9, cost=1), row("gpt-9-big", "medium", .9, cost=1, grade="D"),
            row("gpt-9-small", "medium", .5, cost=.1), row("claude-cheap-2", "medium", .6, cost=.2)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert {a["arm"] for a in group(r, "g-board")["arms"]} == {"codex/gpt-9-small/medium", "claude/claude-cheap-2/medium"}


def test_duplicate_rows_use_latest_and_same_day_cost(tmp_path):
    rows = [row("gpt-9-big", "medium", .8, cost=1), row("gpt-9-big", "medium", .8),
            row("gpt-9-big", "medium", .9, cost=2, observed_at="2026-09-20"),
            row("gpt-9-small", "medium", .5, cost=.1)]
    r = run(tmp_path, rows, [req("implement.standard")])["implement.standard"]
    assert arm(r, "g-board", "codex/gpt-9-big/medium")["score"] == .9
    assert any("duplicate row" in f for f in r["flags"])


def test_single_arm_group_does_not_decide(tmp_path):
    r = run(tmp_path, [row("gpt-9-big", "medium", .8, cost=1)], [req("implement.standard")])["implement.standard"]
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert group(r, "g-board")["unusable"].startswith("one candidate arm")


def test_maker_missing_shared_score_does_not_block_checker(tmp_path):
    rows = [row("claude-cheap-2", "medium", .8, cost=.2), row("claude-strong-2", "medium", .7, cost=2),
            row("gpt-9-small", "medium", .7, metric="code-b", group="gB", cost=.2),
            row("gpt-9-big", "medium", .9, metric="code-b", group="gB", cost=1)]
    reqs = [req("implement.standard"), req("review.audit", constraints=["different_model_family_from:implement.standard"])]
    checker = run(tmp_path, rows, reqs)["review.audit"]
    assert {"model": "claude-cheap-2", "effort": "medium", "metric": "code-b", "group": "gB",
            "need": "maker p for side-by-side comparison"} in checker["missing"]
    assert group(checker, "gB")["choice"] is not None


def test_constraint_falls_back_to_current_when_maker_unresolved(tmp_path):
    reqs = [req("implement.standard"), req("review.audit", constraints=["different_model_family_from:implement.standard"])]
    r = run(tmp_path, [], reqs)["review.audit"]
    assert any("uses its current expands_to" in f for f in r["flags"])
    assert not any("claude-cheap-2" in c for c in r["candidates"])


@pytest.mark.parametrize("bad,message", [
    (req("implement.standard", allowed_efforts=["xhigh"]), "effort_override_reason"),
    (req("implement.standard", evidence_metrics=["code-a"]), "derived"),
    (req("implement.standard", bar={"type": "frontier_best"}), "derived"),
    (req("review.audit", silent_failure_cost_usd=-1), "nonnegative"),
    (req("implement.standard", cost_basis="credits"), "usd_per_task"),
    (req("nope.op"), "not an operating-points.json id"),
])
def test_requirement_validation(tmp_path, bad, message):
    with pytest.raises(ro.RequirementsError, match=message):
        build(tmp_path, [], [bad])


def test_constraint_cycle_is_rejected(tmp_path):
    reqs = [req("implement.standard", constraints=["different_vendor_from:review.audit"]),
            req("review.audit", constraints=["different_vendor_from:implement.standard"])]
    with pytest.raises(ro.RequirementsError, match="cycle"):
        build(tmp_path, [], reqs)


def test_real_pack_schema_determinism_and_no_point_write():
    jsonschema = pytest.importorskip("jsonschema")
    pol = REPO / "data" / "model-choice-policy"
    doc = json.loads((pol / "op-requirements.json").read_text())
    schema = json.loads((pol / "schemas" / "op-requirements-v1.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(doc)
    catalog = ro.Catalog.from_dir(REPO / "data" / "model-catalog")
    points = {p["id"]: p for p in json.loads((pol / "operating-points.json").read_text())["operating_points"]}
    before = (pol / "operating-points.json").read_bytes()
    loaded = ro.load_requirements(pol / "op-requirements.json", catalog, set(points))
    assert set(loaded["ops"]) == set(points)
    first = ro.recommend(catalog, loaded, points)
    second = ro.recommend(catalog, loaded, points)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert all(r["status"] in {"RECOMMENDED", "INSUFFICIENT_EVIDENCE", "NO_CANDIDATES"} for r in first)
    assert all(r["recommended"] is None or r["recommended"]["effort"] not in {"xhigh", "max", "ultra"} for r in first)
    assert (pol / "operating-points.json").read_bytes() == before
