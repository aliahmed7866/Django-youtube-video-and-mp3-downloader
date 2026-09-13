#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
: "${PREFIX:?Run this installer inside Termux}"
export MEDIAHUB_PORT="${MEDIAHUB_PORT:-8083}"
CONFIG_DIR="${MEDIAHUB_CONFIG_DIR:-$HOME/.config/mediahub}"
STATE_DIR="${MEDIAHUB_STATE_DIR:-$HOME/.local/state/mediahub}"
SERVICE_DIR="$PREFIX/var/service/mediahub"
cd "$APP_DIR"
if [ "${MEDIAHUB_FROM_DEPLOY:-0}" != 1 ]; then
  pkg install -y python ffmpeg nodejs clang make termux-services
fi
if ! ffmpeg -version >/dev/null 2>&1; then
  echo 'FFmpeg cannot run. Run pkg upgrade, then retry installation.' >&2
  exit 1
fi
[ -d .venv ] || python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$SERVICE_DIR/log"
chmod 700 "$CONFIG_DIR" "$STATE_DIR"
if [ ! -f "$CONFIG_DIR/env" ]; then
  printf 'export MEDIAHUB_PORT=%q\nexport MEDIAHUB_DATA_DIR=%q\n' "$MEDIAHUB_PORT" "${MEDIAHUB_DATA_DIR:-$HOME/.local/share/mediahub}" > "$CONFIG_DIR/env"
  chmod 600 "$CONFIG_DIR/env"
fi
# Reinstall uses the persisted settings, keeping the registry and service aligned.
. "$CONFIG_DIR/env"
export MEDIAHUB_PORT
.venv/bin/python termux/register.py
{
  printf '#!%s/bin/bash\nexec 2>&1\n' "$PREFIX"
  printf 'cd %q\n. %q\nexec %q run.py\n' "$APP_DIR" "$CONFIG_DIR/env" "$APP_DIR/.venv/bin/python"
} > "$SERVICE_DIR/run"
{
  printf '#!%s/bin/sh\n' "$PREFIX"
  printf 'exec svlogd -tt "%s"\n' "$STATE_DIR"
} > "$SERVICE_DIR/log/run"
chmod +x "$SERVICE_DIR/run" "$SERVICE_DIR/log/run"
export SVDIR="$PREFIX/var/service"
# Start Termux's supervisor now; its profile hook also starts it in new sessions.
[ ! -f "$PREFIX/etc/profile.d/start-services.sh" ] || . "$PREFIX/etc/profile.d/start-services.sh"
wait_for_supervisor() {
  local service="$1"
  for attempt in {1..20}; do
    if [ -p "$SVDIR/$service/supervise/ok" ]; then return 0; fi
    sleep 1
  done
  echo "Supervisor has not picked up $service. Open a new Termux session and retry." >&2
  return 1
}
wait_for_supervisor mediahub
sv-enable mediahub
sv -w 15 restart "$SVDIR/mediahub"
for attempt in {1..15}; do
  if .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$MEDIAHUB_PORT/health', timeout=2)" 2>/dev/null; then
    printf 'Media Hub ready: http://127.0.0.1:%s\n' "$MEDIAHUB_PORT"
    if [ "${MEDIAHUB_FROM_DEPLOY:-0}" != 1 ]; then
      DEPLOY_DIR="$PREFIX/var/service/mediahub-deploy"
      mkdir -p "$DEPLOY_DIR/log" "$STATE_DIR/deploy"
      {
        printf '#!%s/bin/bash\nexec 2>&1\n' "$PREFIX"
        printf 'cd %q\n. %q\nexec %q termux/deploy.py\n' "$APP_DIR" "$CONFIG_DIR/env" "$APP_DIR/.venv/bin/python"
      } > "$DEPLOY_DIR/run"
      printf '#!%s/bin/sh\nexec svlogd -tt "%s/deploy"\n' "$PREFIX" "$STATE_DIR" > "$DEPLOY_DIR/log/run"
      chmod +x "$DEPLOY_DIR/run" "$DEPLOY_DIR/log/run"
      wait_for_supervisor mediahub-deploy
      sv-enable mediahub-deploy
      sv -w 15 restart "$SVDIR/mediahub-deploy"
    fi
    exit 0
  fi
  sleep 1
done
printf 'Health check failed. Inspect %s/current\n' "$STATE_DIR" >&2
exit 1
