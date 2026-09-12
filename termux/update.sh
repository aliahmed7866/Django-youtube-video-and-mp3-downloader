#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
# Refuse to discard local edits or deploy an unrelated branch.
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo 'Local changes found; update stopped.' >&2; exit 1; }
[ "$(git branch --show-current)" = master ] || { echo 'Switch to master before updating.' >&2; exit 1; }
git pull --ff-only origin master
bash termux/install-service.sh
