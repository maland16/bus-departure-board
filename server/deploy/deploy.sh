#!/usr/bin/env bash
# Copy server/ to the Pi, install dependencies, and (re)start the service.
# Usage: server/deploy/deploy.sh [user@host]
set -euo pipefail

TARGET="${1:-busbox@192.168.1.27}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

# config.toml isn't in git (it holds the publish target); it's copied to the Pi from here
if [ ! -f "$HERE/config.toml" ]; then
  echo "server/config.toml not found. Start from the template: cp server/config.example.toml server/config.toml" >&2
  exit 1
fi

rsync -a --delete \
  --exclude '.venv/' --exclude 'data/' --exclude 'data-pi/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  "$HERE/" "$TARGET:busboard/"

ssh "$TARGET" bash -s <<'EOF'
set -euo pipefail
if [ ! -x ~/.local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
cd ~/busboard
~/.local/bin/uv sync --frozen --no-dev
mkdir -p ~/.config/systemd/user
cp deploy/busboard.service ~/.config/systemd/user/busboard.service
systemctl --user daemon-reload
systemctl --user enable busboard.service >/dev/null
systemctl --user restart busboard.service
sleep 2
systemctl --user --no-pager status busboard.service | head -5
EOF
