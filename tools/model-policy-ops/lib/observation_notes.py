"""Two descriptive notes for `route`, read from the observation ledger. Each is emitted only when actionable.

The notes never reorder candidates, never change `judgment_required` and never join a receipt's `request`.
"""
from datetime import datetime, timedelta

MAX_AGE_HOURS = 3
WINDOW_DAYS = 14
MIN_DECISIONS = 3
LABELS = 'completed = worker filed a report; accepted = owner disposition; neither is correctness'


def evidence_note(rows, state, at, max_age_hours, has_observe_command):
    """Missing, stale or unhealthy evidence, or None. A missing file with no observe command is a valid setup."""
    runs = [r for r in rows if r['kind'] == 'source_run']
    if state == 'unreadable' or (state == 'absent' and has_observe_command) or (state == 'observed' and not runs):
        return {'kind': 'observations_missing', 'last_run_at': None, 'age_hours': None, 'sources': {}}
    if not runs:
        return None
    latest = max(runs, key=lambda r: datetime.fromisoformat(r['finished_at']))
    # A source with no observe_command is a valid setup; observe records it as unavailable.
    sources = {name: entry['state'] for name, entry in sorted(latest['sources'].items())
               if not (entry['state'] == 'unavailable' and entry['reason'] == 'no_observe_command')}
    if not sources:
        return None   # nothing left to observe: an excluded-only run is a valid setup, never stale
    age = (at - datetime.fromisoformat(latest['finished_at'])).total_seconds() / 3600
    base = {'last_run_at': latest['finished_at'], 'age_hours': round(age, 2), 'sources': sources}
    if age > max_age_hours:
        return {'kind': 'observations_stale'} | base
    if any(s != 'ok' for s in sources.values()):
        return {'kind': 'observation_source_not_ok'} | base
    return None


def blocked_share_note(rows, receipts, destination, purpose, at, window_days):
    """Share of blocked results among decisions of this purpose that went to `destination`, or None below the evidence bar."""
    if not destination or not purpose:
        return None
    cutoff = at - timedelta(days=window_days)
    decisions = {r['decision_id'] for r in receipts
                 if ((r.get('routing') or {}).get('destination') or {}).get('chosen') == destination
                 and (r.get('launch_facts') or {}).get('purpose') == purpose
                 and cutoff <= datetime.fromisoformat(r['recorded_at']) <= at}
    source = f'destination:{destination}'
    latest, accepted = {}, set()
    for number, row in enumerate(rows):
        if row['kind'] != 'observation' or row['source'] != source or row['decision_id'] not in decisions:
            continue
        if row['fact_type'] == 'destination_report':
            # Latest report wins: by the source's own time, then by ledger order.
            key = (row['source_timestamps'].get('created_at', ''), number)
            if row['decision_id'] not in latest or key > latest[row['decision_id']][0]:
                latest[row['decision_id']] = (key, row['fact']['outcome'])
        elif row['fact_type'] == 'destination_event' and row['fact']['event_type'] == 'accepted':
            accepted.add(row['decision_id'])
    outcomes = [outcome for _, outcome in latest.values()]
    blocked = outcomes.count('blocked')
    if len(outcomes) < MIN_DECISIONS or not blocked:
        return None
    return {'kind': 'destination_blocked_share', 'destination': destination, 'task_kind': purpose, 'window_days': window_days,
            'decisions_with_result': len(outcomes), 'blocked': blocked, 'completed': outcomes.count('completed'),
            'accepted_by_owner': len(accepted & set(latest)), 'labels': LABELS}


def notes(rows, state, receipts, *, at, params, destination, purpose, has_observe_command):
    """The list of actionable notes; empty when neither applies."""
    found = [evidence_note(rows, state, at, params.get('observation_max_age_hours', MAX_AGE_HOURS), has_observe_command),
             blocked_share_note(rows, receipts, destination, purpose, at, params.get('observation_window_days', WINDOW_DAYS))]
    return [n for n in found if n]
