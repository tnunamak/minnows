"""Rebuild clawmeter.json in this directory from a live `clawmeter --json` capture. Read-only.

    python3 scrub.py CLAWMETER_JSON 'SOURCE_ID=default,SOURCE_ID=work,SOURCE_ID=alt'

The capture is from 2026-10-11T00:23Z: the Codex 7d window is at 58% and projected near 105%, while the
Claude accounts are projected near 87%, 72% and 29%. Keeps only the facts that `route` reads, renames the
three Claude sources to the neutral IDs `default`, `work` and `alt` that the route-live-20261010 accounts map
uses, and drops credit pools, reset credits, organization names and informational windows.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
# The live source IDs are the owner's account names, so the mapping is passed on the command line, never stored here.
RENAME = {live: (neutral, neutral.capitalize()) for live, neutral in
          (pair.split('=', 1) for pair in (sys.argv[2].split(',') if len(sys.argv) > 2 else []))}
KEEP_WINDOWS = ('5h', '7d', '7d All', '7d Fable')


def usage(row, source_id=None):
    windows = [{k: w[k] for k in ('name', 'display_name', 'utilization', 'resets_at')} for w in row['windows'] if w['name'] in KEEP_WINDOWS]
    out = {'provider': row['provider'], 'fetched_at': row['fetched_at'], 'windows': windows}
    return out | ({'source_id': source_id} if source_id else {})


def forecast(row):
    return {'windows': {k: {'projected_pct': v['projected_pct']} for k, v in row['forecast']['windows'].items() if k in KEEP_WINDOWS}}


def main(path):
    live = json.loads(Path(path).read_text())
    claude, openai = live['providers']['claude'], live['providers']['openai']
    sources = []
    for source in claude['sources']:
        name, label = RENAME[source['source']['id']]
        sources.append({'source': {'id': name, 'label': label}, 'usage': usage(source['usage'], name), 'forecast': forecast(source)})
    sources.sort(key=lambda s: s['source']['id'])
    providers = {'claude': {'usage': usage(claude['usage']), 'forecast': forecast(claude), 'sources': sources},
                 'openai': {'usage': usage(openai['usage']), 'forecast': forecast(openai)}}
    value = {'schema_version': live['schema_version'], 'providers': providers, 'fetched_at': live['fetched_at']}
    (HERE / 'clawmeter.json').write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')


if __name__ == '__main__':
    main(sys.argv[1])
