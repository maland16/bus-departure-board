#!/usr/bin/env bash
# Installs git hooks that keep src/departure-board/git-version.h up to date
# whenever HEAD changes (commit, checkout, merge, rebase/amend).
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
hooks_dir="$(git -C "$script_dir" rev-parse --absolute-git-dir)/hooks"
marker="# installed by tools/install-git-hooks.sh"

for hook in post-commit post-checkout post-merge post-rewrite; do
  path="$hooks_dir/$hook"
  if [ -f "$path" ] && ! grep -q "$marker" "$path"; then
    echo "Skipping $hook: a hook not installed by this script already exists"
    continue
  fi
  printf '#!/bin/sh\n%s\n"$(git rev-parse --show-toplevel)/tools/update-git-version.sh" >/dev/null\n' "$marker" > "$path"
  chmod +x "$path"
  echo "Installed $hook"
done

"$script_dir/update-git-version.sh"
