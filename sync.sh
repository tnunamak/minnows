#!/usr/bin/env bash
# sync.sh — build the shipped, self-contained skill folders from tools/.
#
# A skill is copied OUT of this repo (into ~/.{claude,codex,gemini}/skills/ or a
# marketplace), so it can't rely on the repo-root lib/ being importable. We VENDOR:
# each shipped skill gets its own executable, shared lib/, and optional tool-private
# lib/, so it runs self-contained anywhere. (See README: vendor-on-ship.)
#
# Run this after editing any tool, SKILL.md, or lib/. install.sh runs it for you.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

# Which tools ship a skill? A tool opts in by having a SKILL.md.
shipped=()
for tool_dir in tools/*/; do
  name="$(basename "$tool_dir")"
  [[ -f "$tool_dir/SKILL.md" ]] || continue
  shipped+=("$name")
done

echo "Vendoring ${#shipped[@]} skill(s): ${shipped[*]}"

for name in "${shipped[@]}"; do
  src="tools/$name"
  dst="skills/$name"
  rm -rf "$dst"
  mkdir -p "$dst/scripts/lib"

  # SKILL.md at the skill root (where agents look for it)
  cp "$src/SKILL.md" "$dst/SKILL.md"

  # the executable -> scripts/<name>
  cp "$src/$name" "$dst/scripts/$name"
  chmod +x "$dst/scripts/$name"

  # vendor the shared lib next to the script so its bootstrap finds ./lib/
  # (exclude __pycache__ — bytecode is interpreter-specific and shouldn't ship)
  for f in lib/*.py; do cp "$f" "$dst/scripts/lib/"; done

  # A tool may also own a private lib/ for state or parsing that does not belong in
  # every other shipped skill. It is vendored only with that tool.
  if [[ -d "$src/lib" ]]; then
    for f in "$src"/lib/*.py; do
      [[ -e "$f" ]] && cp "$f" "$dst/scripts/lib/"
    done
  fi

  # optional supporting dirs (references/, assets/) if a tool has them
  for extra in references assets; do
    [[ -d "$src/$extra" ]] && cp -R "$src/$extra" "$dst/$extra"
  done

  # Runtime dirs a tool's executable resolves RELATIVE TO ITSELF must land next
  # to the vendored script, not at the skill root. slopgate looks for
  # detector/cli.mjs and semantic/jevgate.py beside its own path; without these
  # the vendored copy finds neither layer and silently reports every document
  # clean, which is worse than failing.
  for runtime in detector semantic; do
    if [[ -d "$src/$runtime" ]]; then
      cp -R "$src/$runtime" "$dst/scripts/$runtime"
      rm -rf "$dst/scripts/$runtime/__pycache__"
      # Corpora and recorded runs are development evidence, not shipped payload.
      rm -rf "$dst/scripts/$runtime"/{corpus,loop_corpus*,demo_corpus,site_*,art_demo,loop_revisions,rejected*}
      rm -f  "$dst/scripts/$runtime"/{feature_matrix.json,pairwise_judgments.json,*_results.json,decoy_scores.json,calibration.json}
    fi
  done

  echo "  ✓ $name -> $dst (self-contained)"
done

echo "Done. skills/ now holds shippable, self-contained skill folders."
