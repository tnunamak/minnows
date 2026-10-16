"""Durable contract tests for experimental free-route policy metadata."""
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "model-policy-ops" / "lib"))
from experimental_routes import check_experimental_free_routes  # noqa: E402


def load_validator():
    path = REPO / "scripts" / "validate_data_pack.py"
    spec = importlib.util.spec_from_file_location("experimental_route_data_pack_validator", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_validator()
BASE_ROUTE = json.loads((REPO / "data/model-choice-policy/operating-points.json").read_text())["experimental_free_routes"][0]


def changed_route(change):
    route = copy.deepcopy(BASE_ROUTE)
    change(route)
    return route


@pytest.mark.parametrize("change", [
    lambda route: route.update(status="temporary_free_as_of_2026-02-30"),
    lambda route: route.pop("authentication"),
    lambda route: route["authentication"].update(state="invented"),
    lambda route: route.update(rate_limits=[]),
    lambda route: route["data_policy"].update(classification_required="unknown"),
    lambda route: route["data_policy"].update(sensitive_flag_forces_refusal=False),
    lambda route: route["data_policy"]["prohibited_inputs"].remove("PDPP_data"),
    lambda route: route["data_policy"].pop("provider_zero_retention"),
    lambda route: route.pop("qualified_operations"),
    lambda route: route.update(qualified_operations=["implement.standard"]),
    lambda route: route["t3_transport"].update(enabled=False),
    lambda route: route["t3_transport"].update(authorization="invented"),
    lambda route: route["t3_transport"].update(scope="all_opencode_models"),
    lambda route: route["t3_transport"]["known_risks"].remove("injected_t3_thread_read_private_thread_access"),
    lambda route: route.update(evidence=[]),
    lambda route: route["evidence"][0].update(accessed="2026-02-30"),
    lambda route: route.pop("expires_at_utc"),
    lambda route: route.update(expires_at_utc="2026-10-22T00:00:00+01:00"),
    lambda route: route.pop("expiry_basis"),
])
def test_invalid_route_evidence_and_privacy_metadata_is_rejected(change):
    errors = check_experimental_free_routes([changed_route(change)])
    assert errors


def test_source_backed_refresh_can_change_evidence_without_validator_edits():
    def refresh(route):
        route["status"] = "temporary_free_as_of_2026-10-22"
        route["authentication"] = {"state": "required", "official_docs": "required_documented"}
        route["rate_limits"] = {"requests_per_minute": 60, "daily_requests": "not_published"}
        route["expires_at_utc"] = "2026-10-29T00:00:00Z"
        route["expiry_basis"] = "local_policy_horizon_7_days_after_source_review; not_a_vendor_end_date"
        for source in route["evidence"]:
            source["accessed"] = "2026-10-22"

    assert check_experimental_free_routes([changed_route(refresh)]) == []


def test_both_pack_checkers_use_the_same_experimental_route_contract(tmp_path):
    pack_dir = tmp_path / "model-choice-policy"
    shutil.copytree(REPO / "data/model-choice-policy", pack_dir)
    policy_path = pack_dir / "operating-points.json"
    policy = json.loads(policy_path.read_text())
    policy["experimental_free_routes"][0]["data_policy"]["classification_required"] = "sensitive"
    policy_path.write_text(json.dumps(policy))

    shared_errors = check_experimental_free_routes(policy["experimental_free_routes"])
    data_pack_errors = VALIDATOR.Errors()
    VALIDATOR.validate_policy_pack(pack_dir, data_pack_errors)
    assert all(any(message in item for item in data_pack_errors.items) for message in shared_errors)

    result = subprocess.run(
        [str(REPO / "tools/model-policy-ops/model-policy-ops"), "--policy", str(policy_path), "check"],
        cwd=REPO, text=True, capture_output=True,
    )
    assert result.returncode == 1
    assert all(message in result.stderr for message in shared_errors)


@pytest.mark.parametrize("change", [
    lambda items: items.append(copy.deepcopy(items[0])),
    lambda items: items[0].update(id="review.audit"),
    lambda items: items[0].update(id=["implement.oracle-bounded"]),
    lambda items: items[0].update(proof_classes=["judged"]),
    lambda items: items[0].update(proof_classes=["oracle", "judged"]),
    lambda items: items[0].update(efforts=["low"]),
    lambda items: items[0].update(efforts=["high", "max"]),
    lambda items: items[0].update(scope="open_ended_research"),
    lambda items: items[0].update(scope="known_public_source_lookup_structured_fact_oracle"),
    lambda items: items[0].update(evidence_ref="unreviewed.json"),
    lambda items: items[0].pop("evidence_ref"),
    lambda items: items[0].update(extra="arbitrary"),
])
def test_qualification_contract_agrees_in_both_cli_and_pack_validator(tmp_path, change):
    pack_dir = tmp_path / "model-choice-policy"
    shutil.copytree(REPO / "data/model-choice-policy", pack_dir)
    policy_path = pack_dir / "operating-points.json"
    policy = json.loads(policy_path.read_text())
    change(policy["experimental_free_routes"][0]["qualified_operations"])
    policy_path.write_text(json.dumps(policy))
    shared = check_experimental_free_routes(policy["experimental_free_routes"])
    assert shared
    errors = VALIDATOR.Errors()
    VALIDATOR.validate_policy_pack(pack_dir, errors)
    assert all(any(message in item for item in errors.items) for message in shared)
    result = subprocess.run([str(REPO / "tools/model-policy-ops/model-policy-ops"), "check",
                             "--policy", str(policy_path), "--no-user-policy"],
                            text=True, capture_output=True, cwd=REPO)
    assert result.returncode == 1
    assert all(message in result.stderr for message in shared)


def test_pack_admits_exact_reviewed_scopes_without_changing_primary_arms():
    route = BASE_ROUTE
    assert {item['id'] for item in route['qualified_operations']} == {'implement.oracle-bounded', 'docs.lookup'}
    for item in route['qualified_operations']:
        assert item['proof_classes'] == ['oracle'] and item['efforts'] == ['high']
        evidence = REPO / 'data/model-choice-policy' / item['evidence_ref']
        assert evidence.is_file()
    policy = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
    baseline = json.loads(subprocess.check_output(['git', 'show', 'c2414ded:data/model-choice-policy/operating-points.json'], cwd=REPO))
    # This guard protects every pre-existing arm; independently added ops have their own tests.
    old_ops = {op['id']: op for op in baseline['operating_points']}
    assert {op['id']: op for op in policy['operating_points'] if op['id'] in old_ops} == old_ops
    assert policy['catalog_ref'] == baseline['catalog_ref']


def test_empty_proof_list_revokes_eligibility_without_admitting_another_proof():
    route = copy.deepcopy(BASE_ROUTE)
    route['qualified_operations'][0]['proof_classes'] = []
    assert check_experimental_free_routes([route]) == []
