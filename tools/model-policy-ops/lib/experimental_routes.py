"""Shared validation for policy-declared experimental free routes."""
from datetime import date, datetime
import re


_TEMPORARY_STATUS = re.compile(r"^temporary_free_as_of_(\d{4}-\d{2}-\d{2})$")
_AUTH_STATES = {"none_observed_owner_smoke", "required", "unknown"}
_AUTH_DOC_STATES = {"inconclusive", "none_documented", "required_documented"}
_REQUIRED_PROHIBITED_INPUTS = {
    "private_repositories", "PDPP_data", "secrets", "Jev_text", "personal_data",
}
_REQUIRED_T3_RISKS = {
    "shared_server_host_tools", "injected_t3_thread_read_private_thread_access",
}
QUALIFICATION_EVIDENCE = "evidence/deepseek-public-eval-2026-10-15.json"
QUALIFIED_SCOPES = {
    "implement.oracle-bounded": "small_public_source_change_full_deterministic_oracle",
    "docs.lookup": "known_public_source_lookup_structured_fact_oracle",
}
_TRANSPORT_SCOPE = "qualified_operations_or_fanout.explore_or_resolve_no_op_exact_overrides_public_only"
_EXPLORATORY_SCOPE = "fanout.explore_or_resolve_no_op_exact_overrides_public_only"


def operation_refusal(route, op, *, routed, no_op, proof_class, effort):
    """Check declared qualification, keeping the separately authorized exploration paths."""
    if (routed and op == "fanout.explore") or (not routed and no_op):
        return None
    if check_qualified_operations(route.get("qualified_operations"), "qualified_operations"):
        return "operation_qualification_invalid"
    qualified = next((item for item in route.get("qualified_operations", []) if item["id"] == op), None)
    if qualified is None:
        return "operation_not_qualified"
    if proof_class not in qualified["proof_classes"]:
        return "qualified_operation_requires_oracle_proof"
    if effort not in qualified["efforts"]:
        return "qualified_operation_requires_high_effort"
    return None


def currency_refusal(route, evaluated_at):
    """Refusal code when the route's own evidence, expiry or transport authorization no longer holds, else None."""
    expires_at = parse_utc_timestamp(route.get('expires_at_utc'))
    if expires_at is None:
        return 'verification_expiry_missing_or_invalid'
    if not has_current_evidence(route, evaluated_at):
        return 'verification_evidence_from_future_or_invalid'
    if evaluated_at >= expires_at:
        return 'verification_expired'
    if (route.get('t3_transport') or {}).get('enabled') is not True:
        return 't3_transport_disabled'
    return None


def check_qualified_operations(qualified, label):
    """Accept only the two reviewed scopes; an empty list revokes qualification."""
    if not isinstance(qualified, list):
        return [f"{label} must be a list"]
    errors, seen = [], set()
    for item in qualified:
        if not isinstance(item, dict) or set(item) != {"id", "proof_classes", "efforts", "scope", "evidence_ref"}:
            errors.append(f"{label} entries need id, proof_classes, efforts, scope and evidence_ref only")
            continue
        op = item["id"]
        if not isinstance(op, str) or op not in QUALIFIED_SCOPES:
            errors.append(f"{label} has an unknown or unqualified operation")
            continue
        if op in seen:
            errors.append(f"{label} duplicates {op}")
        seen.add(op)
        if (item["proof_classes"] not in ([], ["oracle"]) or item["efforts"] != ["high"]
                or item["scope"] != QUALIFIED_SCOPES[op]
                or item["evidence_ref"] != QUALIFICATION_EVIDENCE):
            errors.append(f"{label}.{op} requires oracle proof, high effort, its reviewed public scope and qualification evidence")
    return errors


def _iso_date(value):
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def parse_utc_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        return None
    return parsed


def has_current_evidence(route, evaluated_at):
    """Return false for a validly shaped status/source date that is in the future."""
    status = route.get("status")
    status_match = _TEMPORARY_STATUS.fullmatch(status) if isinstance(status, str) else None
    if not status_match or not _iso_date(status_match.group(1)):
        return False
    evidence = route.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    dates = [item.get("accessed") for item in evidence if isinstance(item, dict)]
    if len(dates) != len(evidence) or any(not _iso_date(accessed) for accessed in dates):
        return False
    try:
        status_date = date.fromisoformat(status_match.group(1))
        accessed_dates = [date.fromisoformat(accessed) for accessed in dates]
    except ValueError:
        return False
    return status_date <= evaluated_at.date() and all(accessed <= evaluated_at.date() for accessed in accessed_dates)


def check_experimental_free_routes(routes):
    """Validate durable evidence shape while enforcing the fixed public-data floor."""
    if routes is None:
        return []
    if not isinstance(routes, list):
        return ["experimental_free_routes must be a list"]

    errors = []
    seen = set()
    for index, route in enumerate(routes):
        label = f"experimental_free_routes[{index}]"
        if not isinstance(route, dict):
            errors.append(f"{label} must be an object")
            continue

        identity = (route.get("provider"), route.get("model"), route.get("account"))
        if any(not isinstance(value, str) or not value for value in identity):
            errors.append(f"{label} needs exact provider, model and account strings")
        elif identity in seen:
            errors.append(f"{label} duplicates a provider/model/account tuple")
        else:
            seen.add(identity)

        status = route.get("status")
        status_match = _TEMPORARY_STATUS.fullmatch(status) if isinstance(status, str) else None
        if not status_match or not _iso_date(status_match.group(1)):
            errors.append(f"{label}.status must be temporary_free_as_of_YYYY-MM-DD")
        if type(route.get("cost_usd")) not in (int, float) or route["cost_usd"] != 0:
            errors.append(f"{label}.cost_usd must be 0")

        qualified = route.get("qualified_operations")
        errors.extend(check_qualified_operations(qualified, f"{label}.qualified_operations"))
        transport = route.get("t3_transport")
        risks = transport.get("known_risks") if isinstance(transport, dict) else None
        allowed_scopes = [_TRANSPORT_SCOPE] if qualified else [_TRANSPORT_SCOPE, _EXPLORATORY_SCOPE]
        if (not isinstance(transport, dict) or transport.get("enabled") is not True
                or transport.get("authorization") != "owner_public_experimental_2026-10-15"
                or transport.get("scope") not in allowed_scopes
                or not isinstance(risks, list)
                or any(not isinstance(risk, str) for risk in risks)
                or not _REQUIRED_T3_RISKS.issubset(risks)):
            errors.append(f"{label}.t3_transport must record the owner's public-only experimental authorization and both known isolation risks")

        authentication = route.get("authentication")
        if (not isinstance(authentication, dict)
                or not isinstance(authentication.get("state"), str)
                or authentication.get("state") not in _AUTH_STATES
                or not isinstance(authentication.get("official_docs"), str)
                or authentication.get("official_docs") not in _AUTH_DOC_STATES):
            errors.append(f"{label}.authentication needs supported observed and official-docs states")

        rate_limits = route.get("rate_limits")
        if rate_limits != "unknown" and (not isinstance(rate_limits, dict) or not rate_limits):
            errors.append(f"{label}.rate_limits must be unknown or a non-empty sourced object")

        if (not isinstance(route.get("effort_option"), str) or not route["effort_option"]
                or not isinstance(route.get("effort_values_source"), str) or not route["effort_values_source"]):
            errors.append(f"{label} needs an effort option and its evidence source")

        expires_at = parse_utc_timestamp(route.get("expires_at_utc"))
        if expires_at is None:
            errors.append(f"{label}.expires_at_utc must be a timezone-aware UTC timestamp")
        expiry_basis = route.get("expiry_basis")
        if not isinstance(expiry_basis, str) or "local_policy_horizon" not in expiry_basis:
            errors.append(f"{label}.expiry_basis must state the local_policy_horizon")

        privacy = route.get("data_policy")
        if (not isinstance(privacy, dict)
                or privacy.get("classification_required") != "public"
                or privacy.get("sensitive_flag_forces_refusal") is not True
                or not isinstance(privacy.get("provider_zero_retention"), str)
                or not privacy["provider_zero_retention"].strip()
                or not isinstance(privacy.get("opencode_unpaid_processing"), str)
                or not privacy["opencode_unpaid_processing"].strip()):
            errors.append(f"{label}.data_policy must require public inputs and record both processing layers")
        prohibited = privacy.get("prohibited_inputs") if isinstance(privacy, dict) else None
        if not isinstance(prohibited, list) or not _REQUIRED_PROHIBITED_INPUTS.issubset(prohibited):
            errors.append(f"{label}.data_policy.prohibited_inputs must include private repos, PDPP, secrets, Jev text, and personal data")

        evidence = route.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{label}.evidence must be a non-empty array")
            continue
        for evidence_index, item in enumerate(evidence):
            evidence_label = f"{label}.evidence[{evidence_index}]"
            if (not isinstance(item, dict)
                    or not isinstance(item.get("url"), str)
                    or not item["url"].startswith("https://")
                    or not _iso_date(item.get("accessed"))
                    or not isinstance(item.get("supports"), list)
                    or not item["supports"]
                    or any(not isinstance(claim, str) or not claim for claim in item["supports"])):
                errors.append(f"{evidence_label} needs an HTTPS source, valid access date, and supported claims")
    return errors
