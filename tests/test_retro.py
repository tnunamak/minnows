"""The retro section of `calibrate`: unique tasks, closed outcomes and rework by (op, effort, arm), for any arm."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools' / 'model-policy-ops' / 'lib'))
import calibrate  # noqa: E402

FREE = {'provider': 'opencode', 'model': 'opencode/deepseek-v4.1-flash-free', 'effort': 'high'}
LUNA = {'provider': 'codex', 'model': 'gpt-6-luna', 'effort': 'high'}
OP = 'implement.oracle-bounded'


def receipt(decision, task, arm, op=OP):
    row = {'decision_id': decision, 'request': {'op': op}, 'selection': {'arm': arm}}
    if task:
        row['task_link'] = {'v': 1, 'consumer_task_id': task, 'workflow_id': 'w', 'stage_id': 's', 'role': 'maker'}
    return row


def close(decision, outcome='accepted', check='oracle', repairs=0, close_id=None, supersedes=None):
    return {'kind': 'close', 'close_id': close_id or f'close-{decision}', 'decision_id': decision, 'outcome': outcome,
            'check': check, 'repairs': repairs, 'supersedes': supersedes}


def followup(decision, finding='no_rework_found'):
    return {'kind': 'followup', 'followup_id': f'fu-{decision}', 'decision_id': decision, 'finding': finding,
            'close_id': f'close-{decision}'}


def cohort(report, arm):
    return next(c for c in report['cohorts'] if c['arm'] == arm)


def test_retries_and_repairs_do_not_inflate_unique_tasks():
    receipts = [receipt('a1', 'task-a', FREE), receipt('a2', 'task-a', FREE), receipt('a3', 'task-a', LUNA),
                receipt('b1', 'task-b', FREE), receipt('c1', None, FREE), receipt('d1', 'task-d', FREE, op=None)]
    rows = [close('a1', 'accepted_after_repair', repairs=1), close('a2'), close('b1', 'rejected', repairs=None)]
    report = calibrate.retro(receipts, rows)
    free = cohort(report, FREE)
    assert free['unique_tasks'] == 2 and free['closed_tasks'] == 2
    assert free['by_state'] == {'accepted_after_repair': 1, 'rejected': 1}
    assert free['repairs_reported_sum'] == 1 and free['repairs_unknown'] == 1
    assert report['extra_decisions_in_linked_tasks_excluded'] == 2 and report['unlinked_decisions_not_counted'] == 1
    assert all(c['arm'] != LUNA for c in report['cohorts']), 'a paid retry never creates a Luna cohort task'


def test_unclosed_and_non_verdict_tasks_are_not_closed_outcomes():
    receipts = [receipt('a', 'task-a', FREE), receipt('b', 'task-b', FREE), receipt('c', 'task-c', FREE)]
    report = calibrate.retro(receipts, [close('b', 'abandoned_by_choice', check='none')])
    free = cohort(report, FREE)
    assert free['unique_tasks'] == 3 and free['closed_tasks'] == 0
    assert free['by_state'] == {'unclosed': 2, 'not_a_verdict': 1}


def test_rework_missingness_is_distinct_from_no_rework_and_independent_checks_are_counted():
    receipts = [receipt(name, f'task-{name}', FREE) for name in 'abc']
    rows = [close('a'), close('b', check='parent_claim'), close('c'), followup('a'), followup('c', 'revert')]
    free = cohort(calibrate.retro(receipts, rows), FREE)
    assert free['rework'] == {'none_found': 1, 'found': 1, 'unchecked': 1}
    assert free['closed_with_independent_check'] == 2


def test_comparison_needs_a_closed_task_of_another_arm_at_the_same_op_and_effort():
    receipts = [receipt('f1', 'task-f', FREE), receipt('l1', 'task-l', LUNA), receipt('l2', 'task-m', LUNA),
                receipt('h1', 'task-h', dict(LUNA, effort='medium'))]
    rows = [close('f1'), close('h1')]
    report = calibrate.retro(receipts, rows)
    assert cohort(report, FREE)['comparison']['state'] == 'unmatched'
    rows.append(close('l1'))
    report = calibrate.retro(receipts, rows)
    matched = cohort(report, FREE)['comparison']
    assert matched['state'] == 'matched' and matched['comparators'] == [{'arm': LUNA, 'closed_tasks': 1}]
    assert cohort(report, LUNA)['comparison']['comparators'] == [{'arm': FREE, 'closed_tasks': 1}]


def test_threshold_signal_is_deterministic_and_steps_every_fifty_closed_tasks():
    def ledger(count):
        receipts = [receipt(f'd{n}', f't{n}', FREE) for n in range(count)]
        return receipts, [close(f'd{n}') for n in range(count)]

    assert calibrate.retro(*ledger(49))['signals'] == []
    first = calibrate.retro(*ledger(50))['signals']
    assert first == [{'key': f'retro:v1:{OP}:high:opencode/opencode/deepseek-v4.1-flash-free:50', 'closed_tasks': 50,
                      'threshold': 50, 'comparison': 'unmatched'}]
    assert calibrate.retro(*ledger(50))['signals'] == first
    assert calibrate.retro(*ledger(99))['signals'][0]['key'].endswith(':50')
    assert calibrate.retro(*ledger(100))['signals'][0]['key'].endswith(':100')
