"""Tests read the full receipt: route/resolve print one summary line unless --json (or --brief) is given."""


def full_record(args):
    args = list(map(str, args))
    if args[:1] and args[0] in ('route', 'resolve') and '--brief' not in args and '--json' not in args:
        args.append('--json')
    return args
