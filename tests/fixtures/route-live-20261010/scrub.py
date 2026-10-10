"""Rebuild this fixture directory from live state. Needs clawmeter, t3code and a T3 database. Read-only.

    python3 scrub.py CLAWMETER_JSON T3_MODELS_JSON STATEV2_SQLITE

Keeps only the facts that `route` reads. Drops credit pools, organization names, session IDs and
every provider that route does not use.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
KEEP_MODELS = {'codex': ['gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna', 'gpt-6-astra'],
               'claudeAgent': ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-opus-5', 'claude-sonnet-5']}


def usage(row):
    keep = ('provider', 'source_id', 'fetched_at', 'windows', 'is_expired', 'error')
    return {k: row[k] for k in keep if k in row}


def quota(path):
    live = json.loads(Path(path).read_text())
    providers = {}
    for name in ('claude', 'openai'):
        row = live['providers'][name]
        out = {'usage': usage(row['usage']), 'forecast': row['forecast']}
        if row.get('sources'):
            out['sources'] = [{'source': s['source'], 'usage': usage(s['usage']), 'forecast': s['forecast']} for s in row['sources']]
        providers[name] = out
    return {'schema_version': live['schema_version'], 'providers': providers, 'fetched_at': live['fetched_at'], 'cache': live['cache']}


def catalog(path):
    live = json.loads(Path(path).read_text())
    providers = []
    for p in live['data']['providers']:
        if p.get('driver') not in KEEP_MODELS or not p.get('enabled'):
            continue
        models = []
        for m in p['models']:
            if m['slug'] in KEEP_MODELS[p['driver']]:
                models.append({'slug': m['slug'], 'options': [o for o in m['options'] if o['id'] in ('effort', 'reasoningEffort')]})
        providers.append({'instanceId': p['instanceId'], 'driver': p['driver'], 'enabled': True, 'status': 'ready', 'models': models})
    return {'ok': True, 'data': {'providers': providers}}


def failures(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    rows = db.execute("""SELECT r.provider_instance_id,i.status,json_extract(i.payload_json,'$.startedAt'),
        json_extract(i.payload_json,'$.failure.class'),json_extract(i.payload_json,'$.failure.message')
        FROM orchestration_v2_projection_turn_items i LEFT JOIN orchestration_v2_projection_runs r ON r.run_id=i.run_id
        WHERE i.turn_item_id LIKE '%terminal-failure%' AND json_extract(i.payload_json,'$.startedAt')>='2026-10-09T00:00:00Z'
        ORDER BY 3""").fetchall()
    scrub = lambda text: re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27}', 'SESSION', text or '')
    return {'captured': '2026-10-10', 'events': [{'instance': r[0], 'status': r[1], 'startedAt': r[2], 'class': r[3], 'message': scrub(r[4])} for r in rows]}


if __name__ == '__main__':
    for name, value in (('clawmeter.json', quota(sys.argv[1])), ('t3-models.json', catalog(sys.argv[2])), ('t3-failures.json', failures(sys.argv[3]))):
        (HERE / name).write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
