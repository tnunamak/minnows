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
    lambda items: items[0].update(proof_classes=["none"]),
    lambda items: items[0].update(proof_classes=["maker_judged"]),
    lambda items: items[0].update(efforts=["medium"]),
    lambda items: items[0].update(efforts=[]),
    lambda items: items[0].update(purposes=["invalid-purpose"]),
    lambda items: items[0].update(purposes=[]),
    lambda items: items[0].update(scope="open_ended_research"),
    lambda items: items[0].update(scope="known_public_source_lookup_structured_fact_oracle"),
    lambda items: items[0].update(evidence_ref="unreviewed.json"),
    lambda items: items[0].pop("evidence_ref"),
    lambda items: items[0].update(extra="arbitrary"),
    lambda items: items.append({**copy.deepcopy(items[0]), "scope": "bounded_public_test_design_checked_by_independent_checker",
                                "proof_classes": ["judged"]}),
    lambda items: items.append({**copy.deepcopy(items[1]), "scope": items[0]["scope"]}),
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


def test_pack_admits_the_expanded_trial_scopes_without_changing_primary_arms():
    route = BASE_ROUTE
    items = route['qualified_operations']
    assert {item['id'] for item in items} == {
        'implement.oracle-bounded', 'docs.lookup', 'fanout.dollar-tight', 'recover.report'}
    for item in items:
        assert item['efforts'] == ['high', 'low', 'max']
        assert item['proof_classes'] and set(item['proof_classes']) <= {'oracle', 'judged'}
        assert item['evidence_ref'] == 'evidence/deepseek-public-eval-2026-10-15.json'
        evidence = REPO / 'data/model-choice-policy' / item['evidence_ref']
        assert evidence.is_file()
    # The source-change scope keeps the deterministic oracle; bounded test design is its own entry,
    # limited to one purpose and checked by an independent checker, never the maker.
    source_change = [item for item in items if item['id'] == 'implement.oracle-bounded']
    assert len(source_change) == 2 and sum('purposes' not in item for item in source_change) == 1
    assert [item['scope'] for item in source_change] == [
        'small_public_source_change_full_deterministic_oracle',
        'bounded_public_test_design_checked_by_independent_checker']
    assert [item['proof_classes'] for item in source_change] == [['oracle'], ['judged']]
    assert next(item for item in source_change if 'purposes' in item)['purposes'] == ['test-design']
    assert next(i for i in items if i['id'] == 'fanout.dollar-tight')['purposes'] == ['research']
    assert next(i for i in items if i['id'] == 'recover.report')['scope'] == \
        'bounded_public_status_report_checked_by_independent_checker'
    assert sum('purposes' not in item for item in items) == 3, 'one unrestricted entry per operation'
    assert route['selection'] == {
        'mode': 'preferred_normal_candidate', 'authorization': 'owner_authorized_trial_2026-10-15',
        'benchmark': 'evidence/deepseek-public-eval-2026-10-15.json', 'retrospective': True,
        'authority_note': route['selection']['authority_note'], 'threshold': 50}
    assert 'owner authorized' in route['selection']['authority_note'].lower()
    assert 'does not qualify these operations' in route['selection']['authority_note']
    policy = json.loads((REPO / 'data/model-choice-policy/operating-points.json').read_text())
    baseline = json.loads(subprocess.check_output(['git', 'show', 'c2414ded:data/model-choice-policy/operating-points.json'], cwd=REPO))
    assert policy['operating_points'] == baseline['operating_points']
    assert policy['catalog_ref'] == baseline['catalog_ref']


@pytest.mark.parametrize("change", [
    lambda route: route.pop("selection"),
    lambda route: route["selection"].update(mode="automatic"),
    lambda route: route["selection"].update(authorization="invented"),
    lambda route: route["selection"].update(benchmark="evidence/other.json"),
    lambda route: route["selection"].update(retrospective=False),
    lambda route: route["selection"].update(authority_note=""),
    lambda route: route["selection"].update(threshold=0),
    lambda route: route["selection"].update(threshold="50"),
    lambda route: route["selection"].update(extra="arbitrary"),
    lambda route: route.pop("input_policy"),
    lambda route: route["input_policy"].update(allowed_github_owners=[]),
    lambda route: route["input_policy"].update(allowed_github_owners=["Tnunamak"]),
    lambda route: route["input_policy"].update(other_owners="trust-the-caller"),
    lambda route: route["input_policy"].update(credential_paths_denied=False),
    lambda route: route["input_policy"].update(prohibited_content=["secrets"]),
    lambda route: route["input_policy"].update(company_private="allowed"),
    lambda route: route["input_policy"].update(excluded_github_owners=["tnunamak"]),
    lambda route: route["input_policy"].update(excluded_github_owners=[]),
])
def test_selection_and_input_policy_are_validated_in_both_checkers(tmp_path, change):
    pack_dir = tmp_path / "model-choice-policy"
    shutil.copytree(REPO / "data/model-choice-policy", pack_dir)
    policy_path = pack_dir / "operating-points.json"
    policy = json.loads(policy_path.read_text())
    change(policy["experimental_free_routes"][0])
    policy_path.write_text(json.dumps(policy))
    shared = check_experimental_free_routes(policy["experimental_free_routes"])
    assert shared
    errors = VALIDATOR.Errors()
    VALIDATOR.validate_policy_pack(pack_dir, errors)
    assert all(any(message in item for item in errors.items) for message in shared)


def test_empty_proof_list_revokes_eligibility_without_admitting_another_proof():
    route = copy.deepcopy(BASE_ROUTE)
    route['qualified_operations'][0]['proof_classes'] = []
    assert check_experimental_free_routes([route]) == []
