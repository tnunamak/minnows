#!/usr/bin/env python3
"""Harvest commit messages written by named authorities on commit-writing craft.

Stronger provenance than mine_exemplars.py. That one takes "merged into a
project with a good review culture" as the label. This takes "written by a
specific person whose commit-writing is independently cited as exemplary" --
attributable to a named human, and vouched for by a reputation that predates and
is independent of this experiment.

The authorities come from ai/research/writing-craft/, which already ran this
search: antirez (Redis, subject of a whole corpus entry on dense synopsis),
Tim Pope (tpope, whose 2008 post is the canonical seven-rule commit guide),
Chris Beams (whose 2014 post is the other canonical one), Julia Evans, Simon
Tatham (whose PuTTY/git commit discipline the corpus names), and Linus Torvalds.

Two properties make this class clean by construction rather than by my reading:

  1. Attributable. `git log --author` on the real repository, so authorship is a
     fact rather than an inference.
  2. Pre-LLM. Restricted to commits before 2023, so no candidate can have been
     machine-drafted. This matters more here than anywhere else in the harness:
     it is the only positive class in the whole tool whose cleanliness does not
     rest on somebody's judgement.

Requires the repos to be cloned locally (bare clones are fine and much smaller).

Usage:
  mine_authorities.py --clones ~/.tmp/authority-repos --out corpus/authorities
"""
import argparse
import pathlib
import re
import subprocess

# repo url -> author match strings (git log --author is a regex over name+email)
AUTHORITIES = [
    ("https://github.com/redis/redis.git", "redis",
     ["antirez", "Salvatore Sanfilippo"]),
    ("https://github.com/tpope/vim-fugitive.git", "fugitive",
     ["Tim Pope"]),
    ("https://github.com/tpope/vim-surround.git", "surround",
     ["Tim Pope"]),
    ("https://github.com/git/git.git", "git",
     ["Junio C Hamano", "Jeff King"]),
]

BEFORE = "2023-01-01"
MIN_BODY_WORDS = 45
MAX_BODY_WORDS = 400


def run(args, cwd=None):
    r = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=900)
    return r.stdout if r.returncode == 0 else None


def ensure_clone(url, name, clones):
    d = pathlib.Path(clones) / f"{name}.git"
    if d.exists():
        return d
    d.parent.mkdir(parents=True, exist_ok=True)
    print(f"  cloning {name} (bare, blobless)...", flush=True)
    ok = run(["git", "clone", "--bare", "--filter=blob:none", url, str(d)])
    return d if d.exists() else None


def harvest(repo_dir, authors, limit):
    """Commit messages with a real explanatory body, by these authors."""
    out = []
    for author in authors:
        raw = run(["git", "log", f"--author={author}", f"--before={BEFORE}",
                   "--no-merges", "-n", "400",
                   "--pretty=format:%H%x00%an%x00%B%x01"], cwd=str(repo_dir))
        if not raw:
            continue
        for rec in raw.split("\x01"):
            rec = rec.strip()
            if not rec:
                continue
            parts = rec.split("\x00")
            if len(parts) != 3:
                continue
            sha, an, msg = parts
            lines = msg.strip().splitlines()
            if len(lines) < 3:
                continue
            body = "\n".join(lines[1:]).strip()
            # Drop trailer-only bodies (Signed-off-by, Reviewed-by, ...).
            prose = "\n".join(l for l in body.splitlines()
                              if not re.match(r"^[A-Z][A-Za-z-]+-by:", l)).strip()
            w = len(prose.split())
            if not (MIN_BODY_WORDS <= w <= MAX_BODY_WORDS):
                continue
            out.append({"sha": sha[:9], "author": an, "words": w,
                        "text": msg.strip()})
            if len(out) >= limit:
                return out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clones", default=str(pathlib.Path.home() / ".tmp/authority-repos"))
    ap.add_argument("--out", default="corpus/authorities")
    ap.add_argument("--per-repo", type=int, default=10)
    a = ap.parse_args()

    outdir = pathlib.Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    total = 0
    for url, name, authors in AUTHORITIES:
        d = ensure_clone(url, name, a.clones)
        if not d:
            print(f"{name:12} clone failed, skipping")
            continue
        rows = harvest(d, authors, a.per_repo)
        print(f"{name:12} {len(rows):2} usable  ({', '.join(authors)})")
        for r in rows:
            (outdir / f"{name}-{r['sha']}.txt").write_text(r["text"])
            total += 1
    print(f"\nharvested {total} authority-written commit messages -> {outdir}")
    print(f"provenance: git authorship, pre-{BEFORE}, explanatory body present")


if __name__ == "__main__":
    main()
