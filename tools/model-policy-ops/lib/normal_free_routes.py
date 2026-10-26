"""Normal policy-declared zero-price routes.

A normal route binds one exact provider, model and T3 account with a declared source price of
zero, the public-input floor, the T3 risks it carries (`known_risks`), and dated evidence. It carries
no experimental flag, no local expiry horizon and no transport grant. Its operations name an honest
evidence basis:

- `qualified_free`: a finite deterministic gate sample passed for exactly this scope, at the
  declared effort and proof class. It is not whole-task acceptance.
- `owner_authorized`: the owner asserted that this route is adequate for the declared scope.
  No gate sample or board qualifies it, so the operation must name its owner decision as the
  evidence basis and must not claim gate evidence.
- `unvalidated`: the route is available, but the evidence does not establish the operation's
  adequacy. The pair keeps the router's ordinary judgment-required basis.

This module shapes and checks declarations only. It selects nothing and reads no live source; `missing_evidence`
only checks that each named evidence file exists beside the pack.
"""
import re
from datetime import date

PROOF_CLASSES = ('oracle', 'judged', 'none')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
EVIDENCE_BASES = {
    'bounded_gate_evidence': 'finite deterministic gate evidence for a bounded scope',
    'classification_probes_only': 'a few constrained classification probes; not general adequacy',
    'availability_smoke_only': 'availability was observed; quality and cost are not established',
    'owner_decision': 'the owner asserted adequacy for this scope; no gate sample or board qualifies it',
}
QUALIFIED_BASIS = 'qualified_free'
UNVALIDATED_BASIS = 'unvalidated'
OWNER_BASIS = 'owner_authorized'
AUTH_STATES = {'none_observed_owner_smoke', 'required', 'unknown'}
AUTH_DOC_STATES = {'inconclusive', 'none_documented', 'required_documented'}
REQUIRED_PROHIBITED_INPUTS = {
    'private_repositories', 'PDPP_data', 'secrets', 'Jev_text', 'personal_data',
}
STATUS = re.compile(r'^free_as_of_(\d{4}-\d{2}-\d{2})$')
ROUTE_KEYS = {
    'provider', 'model', 'account', 'status', 'cost_usd', 'authentication', 'rate_limits',
    'effort_option', 'effort_values_source', 'data_policy', 'known_risks', 'evidence', 'operations',
}
REQUIRED_KNOWN_RISKS = {
    'shared_server_host_tools', 'injected_t3_thread_read_private_thread_access',
}
OPERATION_KEYS = {'id', 'basis', 'proof_classes', 'efforts', 'scope', 'evidence_ref', 'evidence_basis'}

EVIDENCE_FUTURE = 'normal_route_evidence_from_future_or_invalid'
OPERATION_MISSING = 'operation_not_declared_for_normal_route'
PROOF_MISSING = 'proof_class_not_declared_for_normal_route'
EFFORT_MISSING = 'effort_not_declared_for_normal_route'


def _iso_date(value):
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _date_or_none(value):
    return date.fromisoformat(value) if _iso_date(value) else None


def _status_date(route):
    match = STATUS.fullmatch(route.get('status')) if isinstance(route.get('status'), str) else None
    return date.fromisoformat(match.group(1)) if match and _iso_date(match.group(1)) else None


def is_current(route, evaluated_at):
    """True when the declaration's observation and every source access date are not in the future.

    A normal route has no expiry. Revocation is declared by removing or changing it, never by time.
    """
    observed = _status_date(route)
    evidence = route.get('evidence')
    if observed is None or not isinstance(evidence, list) or not evidence:
        return False
    accessed = [_date_or_none(item.get('accessed')) for item in evidence if isinstance(item, dict)]
    if len(accessed) != len(evidence) or any(value is None for value in accessed):
        return False
    return observed <= evaluated_at.date() and all(value <= evaluated_at.date() for value in accessed)


def declared(routes, provider, model):
    """The route that declares this exact provider and model, or None."""
    return next((route for route in routes or []
                 if route.get('provider') == provider and route.get('model') == model), None)


def declared_identity(routes, provider, model, account):
    """The route that declares this exact provider, model and account, or None. One route is one binding."""
    return next((route for route in routes or []
                 if (route.get('provider'), route.get('model'), route.get('account')) == (provider, model, account)), None)


def operation(route, op_id):
    """The declared operation entry for this op, or None."""
    return next((item for item in route.get('operations') or [] if item.get('id') == op_id), None)


def refusal(route, op_id, proof_class, effort, evaluated_at):
    """The refusal code for one declared route and request, or None.

    The caller's public classification and sensitive flag are separate caller assertions; see
    `policy_decisions`. Here only the declaration's own dates, operation, proof class and effort apply.
    """
    if not is_current(route, evaluated_at):
        return EVIDENCE_FUTURE
    item = operation(route, op_id)
    if item is None:
        return OPERATION_MISSING
    if proof_class not in item['proof_classes']:
        return PROOF_MISSING
    if effort not in item['efforts']:
        return EFFORT_MISSING
    return None


RETIRED_EXPERIMENTAL_KEY = 'experimental_free_routes'


def check_retired_experimental(policy):
    """The legacy experimental declaration is retired. Its root key must be absent so no flag or expiry can apply."""
    if RETIRED_EXPERIMENTAL_KEY in policy:
        return [f'{RETIRED_EXPERIMENTAL_KEY} is retired; declare the route under free_routes (no experimental flag or expiry)']
    return []


def check_normal_free_routes(routes):
    """Error strings for the optional root `free_routes` list. An absent list declares no normal route."""
    if routes is None:
        return []
    if not isinstance(routes, list):
        return ['free_routes must be a list']
    errors, seen = [], set()
    for index, route in enumerate(routes):
        label = f'free_routes[{index}]'
        if not isinstance(route, dict) or set(route) != ROUTE_KEYS:
            errors.append(f'{label} must be an object with exactly {", ".join(sorted(ROUTE_KEYS))}')
            continue
        identity = (route['provider'], route['model'], route['account'])
        if any(not isinstance(value, str) or not value for value in identity):
            errors.append(f'{label} needs exact provider, model and account strings')
        elif identity in seen:
            errors.append(f'{label} duplicates a provider/model/account tuple')
        else:
            seen.add(identity)
        if _status_date(route) is None:
            errors.append(f'{label}.status must be free_as_of_YYYY-MM-DD')
        if type(route['cost_usd']) not in (int, float) or route['cost_usd'] != 0:
            errors.append(f'{label}.cost_usd must be 0; a normal route declares a zero source price')
        errors.extend(_auth_errors(route['authentication'], f'{label}.authentication'))
        errors.extend(_rate_limit_errors(route['rate_limits'], f'{label}.rate_limits'))
        for key in ('effort_option', 'effort_values_source'):
            if not isinstance(route[key], str) or not route[key]:
                errors.append(f'{label}.{key} must be a non-empty string')
        errors.extend(_data_policy_errors(route['data_policy'], f'{label}.data_policy'))
        errors.extend(_known_risk_errors(route['known_risks'], f'{label}.known_risks'))
        errors.extend(_evidence_errors(route['evidence'], f'{label}.evidence'))
        errors.extend(_operation_errors(route['operations'], label))
    return errors


def _auth_errors(value, label):
    if (not isinstance(value, dict) or set(value) != {'state', 'official_docs'}
            or value['state'] not in AUTH_STATES or value['official_docs'] not in AUTH_DOC_STATES):
        return [f'{label} needs a supported observed state and official-docs state']
    return []


def _rate_limit_errors(value, label):
    if value == 'unknown' or (isinstance(value, dict) and value):
        return []
    return [f'{label} must be unknown or a non-empty sourced object']


def _data_policy_errors(value, label):
    keys = {'classification_required', 'sensitive_flag_forces_refusal', 'provider_zero_retention',
            'unpaid_account_processing', 'prohibited_inputs'}
    if not isinstance(value, dict) or set(value) != keys:
        return [f'{label} must record the public floor for both processing layers']
    errors = []
    if value['classification_required'] != 'public' or value['sensitive_flag_forces_refusal'] is not True:
        errors.append(f'{label} must require public inputs and refuse sensitive ones')
    for key in ('provider_zero_retention', 'unpaid_account_processing'):
        if not isinstance(value[key], str) or not value[key].strip():
            errors.append(f'{label}.{key} must be a non-empty string')
    prohibited = value['prohibited_inputs']
    if not isinstance(prohibited, list) or not REQUIRED_PROHIBITED_INPUTS.issubset(prohibited):
        errors.append(f'{label}.prohibited_inputs must include private repositories, PDPP data, secrets, Jev text and personal data')
    return errors


def _known_risk_errors(value, label):
    if not isinstance(value, list) or not REQUIRED_KNOWN_RISKS.issubset(value):
        return [f'{label} must record both shared-server host-tool and injected private-thread risks']
    return []


def _evidence_errors(value, label):
    if not isinstance(value, list) or not value:
        return [f'{label} must be a non-empty array']
    errors = []
    for index, item in enumerate(value):
        if (not isinstance(item, dict) or set(item) != {'url', 'accessed', 'supports'}
                or not isinstance(item['url'], str) or not item['url'].startswith('https://')
                or not _iso_date(item['accessed'])
                or not isinstance(item['supports'], list) or not item['supports']
                or any(not isinstance(claim, str) or not claim for claim in item['supports'])):
            errors.append(f'{label}[{index}] needs an HTTPS source, a valid access date and supported claims')
    return errors


def _operation_errors(value, route_label):
    if not isinstance(value, list):
        return [f'{route_label}.operations must be a list; an empty list revokes forward availability']
    errors, seen = [], set()
    for index, item in enumerate(value):
        label = f'{route_label}.operations[{index}]'
        if not isinstance(item, dict) or set(item) != OPERATION_KEYS:
            errors.append(f'{label} needs exactly {", ".join(sorted(OPERATION_KEYS))}')
            continue
        if not isinstance(item['id'], str) or not item['id']:
            errors.append(f'{label}.id must be a non-empty op id')
        elif item['id'] in seen:
            errors.append(f'{label} duplicates {item["id"]}')
        else:
            seen.add(item['id'])
        basis = item['basis']
        if basis not in (QUALIFIED_BASIS, UNVALIDATED_BASIS, OWNER_BASIS):
            errors.append(f'{label}.basis must be {QUALIFIED_BASIS}, {UNVALIDATED_BASIS} or {OWNER_BASIS}')
            continue
        proofs, efforts = item['proof_classes'], item['efforts']
        if (not isinstance(proofs, list) or not proofs or set(proofs) - set(PROOF_CLASSES)
                or len(set(proofs)) != len(proofs)):
            errors.append(f'{label}.proof_classes must be a non-empty list of distinct {", ".join(PROOF_CLASSES)}')
        if not isinstance(efforts, list) or not efforts or set(efforts) - set(EFFORTS):
            errors.append(f'{label}.efforts must declare only known efforts')
        if basis == QUALIFIED_BASIS:
            if proofs != ['oracle']:
                errors.append(f'{label}: {QUALIFIED_BASIS} requires exactly oracle proof; gate evidence is not judged evidence')
            if item['evidence_basis'] != 'bounded_gate_evidence':
                errors.append(f'{label}: {QUALIFIED_BASIS} requires evidence_basis bounded_gate_evidence')
        elif basis == OWNER_BASIS:
            if item['evidence_basis'] != 'owner_decision':
                errors.append(f'{label}: {OWNER_BASIS} requires evidence_basis owner_decision; it must not claim gate evidence')
            if proofs == ['none']:
                errors.append(f'{label}: {OWNER_BASIS} needs an oracle or judged proof class')
        else:
            if proofs != ['judged']:
                errors.append(f'{label}: {UNVALIDATED_BASIS} requires exactly judged proof')
            if item['evidence_basis'] == 'bounded_gate_evidence':
                errors.append(f'{label}: {UNVALIDATED_BASIS} must not claim gate evidence')
        if item['evidence_basis'] not in EVIDENCE_BASES:
            errors.append(f'{label}.evidence_basis must be one of {", ".join(sorted(EVIDENCE_BASES))}')
        if not isinstance(item['scope'], str) or not item['scope'].strip():
            errors.append(f'{label}.scope must be a non-empty string')
        ref = item['evidence_ref']
        if (not isinstance(ref, str) or not ref.startswith('evidence/') or '..' in ref
                or not ref.endswith('.json')):
            errors.append(f'{label}.evidence_ref must name a JSON file under evidence/')
    return errors


def missing_evidence(routes, base):
    """Error strings for each operation `evidence_ref` that does not name a file beside the policy (`base`)."""
    if not isinstance(routes, list):
        return []  # a non-list is already reported by check_normal_free_routes
    errors = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        for item in route.get('operations') or []:
            ref = item.get('evidence_ref') if isinstance(item, dict) else None
            if isinstance(ref, str) and not (base / ref).is_file():
                errors.append(f'free_routes evidence_ref {ref} is missing')
    return errors
