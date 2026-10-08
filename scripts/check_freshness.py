#!/usr/bin/env python3
"""Warn/fail when load-bearing catalog tables exceed max age."""
from __future__ import annotations
import argparse, json, sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "data" / "model-catalog"
DECISION = REPO / "data" / "decision-model-catalog"
LOAD_BEARING = [
    "pricing/anthropic-api-2026-07.json",
    "pricing/openai-api-2026-07.json",
    "pricing/codex-credits-2026-07.json",
    "pricing/xai-api-2026-07.json",
    "capabilities/effort-surfaces-2026-07.json",
]
# Boards (FRESHNESS.md monthly tier), matched by source URL. A board is fresh when its
# NEWEST snapshot is: a dated performance file that cites one of its sources, including a
# re-read that found no new rows. Superseded snapshots keep their old retrieved_at forever
# (rules: never rewrite an old dated snapshot), so they are not checked one by one.
BOARDS = {
    "Artificial Analysis": ("artificialanalysis.ai",),
    "Terminal-Bench": ("tbench.ai",),
    "SEAL SWE-Bench Pro": ("scale.com/leaderboard/swe_bench_pro",),
    "ARC Prize": ("arcprize.org",),
}


def cited_source_ids(doc: dict) -> set[str]:
    ids = set(doc.get("source_ids") or [])
    for key in ("scores", "claims"):
        ids.update(r["source_id"] for r in doc.get(key) or [] if isinstance(r, dict) and r.get("source_id"))
    return ids


def board_snapshots() -> list[tuple[Path, str]]:
    """Newest catalog performance file per tracked board; a missing board is reported."""
    sources = json.loads((CATALOG / "SOURCES.json").read_text())["sources"]
    url_of = {s["id"]: s.get("url", "") for s in sources}
    newest: dict[str, tuple[str, Path]] = {}
    for path in sorted((CATALOG / "performance").glob("*.json")):
        doc = json.loads(path.read_text())
        urls = [url_of.get(i, "") for i in cited_source_ids(doc)]
        for board, needles in BOARDS.items():
            if any(n in u for u in urls for n in needles):
                ra = doc.get("retrieved_at") or ""
                if board not in newest or ra > newest[board][0]:
                    newest[board] = (ra, path)
    out = []
    for board in BOARDS:
        if board in newest:
            path = newest[board][1]
            out.append((path, f"performance/{path.name} (newest {board} snapshot)"))
        else:
            out.append((CATALOG / "performance" / f"<no {board} snapshot>", f"board {board}: no snapshot"))
    return out


def decision_files() -> list[tuple[Path, str]]:
    """decision-model-catalog: every pricing table, plus every performance document that
    carries at least one live-board row (third_party_board). Launch tables are n/a."""
    out: list[tuple[Path, str]] = []
    for path in sorted((DECISION / "pricing").glob("*.json")):
        out.append((path, f"decision-model-catalog/pricing/{path.name}"))
    for path in sorted((DECISION / "performance").glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            doc = None  # unreadable: let the checker below report it
        rows = (doc or {}).get("scores") or []
        if doc is None or any(isinstance(r, dict) and r.get("source_type") == "third_party_board" for r in rows):
            out.append((path, f"decision-model-catalog/performance/{path.name}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=int, default=45)
    ap.add_argument("--fail", action="store_true", help="exit 1 on stale")
    args = ap.parse_args()
    today = date.today()
    stale = []
    targets = [(CATALOG / rel, rel) for rel in LOAD_BEARING] + board_snapshots() + decision_files()
    for path, rel in targets:
        if not path.is_file():
            print(f"missing {rel}", file=sys.stderr)
            stale.append(rel)
            continue
        data = json.loads(path.read_text())
        ra = data.get("retrieved_at")
        if not ra:
            print(f"{rel}: no retrieved_at", file=sys.stderr)
            stale.append(rel)
            continue
        d = date.fromisoformat(ra)
        age = (today - d).days
        status = "STALE" if age > args.max_age_days else "ok"
        print(f"{status:5} {rel} retrieved_at={ra} age_days={age}")
        if age > args.max_age_days:
            stale.append(rel)
    if stale and args.fail:
        print(f"check_freshness: {len(stale)} stale", file=sys.stderr)
        return 1
    print("check_freshness: done")
    return 0

if __name__ == "__main__":
    sys.exit(main())
