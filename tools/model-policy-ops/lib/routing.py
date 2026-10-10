"""Deterministic routing mechanism. It holds no model names and no preferences.

The pack carries the policy: candidate arms with their evidence basis, and the routing parameters.
`route` pairs each candidate arm with each live catalog account, removes the pairs that a filter
rejects, ranks the rest by a lexicographic key, and explains each step in a trace. It never learns
from outcomes and it never dispatches work.
"""
from directives import matching
from runtime_sources import digest, driver_family, provider_instance, providers, quota_context, quota_forecast, resolve_arm

ROUTER_VERSION = 1
ARM_KEYS = ('provider', 'model', 'effort')
PACE_RANK = {'on_track': 0, 'unknown': 1, 'at_risk': 2}
AVAILABILITY_RANK = {'healthy': 0, 'unknown': 1, 'demoted': 2}
DEMOTING = ('rate_limit', 'transport')
SELECTION_BASIS = {'primary': 'primary', 'task_benchmark_prior': 'evidence_prior'}
EVIDENCE_LABEL = {'primary': 'primary', 'task_benchmark_prior': 'prior', 'unvalidated': 'unvalidated'}
INDEPENDENCE_LEVELS = ('vendor', 'family', 'model')
EFFECTS = ('avoid', 'prefer', 'authorize', 'allow-metered')
# The filter that removes a pair, in the order the contract applies them, and the reason that a bypass of it raises.
FINAL_REASONS = {'not_runnable': 'final_not_runnable', 'requires_not_met': 'final_requires_not_met',
                 'billing_not_subscription': 'final_metered_without_directive', 'avoid_directive': 'final_avoided',
                 'quota_exhausted': 'final_quota_exhausted', 'independence': 'final_independence_bypassed'}


def expand_candidates(op):
    """Ordered candidates: the op's `expands_to` as the primary, then the pack's `candidates` as alternates."""
    rows = [dict(op['expands_to'], basis='primary'), *op.get('candidates', [])]
    return [{'arm': {k: row[k] for k in ARM_KEYS}, 'basis': row['basis'], 'prior': row.get('prior'),
             'known_gaps': row.get('known_gaps', []), 'evidence_refs': row.get('evidence_refs', []),
             'requires': row.get('requires')} for row in rows]


def catalog_instances(available, family):
    return sorted({instance for provider in providers(available)
                   if isinstance(instance := provider_instance(provider), str) and driver_family(provider) == family})


def quota_view(quota, quota_meta, arm, entry, pace_windows, now):
    """Quota facts for one account: exhaustion, pace class, and the window that binds the pace.

    Pace is `unknown` for an unmapped account, a quota source that is not a plain snapshot, a pace
    window with no forecast, an expired window, or no pace window at all. Unknown is never headroom.
    """
    view = {'state': 'unmapped', 'provider_id': None, 'source_id': None, 'fetched_at': None, 'cache': None,
            'windows': [], 'exhausted': False, 'pace': 'unknown', 'pace_reason': 'unmapped_account', 'binding': None}
    if not entry or not entry.get('quota_provider'):
        return view
    provider_id, source_id = entry['quota_provider'], entry.get('quota_source')
    info = quota_context(quota, quota_meta, arm, provider_id, source_id, now)
    forecast = quota_forecast(quota, provider_id, source_id) or {}
    cache = quota.get('cache')
    windows = [w | {'projected_pct': forecast.get(w['name']),
                    'remaining_pct': max(0, 100 - w['utilization']) if isinstance(w.get('utilization'), (int, float)) else None}
               for w in info['windows'] if w['relevant'] or w['name'] in pace_windows]
    view.update(state=info['state'], provider_id=provider_id, source_id=source_id, fetched_at=info.get('timestamp'),
                cache=cache if isinstance(cache, dict) else None, windows=windows, source_state=quota_meta['state'])
    if info['state'] != 'snapshot':
        return view | {'pace_reason': f'quota_{info["state"]}'}
    view['exhausted'] = any(w['state'] == 'exhausted' for w in info['windows'])
    by_name = {w['name']: w for w in windows}
    considered = [name for name in pace_windows if name in by_name or name in forecast]
    if not considered:
        return view | {'pace_reason': 'no_pace_window'}
    if any(name not in forecast or name not in by_name or by_name[name]['state'] == 'stale' for name in considered):
        return view | {'pace_reason': 'no_forecast_or_expired_window'}
    binding = max(considered, key=lambda name: (forecast[name], name))
    window = by_name[binding]
    return view | {'pace': 'at_risk' if forecast[binding] >= 100 else 'on_track', 'pace_reason': None,
                   'binding': {'window': binding, 'projected_pct': forecast[binding], 'utilization': window['utilization'],
                               'remaining_pct': window['remaining_pct'], 'resets_at': window['resets_at']}}


def quota_summary(views, instance, arm, view):
    """Store the full quota view once in `views`; return the key that a trace row cites.

    The key is the instance. A view that differs by arm (relevant windows) gets the model name appended.
    """
    key = instance
    if key in views and views[key] != view:
        key = f'{instance}:{arm["model"]}'
    views[key] = view
    return key


def quota_row(view, ref):
    """What a filter or rank key used from the quota view; the full view is in `routing.quota`[ref]."""
    return {'pace': view['pace'], 'pace_reason': view['pace_reason'], 'exhausted': view['exhausted'],
            'projected_pct': view['binding']['projected_pct'] if view['binding'] else None, 'ref': ref}


def availability_view(failures, demote_count, instance):
    """`healthy`, `unknown` or `demoted`. Only rate_limit and transport events demote. Telemetry gone is unknown.

    Failure events that do not join to an instance could belong to any of them, so availability is unknown for all.
    """
    if failures['state'] != 'observed' or demote_count is None:
        return {'class': 'unknown', 'reason': failures['reason'] or 'no_demote_count_in_pack', 'events': {}}
    if failures.get('unjoined'):
        return {'class': 'unknown', 'reason': 'unjoined_failure_events', 'unjoined': failures['unjoined'], 'events': {}}
    events = failures['instances'].get(instance, {})
    demoting = sum(events.get(name, 0) for name in DEMOTING)
    return {'class': 'demoted' if demoting >= demote_count else 'healthy', 'reason': None,
            'demoting_events': demoting, 'threshold': demote_count, 'events': events}


def maker_targets(maker):
    """The (vendor, requested model) pairs that a checker must differ from. Empty when the maker's vendor is unknown.

    A maker with several child runs lists every run whose vendor is known.
    """
    if maker.get('runs'):
        return [(r['provider'], r['model']) for r in maker['runs'] if r['provider']]
    if maker['requested'] is None or maker['vendor']['value'] is None:
        return []
    return [(maker['vendor']['value'], maker['requested']['model'])]


def maker_vendor_coverage(maker):
    """(known vendors, complete). Complete is false when any maker run has an unknown vendor, or when there is no maker."""
    if not maker:
        return [], False
    if maker.get('runs'):
        return sorted({r['provider'] for r in maker['runs'] if r['provider']}), all(r['provider'] for r in maker['runs'])
    vendors = sorted({v for v, _ in maker_targets(maker)})
    return vendors, bool(vendors)


def independence_check(level, maker, arm, line_of):
    """(ok, requested_only, why_not). A different vendor is fixed by the T3 driver kind.

    Within one vendor, family and model rest on the requested model only, so they are `requested_only`.
    The family is the model line from `line_of`: a different version of the same line is the same family,
    and a line that is unknown cannot pass. The checker must pass against every maker target.
    """
    requested_only = False
    for vendor, model in maker_targets(maker):
        if vendor != arm['provider']:
            continue
        if level == 'vendor':
            return False, False, 'same_vendor'
        requested_only = True
        if level == 'family':
            mine, theirs = line_of(arm['provider'], arm['model']), line_of(vendor, model)
            if mine is None or theirs is None:
                return False, False, 'line_unknown'
            if mine == theirs:
                return False, True, 'same_family'
        elif arm['model'] == model:
            return False, True, 'same_model'
    return True, requested_only, None


def eligibility(candidate, authorizers, proof_class):
    """(class, selection_basis, reasons). Class 0 selects without judgment, class 1 needs it."""
    reasons = []
    requires = (candidate['requires'] or {}).get('proof_class')
    if requires and proof_class is None:
        reasons.append('requires_unknown')
    basis = candidate['basis']
    selection = SELECTION_BASIS.get(basis)
    if basis == 'unvalidated':
        if authorizers:
            selection = 'authorized_exception'
        else:
            reasons.append('unvalidated_candidate')
    return (1 if reasons else 0), (None if reasons else selection), reasons


def assess_pair(ctx, op_id, arm, instance, requires):
    """Every fact and every filter hit for one (arm, account) pair, in the contract's filter order.

    `route` removes a pair on its first hit. The final pair after an override gets the same assessment,
    so the owner sees every filter that the override bypassed.
    """
    facts = {'provider': arm['provider'], 'model': arm['model'], 'account': instance, 'op': op_id}
    matched = {effect: matching(ctx['directives'], effect, **facts) for effect in EFFECTS}
    entry = ctx['accounts'].get(instance)
    billing = entry['billing'] if entry else 'unmapped'
    selection = resolve_arm(arm, ctx['available'], ctx['available_meta'], instance)
    view = quota_view(ctx['quota'], ctx['quota_meta'], arm, entry, ctx['pace_windows'], ctx['now'])
    proof_class = ctx['proof_class']
    hits = []
    if selection['state'] != 'catalog_present':
        hits.append(('not_runnable', {'state': selection['state']}))
    if requires and proof_class is not None and proof_class not in requires:
        hits.append(('requires_not_met', {'proof_class': proof_class, 'requires': requires}))
    if billing != 'subscription' and not matched['allow-metered']:
        hits.append(('billing_not_subscription', {'billing': billing}))
    if matched['avoid']:
        hits.append(('avoid_directive', {'directives': [d['id'] for d in matched['avoid']]}))
    if view['exhausted']:
        hits.append(('quota_exhausted', {'windows': [w['name'] for w in view['windows'] if w['state'] == 'exhausted']}))
    requested_only = False
    if ctx['level'] and ctx['maker']:
        ok, requested_only, why = independence_check(ctx['level'], ctx['maker'], arm, ctx['line_of'])
        if not ok:
            hits.append(('independence', {'level': ctx['level'], 'why': why}))
    return {'matched': matched, 'billing': billing, 'selection': selection, 'view': view, 'hits': hits, 'requested_only': requested_only}


def is_review(op, params):
    return op.get('task_family') in params.get('review_task_families', [])


def make_context(op, params, *, available, available_meta, quota, quota_meta, accounts, directives, maker, independence,
                 proof_class, line_of, failures, freshness, now):
    """The inputs that every pair assessment shares. A review op without a pack `independence_default` requires `vendor`.

    `level_source` says where the level came from: the `flag`, the `pack` or the `mechanism_default`.
    """
    review = is_review(op, params)
    if independence:
        level, source = independence, 'flag'
    elif review:
        level, source = params.get('independence_default', 'vendor'), 'pack' if 'independence_default' in params else 'mechanism_default'
    else:
        level, source = None, None
    return {'available': available, 'available_meta': available_meta, 'quota': quota, 'quota_meta': quota_meta,
            'accounts': accounts, 'directives': directives, 'maker': maker, 'level': level, 'level_source': source, 'review': review,
            'proof_class': proof_class, 'line_of': line_of, 'failures': failures, 'freshness': freshness,
            'pace_windows': params.get('pace_windows', []), 'now': now}


def tag(reasons, applies_to):
    return [r | {'applies_to': applies_to} for r in reasons]


def final_view(ctx, op, arm, instance, views):
    """The final pair after an override, through the same filters and judgment rules as a routed pair.

    The override stays allowed. The block records every filter that the override bypassed. The reasons
    are the routed-pair rules applied to the final pair, plus one `final_*` reason per bypassed filter.
    The final pair is matched to a pack candidate by exact provider, model and effort, because evidence
    is keyed on the exact arm; a pair that the pack does not name, including an effort change, is
    `unvalidated`.
    """
    key = ('provider', 'model', 'effort')
    candidate = next((c for c in expand_candidates(op) if all(c['arm'][k] == arm[k] for k in key)),
                     {'basis': 'unvalidated', 'requires': None})
    found = assess_pair(ctx, op['id'], arm, instance, (candidate['requires'] or {}).get('proof_class'))
    view = found['view']
    ref = quota_summary(views, instance, arm, view)
    bypassed = [{'filter': name, 'detail': detail} for name, detail in found['hits']]
    _, _, ineligible = eligibility(candidate, found['matched']['authorize'], ctx['proof_class'])
    reasons = [{'code': FINAL_REASONS[name], 'detail': detail} for name, detail in found['hits']]
    reasons += pair_reasons(ctx, arm=arm, instance=instance, pace=view['pace'], eligibility_reasons=ineligible,
                            requested_only=found['requested_only'] and not any(name == 'independence' for name, _ in found['hits']))
    return {'billing': found['billing'], 'filters_bypassed': bypassed, 'pace': view['pace'], 'quota_ref': ref}, tag(reasons, 'final')


def route(op, params, ctx):
    """Return the `routing` block. `chosen` is None when no pair survives every filter."""
    demote_count = params.get('failure_demote_count')
    failures = ctx['failures']
    trace = []
    conflicts = []
    applied = set()
    views = {}
    candidates = expand_candidates(op)
    for index, candidate in enumerate(candidates):
        arm = candidate['arm']
        instances = catalog_instances(ctx['available'], arm['provider'])
        base = {'candidate': index, 'arm': arm, 'basis': candidate['basis']}
        if not instances:
            trace.append(base | {'instance': None, 'removed_by': 'no_catalog_account', 'detail': {'catalog': ctx['available_meta']['state']}})
        for instance in instances:
            found = assess_pair(ctx, op['id'], arm, instance, (candidate['requires'] or {}).get('proof_class'))
            matched, view = found['matched'], found['view']
            applied.update(d['id'] for rows in matched.values() for d in rows)
            row = base | {'instance': instance, 'billing': found['billing'],
                          'directives': {effect: [d['id'] for d in rows] for effect, rows in matched.items() if rows}}
            if matched['avoid'] and (matched['prefer'] or matched['authorize']):
                conflicts.append({'candidate': index, 'instance': instance, 'avoid': [d['id'] for d in matched['avoid']],
                                  'overridden': [d['id'] for d in matched['prefer'] + matched['authorize']], 'resolution': 'avoid wins'})
            row['quota'] = quota_row(view, quota_summary(views, instance, arm, view))
            if found['hits']:
                removed, detail = found['hits'][0]
                trace.append(row | {'removed_by': removed, 'detail': detail})
                continue
            klass, selection_basis, reasons = eligibility(candidate, matched['authorize'], ctx['proof_class'])
            health = availability_view(failures, demote_count, instance)
            rank = {'eligibility': klass, 'prefer': 0 if matched['prefer'] else 1, 'pace': PACE_RANK[view['pace']],
                    'availability': AVAILABILITY_RANK[health['class']], 'candidate_order': index,
                    'projected_pct': view['binding']['projected_pct'] if view['binding'] else None, 'instance': instance}
            trace.append(row | {'removed_by': None, 'availability': health, 'eligibility_reasons': reasons,
                                'selection_basis': selection_basis, 'requested_only_independence': found['requested_only'], 'rank': rank,
                                '_target': found['selection']['target'], '_authorizers': matched['authorize'], '_view': view})
    survivors = sorted((t for t in trace if t['removed_by'] is None), key=lambda t: rank_tuple(t['rank']))
    chosen = chosen_view(survivors[0], candidates) if survivors else None
    for position, row in enumerate(survivors):
        row['rank_position'] = position
    for row in trace:
        for private in ('_authorizers', '_target', '_view'):
            row.pop(private, None)
    unknown_line = sum(1 for t in trace if t['removed_by'] == 'independence' and t['detail']['why'] == 'line_unknown')
    reasons = routed_reasons(ctx, chosen, survivors[0] if survivors else None, conflicts, unknown_line)
    return {'router_version': ROUTER_VERSION, 'params': params, 'params_sha256': digest(params),
            'independence': {'required': ctx['level'], 'required_source': ctx['level_source'], 'maker': ctx['maker']},
            'directives_applied': sorted(applied), 'directives_unmatched': sorted(d['id'] for d in ctx['directives'] if d['id'] not in applied),
            'conflicts': conflicts,
            'rank_keys': ['eligibility', 'prefer', 'pace', 'availability', 'candidate_order', 'projected_pct (load-balancing heuristic)', 'instance'],
            'quota': views, 'trace': trace, 'chosen': chosen, 'judgment_required': judgment_needed(reasons), 'judgment_reasons': reasons}


def finalize(block, ctx, op, arm, instance, selection, *, routed, overridden):
    """Add `routed` and `final` to the routing block. With an override, `judgment_required` follows the final pair.

    The routed reasons stay in the list, tagged `routed`. A final pair with no target needs judgment.
    """
    if instance:
        facts, final_reasons = final_view(ctx, op, arm, instance, block['quota'])
    else:
        facts, final_reasons = {'billing': None, 'filters_bypassed': [], 'pace': None, 'quota_ref': None}, []
    if selection['target'] is None and overridden:
        final_reasons += tag([{'code': 'final_target_null', 'detail': {'state': selection['state'], 'account': instance}}], 'final')
    block |= {'routed': routed, 'final': {'arm': arm, 'account': instance, 'overridden': overridden, 'target': selection['target']} | facts}
    if overridden:
        block['judgment_reasons'] += final_reasons
        block['judgment_required'] = judgment_needed(final_reasons)


def judgment_needed(reasons):
    return any(not r.get('informational') for r in reasons)


def rank_tuple(rank):
    return (rank['eligibility'], rank['prefer'], rank['pace'], rank['availability'], rank['candidate_order'],
            float('inf') if rank['projected_pct'] is None else rank['projected_pct'], rank['instance'])


def chosen_view(row, candidates):
    candidate = candidates[row['candidate']]
    authorizers = row['_authorizers'] if row['selection_basis'] == 'authorized_exception' else []
    return {'candidate': row['candidate'], 'arm': row['arm'], 'instance': row['instance'], 'target': row['_target'],
            'basis': candidate['basis'], 'selection_basis': row['selection_basis'], 'evidence': EVIDENCE_LABEL[candidate['basis']],
            'prior': candidate['prior'], 'known_gaps': candidate['known_gaps'], 'evidence_refs': candidate['evidence_refs'],
            'authorized_by': [{'id': d['id'], 'source': d['source'], 'until': d['until']} for d in authorizers],
            'pace': row['quota']['pace'], 'quota_binding': row['_view']['binding'], 'availability': row['availability']['class']}


def routed_reasons(ctx, chosen, row, conflicts, unknown_line):
    """Judgment reasons for the routed pair, each tagged `routed`. A non-empty list means the parent decides."""
    reasons = []
    if unknown_line:
        reasons.append({'code': 'independence_line_unknown', 'detail': {'level': ctx['level'], 'pairs_removed': unknown_line}})
    if chosen is None:
        return tag([{'code': 'no_eligible_pair', 'detail': 'every candidate pair was removed by a filter'}] + reasons, 'routed')
    reasons += pair_reasons(ctx, arm=chosen['arm'], instance=chosen['instance'], pace=chosen['pace'],
                            eligibility_reasons=row['eligibility_reasons'], requested_only=row['requested_only_independence'])
    if conflicts:
        reasons.append({'code': 'directive_conflict', 'detail': [c['avoid'] + c['overridden'] for c in conflicts]})
    return tag(reasons, 'routed')


def pair_reasons(ctx, *, arm, instance, pace, eligibility_reasons, requested_only):
    """The judgment rules that depend on one (arm, account) pair; the routed pair and the final pair share them.

    Each reason is {code, detail}. `informational` reasons keep the target.
    """
    level, maker, failures = ctx['level'], ctx['maker'], ctx['failures']
    reasons = [{'code': code, 'detail': arm} for code in eligibility_reasons]
    if pace != 'on_track':
        reasons.append({'code': 'pace_not_on_track', 'detail': pace})
    events = failures['instances'].get(instance, {}) if failures['state'] == 'observed' else {}
    if events.get('auth_config'):
        reasons.append({'code': 'auth_config_events', 'detail': {'instance': instance, 'count': events['auth_config']}})
    if level and maker:
        if maker['provenance'] == 'unknown':
            reasons.append({'code': 'independence_maker_unknown', 'detail': maker.get('issue')})
        if requested_only:
            reasons.append({'code': 'independence_requested_only', 'detail': level})
        if maker['provenance'] == 'caller_claim':
            reasons.append({'code': 'independence_caller_claim', 'detail': 'maker vendor and model are the caller\'s claim'})
    if ctx['review'] and not maker:
        reasons.append({'code': 'review_without_maker', 'detail': 'independence cannot be checked'})
    for newer in ctx['freshness'].get('newer_ga_not_in_pack', []):
        if newer['pack_model'] == arm['model']:
            reasons.append({'code': 'newer_ga_model_not_in_pack', 'informational': True, 'detail': newer})
    return reasons
