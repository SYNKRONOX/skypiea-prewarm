#!/usr/bin/env bash
# Screenshot of the console's ?snapshot=1 view with a LOCALLY installed Chrome/Chromium
# (no download). CHROME=<path> overrides the search. On WSL the Windows Chrome is used and
# writes through C:/Users/<you>/AppData/Local/Temp.   demo/screenshot.sh docs/img/x.png [W H]
set -euo pipefail
out="${1:?output png}"; W="${2:-1920}"; H="${3:-1080}"
url="http://127.0.0.1:8090/?snapshot=1"
chrome="${CHROME:-$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)}"
if [ -z "$chrome" ] && [ -x "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" ]; then
  chrome="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
fi
[ -n "$chrome" ] || { echo "no local Chrome/Chromium found: screenshots skipped" >&2; exit 3; }
mkdir -p "$(dirname "$out")"
if [[ "$chrome" == /mnt/c/* ]]; then
  win_user=$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r')
  tmp="C:/Users/$win_user/AppData/Local/Temp/skypiea_shot.png"
  (cd /mnt/c && "$chrome" --headless=new --disable-gpu --no-first-run --hide-scrollbars \
    --window-size="$W,$H" --virtual-time-budget=4000 \
    --user-data-dir="C:\\Users\\$win_user\\AppData\\Local\\Temp\\skypiea_chrome" \
    --screenshot="$tmp" "$url" 2>/dev/null) || true
  cp "/mnt/c/Users/$win_user/AppData/Local/Temp/skypiea_shot.png" "$out"
else
  "$chrome" --headless=new --disable-gpu --no-first-run --hide-scrollbars --window-size="$W,$H" \
    --virtual-time-budget=4000 --screenshot="$out" "$url" 2>/dev/null
fi
echo "wrote $out"
