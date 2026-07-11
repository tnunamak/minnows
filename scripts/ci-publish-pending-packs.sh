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
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

published=0

sync_index() {
  local name="$1" tag="$2" desc tmp
  desc="$(jq -r --arg p "$name" '.packs[$p].description // empty' data/index.json)"
  [[ -n "$desc" ]] || desc="$name"
  tmp="$(mktemp)"
  jq --arg p "$name" --arg t "$tag" --arg r "$OWNER_REPO" --arg d "$desc" '
    .updated_at = (now | strftime("%Y-%m-%d")) |
    .repo = $r |
    .packs[$p] = ((.packs[$p] // {}) + {
      latest_tag: $t, description: $d,
      pack_json: ("https://raw.githubusercontent.com/\($r)/\($t)/data/\($p)/pack.json"),
      tarball: ("https://github.com/\($r)/releases/download/\($t)/\($t).tar.gz"),
      tree: ("https://github.com/\($r)/tree/\($t)/data/\($p)"),
      releases: ("https://github.com/\($r)/releases?q=data-\($p)&expanded=true"),
      readme: ("https://github.com/\($r)/blob/main/data/\($p)/README.md")
    })
  ' data/index.json >"$tmp"
  mv "$tmp" data/index.json
}

for pack_json in data/*/pack.json; do
  [[ -f "$pack_json" ]] || continue
  pack_dir="$(dirname "$pack_json")"
  name="$(basename "$pack_dir")"
  tag="$(jq -r '.tag // empty' "$pack_json")"
  [[ -n "$tag" && "$tag" != "null" ]] || continue

  if gh release view "$tag" --repo "$OWNER_REPO" >/dev/null 2>&1; then
    echo "verify  $name  existing release: $tag"
    git cat-file -e "$tag:$pack_json" || { echo "tag $tag lacks $pack_json" >&2; exit 1; }
    git show "$tag:$pack_json" >"$DIST/${tag}-tag-pack.json"
    diff -u <(jq -S . "$pack_json") <(jq -S . "$DIST/${tag}-tag-pack.json") || { echo "working pack disagrees semantically with existing tag $tag" >&2; exit 1; }
    verify_dir="$(mktemp -d)"
    gh release download "$tag" --repo "$OWNER_REPO" --dir "$verify_dir" --pattern "${tag}.tar.gz" --pattern "${tag}.SHA256SUMS" --pattern "${tag}-pack.json"
    (cd "$verify_dir" && sha256sum -c "${tag}.SHA256SUMS") || { echo "release checksum failed for $tag" >&2; exit 1; }
    diff -u <(jq -S . "$DIST/${tag}-tag-pack.json") <(jq -S . "$verify_dir/${tag}-pack.json") || { echo "pack asset disagrees with tag $tag" >&2; exit 1; }
    tar -xzf "$verify_dir/${tag}.tar.gz" -C "$verify_dir"
    diff -u <(jq -S . "$DIST/${tag}-tag-pack.json") <(jq -S . "$verify_dir/$name/pack.json") || { echo "tar pack.json disagrees with tag $tag" >&2; exit 1; }
    git ls-tree -r --name-only "$tag" "$pack_dir" | sed 's#^data/##' | sort >"$verify_dir/expected-members"
    tar -tzf "$verify_dir/${tag}.tar.gz" | sed '/\/$/d' | sort >"$verify_dir/actual-members"
    cmp "$verify_dir/expected-members" "$verify_dir/actual-members" || { echo "tar has missing or undeclared members for $tag" >&2; exit 1; }
    while IFS= read -r member; do
      rel="${member#"$name/"}"
      [[ "$rel" == "pack.json" ]] && continue
      git show "$tag:$pack_dir/$rel" >"$verify_dir/tag-file"
      cmp "$pack_dir/$rel" "$verify_dir/tag-file" || { echo "working file $pack_dir/$rel drifted without a tag bump" >&2; exit 1; }
      cmp "$verify_dir/$name/$rel" "$verify_dir/tag-file" || { echo "tar asset file $rel disagrees with tag $tag" >&2; exit 1; }
    done <"$verify_dir/expected-members"
    rm -rf "$verify_dir"
    sync_index "$name" "$tag"
    continue
  fi

  echo "publish  $name  →  $tag"
  if [[ "$tag" =~ -v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    semver="${BASH_REMATCH[1]}"
  else
    echo "skip $name: tag $tag does not end in -vX.Y.Z" >&2
    continue
  fi
  if git rev-parse "$tag" >/dev/null 2>&1 && [[ "$(git rev-list -n1 "$tag")" != "$(git rev-parse HEAD)" ]]; then
    echo "refusing stale unpublished tag $tag: tag commit != current HEAD" >&2
    exit 1
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

  gh release create "$tag" \
    "$tar_path" \
    "$DIST/${tag}.SHA256SUMS" \
    "$DIST/${tag}-pack.json" \
    --repo "$OWNER_REPO" \
    --title "$name v$semver" \
    --notes-file "$notes"
  post_dir="$(mktemp -d)"
  gh release download "$tag" --repo "$OWNER_REPO" --dir "$post_dir"
  cmp "$tar_path" "$post_dir/${tag}.tar.gz" && cmp "$DIST/${tag}.SHA256SUMS" "$post_dir/${tag}.SHA256SUMS" && cmp "$DIST/${tag}-pack.json" "$post_dir/${tag}-pack.json" \
    || { echo "post-publish artifact verification failed for $tag" >&2; exit 1; }
  rm -rf "$post_dir"

  sync_index "$name" "$tag"

  published=$((published + 1))
  echo "released https://github.com/${OWNER_REPO}/releases/tag/${tag}"
done

if ! git diff --quiet data/index.json 2>/dev/null; then
  git add data/index.json
  git commit -m "chore(data): sync index.json after pack release [skip ci]"
  git push origin HEAD
fi

echo "ci-publish-pending-packs: published=$published"
