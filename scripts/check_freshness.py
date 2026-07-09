#!/usr/bin/env python3
"""Warn/fail when load-bearing catalog tables exceed max age."""
from __future__ import annotations
import argparse, json, sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "data" / "model-catalog"
LOAD_BEARING = [
    "pricing/anthropic-api-2026-07.json",
    "pricing/openai-api-2026-07.json",
    "pricing/codex-credits-2026-07.json",
    "pricing/xai-api-2026-07.json",
    "capabilities/effort-surfaces-2026-07.json",
    # boards (FRESHNESS.md monthly tier)
    "performance/artificial-analysis-2026-07.json",
    "performance/terminal-bench-2026-07.json",
    "performance/swe-bench-2026-07.json",
    "performance/arcprize-gpt-5-6-2026-07.json",
]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=int, default=45)
    ap.add_argument("--fail", action="store_true", help="exit 1 on stale")
    args = ap.parse_args()
    today = date.today()
    stale = []
    for rel in LOAD_BEARING:
        path = CATALOG / rel
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
