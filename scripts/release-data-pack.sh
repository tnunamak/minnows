#!/usr/bin/env bash
# release-data-pack.sh — package a data pack and optionally create a GitHub release.
#
# Usage:
#   ./scripts/release-data-pack.sh <pack> <semver> [--push]
#   ./scripts/release-data-pack.sh model-catalog 0.1.0
#   ./scripts/release-data-pack.sh model-catalog 0.1.0 --push
#
# Without --push: writes dist/<tag>.tar.gz + SHA256SUMS and prints next steps.
# With --push: requires clean-enough git, tags, pushes tag, gh release create.
set -euo pipefail

PACK="${1:-}"
SEMVER="${2:-}"
PUSH=0
[[ "${3:-}" == "--push" ]] && PUSH=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "$PACK" || -z "$SEMVER" || "$PACK" == "-h" ]]; then
  cat <<'EOF'
Usage: release-data-pack.sh <pack> <semver> [--push]

  semver   e.g. 0.1.0 (with or without leading v)
  --push   git tag + push + gh release create with tarball assets
EOF
  exit 0
fi

SEMVER="${SEMVER#v}"
TAG="data-${PACK}-v${SEMVER}"
PACK_DIR="$REPO_ROOT/data/$PACK"
[[ -d "$PACK_DIR" ]] || { echo "missing $PACK_DIR" >&2; exit 1; }
[[ -f "$PACK_DIR/pack.json" ]] || { echo "missing pack.json" >&2; exit 1; }

# Keep pack.json tag field in sync
if command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  jq --arg t "$TAG" --arg d "$(date -u +%Y-%m-%d)" \
    '.tag = $t | .generated_at = $d' "$PACK_DIR/pack.json" >"$tmp"
  mv "$tmp" "$PACK_DIR/pack.json"
fi

DIST="$REPO_ROOT/dist"
mkdir -p "$DIST"
TAR="$DIST/${TAG}.tar.gz"
rm -f "$TAR"

# Archive as <pack>/...
tar -czf "$TAR" -C "$REPO_ROOT/data" "$PACK"
(
  cd "$DIST"
  sha256sum "$(basename "$TAR")" >"${TAG}.SHA256SUMS"
  cp "$PACK_DIR/pack.json" "${TAG}-pack.json"
)

echo "Built $TAR"
sha256sum "$TAR"

# Refresh data/index.json latest pointer
if command -v jq >/dev/null 2>&1 && [[ -f "$REPO_ROOT/data/index.json" ]]; then
  tmp="$(mktemp)"
  OWNER_REPO="$(git remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/]+/[^/]+)(\.git)?$#\1#' || echo tnunamak/minnows)"
  jq --arg p "$PACK" --arg t "$TAG" --arg r "$OWNER_REPO" '
    .updated_at = (now | strftime("%Y-%m-%d")) |
    .repo = $r |
    .packs[$p] = (.packs[$p] // {}) + {
      latest_tag: $t,
      pack_json: ("https://raw.githubusercontent.com/\($r)/\($t)/data/\($p)/pack.json"),
      tarball: ("https://github.com/\($r)/releases/download/\($t)/\($t).tar.gz"),
      tree: ("https://github.com/\($r)/tree/\($t)/data/\($p)"),
      releases: ("https://github.com/\($r)/releases?q=data-\($p)&expanded=true"),
      readme: ("https://github.com/\($r)/blob/main/data/\($p)/README.md")
    }
  ' "$REPO_ROOT/data/index.json" >"$tmp"
  mv "$tmp" "$REPO_ROOT/data/index.json"
  echo "Updated data/index.json → latest_tag=$TAG"
fi

if [[ "$PUSH" -ne 1 ]]; then
  cat <<EOF

Next steps (no --push):
  1. Commit data/ changes (pack.json tag, index.json, files).
  2. git tag $TAG && git push origin main $TAG
  3. gh release create $TAG dist/${TAG}.tar.gz dist/${TAG}.SHA256SUMS dist/${TAG}-pack.json \\
       --title "$PACK v$SEMVER" --notes-file data/$PACK/README.md

Or re-run with --push after committing.
EOF
  exit 0
fi

command -v gh >/dev/null 2>&1 || { echo "gh required for --push" >&2; exit 1; }

if ! git rev-parse "$TAG" >/dev/null 2>&1; then
  git tag -a "$TAG" -m "data pack $PACK v$SEMVER"
fi
git push origin "refs/tags/$TAG"
git push origin HEAD

NOTES="$DIST/${TAG}-notes.md"
{
  echo "## $PACK v$SEMVER"
  echo
  echo "Data pack release. Download the \`.tar.gz\` asset (no URL construction needed)."
  echo
  echo "- **pack.json** in assets or: \`data/$PACK/pack.json\` at tag \`$TAG\`"
  echo "- **Index:** https://github.com/tnunamak/minnows/blob/main/data/index.json"
  echo
  if [[ -f "$PACK_DIR/README.md" ]]; then
    # include changelog section if present
    sed -n '/## Changelog/,$p' "$PACK_DIR/README.md" | head -40
  fi
} >"$NOTES"

gh release create "$TAG" \
  "$TAR" \
  "$DIST/${TAG}.SHA256SUMS" \
  "$DIST/${TAG}-pack.json" \
  --title "$PACK v$SEMVER" \
  --notes-file "$NOTES" \
  || gh release upload "$TAG" \
    "$TAR" "$DIST/${TAG}.SHA256SUMS" "$DIST/${TAG}-pack.json" --clobber

echo "Released https://github.com/tnunamak/minnows/releases/tag/$TAG"
