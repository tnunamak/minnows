"""Settings for the unfinished-business pipeline.

One setting decides where everything personal lives: the data folder,
`$UNFINISHED_HOME` (default ~/Documents/unfinished). It holds the ledger
(LEDGER.md, ledger.jsonl, evidence/, decisions.jsonl) and `state/` (the working
database, the decision-model cache, logs). Code never writes anywhere else.

Optional `<data>/config.json` overrides the defaults below. Source paths and
owner-specific heuristics belong there, not in this repo.
"""
import json
import os
import shlex
import subprocess
from pathlib import Path

DATA = Path(os.environ.get("UNFINISHED_HOME", "~/Documents/unfinished")).expanduser()
STATE = DATA / "state"
EVIDENCE = DATA / "evidence"
WORK_DB = STATE / "work.sqlite3"
JEV_CACHE = STATE / "jev-cache.sqlite3"
CONVO_LEDGER = Path(os.environ.get(
    "CONVO_LEDGER", "~/.local/share/minnows/convo/ledger.sqlite3")).expanduser()

DEFAULTS = {
    # How prompts refer to the person whose sessions are mined.
    "owner_name": "the owner",
    # Claude models for extraction, adjudication and checking (headless `claude -p`).
    "extract_model": "haiku",
    "adjudicate_model": "claude-sonnet-5-5",
    "resolve_model": "claude-sonnet-5-5",
    # Optional CLAUDE_CONFIG_DIR for those calls (an account or API-key profile).
    "claude_config_dir": None,
    # Shell commands that print a secret, by env name, used when the env var is unset.
    "secret_commands": {},
    # Window, in days, for sessions and threads.
    "window_days": 30,
    # Extra regexes for user turns that software or another agent wrote.
    "automated_prefixes": [],
    # Extra line prefixes that mark agent text relayed into the owner's turn.
    "relayed_prefixes": [],
    # Item sources. All optional; empty lists skip the source.
    "waspflow_homes": ["~/.local/state/waspflow"],
    "inbox_dirs": [],          # [{"dir", "project", "triage_file"?, "exclude_regex"?}]
    "misses_files": [],        # markdown tables "| # | date | gap | impact | workaround | status |"
    "open_section_files": [],  # [{"path", "project"}]: bullets under "## Open"
    "repo_roots": ["~/code"],  # git checkouts scanned for unpushed work and PR repo names
    "github": True,            # open PRs and review requests via `gh search prs`
    "t3": True,                # T3 Code threads via `t3code`
}


def load():
    cfg = dict(DEFAULTS)
    p = DATA / "config.json"
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


CFG = load()


def owner():
    return CFG["owner_name"]


def secret(name):
    """Env var first, then the configured command. Never logged."""
    if os.environ.get(name):
        return os.environ[name]
    cmd = CFG["secret_commands"].get(name)
    if not cmd:
        raise RuntimeError(f"{name} is not set and no secret_commands entry exists in {DATA / 'config.json'}")
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=True).stdout.strip()


def claude_env():
    env = dict(os.environ)
    if CFG.get("claude_config_dir"):
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(CFG["claude_config_dir"])
    return env


def ensure_dirs():
    STATE.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)


def since_default(days=None):
    """ISO date `window_days` (or `days`) ago, the default lower bound for sessions."""
    import datetime as dt
    return (dt.date.today() - dt.timedelta(days=days if days is not None else CFG["window_days"])).isoformat()
