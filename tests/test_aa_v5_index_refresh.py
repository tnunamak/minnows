"""Regression tests for the Artificial Analysis Intelligence Index v5.0 refresh (catalog v0.5.17, policy v0.2.10).

Pins what the refresh must not change (partial v5 snapshot bytes, policy pairs, recommender picks) and what it adds
(full v5 snapshot values against a bounded raw-roster fixture, a v5-only cost-vs-intelligence chart, explicit gaps).

Run: uv run --with pytest --with jsonschema pytest tests/test_aa_v5_index_refresh.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "data" / "model-catalog"
POLICY = REPO / "data" / "model-choice-policy"
PERF = CATALOG / "performance"
FULL = PERF / "artificial-analysis-intelligence-index-v5-full-2026-10-28.json"
PARTIAL = PERF / "artificial-analysis-intelligence-index-v5-2026-10-28.json"
FIXTURE = REPO / "tests" / "fixtures" / "aa-v5-roster-excerpt-2026-10-28.json"
V5 = "artificial-analysis-intelligence-index-v5-0"
V4 = "artificial-analysis-intelligence-index-v4-3-2"
TAG = "data-model-catalog-v0.5.17"
PARTIAL_SHA256 = "3808289cae16aa86c574441e7b7cad7f6e126c8ff7a963e67592bf84996446b1"

SPEC = importlib.util.spec_from_file_location("render_charts_v5", REPO / "scripts" / "render_model_catalog_charts.py")
assert SPEC is not None and SPEC.loader is not None
rc = importlib.util.module_from_spec(SPEC)
sys.modules["render_charts_v5"] = rc
SPEC.loader.exec_module(rc)

# raw roster slug -> (catalog model, effort). The effort is the catalog's; the roster's effort label is in the fixture.
FIXTURE_KEYS = {
    "claude-opus-5-5": ("claude-opus-5-5", "max"),
    "claude-sonnet-5-5": ("claude-sonnet-5-5", "max"),
    "gpt-6-astra-xhigh": ("gpt-6-astra", "xhigh"),
    "gpt-6-1-terra": ("gpt-6.1-terra", "max"),
    "gpt-6-luna-non-reasoning": ("gpt-6-luna", "none"),
    "claude-haiku-5-5": ("claude-haiku-5-5", "max"),
}
# roster per-eval slug -> catalog metric id
EVAL_METRICS = {
    "aa-briefcase": "aa-briefcase-v1-1",
    "automationbench-aa": "automationbench-aa",
    "terminalbench-4-0": "terminal-bench-4-0",
    "frontiercode-2-0": "frontiercode-2-0",
    "omniscience": "aa-omniscience-v1-1",
    "gdp-pdf": "gdp-pdf",
    "hle-diamond": "hle-diamond",
    "critpt-v2-hard": "critpt-v2-hard",
    "terminal-bench-science": "terminal-bench-science-0-1",
}
# Frontier of the cost-vs-intelligence chart, v5.0 full snapshot, catalog v0.5.17 (renderer build_hero).
FRONTIER_V5 = [
    ("gpt-6-luna", "low"), ("gpt-6-luna", "medium"), ("claude-haiku-5-5", "low"), ("gpt-6.1-terra", "low"),
    ("claude-haiku-5-5", "high"), ("gpt-6.1-sol", "low"), ("gpt-6.1-terra", "medium"), ("gpt-6.1-terra", "high"),
    ("gpt-6.1-sol", "medium"), ("gpt-6.1-sol", "high"), ("gpt-6.1-sol", "xhigh"), ("gpt-6.1-sol", "max"),
    ("gpt-6-astra", "high"), ("gpt-6-astra", "xhigh"), ("claude-opus-5-5", "xhigh"), ("claude-opus-5-5", "max"),
]
# Every configured pair and candidate, as they were before this refresh (policy 0.2.9) and are after (0.2.10).
POLICY_PAIRS = {
    "recover.report": (("claude-haiku-5-5", "medium"), [("claude-sonnet-5-5", "medium")]),
    "fanout.explore": (("gpt-6.1-sol", "medium"), [("claude-opus-5-5", "medium"), ("claude-sonnet-5-5", "medium")]),
    "fanout.dollar-tight": (("gpt-6-luna", "medium"), [("claude-sonnet-5-5", "medium")]),
    "docs.lookup": (("claude-haiku-5-5", "medium"), [("claude-sonnet-5-5", "medium")]),
    "implement.standard": (("claude-opus-5-5", "medium"), [("gpt-6.1-sol", "medium")]),
    "implement.quota-tight": (("claude-sonnet-5-5", "medium"), [("gpt-6.1-sol", "medium")]),
    "implement.oracle-bounded": (("gpt-6-luna", "high"), [("claude-sonnet-5-5", "medium")]),
    "implement.accuracy-first": (("claude-opus-5-5", "high"), [("gpt-6.1-sol", "high")]),
    "review.audit": (("gpt-6.1-sol", "high"), [("claude-opus-5-5", "high"), ("claude-sonnet-5-5", "high")]),
    "advisor.deep": (("claude-opus-5-5", "high"), [("gpt-6.1-sol", "high")]),
    "ui.computer-use": (("gpt-6.1-sol", "medium"), [("claude-opus-5-5", "medium")]),
    "grok.explore-only": (("grok-4.7", "medium"), []),
    "review.rule-check": (("claude-haiku-5-5", "medium"), []),
}
# Recommender output before and after the refresh: every op's status and recommended model.
RECOMMENDER: dict[str, tuple[str, str | None]] = {op: ("INSUFFICIENT_EVIDENCE", None) for op in POLICY_PAIRS}
RECOMMENDER["grok.explore-only"] = ("RECOMMENDED", "grok-4.7")


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _v5_rows(snapshot):
    return [s for s in snapshot["scores"] if s["metric_id"] == V5]


def _key(row):
    return (row["model"], row.get("effort") or row.get("effort_verbatim") or "unknown")


def _hero():
    pack = rc.Pack.read()
    return pack, rc.build_hero(pack)


# ---------------------------------------------------------------- source values

def test_full_snapshot_index_values_equal_raw_roster_fixture():
    snap = _load(FULL)
    rows = {_key(r): r for r in _v5_rows(snap)}
    fixture = _load(FIXTURE)["objects"]
    assert len(fixture) == len(FIXTURE_KEYS)
    for obj in fixture:
        key = FIXTURE_KEYS[obj["slug"]]
        row = rows[key]
        assert abs(row["score"] - obj["intelligenceIndex"]) < 1e-3, (obj["slug"], row["score"])
        raw_cost = obj["intelligenceIndexCostPerTask"]["cost"]["total"]
        assert abs(row["cost"]["value"] - raw_cost) < 1e-4, (obj["slug"], row["cost"]["value"], raw_cost)
        assert row["cost"]["unit"] == "usd_per_task" and row["cost"]["basis"] == "api_usd"


def test_full_snapshot_per_eval_values_equal_raw_roster_fixture():
    snap = _load(FULL)
    fixture = _load(FIXTURE)["objects"]

    checked = 0
    for obj in fixture:
        model, effort = FIXTURE_KEYS[obj["slug"]]
        for ev in obj["intelligenceIndexEvaluations"]:
            metric = EVAL_METRICS[ev["slug"]]
            matches = [s for s in snap["scores"] if s["metric_id"] == metric and s["model"] == model
                       and s.get("effort") == effort]
            assert len(matches) == 1, (obj["slug"], metric)
            # the snapshot stores per-eval scores to two decimals (e.g. 48.13 for the roster's 48.1333)
            assert abs(matches[0]["score"] - ev["score"]) < 0.005, (obj["slug"], metric, matches[0]["score"], ev["score"])
            checked += 1
    assert checked == len(fixture) * 9



def test_article_quoted_costs_round_to_the_stored_values():
    # The v5 article quotes Opus 5.5 (max) at $10.0; the roster and the snapshot store 10.0364.
    # The current roster rounds xhigh to $0.40 and max to $0.96; this does not explain the historical article's disagreement.
    rows = {_key(r): r for r in _v5_rows(_load(FULL))}
    assert round(rows[("claude-opus-5-5", "max")]["cost"]["value"], 1) == 10.0
    assert round(rows[("gpt-6.1-terra", "max")]["cost"]["value"], 2) == 0.96
    assert round(rows[("gpt-6.1-terra", "xhigh")]["cost"]["value"], 2) == 0.40


def test_critpt_disagreement_is_recorded_not_resolved_by_the_article():
    # Article: GPT-6 Astra CritPt v2 hard subset 72%. The roster payload says 70.5 and the snapshot keeps the payload.
    snap = _load(FULL)
    row = [s for s in snap["scores"] if s["metric_id"] == "critpt-v2-hard" and s["model"] == "gpt-6-astra"
           and s.get("effort") == "xhigh"]
    assert len(row) == 1 and abs(row[0]["score"] - 0.705) < 1e-3


# ---------------------------------------------------------------- partial snapshot and no-mixing

def test_partial_v5_snapshot_is_byte_identical():
    assert hashlib.sha256(PARTIAL.read_bytes()).hexdigest() == PARTIAL_SHA256


def test_partial_rows_agree_with_full_rows_within_rounding():
    part = {_key(r): r for r in _v5_rows(_load(PARTIAL))}
    full = {_key(r): r for r in _v5_rows(_load(FULL))}
    shared = set(part) & set(full)
    assert len(shared) == 11
    for k in shared:
        assert abs(part[k]["score"] - full[k]["score"]) <= 0.5, k


def test_chart_metric_is_v5_and_guide_points_at_it():
    guide = _load(CATALOG / "guide.json")
    assert guide["hero"]["metric_id"] == V5
    metrics = {m["id"]: m for m in _load(CATALOG / "metrics.json")["metrics"]}
    assert V5 in metrics and metrics[V5]["comparable"] is False
    assert "v5.0" in rc.Pack.read().metrics[V5]["name"]


def test_hero_rows_are_v5_only_and_v4_rows_never_reach_the_chart():
    pack, hero = _hero()
    assert pack.hero_rows, "chart has no rows"
    assert {r["metric_id"] for r in pack.hero_rows} == {V5}
    assert hero["metric"].endswith("v5.0")
    v4 = [r for r in _load(CATALOG / "performance" / "artificial-analysis-intelligence-index-opus-5-5-2026-09.json")["scores"]]
    assert v4 and all(r["metric_id"] == V4 for r in v4)  # the v4 rows exist in the catalog, outside the chart
    v5_scores = {round(r["score"], 4) for r in _v5_rows(_load(FULL)) + _v5_rows(_load(PARTIAL))}
    assert all(round(p.score, 4) in v5_scores for p in hero["points"])


def test_hero_uses_the_precise_full_snapshot_not_the_rounded_partial_one():
    _, hero = _hero()
    terra_max = [p for p in hero["points"] if (p.model, p.effort) == ("gpt-6.1-terra", "max")]
    assert len(terra_max) == 1 and abs(terra_max[0].score - 50.9765) < 1e-3


# ---------------------------------------------------------------- frontier invariants

def test_frontier_is_the_pinned_v5_pairs():
    _, hero = _hero()
    got = [(p.model, p.effort) for p in hero["frontier"]]
    assert got == FRONTIER_V5


def test_frontier_is_pareto_within_the_chart_regime():
    _, hero = _hero()
    front = hero["frontier"]
    points = hero["points"]
    keys = {(p.model, p.effort) for p in points}
    assert {(p.model, p.effort) for p in front} <= keys
    # strictly increasing score as cost rises along the frontier
    for a, b in zip(front, front[1:]):
        assert a.cost <= b.cost and a.score < b.score
    # no plotted point dominates a frontier point
    for f in front:
        for p in points:
            dominates = p.cost <= f.cost and p.score >= f.score and (p.cost < f.cost or p.score > f.score)
            assert not dominates, (f.model, f.effort, p.model, p.effort)


def test_settings_without_a_cost_are_never_on_the_frontier():
    snap = _load(FULL)
    no_cost = {(r["model"], r.get("effort")) for r in _v5_rows(snap) if not r.get("cost")}
    assert no_cost, "expected at least one v5 Index row without a cost"
    _, hero = _hero()
    front = {(p.model, p.effort) for p in hero["frontier"]}
    assert not (front & no_cost)


def test_every_frontier_point_is_a_v5_row_with_the_same_score():
    _, hero = _hero()
    v5 = {}
    for r in _v5_rows(_load(FULL)) + _v5_rows(_load(PARTIAL)):
        v5.setdefault(_key(r), set()).add(round(r["score"], 4))
    for p in hero["frontier"]:
        assert round(p.score, 4) in v5[(p.model, p.effort)], (p.model, p.effort)


# ---------------------------------------------------------------- gaps and the snapshot shape

def test_snapshot_records_every_unmapped_catalog_key_with_a_reason():
    snap = _load(FULL)
    assert len(snap["missing"]) == 52
    for m in snap["missing"]:
        assert isinstance(m, str) and m.strip(), m
    assert any("gemini-4-carbon" in m for m in snap["missing"])
    assert any("kimi-k3" in m and "Not mapped" in m for m in snap["missing"])


def test_full_snapshot_declares_its_regime_and_source():
    snap = _load(FULL)
    assert snap["id"] == "artificial-analysis-intelligence-index-v5-full-2026-10-28"
    assert "aa-model-pages-v5-roster-2026-10-28" in snap["source_ids"]
    assert all(s["metric_id"] != V4 for s in snap["scores"])
    assert all(s.get("observed_at") == "2026-10-28" for s in snap["scores"])


# ---------------------------------------------------------------- policy: pairs, Luna max, Haiku max, picks

def test_policy_configured_pairs_and_candidates_are_unchanged():
    ops = _load(POLICY / "operating-points.json")["operating_points"]
    got = {}
    for op in ops:
        e = op["expands_to"]
        got[op["id"]] = ((e["model"], e["effort"]), [(c["model"], c.get("effort")) for c in op.get("candidates", [])])
    assert got == POLICY_PAIRS


def test_explicit_luna_and_haiku_max_overrides_still_resolve(tmp_path):
    import os

    models = [('codex', 'codex', 'gpt-6-luna', 'reasoningEffort'),
              ('claude-fixture', 'claudeAgent', 'claude-haiku-5-5', 'effort')]
    available = tmp_path / 'available.json'
    available.write_text(json.dumps({'providers': [
        {'instanceId': account, 'driver': driver, 'enabled': True,
         'models': [{'slug': model, 'options': [{'id': carrier, 'values': [{'id': 'max'}]}]}]}
        for account, driver, model, carrier in models]}))
    for account, driver, model, carrier in models:
        provider = 'codex' if driver == 'codex' else 'claude'
        proc = subprocess.run([
            sys.executable, str(REPO / 'tools/model-policy-ops/model-policy-ops'),
            'resolve', 'implement.oracle-bounded', '--json', '--policy', str(POLICY / 'operating-points.json'),
            '--available', str(available), '--receipts', str(tmp_path / 'decisions.jsonl'),
            '--proof-class', 'oracle', '--inputs', 'local', '--purpose', 'execution',
            '--override', 'provider=' + provider, '--override', 'model=' + model,
            '--override', 'account=' + account, '--override', 'effort=max',
            '--reason', 'Explicit bounded maker; deterministic oracle checks the result'],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / 'config')})
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out['launch_ready'] is True
        assert out['target'] == {'providerInstanceId': account, 'model': model, 'options': {carrier: 'max'}}


def test_recommender_picks_are_unchanged():
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "recommend_ops.py"), "--json"],
                          capture_output=True, text=True, check=True, cwd=REPO)
    results = json.loads(proc.stdout)["results"]
    got = {r["op"]: (r["status"], (r.get("recommended") or {}).get("model")
                     if isinstance(r.get("recommended"), dict) else r.get("recommended")) for r in results}
    assert got == RECOMMENDER


def test_pins_agree_across_packs_and_docs():
    tag = _load(CATALOG / "pack.json")["tag"]
    assert tag == TAG
    ops = _load(POLICY / "operating-points.json")
    assert ops["catalog_ref"] == tag and ops["policy_version"] == "0.2.10"
    assert _load(POLICY / "op-requirements.json")["catalog_ref"] == tag
    policy_pack = _load(POLICY / "pack.json")
    assert policy_pack["related"]["catalog"] == tag
    assert policy_pack["tag"] == "data-model-choice-policy-v0.2.10"
    assert "Pins catalog v0.5.17." in policy_pack["description"]
    readme = (POLICY / "README.md").read_text(encoding="utf-8")
    assert "TAG=data-model-choice-policy-v0.2.10" in readme
    assert "### v0.2.10" in readme


def test_policy_evidence_refs_resolve_to_the_v5_snapshot():
    ref = "catalog://performance/artificial-analysis-intelligence-index-v5-full-2026-10-28"
    ops = _load(POLICY / "operating-points.json")["operating_points"]
    assert all(ref in op["evidence_refs"] for op in ops)
    assert all(any(g.startswith("AA Intelligence Index v5.0 (") for g in op["known_gaps"]) for op in ops)
    assert (PERF / "artificial-analysis-intelligence-index-v5-full-2026-10-28.json").exists()


def test_new_metric_ids_are_defined_once():
    ids = [m["id"] for m in _load(CATALOG / "metrics.json")["metrics"]]
    assert len(ids) == len(set(ids))
    for new in ("automationbench-aa", "frontiercode-2-0", "hle-diamond", "critpt-v2-hard", "aa-omniscience-v1-1"):
        assert ids.count(new) == 1


def test_frontiercode_rows_are_available_to_existing_coding_advice():
    from test_recommend_ops import ro

    catalog = ro.Catalog.from_dir(CATALOG)
    ops = {o['id'] for o in _load(POLICY / 'operating-points.json')['operating_points']}
    requirements = ro.load_requirements(POLICY / 'op-requirements.json', catalog, ops)
    for op in ('implement.standard', 'implement.quota-tight', 'implement.oracle-bounded',
               'implement.accuracy-first', 'review.audit'):
        assert 'frontiercode-2-0' in requirements['ops'][op]['evidence_metrics'], op


def test_change_table_preserves_missing_costs_and_every_current_index_score():
    lines = (CATALOG / 'AA-INDEX-CHANGES.md').read_text().splitlines()
    table = [[v.strip() for v in line.strip('|').split('|')]
             for line in lines if line.startswith('|')]
    header = table[0]
    rows = {(r[0], r[1]): r for r in table[2:]}
    partial = {_key(r): r for r in _v5_rows(_load(PARTIAL))}
    for key, old in partial.items():
        cost = rows[key][header.index('partial v5 score / $')].split(' / ')[1]
        if 'cost' not in old:
            assert cost == '—', (key, 'missing historical cost must not become zero', cost)
    for current in _v5_rows(_load(FULL)):
        key = _key(current)
        assert key in rows, ('unpriced Index settings still need a score row', key)
        row = rows[key]
        assert float(row[header.index('v5.0 score')]) == round(current['score'], 2)
        if 'cost' not in current:
            assert row[header.index('v5.0 $/task')] == '—'
            assert row[header.index('v5 frontier')] == 'unpriced'


def test_benchmark_advice_cannot_rewrite_configured_pairs_or_candidates(tmp_path):
    # Benchmark frontiers only nominate candidates. Rewriting every v5 score and cost in a copy of the catalog
    # may change advisory recommendations, but must never write the configured policy.
    import shutil

    before = (POLICY / "operating-points.json").read_bytes()
    catalog = tmp_path / "model-catalog"
    shutil.copytree(CATALOG, catalog)
    for path in (catalog / "performance").glob("*intelligence-index*v5*.json"):
        doc = _load(path)
        for s in doc["scores"]:
            if isinstance(s.get("score"), (int, float)):
                s["score"] = 100.0 - s["score"]  # invert the ranking
            if isinstance(s.get("cost"), dict) and isinstance(s["cost"].get("value"), (int, float)):
                s["cost"]["value"] = s["cost"]["value"] * 37.0
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "recommend_ops.py"), "--catalog", str(catalog),
         "--policy", str(POLICY), "--json"],
        capture_output=True, text=True, check=True, cwd=REPO)
    results = json.loads(proc.stdout)["results"]
    assert {r["op"] for r in results} == set(POLICY_PAIRS)
    assert (POLICY / "operating-points.json").read_bytes() == before
    ops = _load(POLICY / "operating-points.json")["operating_points"]
    assert {o["id"]: (o["expands_to"]["model"], o["expands_to"]["effort"]) for o in ops} == \
        {k: v[0] for k, v in POLICY_PAIRS.items()}


def test_route_does_not_consume_benchmark_frontier_or_recommendations(tmp_path, monkeypatch, capsys):
    import runpy
    from datetime import datetime

    import pytest
    from test_route import World
    import directives

    world = World(tmp_path)
    world.write()
    original = Path.read_text

    def reject_benchmark_reads(path, *args, **kwargs):
        if 'performance' in path.parts or path.name in ('guide.json', 'recommend_ops.py'):
            raise AssertionError('routing must not rank from benchmark files: ' + str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', reject_benchmark_reads)
    monkeypatch.setattr(directives, 'now', lambda: datetime.fromisoformat('2026-10-10T00:30:00+00:00'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    tool = REPO / 'tools/model-policy-ops/model-policy-ops'
    monkeypatch.setattr(sys, 'argv', [str(tool), 'route', 'fanout.explore', *map(str, world.args()),
                                    '--json', '--decision-id', 'benchmark-boundary', '--purpose', 'exploration',
                                    '--proof-class', 'judged', '--inputs', 'local'])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(tool), run_name='__main__')
    assert stopped.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result['target']['model'] == 'gpt-6.1-sol'
    assert result['target']['options'] == {'reasoningEffort': 'medium'}
