"""Per-user overlay on the pack's `routing` object.

The pack holds evidence policy. A user may tune only the parameters in `TUNABLE` (their own thresholds and
observation limits) from `~/.config/model-policy/user-policy.json`: `{"schema_version": 1, "routing": {...}}`.
Each key replaces the pack's value whole. The caller checks the merged policy with the pack validator.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

SCHEMA_VERSION = 1
TUNABLE = ('quota_substitution', 'quota_bands', 'drain_accounts', 'observation_max_age_hours', 'observation_window_days')


def default_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'model-policy/user-policy.json'


def read(path, required=False):
    """(routing, info), or (None, None) when no file exists. Any other problem is a ValueError.

    With `required` (an explicitly named path), a missing file is an error, so a typo cannot silently turn the
    overlay off.

    A symlink is followed, because a user may deploy config from a dotfiles repo. The target must exist, be a
    regular file owned by the current user, and not be writable by group or world.
    """
    given = os.path.abspath(path)
    resolved = os.path.realpath(path)
    try:
        os.lstat(given)
    except (FileNotFoundError, NotADirectoryError):
        if required:
            raise ValueError(f'user policy {given}: no such file')
        return None, None
    except OSError as error:
        raise ValueError(f'user policy {given}: cannot inspect: {error.strerror}') from None
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError(f'user policy {given}: cannot read {resolved}: {error.strerror}') from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ValueError(f'user policy {given}: {resolved} is not a regular file')
    with os.fdopen(fd, 'rb') as stream:
        if info.st_uid != os.geteuid():
            raise ValueError(f'user policy {given}: {resolved} must be owned by the current user')
        if info.st_mode & 0o022:
            raise ValueError(f'user policy {given}: {resolved} must not be group- or world-writable')
        raw = stream.read()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError(f'user policy {given}: not valid JSON') from None
    if not isinstance(value, dict) or set(value) != {'schema_version', 'routing'} or type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION:
        raise ValueError(f'user policy {given}: must be an object with exactly schema_version {SCHEMA_VERSION} and routing')
    routing = value['routing']
    if not isinstance(routing, dict):
        raise ValueError(f'user policy {given}: routing must be an object')
    unknown = sorted(set(routing) - set(TUNABLE))
    if unknown:
        raise ValueError(f'user policy {given}: routing keys {", ".join(unknown)} are not user-tunable (allowed: {", ".join(TUNABLE)})')
    return routing, {'path': given, 'resolved_path': resolved, 'sha256': hashlib.sha256(raw).hexdigest(), 'keys': sorted(routing)}


def merge(pack, routing):
    """The effective policy: the pack with each overlay key replacing the pack's value whole."""
    return pack | {'routing': (pack.get('routing') or {}) | routing}


def effective(pack, path):
    """(policy, info) for the pack and the overlay at `path` (None skips it). `info` is None when none applied."""
    routing, info = read(path) if path is not None else (None, None)
    if info is None:
        return pack, None
    return merge(pack, routing), info
