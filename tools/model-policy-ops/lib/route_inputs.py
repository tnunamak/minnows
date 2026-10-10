"""Readers for the local inputs of `route` and `calibrate`. Each one degrades to `unknown`, never a crash."""
import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from decision_receipts import find_receipt
from delegation_audit import effort
from runtime_sources import digest, driver_family, provider_instance, providers

PREFIX = 'orchestration_v2_projection_'
BILLING = ('subscription', 'metered')


def default_accounts_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'model-policy/accounts.json'


def read_accounts(path):
    """Account map: {instance: {quota_provider, quota_source, billing}}. A missing file maps nothing."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}, {'state': 'missing', 'sha256': None}
    except ValueError as error:
        raise ValueError('accounts file is not valid JSON') from error
    rows = value.get('accounts') if isinstance(value, dict) else None
    if not isinstance(rows, dict) or value.get('schema_version') != 1:
        raise ValueError('accounts file needs schema_version 1 and an "accounts" object')
    for instance, entry in rows.items():
        if not isinstance(entry, dict) or entry.get('billing') not in BILLING:
            raise ValueError(f'accounts.{instance}: billing must be subscription or metered')
        for key in ('quota_provider', 'quota_source'):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                raise ValueError(f'accounts.{instance}.{key} must be a string or null')
    return rows, {'state': 'loaded', 'sha256': digest(rows)}


FAILURE_CLASSES = ('rate_limit', 'transport', 'auth_config', 'content_policy', 'unknown')
# Patterns on the observed terminal-failure message text, matched in this order.
FAILURE_PATTERNS = (
    ('rate_limit', ('rate limit reached',)),
    ('transport', ('connection error', 'stream closed', 'stream disconnected', 'connection refused')),
    ('auth_config', ('could not authenticate', 'no conversation found', 'insufficient context allowance',
                     'still running background agents')),
    ('content_policy', ('flagged for possible',)),
)
CANCELLATIONS = ('cancelled', 'canceled', 'interrupted')
FAILURE_TABLES = {'turn_items': {'turn_item_id', 'run_id', 'status', 'updated_at', 'payload_json'},
                  'runs': {'run_id', 'provider_instance_id'}}


def classify_failure(message):
    text = (message or '').lower()
    for name, patterns in FAILURE_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return name
    return 'unknown'


def read_failures(db_path, now, lookback_minutes):
    """Terminal-failure events per provider instance and class, within the lookback.

    The events are T3 turn items whose ID contains `terminal-failure` and whose status is `failed`.
    A recovered item (status completed), a cancellation and an interruption are not failures.
    They join to the instance through the run. The database opens read-only and query-only, in one
    transaction. A missing database, table or column gives state `unknown` with a reason, never healthy.
    """
    unknown = lambda reason: {'state': 'unknown', 'reason': reason, 'lookback_minutes': lookback_minutes, 'instances': {}, 'unjoined': None}
    if not db_path:
        return unknown('not_supplied')
    if lookback_minutes is None:
        return unknown('no_lookback_in_pack')
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return unknown('database_unreadable')
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for name, needed in FAILURE_TABLES.items():
            columns = {row[1] for row in db.execute(f'PRAGMA table_info({PREFIX}{name})')}
            if not columns:
                return unknown(f'{name}_table_absent')
            if not needed <= columns:
                return unknown('schema_mismatch')
        since = (now - timedelta(minutes=lookback_minutes)).isoformat()
        rows = db.execute(f"""SELECT r.provider_instance_id,
              json_extract(i.payload_json,'$.failure.class'),
              coalesce(json_extract(i.payload_json,'$.failure.message'),json_extract(i.payload_json,'$.title'),'')
            FROM {PREFIX}turn_items i LEFT JOIN {PREFIX}runs r ON r.run_id=i.run_id
            WHERE i.turn_item_id LIKE '%terminal-failure%' AND i.status='failed'
              AND julianday(coalesce(json_extract(i.payload_json,'$.startedAt'),i.updated_at))>=julianday(?)
            ORDER BY i.turn_item_id""", (since,)).fetchall()
        db.commit()
    except sqlite3.Error:
        return unknown('read_error')
    finally:
        db.close()
    instances = {}
    unjoined = 0
    for instance, native_class, message in rows:
        if str(native_class).lower() in CANCELLATIONS:
            continue
        if instance is None:
            unjoined += 1
            continue
        events = instances.setdefault(instance, {})
        name = classify_failure(message)
        events[name] = events.get(name, 0) + 1
    return {'state': 'observed', 'reason': None, 'lookback_minutes': lookback_minutes, 'instances': instances, 'unjoined': unjoined}


def read_maker_runs(db_path, decision_id):
    """Every T3 child run that `delegate_task` calls with this clientRequestId started: their requested targets, or [].

    One decision ID can start several runs (a retry on another instance). Each run appears once.
    """
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return []
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = db.execute(f"""SELECT DISTINCT c.run_id,c.provider_instance_id,json_extract(c.payload_json,'$.modelSelection.model'),
              json_quote(json_extract(c.payload_json,'$.modelSelection.options'))
            FROM {PREFIX}turn_items i JOIN {PREFIX}runs c ON c.run_id=coalesce(
              json_extract(i.payload_json,'$.output.childRunId'),json_extract(i.payload_json,'$.output.structuredContent.childRunId'))
            WHERE i.type='dynamic_tool' AND json_extract(i.payload_json,'$.toolName') LIKE '%delegate_task'
              AND json_extract(i.payload_json,'$.input.clientRequestId')=? ORDER BY c.run_id""", (decision_id,)).fetchall()
        db.commit()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    return [{'providerInstanceId': r[1], 'model': r[2], 'options': json.loads(r[3]) if r[3] else None} for r in rows]


def instance_families(available):
    """{T3 instance ID: driver family} from the live catalog."""
    return {instance: driver_family(provider) for provider in providers(available)
            if isinstance(instance := provider_instance(provider), str)}


def known_providers(policy, available, lineage_key=None):
    """Provider names that the pack, the live catalog or the lineage map declare."""
    named = {arm['provider'] for op in policy.get('operating_points', []) for arm in [op['expands_to'], *op.get('candidates', [])]}
    return named | set(instance_families(available).values()) | set(lineage_key or {})


def resolve_maker(receipts_path, decision_id, model_spec, db_path, available, known=()):
    """The maker that a checker must differ from, with the provenance of each fact.

    Provenance: `t3_run_config` (the T3 child run's instance and model equal the receipt target),
    `receipt_only`, `caller_claim` (--maker-model) or `unknown`. When the child run is found, its
    instance driver kind gives the vendor and its model is the requested model; the receipt gives
    neither. An effort difference is reported in `effort_mismatch` and does not change provenance.
    When one decision ID started several runs that differ in instance or model, provenance is
    `unknown` with the issue `multiple_child_runs`, and `runs` lists every run; the checker must
    differ from all of them. The served model is never attested: T3 records the requested model only.
    """
    if decision_id and model_spec:
        raise ValueError('--maker and --maker-model are exclusive')
    if model_spec:
        provider, sep, model = model_spec.partition(':')
        if not sep or not provider or not model:
            raise ValueError('--maker-model takes PROVIDER:MODEL, for example claude:claude-sonnet-5-5')
        if known and provider not in known:
            raise ValueError(f'--maker-model provider {provider!r} is not a known provider ({", ".join(sorted(known))})')
        return {'decision_id': None, 'provenance': 'caller_claim', 'vendor': {'value': provider, 'basis': 'caller_claim'},
                'requested': {'provider': provider, 'model': model, 'effort': None}, 'served_model': 'unattested'}
    receipt = find_receipt(receipts_path, decision_id)
    if receipt is None:
        raise ValueError('maker receipt not found')
    target = receipt.get('target')
    arm = receipt['selection']['arm']
    maker = {'decision_id': decision_id, 'provenance': 'unknown', 'vendor': {'value': None, 'basis': 'unknown'},
             'requested': None, 'served_model': 'unattested'}
    if not isinstance(target, dict):
        return maker | {'issue': 'receipt_has_no_target'}
    families = instance_families(available)
    raw_runs = read_maker_runs(db_path, decision_id) if db_path else []
    if not raw_runs:
        vendor = families.get(target.get('providerInstanceId'))
        basis = 't3_driver_kind' if vendor else 'receipt_arm'
        return maker | {'provenance': 'receipt_only', 'vendor': {'value': vendor or arm['provider'], 'basis': basis},
                        'requested': {'provider': vendor or arm['provider'], 'model': target['model'], 'effort': arm['effort']}}
    runs = [{'instance': r['providerInstanceId'], 'provider': families.get(r['providerInstanceId']), 'model': r['model'], 'effort': effort(r)}
            for r in raw_runs]
    if len({(r['instance'], r['model']) for r in runs}) > 1:
        return maker | {'issue': 'multiple_child_runs', 'runs': runs}
    run = runs[0]
    maker |= {'requested': {'provider': run['provider'], 'model': run['model'], 'effort': run['effort']}}
    if run['provider'] is None:
        return maker | {'issue': 'run_instance_not_in_catalog'}
    maker['vendor'] = {'value': run['provider'], 'basis': 't3_driver_kind'}
    if (run['instance'], run['model']) != (target.get('providerInstanceId'), target.get('model')):
        return maker | {'issue': 'child_run_differs_from_receipt'}
    maker['provenance'] = 't3_run_config'
    efforts = {r['effort'] for r in runs}
    if efforts != {effort(target)}:
        maker['effort_mismatch'] = {'receipt': effort(target), 'run': run['effort'] if len(efforts) == 1 else sorted(efforts, key=str)}
    return maker


def default_model_catalog_path():
    if os.environ.get('DATA_PACKS_HOME'):
        return Path(os.environ['DATA_PACKS_HOME']) / 'model-catalog/models.json'
    return Path(__file__).resolve().parents[3] / 'data/model-catalog/models.json'


def read_model_catalog(path):
    """{model id: {family, tier, status, released}} from the model-catalog pack, or {} when unreadable."""
    try:
        rows = json.loads(Path(path).read_text(encoding='utf-8'))['models']
        return {r['id']: {k: r.get(k) for k in ('family', 'tier', 'status', 'released')} for r in rows if isinstance(r, dict) and 'id' in r}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def line_of(provider, model, model_catalog, lineage_key):
    """The model line as (vendor, value of the model-catalog field that the pack's `lineage_key` names), or None.

    A provider with no mapping, or a model that the model-catalog pack does not know, has no line.
    """
    field = (lineage_key or {}).get(provider)
    value = (model_catalog.get(model) or {}).get(field) if field else None
    return (provider, value) if value else None


def pack_arms(policy):
    """Every (provider, exact model) that the pack names, in `expands_to` or `candidates`."""
    return {(arm['provider'], arm['model']) for op in policy.get('operating_points', [])
            for arm in [op['expands_to'], *op.get('candidates', [])]}


def catalog_models(available_providers):
    """Exact model IDs in the live catalog, by provider family."""
    result = {}
    for provider in available_providers:
        for model in provider.get('models', []):
            result.setdefault(driver_family(provider), set()).add(model.get('id', model.get('slug')))
    return result


def freshness(policy, available_providers, model_catalog, lineage_key=None):
    """Stale-pack facts (D5): pack models missing from the live catalog, and newer GA models not in the pack.

    A newer model is on the same line (see `line_of`), has status `ga` and a later `released` date.
    A model with no known line is never called newer; it is listed in `unchecked` with the reason,
    and the state is then `partial`.
    """
    by_provider = catalog_models(available_providers)
    live = set().union(*by_provider.values()) if by_provider else set()
    pack = pack_arms(policy)
    pack_names = {model for _, model in pack}
    missing = sorted(m for m in pack_names if live and m not in live)
    newer = []
    unchecked = []
    for provider, model in sorted(pack):
        line = line_of(provider, model, model_catalog, lineage_key)
        base = model_catalog.get(model)
        if line is None or not base or not base['released']:
            unchecked.append({'provider': provider, 'model': model, 'reason': 'line_unknown' if line is None else 'release_date_unknown'})
            continue
        for other in sorted(by_provider.get(provider, set()) - pack_names):
            info = model_catalog.get(other)
            if (info and info['status'] == 'ga' and line_of(provider, other, model_catalog, lineage_key) == line
                    and info['released'] and info['released'] > base['released']):
                newer.append({'pack_model': model, 'newer_model': other, 'released': info['released'], 'line': list(line)})
    state = 'unknown' if not (live and model_catalog) else 'partial' if unchecked else 'checked'
    return {'state': state, 'missing_from_live_catalog': missing, 'newer_ga_not_in_pack': newer, 'unchecked': unchecked}
