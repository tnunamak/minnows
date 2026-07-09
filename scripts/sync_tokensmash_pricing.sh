#!/usr/bin/env bash
# Sync model-catalog pricing tables into tokensmash's vendored data/pricing.
# Usage: ./scripts/sync_tokensmash_pricing.sh [/path/to/tokensmash]
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKENSMASH="${1:-${TOKENSMASH_ROOT:-$HOME/code/tokensmash}}"
SRC="$REPO_ROOT/data/model-catalog/pricing"
DEST="$TOKENSMASH/src/tokensmash/data/pricing"
[[ -d "$SRC" ]] || { echo "missing $SRC" >&2; exit 1; }
[[ -d "$DEST" ]] || { echo "missing tokensmash pricing dir: $DEST" >&2; exit 1; }

CATALOG_TAG="$(python3 -c "import json; print(json.load(open('$REPO_ROOT/data/model-catalog/pack.json'))['tag'])")"
STAMP="$(date -u +%Y-%m-%d)"

python3 - "$SRC" "$DEST" "$CATALOG_TAG" "$STAMP" <<'PY'
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
tag = sys.argv[3]
stamp = sys.argv[4]
files = [
    "anthropic-api-2026-07.json",
    "openai-api-2026-07.json",
    "codex-credits-2026-07.json",
    "xai-api-2026-07.json",
    "google-api-2026-07.json",
]
for name in files:
    p = src / name
    if not p.is_file():
        print(f"skip missing {name}")
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    data.pop("$schema", None)
    note = data.get("notes") or ""
    suffix = f" | vendored_from: {tag} ({stamp})"
    if "vendored_from:" not in note:
        data["notes"] = (note + suffix).strip(" |")
    out = dest / name
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"synced {name} -> {out}")
for stale in (
    "anthropic-api-2026-06.json",
    "openai-api-2026-06.json",
    "codex-credits-2026-06.json",
):
    sp = dest / stale
    if sp.is_file():
        sp.unlink()
        print(f"removed stale {stale}")
print(f"tokensmash pricing sync complete (from {tag})")
PY
