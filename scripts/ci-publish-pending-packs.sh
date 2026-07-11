#!/usr/bin/env bash
# ci-publish-pending-packs.sh — create GitHub releases for any pack.json tag
# that is not yet published. Idempotent; safe on every main push.
#
# Expects: gh authenticated (GH_TOKEN/GITHUB_TOKEN), jq, python3 + jsonschema.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v gh >/dev/null 2>&1 || { echo "gh required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq required" >&2; exit 1; }

python3 scripts/validate_data_pack.py --require-jsonschema

OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo tnunamak/minnows)"
DIST="$REPO_ROOT/dist"
mkdir -p "$DIST"

published=0

for pack_json in data/*/pack.json; do
  [[ -f "$pack_json" ]] || continue
  pack_dir="$(dirname "$pack_json")"
  name="$(basename "$pack_dir")"
  tag="$(jq -r '.tag // empty' "$pack_json")"
  [[ -n "$tag" && "$tag" != "null" ]] || continue

  if gh release view "$tag" --repo "$OWNER_REPO" >/dev/null 2>&1; then
    echo "ok  $name  release exists: $tag"
    continue
  fi

  echo "publish  $name  →  $tag"
  if [[ "$tag" =~ -v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    semver="${BASH_REMATCH[1]}"
  else
    echo "skip $name: tag $tag does not end in -vX.Y.Z" >&2
    continue
  fi

  tar_path="$DIST/${tag}.tar.gz"
  rm -f "$tar_path"
  tar -czf "$tar_path" -C "$REPO_ROOT/data" "$name"
  (
    cd "$DIST"
    sha256sum "$(basename "$tar_path")" >"${tag}.SHA256SUMS"
    cp "$REPO_ROOT/$pack_json" "${tag}-pack.json"
  )

  if ! git rev-parse "$tag" >/dev/null 2>&1; then
    git tag -a "$tag" -m "data pack $name v$semver"
  fi
  git push origin "refs/tags/$tag" 2>/dev/null || git push origin "refs/tags/$tag"

  notes="$DIST/${tag}-notes.md"
  {
    echo "## $name v$semver"
    echo
    echo "Automated data-pack release (CI). Download the \`.tar.gz\` asset."
    echo
    echo "- **Index:** https://github.com/${OWNER_REPO}/blob/main/data/index.json"
    echo
    if [[ -f "$pack_dir/README.md" ]]; then
      sed -n '/## Changelog/,$p' "$pack_dir/README.md" | head -40
    fi
  } >"$notes"

  if ! gh release view "$tag" --repo "$OWNER_REPO" >/dev/null 2>&1; then
    gh release create "$tag" \
      "$tar_path" \
      "$DIST/${tag}.SHA256SUMS" \
      "$DIST/${tag}-pack.json" \
      --repo "$OWNER_REPO" \
      --title "$name v$semver" \
      --notes-file "$notes"
  else
    gh release upload "$tag" \
      "$tar_path" "$DIST/${tag}.SHA256SUMS" "$DIST/${tag}-pack.json" \
      --repo "$OWNER_REPO" --clobber
  fi

  # Point index at this tag (preserve description if set)
  desc="$(jq -r --arg p "$name" '.packs[$p].description // empty' data/index.json)"
  [[ -n "$desc" ]] || desc="$name"
  tmp="$(mktemp)"
  jq --arg p "$name" --arg t "$tag" --arg r "$OWNER_REPO" --arg d "$desc" '
    .updated_at = (now | strftime("%Y-%m-%d")) |
    .repo = $r |
    .packs[$p] = ((.packs[$p] // {}) + {
      latest_tag: $t,
      description: $d,
      pack_json: ("https://raw.githubusercontent.com/\($r)/\($t)/data/\($p)/pack.json"),
      tarball: ("https://github.com/\($r)/releases/download/\($t)/\($t).tar.gz"),
      tree: ("https://github.com/\($r)/tree/\($t)/data/\($p)"),
      releases: ("https://github.com/\($r)/releases?q=data-\($p)&expanded=true"),
      readme: ("https://github.com/\($r)/blob/main/data/\($p)/README.md")
    })
  ' data/index.json >"$tmp"
  mv "$tmp" data/index.json

  published=$((published + 1))
  echo "released https://github.com/${OWNER_REPO}/releases/tag/${tag}"
done

if [[ "$published" -gt 0 ]] && ! git diff --quiet data/index.json 2>/dev/null; then
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git add data/index.json
  git commit -m "chore(data): sync index.json after pack release [skip ci]"
  git push origin HEAD
fi

echo "ci-publish-pending-packs: published=$published"
