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
