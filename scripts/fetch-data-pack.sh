#!/usr/bin/env bash
# fetch-data-pack.sh — download a minnows data pack by name (latest or version).
#
# Usage:
#   ./scripts/fetch-data-pack.sh <pack> [vX.Y.Z|data-<pack>-vX.Y.Z]
#   ./scripts/fetch-data-pack.sh model-catalog
#   ./scripts/fetch-data-pack.sh model-catalog v0.1.0
#
# Env:
#   MINNOWS_REPO   default tnunamak/minnows
#   DEST           default ./<pack> (extracted directory name)
#   INDEX_URL      override index (default: main data/index.json)
set -euo pipefail

PACK="${1:-}"
VER="${2:-}"
REPO="${MINNOWS_REPO:-tnunamak/minnows}"
INDEX_URL="${INDEX_URL:-https://raw.githubusercontent.com/${REPO}/main/data/index.json}"

if [[ -z "$PACK" || "$PACK" == "-h" || "$PACK" == "--help" ]]; then
  cat <<'EOF'
Usage: fetch-data-pack.sh <pack> [version]

  pack      e.g. model-catalog
  version   optional: v0.1.0 or data-model-catalog-v0.1.0
            omit to use latest_tag from data/index.json on main

Downloads the release tarball and extracts it into ./<pack>/ (or $DEST).
EOF
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl required" >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq required" >&2
  exit 1
fi

if [[ -n "$VER" ]]; then
  if [[ "$VER" == data-* ]]; then
    TAG="$VER"
  elif [[ "$VER" == v* ]]; then
    TAG="data-${PACK}-${VER}"
  else
    TAG="data-${PACK}-v${VER}"
  fi
  TARBALL="https://github.com/${REPO}/releases/download/${TAG}/${TAG}.tar.gz"
else
  echo "Resolving latest tag for pack '$PACK' from index…" >&2
  INDEX="$(curl -fsSL "$INDEX_URL")"
  TAG="$(jq -r --arg p "$PACK" '.packs[$p].latest_tag // empty' <<<"$INDEX")"
  TARBALL="$(jq -r --arg p "$PACK" '.packs[$p].tarball // empty' <<<"$INDEX")"
  if [[ -z "$TAG" || -z "$TARBALL" || "$TARBALL" == "null" ]]; then
    echo "Pack '$PACK' not found in $INDEX_URL" >&2
    echo "Known packs:" >&2
    jq -r '.packs | keys[]' <<<"$INDEX" >&2 || true
    exit 1
  fi
fi

DEST="${DEST:-$PWD/$PACK}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Fetching $TARBALL" >&2
if ! curl -fsSL -L "$TARBALL" -o "$TMP/pack.tar.gz"; then
  echo "Download failed. If this tag was not released yet, create it with:" >&2
  echo "  ./scripts/release-data-pack.sh $PACK ${TAG#data-${PACK}-v}" >&2
  exit 1
fi

mkdir -p "$TMP/out"
tar -xzf "$TMP/pack.tar.gz" -C "$TMP/out"
# tarball contains <pack>/...
if [[ -d "$TMP/out/$PACK" ]]; then
  rm -rf "$DEST"
  mkdir -p "$(dirname "$DEST")"
  mv "$TMP/out/$PACK" "$DEST"
else
  # flat fallback
  rm -rf "$DEST"
  mkdir -p "$DEST"
  mv "$TMP/out"/* "$DEST/" 2>/dev/null || true
fi

echo "Installed pack '$PACK' ($TAG) → $DEST" >&2
if [[ -f "$DEST/pack.json" ]]; then
  jq -r '"tag=" + .tag + " files=" + (.files|length|tostring)' "$DEST/pack.json" >&2
fi
