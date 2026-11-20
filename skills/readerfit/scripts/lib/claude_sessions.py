"""
claude_sessions — shared parsing for Claude Code session logs.

Claude Code stores each session as a JSONL file at:
    ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
where <project-slug> is the cwd with '/' replaced by '-' (leading '-' kept).

This module is the ONE source of truth for:
  - locating project dirs and session files (cwd <-> slug, session-id lookup)
  - detecting compaction boundaries within a session file

Both `convo` (read transcripts) and `uncompact` (recover pre-compaction sessions)
depend on it. Keep it stdlib-only and side-effect-free so it vendors cleanly into
shipped skill folders (see minnows README: vendor-on-ship at the skill boundary).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# The marker Claude Code injects as a `user` message at a compaction boundary.
COMPACTION_MARKER = "continued from a previous conversation"


# --------------------------------------------------------------------------------------
# Locating projects and sessions
# --------------------------------------------------------------------------------------

def claude_project_roots() -> list[Path]:
    """All Claude Code `projects/` roots that may hold real sessions on this machine.

    Claude Code writes to a single config dir per invocation, but that dir is NOT always
    `~/.claude` — the official `CLAUDE_CONFIG_DIR` env var repoints it (e.g. a separate
    profile), and people also keep other `~/.claude*` dirs around (backups, alternate
    profiles) that can independently hold real `projects/`. Do not hardcode any specific
    alternate name: discover generically so a single-config-dir user sees identical
    behavior (one root, no cost) and a multi-profile user is not silently invisible to
    themselves.

    Order: default `~/.claude`, then `$CLAUDE_CONFIG_DIR` if set, then any other
    `~/.claude*` directory with a `projects/` subdir — deduplicated, existing only.
    """
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(p: Optional[Path]):
        if p is None:
            return
        projects = p / "projects"
        try:
            resolved = projects.resolve()
        except OSError:
            resolved = projects
        if resolved in seen:
            return
        if projects.exists():
            seen.add(resolved)
            roots.append(projects)

    add(CLAUDE_PROJECTS.parent)
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_dir:
        add(Path(env_dir).expanduser())
    home = Path.home()
    try:
        candidates = sorted(home.glob(".claude*"))
    except OSError:
        candidates = []
    for cand in candidates:
        if cand.is_dir():
            add(cand)
    return roots


def cwd_to_project_dirs(cwd: str) -> list[Path]:
    """Every project slug directory for `cwd`, across ALL discovered roots.

    A given project can have real sessions under more than one root at once (e.g. some
    written under the default `~/.claude` config, others under a `CLAUDE_CONFIG_DIR`-scoped
    profile) — callers that need full coverage (convo's scoped `list`/`grep`) must not stop
    at the first match. Order matches `claude_project_roots()`.
    """
    slug = cwd.replace("/", "-").lstrip("-")
    found: list[Path] = []
    for root in claude_project_roots():
        candidate = root / f"-{slug}"
        if candidate.exists():
            found.append(candidate)
            continue
        for d in root.iterdir():
            if d.name.replace("-", "/").lstrip("/") == cwd.lstrip("/"):
                found.append(d)
                break
    return found


def cwd_to_project_dir(cwd: str) -> Optional[Path]:
    """Convert a working directory path to ONE Claude projects slug directory.

    Single-result convenience for callers (e.g. uncompact) that only need any one match.
    Prefer `cwd_to_project_dirs` when full cross-root coverage matters.
    """
    dirs = cwd_to_project_dirs(cwd)
    return dirs[0] if dirs else None


def project_dir_to_cwd(project_dir: Path) -> str:
    """Best-effort decode a project slug back to a cwd path.

    Lossy (can't distinguish '-' in a dir name from a separator). Prefer reading
    the real `cwd` field out of a session's JSONL records when accuracy matters
    (see cwd_from_lines).
    """
    return "/" + project_dir.name.replace("-", "/").lstrip("/")


def find_project_dir_for_session(session_id: str) -> Optional[Path]:
    """Search all project dirs (across every discovered root) for <session_id>.jsonl."""
    for root in claude_project_roots():
        for project_dir in root.iterdir():
            if project_dir.is_dir() and (project_dir / f"{session_id}.jsonl").exists():
                return project_dir
    return None


def session_path_for(session_id: str) -> Optional[Path]:
    """Full path to a session file given its id, or None if not found."""
    pd = find_project_dir_for_session(session_id)
    return (pd / f"{session_id}.jsonl") if pd else None


def cwd_from_lines(lines: list[str]) -> Optional[str]:
    """Read the real cwd out of a session's JSONL records (authoritative).

    More reliable than decoding the project slug, which can't distinguish '-' in a
    dir name from a path separator.
    """
    for line in lines:
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("cwd"):
            return d["cwd"]
    return None


# --------------------------------------------------------------------------------------
# Compaction boundaries
# --------------------------------------------------------------------------------------

def parse_boundaries(lines: list[str]) -> list[dict]:
    """Find all compaction boundaries in a session's JSONL lines.

    A boundary is a `user` line whose `message.content` is a string containing the
    COMPACTION_MARKER (Claude Code's "continued from a previous conversation..." summary).
    A single file may contain several (the session compacted multiple times).

    Returns a list of dicts, in file order:
        {
          "boundary_line":  index of the marker line,
          "last_real_line": index of the last real user/assistant message BEFORE it
                            (or None if none found),
          "snippet":        short preview of the last assistant text before the boundary,
        }
    """
    boundaries: list[dict] = []
    for i, line in enumerate(lines):
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("type") != "user":
            continue
        content = d.get("message", {}).get("content", "")
        if not isinstance(content, str) or COMPACTION_MARKER not in content:
            continue

        # Last real user/assistant message before the boundary.
        last_real = None
        for j in range(i - 1, -1, -1):
            try:
                prev = json.loads(lines[j])
            except (json.JSONDecodeError, TypeError):
                continue
            if prev.get("type") not in ("user", "assistant"):
                continue
            if not prev.get("message", {}).get("content", ""):
                continue
            last_real = j
            break

        # Snippet from the last assistant text block before the boundary.
        snippet = ""
        for j in range(i - 1, -1, -1):
            try:
                prev = json.loads(lines[j])
            except (json.JSONDecodeError, TypeError):
                continue
            if prev.get("type") != "assistant":
                continue
            for block in prev.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    snippet = block["text"].strip()[:120].replace("\n", " ")
                    break
            if snippet:
                break

        boundaries.append({
            "boundary_line": i,
            "last_real_line": last_real,
            "snippet": snippet or "(no text snippet available)",
        })
    return boundaries
