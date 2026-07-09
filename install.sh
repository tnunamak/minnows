#!/usr/bin/env bash
# install.sh — wire minnows into this machine.
#
#   1. Build self-contained skill folders   (sync.sh: vendor lib into skills/*)
#   2. Symlink shipped skills into each agent (~/.claude, ~/.codex, ~/.gemini)
#   3. Symlink every tool executable onto PATH (~/.local/bin/<tool>)
#
# Idempotent: safe to run repeatedly. dotfiles' setup.sh calls this after pulling.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

# 1. Build shipped skill folders from tools/.
bash "$REPO/sync.sh"

# 2. Symlink shipped skills into each agent's skills dir.
echo "Symlinking skills into agents..."
for agent_dir in "$HOME/.claude" "$HOME/.codex" "$HOME/.gemini"; do
  mkdir -p "$agent_dir/skills"
  for skill in "$REPO"/skills/*/; do
    [[ -d "$skill" ]] || continue
    sname="$(basename "$skill")"
    # rm -rf first: `ln -sfn` nests INTO an existing real directory instead of
    # replacing it (e.g. an older standalone copy of this skill), which silently
    # shadows the new symlink. Remove any existing entry, then link cleanly.
    rm -rf "$agent_dir/skills/$sname"
    ln -sfn "${skill%/}" "$agent_dir/skills/$sname"
    echo "  ✓ $agent_dir/skills/$sname"
  done
done

# 3. Symlink tool executables onto PATH. Every tool is a CLI (skill optional).
echo "Symlinking tool executables onto PATH..."
mkdir -p "$HOME/.local/bin"
for tool_dir in "$REPO"/tools/*/; do
  name="$(basename "$tool_dir")"
  exe="$tool_dir$name"
  [[ -f "$exe" ]] || continue
  chmod +x "$exe"
  ln -sfn "$exe" "$HOME/.local/bin/$name"
  echo "  ✓ ~/.local/bin/$name"
done

# 4. Data packs → XDG data home (path-based; not on PATH).
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/minnows-data"
echo "Symlinking data packs into $DATA_HOME..."
mkdir -p "$DATA_HOME"
if [[ -d "$REPO/data" ]]; then
  for pack_dir in "$REPO"/data/*/; do
    [[ -d "$pack_dir" ]] || continue
    pname="$(basename "$pack_dir")"
    [[ -f "$pack_dir/pack.json" ]] || continue
    rm -rf "$DATA_HOME/$pname"
    ln -sfn "${pack_dir%/}" "$DATA_HOME/$pname"
    echo "  ✓ $DATA_HOME/$pname"
  done
fi
# Convenience scripts on PATH
for s in fetch-data-pack release-data-pack; do
  if [[ -f "$REPO/scripts/${s}.sh" ]]; then
    chmod +x "$REPO/scripts/${s}.sh"
    ln -sfn "$REPO/scripts/${s}.sh" "$HOME/.local/bin/$s"
    echo "  ✓ ~/.local/bin/$s"
  fi
done

echo
echo "minnows installed."
echo "  Tools:  on PATH (~/.local/bin)"
echo "  Skills: ~/.claude|codex|gemini/skills"
echo "  Data:   $DATA_HOME  (export DATA_PACKS_HOME=$DATA_HOME)"
echo "Note: ~/.local/bin must be on \$PATH (dotfiles shell config handles this)."
