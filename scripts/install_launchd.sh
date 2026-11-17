#!/bin/sh
# Install (or remove) the launchd jobs: the LLM router (always on) and the nightly promo run.
#   ./scripts/install_launchd.sh              # install, nightly run at 02:30
#   ./scripts/install_launchd.sh 3 15         # nightly run at 03:15
#   ./scripts/install_launchd.sh --uninstall
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
JOBS="com.daian.llm-router com.daian.promo-daily"

if [ "$1" = "--uninstall" ]; then
  for j in $JOBS; do
    launchctl bootout "$DOMAIN/$j" 2>/dev/null || true
    rm -f "$AGENTS/$j.plist"
  done
  echo "Removed: $JOBS"
  exit 0
fi

HOUR="${1:-2}"; MINUTE="${2:-30}"
# launchd starts jobs with a bare PATH: give them the venv (piper), Homebrew/usr-local
# (ffmpeg), Node (npx for Remotion) and the system tools.
NODE_DIR="$(dirname "$(command -v npx || echo /usr/local/bin/npx)")"
FFMPEG_DIR="$(dirname "$(command -v ffmpeg || echo /usr/local/bin/ffmpeg)")"
PATHS="$REPO/.venv/bin:$NODE_DIR:$FFMPEG_DIR:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$AGENTS" "$REPO/projects/logs"
for j in $JOBS; do
  sed -e "s#__REPO__#$REPO#g" -e "s#__PATH__#$PATHS#g" -e "s#__HOUR__#$HOUR#g" -e "s#__MINUTE__#$MINUTE#g" \
    "$REPO/scripts/launchd/$j.plist" > "$AGENTS/$j.plist"
  plutil -lint "$AGENTS/$j.plist" >/dev/null
  launchctl bootout "$DOMAIN/$j" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$AGENTS/$j.plist"
done
echo "Installed: router (always on) + nightly promo run at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
echo "Check:     launchctl print $DOMAIN/com.daian.promo-daily | grep -E 'state|last exit'"
echo "Run now:   launchctl kickstart $DOMAIN/com.daian.promo-daily"
W=$(( (HOUR * 60 + MINUTE - 5 + 1440) % 1440 ))
echo "Wake Mac:  sudo pmset repeat wakeorpoweron MTWRFSU $(printf '%02d:%02d:00' $((W / 60)) $((W % 60)))   # 5 min before the run"
