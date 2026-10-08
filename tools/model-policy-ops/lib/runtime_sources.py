"""Bounded runtime readers. Catalog presence is not authentication evidence."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_source(spec, command):
    if not spec:
        return {}, {'state': 'missing', 'timestamp': None, 'sha256': None, 'issue': 'not_supplied'}
    try:
        if spec == 'auto':
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if result.returncode:
                return {}, {'state': 'error', 'timestamp': None, 'sha256': None, 'issue': 'command_failed', 'exit_code': result.returncode}
            raw = result.stdout
        else:
            raw = Path(spec).read_text()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('source must be an object')
        state = 'error' if value.get('ok') is False or value.get('error') else 'snapshot'
        if value.get('stale') or value.get('is_expired'):
            state = 'stale'
        timestamp = value.get('fetched_at') or value.get('generated_at')
        return value, {'state': state, 'timestamp': timestamp, 'sha256': digest(value),
                       'freshness': 'source_declared_only' if timestamp else 'unknown'}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {}, {'state': 'error', 'timestamp': None, 'sha256': None,
                    'issue': 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'malformed_json' if isinstance(error, ValueError) else 'source_unreadable'}


def providers(value):
    value = value.get('structuredContent', value)
    if not isinstance(value, dict):
        return []
    value = value.get('data', value)
    if not isinstance(value, dict):
        return []
    rows = value.get('providers')
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('models'), list):
            return []
        for model in row['models']:
            if not isinstance(model, dict) or not isinstance(model.get('options') or [], list):
                return []
            for option in model.get('options') or []:
                if not isinstance(option, dict):
                    return []
                values = option.get('values', option.get('options', []))
                if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                    return []
    return rows


def resolve_arm(arm, available, metadata, account=None):
    candidates = []
    for provider in providers(available):
        driver = provider.get('driverKind', provider.get('driver'))
        family = 'claude' if driver == 'claudeAgent' else driver
        if family != arm['provider']:
            continue
        instance = provider.get('providerInstanceId', provider.get('instanceId'))
        if not isinstance(instance, str):
            continue
        candidates.append(instance)
    result = {'arm': arm, 'accounts': candidates, 'state': 'unknown', 'target': None,
              'authentication': 'not_attested'}
    if metadata['state'] in ('missing', 'error', 'stale', 'schema_mismatch'):
        result['state'] = metadata['state']
        return result
    if account is None and len(candidates) > 1:
        result['state'] = 'ambiguous_account'
        return result
    instance = account or (candidates[0] if candidates else None)
    for provider in providers(available):
        if provider.get('providerInstanceId', provider.get('instanceId')) != instance or instance not in candidates:
            continue
        if provider.get('enabled') is False or provider.get('canRunChildTask') is False or provider.get('runnable') is False or provider.get('status', 'ready') not in ('ready', 'available'):
            result['state'] = 'unavailable_account'
            return result
        for model in provider.get('models', []):
            if model.get('id', model.get('slug')) != arm['model']:
                continue
            if model.get('enabled') is False or model.get('canRunChildTask') is False or model.get('runnable') is False or model.get('status', 'ready') not in ('ready', 'available'):
                result['state'] = 'unavailable_model'
                return result
            options = [o for o in model.get('options') or [] if o.get('id') in ('effort', 'reasoningEffort', 'thinking')]
            if len(options) != 1:
                result['state'] = 'unknown_effort_surface'
                return result
            option = options[0]
            allowed = [v['id'] for v in option.get('values', option.get('options', [])) if isinstance(v, dict) and 'id' in v]
            result['effort_option'] = {'key': option['id'], 'allowed': allowed}
            if arm['effort'] not in allowed:
                result['state'] = 'unsupported_effort'
                return result
            result.update(state='catalog_present', target={'providerInstanceId': instance, 'model': arm['model'],
                          'options': {option['id']: arm['effort']}})
            return result
    result['state'] = 'unavailable_model' if instance in candidates else 'unavailable_account'
    return result


def quota_context(value, metadata, arm, provider_id=None, source_id=None):
    """Keep real clawmeter identities. Account-to-source mapping is caller supplied."""
    result = {'source': metadata, 'provider_id': provider_id, 'source_id': source_id,
              'state': 'unknown', 'windows': [], 'accounts': []}
    rows = value.get('providers', {})
    if not isinstance(rows, dict):
        result['state'] = 'schema_mismatch'
        return result
    for row in rows.values():
        if not isinstance(row, dict) or not isinstance(row.get('sources') or [], list):
            result['state'] = 'schema_mismatch'
            return result
        for source in row.get('sources') or []:
            if not isinstance(source, dict) or not isinstance(source.get('source'), dict):
                result['state'] = 'schema_mismatch'
                return result
    if not provider_id:
        result['accounts'] = [{'provider_id': p, 'source_ids': [s.get('source', {}).get('id') for s in row.get('sources') or []]} for p, row in rows.items()]
        result['state'] = 'mapping_required' if rows else metadata['state']
        return result
    row = rows.get(provider_id)
    if not isinstance(row, dict):
        result['state'] = 'missing_provider'
        return result
    sources = row.get('sources') or []
    if sources:
        result['accounts'] = [s.get('source', {}).get('id') for s in sources]
        selected = [s for s in sources if s.get('source', {}).get('id') == source_id]
        if len(selected) != 1:
            result['state'] = 'source_required'
            return result
        row = selected[0]
    elif source_id:
        result['state'] = 'missing_source'
        return result
    usage = row.get('usage', {})
    if not isinstance(usage, dict) or not isinstance(usage.get('windows'), (list, type(None))):
        result['state'] = 'schema_mismatch'
        return result
    result['timestamp'] = usage.get('fetched_at')
    result['state'] = 'error' if usage.get('error') else 'stale' if usage.get('is_expired') else 'snapshot'
    if metadata['state'] in ('error', 'stale', 'missing'):
        result['state'] = metadata['state']
    if not usage.get('windows') and result['state'] == 'snapshot':
        result['state'] = 'unknown_windows'
    for window in usage.get('windows') or []:
        if not isinstance(window, dict):
            result['state'] = 'schema_mismatch'
            result['windows'] = []
            return result
        name = window.get('name', '')
        if not isinstance(name, str):
            result['state'] = 'schema_mismatch'
            result['windows'] = []
            return result
        # These are observed clawmeter window names, not account aliases or price estimates.
        informational = bool(window.get('currency')) or name.lower() in ('bonus', 'extra')
        all_models = name in ('5h', '7d', '7d All')
        tier = name.removeprefix('7d ').lower() if name.startswith('7d ') else None
        tier_match = tier in ('haiku', 'sonnet', 'opus', 'fable') and tier in arm['model'].lower()
        relevant = not informational and (all_models or tier_match)
        utilization = window.get('utilization')
        expired = False
        try:
            expired = datetime.fromisoformat(window['resets_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc)
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        state = 'stale' if expired and relevant else 'exhausted' if relevant and isinstance(utilization, (int, float)) and utilization >= 100 else 'reported' if relevant else 'informational'
        result['windows'].append({k: window.get(k) for k in ('name', 'display_name', 'utilization', 'resets_at', 'currency', 'used', 'limit')} | {'relevant': bool(relevant), 'state': state})
    return result
