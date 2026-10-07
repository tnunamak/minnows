"""Validation-path tests for data/decision-model-catalog.

Run: uv run --with pytest --with jsonschema pytest tests/test_validate_decision_pack.py
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vdp = _load("validate_data_pack", "scripts/validate_data_pack.py")
cf = _load("check_freshness", "scripts/check_freshness.py")

BASELINE = REPO / "data" / "model-catalog"
SEED = REPO / "data" / "decision-model-catalog"
SCHEMAS = SEED / "schemas"


def baseline_model() -> tuple[str, str | None]:
    """A real baseline model id and, if any model has one, an alias."""
    models = json.loads((BASELINE / "models.json").read_text())["models"]
    first = models[0]["id"]
    alias = next((a for m in models for a in m.get("aliases") or [] if a), None)
    return first, alias


SRC = "acme-launch-2026-10-01"

SOURCES = {
    "$schema": "schemas/sources-v1.schema.json", "schema_version": 1,
    "id": "decision-model-catalog-sources", "retrieved_at": "2026-10-06",
    "sources": [{
        "id": SRC, "url": "https://example.com/launch", "title": "Acme launch",
        "publisher": "Acme", "published": "2026-10-01", "retrieved_at": "2026-10-06",
        "kind": "vendor_blog",
    }],
}
MODELS = {
    "id": "decision-model-catalog-models", "schema_version": 1, "generated_at": "2026-10-06",
    "models": [{
        "id": "acme-decider", "provider": "acme", "family": "acme-decider", "status": "early_access",
        "aliases": [], "weights": "open", "license": None, "base_model": None,
        "params_total": 26e9, "params_active": 4e9, "hf_repo": None, "surfaces": ["workers-ai"],
    }],
}
METRICS = {
    "id": "decision-model-catalog-metrics", "schema_version": 1, "generated_at": "2026-10-06",
    "metrics": [
        {"id": "acme-bench-accuracy", "name": "Acme bench accuracy", "direction": "higher_better",
         "unit_default": "accuracy", "publisher": "Acme", "suite": "acme-bench"},
        {"id": "latency-ms", "name": "Decision latency", "direction": "lower_better", "unit_default": "ms"},
        {"id": "cost-per-decision", "name": "Cost per decision", "direction": "lower_better",
         "unit_default": "usd_per_decision"},
    ],
}
CAVEATS = {
    "id": "decision-model-catalog-caveats", "schema_version": 1, "generated_at": "2026-10-06",
    "caveats": [{
        "id": "rtt-vs-on-platform",
        "statement": "Hosted round-trip latency includes network time that on-platform latency does not.",
        "affects": {"models": ["acme-decider"], "metrics": ["latency-ms"], "sources": [SRC]},
        "strength": "strong",
        "implication": "Do not compute a speed ratio across vantages.",
        "evidence": [{"source_id": SRC, "quote": "verbatim quote"}],
    }],
}
MEAS = {"measured_by": "Acme", "vantage": "on_platform", "percentile": "p50"}


def perf_doc(*scores: dict) -> dict:
    return {
        "id": "acme-launch-2026-10", "schema_version": 1, "kind": "performance", "provider": "acme",
        "retrieved_at": "2026-10-06", "source_urls": ["https://example.com/launch"],
        "source_ids": [SRC], "scores": list(scores),
    }


def acc_row(model: str = "acme-decider", **kw) -> dict:
    row = {"model": model, "metric": "Acme bench", "metric_id": "acme-bench-accuracy",
           "score": 0.9, "unit": "accuracy", "source_id": SRC, "source_type": "vendor_table",
           "snapshot_id": "acme-launch", "comparability_group": "acme-bench-v1"}
    row.update(kw)
    return row


def lat_row(**kw) -> dict:
    row = {"model": "acme-decider", "metric": "latency", "metric_id": "latency-ms", "score": 39.0,
           "unit": "ms", "source_id": SRC, "source_type": "vendor_claim", "measurement": dict(MEAS)}
    row.update(kw)
    return row


def write(pack: Path, rel: str, doc: dict) -> None:
    path = pack / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    pj = json.loads((pack / "pack.json").read_text())
    if rel not in pj["files"]:
        pj["files"] = sorted([*pj["files"], rel])
        (pack / "pack.json").write_text(json.dumps(pj, indent=2))


def skeleton(dst: Path) -> None:
    """Empty pack: real schemas and docs, no data files (the live pack is populated; tests start clean)."""
    dst.mkdir()
    shutil.copytree(SEED / "schemas", dst / "schemas")
    for name in ("README.md", "SCHEMA.md", "FRESHNESS.md", "CHANGELOG.md"):
        shutil.copy(SEED / name, dst / name)
    pj = json.loads((SEED / "pack.json").read_text())
    data_files = {"SOURCES.json", "caveats.json", "metrics.json", "models.json"}
    pj["files"] = sorted(f for f in pj["files"] if (f.startswith("schemas/") or "/" not in f) and f not in data_files)
    (dst / "pack.json").write_text(json.dumps(pj, indent=2))


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    """A populated, valid decision pack in a tmp dir (real schemas, fake data)."""
    dst = tmp_path / "decision-model-catalog"
    skeleton(dst)
    write(dst, "SOURCES.json", SOURCES)
    write(dst, "models.json", MODELS)
    write(dst, "metrics.json", METRICS)
    write(dst, "caveats.json", CAVEATS)
    return dst


def run(pack: Path) -> list[str]:
    errors = vdp.Errors()
    vdp.validate_decision_model_catalog(pack, errors, baseline_dir=BASELINE)
    return errors.items


def has(errors: list[str], needle: str) -> bool:
    return any(needle in e for e in errors)


def schema_errors(schema_name: str, doc: dict) -> list[str]:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMAS / schema_name).read_text())
    v = jsonschema.Draft202012Validator(schema)
    return [e.message for e in v.iter_errors(doc)]


# --- the shipped pack -------------------------------------------------------

def test_shipped_pack_is_valid():
    errors = vdp.Errors()
    vdp.validate_decision_model_catalog(SEED, errors)
    assert errors.items == []


def test_populated_fixture_is_valid(pack):
    write(pack, "performance/acme.json", perf_doc(acc_row(), lat_row()))
    assert run(pack) == []


def test_model_catalog_validation_unchanged():
    errors = vdp.Errors()
    vdp.validate_model_catalog(BASELINE, errors)
    assert errors.items == []


# --- model resolution -------------------------------------------------------

def test_baseline_model_resolves_via_model_catalog(pack):
    base_id, alias = baseline_model()
    rows = [acc_row(model=base_id), acc_row(model=f"{base_id}@some-harness")]
    if alias:
        rows.append(acc_row(model=alias))
    write(pack, "performance/baselines.json", perf_doc(*rows))
    assert run(pack) == []


def test_unknown_model_fails(pack):
    write(pack, "performance/x.json", perf_doc(acc_row(model="no-such-model-anywhere")))
    assert has(run(pack), "'no-such-model-anywhere' not in models.json")


def test_duplicating_a_baseline_model_locally_fails(pack):
    base_id, _ = baseline_model()
    models = copy.deepcopy(MODELS)
    models["models"].append({**models["models"][0], "id": base_id})
    write(pack, "models.json", models)
    assert has(run(pack), "already exists in the baseline model-catalog")


# --- caveats ----------------------------------------------------------------

def test_unknown_caveat_id_on_score_fails(pack):
    write(pack, "performance/x.json", perf_doc(acc_row(caveat_ids=["no-such-caveat"])))
    assert has(run(pack), "caveat_ids entry 'no-such-caveat' not in caveats.json")


def test_known_caveat_id_passes(pack):
    write(pack, "performance/x.json", perf_doc(lat_row(caveat_ids=["rtt-vs-on-platform"])))
    assert run(pack) == []


def test_unknown_caveat_id_on_model_fails(pack):
    models = copy.deepcopy(MODELS)
    models["models"][0]["caveat_ids"] = ["nope-nope"]
    write(pack, "models.json", models)
    assert has(run(pack), "caveat_ids entry 'nope-nope' not in caveats.json")


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda c: c["affects"].update(models=["ghost-model"]), "affects.models entry 'ghost-model'"),
        (lambda c: c["affects"].update(metrics=["ghost-metric"]), "affects.metrics entry 'ghost-metric'"),
        (lambda c: c["affects"].update(sources=["ghost-source"]), "affects.sources entry 'ghost-source'"),
        (lambda c: c.update(evidence=[]), "evidence must be a non-empty array"),
        (lambda c: c["evidence"][0].update(source_id="ghost-source"), "source_id 'ghost-source' not in SOURCES.json"),
        (lambda c: c.update(strength="certain"), "strength must be one of"),
        (lambda c: c.update(affects={}), "affects must name at least one"),
    ],
)
def test_bad_caveat_fails(pack, mutate, needle):
    caveats = copy.deepcopy(CAVEATS)
    mutate(caveats["caveats"][0])
    write(pack, "caveats.json", caveats)
    assert has(run(pack), needle)


def test_duplicate_caveat_id_fails(pack):
    caveats = copy.deepcopy(CAVEATS)
    caveats["caveats"].append(copy.deepcopy(caveats["caveats"][0]))
    write(pack, "caveats.json", caveats)
    assert has(run(pack), "duplicate caveat id")


def test_caveat_may_affect_a_baseline_model(pack):
    base_id, _ = baseline_model()
    caveats = copy.deepcopy(CAVEATS)
    caveats["caveats"][0]["affects"]["models"] = [base_id]
    write(pack, "caveats.json", caveats)
    assert run(pack) == []


# --- latency / cost context -------------------------------------------------

def test_latency_row_without_vantage_fails(pack):
    row = lat_row()
    del row["measurement"]["vantage"]
    write(pack, "performance/x.json", perf_doc(row))
    assert has(run(pack), "latency row (unit ms) requires measurement.vantage")
    assert schema_errors("performance-v1.schema.json", perf_doc(row))


def test_latency_row_without_measurement_fails(pack):
    row = lat_row()
    del row["measurement"]
    write(pack, "performance/x.json", perf_doc(row))
    errs = run(pack)
    assert has(errs, "requires measurement.measured_by")
    assert has(errs, "requires measurement.percentile")
    assert schema_errors("performance-v1.schema.json", perf_doc(row))


def test_latency_row_with_bad_vantage_fails(pack):
    write(pack, "performance/x.json", perf_doc(lat_row(measurement={**MEAS, "vantage": "the-cloud"})))
    assert has(run(pack), "measurement.vantage must be one of")


def test_latency_row_with_full_context_passes_both_layers(pack):
    row = lat_row(measurement={**MEAS, "hardware": "A10", "region": "iad", "input_tokens": 700,
                               "questions_per_request": 1, "prefix_cache": None, "n": 1000})
    write(pack, "performance/x.json", perf_doc(row))
    assert run(pack) == []
    assert schema_errors("performance-v1.schema.json", perf_doc(row)) == []


def test_cost_per_decision_row_needs_measured_by(pack):
    row = {"model": "acme-decider", "metric": "cost", "metric_id": "cost-per-decision", "score": 0.00001,
           "unit": "usd_per_decision", "source_id": SRC, "source_type": "vendor_claim"}
    write(pack, "performance/x.json", perf_doc(row))
    assert has(run(pack), "cost row (unit usd_per_decision) requires measurement.measured_by")
    row["measurement"] = {"measured_by": "Acme", "input_tokens": 300}
    write(pack, "performance/x.json", perf_doc(row))
    assert run(pack) == []


def test_score_without_metric_id_fails(pack):
    row = acc_row()
    del row["metric_id"]
    write(pack, "performance/x.json", perf_doc(row))
    assert has(run(pack), "decision score rows require metric_id")


def test_unregistered_metric_id_fails(pack):
    write(pack, "performance/x.json", perf_doc(acc_row(metric_id="not-registered")))
    assert has(run(pack), "metric_id 'not-registered' not in metrics.json")


def test_decision_units_are_accepted_and_unknown_unit_fails(pack):
    write(pack, "performance/x.json", perf_doc(acc_row(unit="ece", metric_id="acme-bench-accuracy"),
                                               acc_row(unit="furlongs")))
    errs = run(pack)
    assert has(errs, "unit must be one of")
    assert not has(errs, "scores[0]: unit")


def test_mixed_source_types_on_one_metric_must_be_non_comparable(pack):
    write(pack, "performance/x.json",
          perf_doc(acc_row(), acc_row(source_type="community_report", snapshot_id="other", comparability_group="g2")))
    assert has(run(pack), "mixes source/harness classes")


# --- provenance / envelope --------------------------------------------------

def test_unknown_source_id_fails(pack):
    write(pack, "performance/x.json", perf_doc(acc_row(source_id="ghost-source")))
    assert has(run(pack), "source_id 'ghost-source' not in SOURCES.json")


def test_pack_file_list_must_match_disk(pack):
    (pack / "performance").mkdir(exist_ok=True)
    (pack / "performance" / "unlisted.json").write_text("{}")
    assert has(run(pack), "file on disk not listed in pack.json: performance/unlisted.json")


def test_listed_file_missing_fails(pack):
    pj = json.loads((pack / "pack.json").read_text())
    pj["files"].append("performance/ghost.json")
    (pack / "pack.json").write_text(json.dumps(pj))
    assert has(run(pack), "listed file missing: performance/ghost.json")


def test_wrong_registry_ids_fail(pack):
    write(pack, "SOURCES.json", {**SOURCES, "id": "model-catalog-sources"})
    write(pack, "models.json", {**MODELS, "id": "model-catalog-models"})
    write(pack, "metrics.json", {**METRICS, "id": "model-catalog-metrics"})
    write(pack, "caveats.json", {**CAVEATS, "id": "model-catalog-caveats"})
    errs = run(pack)
    for kind in ("sources", "models", "metrics", "caveats"):
        assert has(errs, f"id must be 'decision-model-catalog-{kind}'")


# --- models -----------------------------------------------------------------

@pytest.mark.parametrize(
    "patch, needle",
    [
        ({"weights": "semi"}, "weights must be 'open' or 'closed'"),
        ({"status": "vapor"}, "status must be one of"),
        ({"params_active": 30e9}, "params_active must not exceed params_total"),
        ({"surfaces": ["carrier-pigeon"]}, "surfaces must be an array drawn from"),
        ({"params_total": -1}, "params_total must be a positive number or null"),
    ],
)
def test_bad_decision_model_fields_fail(pack, patch, needle):
    models = copy.deepcopy(MODELS)
    models["models"][0].update(patch)
    write(pack, "models.json", models)
    assert has(run(pack), needle)


def test_decision_status_values_and_nullable_fields_pass(pack):
    models = copy.deepcopy(MODELS)
    base = models["models"][0]
    models["models"] = [
        {**base, "id": "acme-decider" if i == 0 else f"m{i}", "family": "f", "status": s, "params_total": None, "params_active": None}
        for i, s in enumerate(["early_access", "limited_preview", "community_finetune", "ga"])
    ]
    write(pack, "models.json", models)
    assert run(pack) == []
    assert schema_errors("models-v1.schema.json", models) == []


# --- pricing ----------------------------------------------------------------

def price_doc(row: dict) -> dict:
    return {"id": "acme-pricing-2026-10", "schema_version": 1, "kind": "api_usd", "agent": "other",
            "retrieved_at": "2026-10-06", "source_urls": ["https://example.com/pricing"],
            "source_ids": [SRC], "models": {"acme-decider": row},
            "match": [{"pattern": "acme-decider", "model": "acme-decider"}]}


def test_per_token_row_with_free_output_passes(pack):
    row = {"billing_basis": "per_token", "fresh_input_per_m": 0.09, "cache_read_per_m": 0,
           "cache_write_per_m": 0, "output_per_m": 0, "output_free": True}
    write(pack, "pricing/acme.json", price_doc(row))
    assert run(pack) == []
    assert schema_errors("pricing-v1.schema.json", price_doc(row)) == []


def test_null_cache_rates_need_a_status(pack):
    row = {"billing_basis": "per_token", "fresh_input_per_m": 0.09, "cache_read_per_m": None,
           "cache_write_per_m": None, "output_per_m": 0, "output_free": True}
    write(pack, "pricing/x.json", price_doc(row))
    assert has(run(pack), "cache_read_per_m null requires cache_rates_status.cache_read")
    row["cache_rates_status"] = {"cache_read": "not_published", "cache_write": "vendor_states_none_charged"}
    write(pack, "pricing/x.json", price_doc(row))
    assert run(pack) == []
    assert schema_errors("pricing-v1.schema.json", price_doc(row)) == []
    row["cache_read_per_m"] = 0.09  # a number next to not_published is an inferred rate
    write(pack, "pricing/x.json", price_doc(row))
    assert has(run(pack), "cache_read_per_m must be null when cache_rates_status.cache_read is not_published")


def test_per_token_row_missing_a_rate_fails(pack):
    row = {"billing_basis": "per_token", "fresh_input_per_m": 0.09}
    write(pack, "pricing/acme.json", price_doc(row))
    assert has(run(pack), "per_token row missing output_per_m")
    assert schema_errors("pricing-v1.schema.json", price_doc(row))


def test_raw_units_with_sourced_conversion_pass(pack):
    row = {"billing_basis": "raw_units", "raw_units": {"unit": "neuron", "input_per_m": 1000.0},
           "usd_conversion": {"usd_per_unit": 0.000011, "source_id": SRC}, "fresh_input_per_m": 0.011,
           "cache_read_per_m": 0, "cache_write_per_m": 0, "output_per_m": 0}
    write(pack, "pricing/acme.json", price_doc(row))
    assert run(pack) == []


def test_usd_next_to_raw_units_needs_conversion(pack):
    row = {"billing_basis": "raw_units", "raw_units": {"unit": "neuron", "input_per_m": 1000.0},
           "fresh_input_per_m": 0.011}
    write(pack, "pricing/acme.json", price_doc(row))
    assert has(run(pack), "need usd_conversion")


def test_usd_conversion_source_must_resolve(pack):
    row = {"billing_basis": "raw_units", "raw_units": {"unit": "neuron"},
           "usd_conversion": {"usd_per_unit": 0.000011, "source_id": "ghost-source"}}
    write(pack, "pricing/acme.json", price_doc(row))
    assert has(run(pack), "source_id 'ghost-source' not in SOURCES.json")


def test_free_output_with_nonzero_rate_fails(pack):
    row = {"billing_basis": "per_token", "fresh_input_per_m": 1, "cache_read_per_m": 0,
           "cache_write_per_m": 0, "output_per_m": 2, "output_free": True}
    write(pack, "pricing/acme.json", price_doc(row))
    assert has(run(pack), "output_free=true requires output_per_m == 0")


def test_per_request_row_needs_fee(pack):
    write(pack, "pricing/acme.json", price_doc({"billing_basis": "per_request"}))
    assert has(run(pack), "billing_basis per_request requires per_request_usd")


# --- capabilities -----------------------------------------------------------

def cap_doc(surface: dict) -> dict:
    return {"id": "acme-capabilities-2026-10", "schema_version": 1, "kind": "capabilities",
            "retrieved_at": "2026-10-06", "source_urls": ["https://example.com/docs"],
            "source_ids": [SRC], "surfaces": [surface]}


def test_capability_surface_passes_and_baseline_model_resolves(pack):
    base_id, _ = baseline_model()
    surface = {"model": "acme-decider", "surface": "workers-ai", "question_types": ["choice", "noul", "score"],
               "max_options": 28, "abstain_supported": True, "returns_probabilities": True,
               "calibration_claimed": None, "rationale_supported": False,
               "context": {"state_tokens": 4096, "total_tokens": 8192},
               "questions_per_request": {"max": 8, "fan_out_supported": True, "packing_supported": None},
               "output_tokens_billed": False, "source_ids": [SRC]}
    write(pack, "capabilities/acme.json", cap_doc(surface))
    assert run(pack) == []
    assert schema_errors("capabilities-v1.schema.json", cap_doc(surface)) == []
    write(pack, "capabilities/baseline.json", cap_doc({"model": base_id, "surface": "openrouter"}))
    assert run(pack) == []


@pytest.mark.parametrize(
    "patch, needle",
    [
        ({"surface": "usb-stick"}, "surface must be one of"),
        ({"model": "ghost-model"}, "'ghost-model' not in models.json"),
        ({"abstain_supported": "yes"}, "abstain_supported must be boolean or null"),
        ({"max_options": 0}, "max_options must be a positive integer or null"),
        ({"caveat_ids": ["ghost-caveat"]}, "caveat_ids entry 'ghost-caveat' not in caveats.json"),
    ],
)
def test_bad_capability_surface_fails(pack, patch, needle):
    write(pack, "capabilities/acme.json", cap_doc({"model": "acme-decider", "surface": "workers-ai", **patch}))
    assert has(run(pack), needle)


# --- freshness --------------------------------------------------------------

def test_freshness_includes_decision_pricing_and_board_files(pack, monkeypatch):
    write(pack, "pricing/acme.json", price_doc({"billing_basis": "free"}))
    write(pack, "performance/board.json", perf_doc(acc_row(source_type="third_party_board")))
    write(pack, "performance/launch.json", perf_doc(acc_row()))
    monkeypatch.setattr(cf, "DECISION", pack)
    names = sorted(label for _, label in cf.decision_files())
    assert names == ["decision-model-catalog/performance/board.json", "decision-model-catalog/pricing/acme.json"]
