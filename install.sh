#!/usr/bin/env bash
# Link logi-mx as an agent skill (Claude Code, Codex, Kimi, OpenCode).
# --uninstall removes the links.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
targets=("$HOME/.agents/skills" "$HOME/.claude/skills")

for dir in "${targets[@]}"; do
  link="$dir/logi-mx"
  if [[ "${1:-}" == "--uninstall" ]]; then
    [[ -L "$link" ]] && rm "$link" && echo "removed $link"
    continue
  fi
  mkdir -p "$dir"
  if [[ -e "$link" && ! -L "$link" ]]; then
    echo "skip $link: exists and is not a link" >&2
    continue
  fi
  ln -sfn "$here" "$link"
  echo "linked $link -> $here"
done
