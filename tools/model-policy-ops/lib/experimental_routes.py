"""Shared validation for policy-declared experimental free routes.

The owner-authorized trial of 2026-10-15 makes the free route the preferred normal candidate for
checked cheap-tier work, subject to structured input provenance. The reviewed set covers the bounded
checked operations (implementation with a full acceptance standard, documentation lookup, research
fanout, bounded status reports and bounded test design). The 2026-10-15 matched benchmark stays
immutable and is not evidence of wider qualification.
"""
from datetime import date, datetime
import re

import input_provenance


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
# Catalog-supported effort surfaces, in none other than the live T3 catalog's low/high/max.
ALLOWED_EFFORTS = ("low", "high", "max")
# Absent or `none` checking is never eligible; a trial step must be checked.
ALLOWED_PROOF_CLASSES = ("oracle", "judged")
TRIAL_PURPOSES = ("execution", "review", "research", "exploration", "test-design")
TRIAL_AUTHORIZATION = "owner_authorized_trial_2026-10-15"
TRIAL_SELECTION_MODE = "preferred_normal_candidate"
# The reviewed public scope of each qualified operation. An operation may carry several entries when
# its purposes differ, and each entry needs its own reviewed scope. The trial does not add a broader
# scope than these, and it never qualifies a broad, unbounded or provider-restricted operation.
QUALIFIED_SCOPES = {
    "implement.oracle-bounded": ("small_public_source_change_full_deterministic_oracle",
                                 "bounded_public_test_design_checked_by_independent_checker"),
    "docs.lookup": ("known_public_source_lookup_structured_fact_oracle",),
    "fanout.dollar-tight": ("bounded_public_research_checked_by_ci_or_independent_checker",),
    "recover.report": ("bounded_public_status_report_checked_by_independent_checker",),
}
# Judged proof is checked by an independent checker, never by the maker that produced the work.
JUDGED_PROOF = "judged"
_ENTRY_KEYS = {"id", "proof_classes", "efforts", "scope", "evidence_ref"}
_SELECTION_KEYS = {"mode", "authorization", "benchmark", "retrospective", "authority_note", "threshold"}
_TRANSPORT_SCOPE = "qualified_operations_or_fanout.explore_or_resolve_no_op_exact_overrides_public_only"
_EXPLORATORY_SCOPE = "fanout.explore_or_resolve_no_op_exact_overrides_public_only"


def eligible_operation(route, op, purpose=None):
    """The qualification entry for (op, purpose), or None. A stated purpose wins over the general entry.

    One operation can be qualified twice: an unrestricted entry and a purpose-limited one. A caller
    that states that purpose gets the purpose entry (its own scope and proof classes); every other
    caller gets the unrestricted entry. An absent purpose never meets a stated restriction, so the
    purpose entry is unreachable without one.
    """
    entries = [item for item in route.get("qualified_operations") or []
               if isinstance(item, dict) and item.get("id") == op]
    for item in entries:
        if item.get("purposes") is not None and purpose in item["purposes"]:
            return item
    for item in entries:
        if item.get("purposes") is None:
            return item
    return None


def operation_refusal(route, op, *, routed, no_op, proof_class, effort, purpose=None):
    """Check declared qualification, keeping the separately authorized exploration paths."""
    if (routed and op == "fanout.explore") or (not routed and no_op):
        return None
    if check_qualified_operations(route.get("qualified_operations"), "qualified_operations"):
        return "operation_qualification_invalid"
    qualified = eligible_operation(route, op, purpose)
    if qualified is None:
        return "operation_not_qualified"
    if proof_class not in qualified["proof_classes"]:
        return "qualified_operation_requires_allowed_proof"
    if effort not in qualified["efforts"]:
        return "qualified_operation_requires_allowed_effort"
    return None


def check_qualified_operations(qualified, label):
    """Accept only the reviewed scopes; an empty list revokes qualification. An empty proof list revokes proof.

    One operation may appear more than once when its purposes differ: each entry then needs its own
    reviewed scope, and at most one entry may be unrestricted, so a purpose-limited entry can never
    shadow the normal one.
    """
    if not isinstance(qualified, list):
        return [f"{label} must be a list"]
    errors, seen, unrestricted = [], set(), set()
    for item in qualified:
        if not isinstance(item, dict) or set(item) - {"purposes"} != _ENTRY_KEYS:
            errors.append(f"{label} entries need id, proof_classes, efforts, scope, evidence_ref and optional purposes only")
            continue
        op = item["id"]
        if not isinstance(op, str) or op not in QUALIFIED_SCOPES:
            errors.append(f"{label} has an unknown or unqualified operation")
            continue
        proof = {(op, item["scope"])}
        if proof & seen:
            errors.append(f"{label} duplicates {op} with scope {item['scope']}")
        seen |= proof
        if "purposes" not in item:
            if op in unrestricted:
                errors.append(f"{label} has more than one unrestricted entry for {op}")
            unrestricted.add(op)
        proofs = item["proof_classes"]
        bad_proofs = proofs != [] and not (isinstance(proofs, list) and proofs and set(proofs) <= set(ALLOWED_PROOF_CLASSES))
        efforts = item["efforts"]
        bad_efforts = not (isinstance(efforts, list) and efforts and set(efforts) <= set(ALLOWED_EFFORTS))
        bad_purposes = "purposes" in item and not (
            isinstance(item["purposes"], list) and item["purposes"] and set(item["purposes"]) <= set(TRIAL_PURPOSES))
        if (bad_proofs or bad_efforts or bad_purposes
                or item["scope"] not in QUALIFIED_SCOPES[op]
                or item["evidence_ref"] != QUALIFICATION_EVIDENCE):
            errors.append(f"{label}.{op} requires a non-empty subset of oracle|judged proofs, low|high|max efforts, "
                          "one of its reviewed public scopes and qualification evidence")
    return errors


def check_selection(selection, label):
    """Validate the trial's authority record: owner-authorized, retrospective, immutable benchmark."""
    if not isinstance(selection, dict) or set(selection) != _SELECTION_KEYS:
        return [f"{label} needs exactly mode, authorization, benchmark, retrospective, authority_note and threshold"]
    errors = []
    if selection["mode"] != TRIAL_SELECTION_MODE:
        errors.append(f"{label}.mode must be {TRIAL_SELECTION_MODE}")
    if selection["authorization"] != TRIAL_AUTHORIZATION:
        errors.append(f"{label}.authorization must be {TRIAL_AUTHORIZATION}")
    if selection["benchmark"] != QUALIFICATION_EVIDENCE:
        errors.append(f"{label}.benchmark must name the immutable qualification evidence")
    if selection["retrospective"] is not True:
        errors.append(f"{label}.retrospective must be true: the expansion is a trial, not benchmark qualification")
    if not isinstance(selection["authority_note"], str) or not selection["authority_note"].strip():
        errors.append(f"{label}.authority_note must state the expansion's authority honestly")
    if type(selection["threshold"]) is not int or selection["threshold"] < 1:
        errors.append(f"{label}.threshold must be a positive integer")
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
        if qualified:
            errors.extend(check_selection(route.get("selection"), f"{label}.selection"))
            if not isinstance(route.get("input_policy"), dict):
                errors.append(f"{label}.input_policy must declare the trial's structured input policy")
            else:
                errors.extend(f"{label}.{message}" for message in input_provenance.check_policy(route["input_policy"]))
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


def trial_selection(routes, *, op_id, purpose, proof_class, effort_override, public, sensitive, manifest,
                    evaluated_at, explicit_target=False):
    """The preferred free-route candidate for this call, and the reason when there is none.

    The trial is the normal candidate for a qualified operation, not an opt-in flag. It is eligible
    only when every gate holds: public classification, no sensitive flag, a provider/model/account
    choice the caller did not pin to another arm, an allowed input manifest, a qualified operation
    for the stated purpose, a checked proof class, an effort the entry and the live catalog allow,
    and current unexpired evidence with enabled transport. Each gate produces one reason, so a
    fallback to a paid arm is always explained rather than silent.

    Returns `(selection, status)`. `selection` is `{route, entry, effort, provenance}` or None.
    `status` is `{state, reason, route}` where `state` is `selected`, `not_selected` or `not_qualified`.
    """
    def status(state, reason, route=None):
        return None, {"state": state, "reason": reason, "route": (route or {}).get("model")}

    declared = [route for route in routes
                if any(isinstance(item, dict) and item.get("id") == op_id
                       for item in route.get("qualified_operations") or [])]
    if not declared:
        # An ordinary operation never carries a trial block: the trial only explains itself where it could apply.
        return status("not_applicable", "operation_not_declared")
    if sensitive:
        return status("not_selected", "sensitive_inputs", declared[0])
    if not public:
        return status("not_selected", "public_classification_required", declared[0])
    if explicit_target:
        return status("not_selected", "explicit_provider_model_account_override", declared[0])
    checked = [route for route in declared if eligible_operation(route, op_id, purpose) is not None]
    if not checked:
        return status("not_qualified", "purpose_not_qualified", declared[0])
    route = checked[0]
    entry = eligible_operation(route, op_id, purpose)
    provenance = input_provenance.assess(manifest, route.get("input_policy"))
    if provenance["state"] != "allowed":
        if provenance["state"] == "absent":
            return status("not_selected", "input_provenance_required", route)
        return status("not_selected", f"input_provenance_refused_{provenance['refusal']}", route)
    if proof_class not in entry["proof_classes"]:
        return status("not_qualified", "proof_class_not_eligible", route)
    effort = effort_override or entry["efforts"][0]
    if effort not in entry["efforts"]:
        return status("not_qualified", "effort_not_allowed", route)
    if not has_current_evidence(route, evaluated_at):
        return status("not_selected", "evidence_not_current", route)
    expires_at = parse_utc_timestamp(route.get("expires_at_utc"))
    if expires_at is None or evaluated_at >= expires_at:
        return status("not_selected", "evidence_expired", route)
    if (route.get("t3_transport") or {}).get("enabled") is not True:
        return status("not_selected", "transport_disabled", route)
    selection = {"route": route, "entry": entry, "effort": effort, "provenance": provenance,
                 "independent_check_required": proof_class == JUDGED_PROOF}
    return selection, {"state": "selected", "reason": None, "route": route["model"]}
