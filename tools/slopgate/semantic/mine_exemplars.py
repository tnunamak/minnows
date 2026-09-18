#!/usr/bin/env python3
"""Harvest known-good writing automatically, instead of asking a human to label.

The problem this solves: thresholds set against examples I picked encode MY
taste. Owner labels are better but do not scale, and asking for them is the thing
we are trying to avoid.

The way out is that good examples already exist in public, pre-labelled by a
mechanism stronger than anyone's opinion: projects where a maintainer will reject
a bad description before it merges. ai/research/writing-craft/ already identified
which projects those are and, importantly, which ones do NOT qualify --
"broad-audience-dev-tools-eslint-and-homebrew-write-outsider-clear-prs-prettier-
and-recent-vscode-mostly-dont" is a negative finding worth honouring rather than
rediscovering.

So the ground truth here is PROVENANCE, not judgement:

  positive  a merged PR body from a project whose review culture is documented
            (in the corpus) to enforce description quality, written by a human
            account, before the LLM era where possible.

  negative  a PR body from the same domain that a maintainer actually asked to
            be rewritten, or a machine-generated draft.

The pre-2023 cutoff matters: a PR body merged in 2019 cannot be LLM-written, so
the positive class is clean by construction rather than by my reading of it.

Usage:
  mine_exemplars.py --out corpus/mined [--per-repo 8] [--before 2023-01-01]
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

# Sourced from ai/research/writing-craft/. Only projects the corpus found to
# ACTUALLY write causal, outsider-clear descriptions -- the same corpus entry
# explicitly found Prettier and recent VSCode do not, so they are excluded.
EXEMPLAR_REPOS = [
    "django/django",
    "eslint/eslint",
    "Homebrew/brew",
    "redis/redis",
    "postgres/postgres",
    "immich-app/immich",
    "home-assistant/core",
    "curl/curl",
]

BOT = re.compile(r"bot|dependabot|renovate|github-actions|\[bot\]", re.I)
BOILERPLATE = re.compile(
    r"^\s*(#+\s*)?(checklist|type of change|screenshots?|related issues?)\b", re.I | re.M)


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return None
    return r.stdout


def strip_template(body):
    """Drop template scaffolding so we measure authored prose, not the form."""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"^\s*-\s*\[[ xX]\].*$", "", body, flags=re.M)  # checkboxes
    return body.strip()


def authored_words(body):
    """Words outside code fences, tables, quotes and link-only lines."""
    b = re.sub(r"```.*?```", " ", body, flags=re.S)
    b = re.sub(r"^\s*[|>].*$", " ", b, flags=re.M)
    b = re.sub(r"^\s*#+.*$", " ", b, flags=re.M)
    return len(b.split())


def mine(repo, per_repo, before):
    """Fetch merged PRs from a date window via search, then filter for prose.

    The listing endpoint returns newest-first, so a pre-LLM window is
    unreachable through it without paginating years back. Search takes the date
    range directly.
    """
    out = []
    q = (f"repo:{repo}+is:pr+is:merged+created:2019-01-01..{before}")
    raw = gh(["api", f"search/issues?q={q}&per_page=100",
              "--jq", ".items[] | [.number, .user.login] | @tsv"])
    if not raw:
        return out
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        num, user = parts
        if BOT.search(user):
            continue
        body = gh(["api", f"repos/{repo}/pulls/{num}", "--jq", ".body"])
        if not body or body.strip() in ("", "null"):
            continue
        body = strip_template(body)
        w = authored_words(body)
        if not (90 <= w <= 500):
            continue
        if len(BOILERPLATE.findall(body)) >= 2:
            continue
        out.append({"repo": repo, "number": num, "user": user,
                    "created": "", "words": w, "body": body})
        if len(out) >= per_repo:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="corpus/mined")
    ap.add_argument("--per-repo", type=int, default=8)
    ap.add_argument("--before", default="2023-01-01",
                    help="only PRs created before this date (pre-LLM, so the "
                         "positive class is clean by construction)")
    a = ap.parse_args()

    outdir = pathlib.Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    total = 0
    for repo in EXEMPLAR_REPOS:
        rows = mine(repo, a.per_repo, a.before)
        print(f"{repo:26} {len(rows):2} usable", flush=True)
        for r in rows:
            slug = repo.replace("/", "_")
            (outdir / f"{slug}-{r['number']}.txt").write_text(r["body"])
            total += 1
    print(f"\nmined {total} exemplar PR bodies -> {outdir}")
    print("provenance: merged, human-authored, pre-LLM-era, from projects the "
          "corpus found to enforce description quality")


if __name__ == "__main__":
    main()
