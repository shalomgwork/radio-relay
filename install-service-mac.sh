#!/usr/bin/env bash
# Mac mini: run the server at login, restart if it dies, with microphone permission.
# macOS only grants mic access to app bundles, so the server runs inside a tiny RadioRelay.app wrapper.
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(pwd); chmod +x run.sh
UID_=$(id -u); LABEL=dev.radiorelay.server; PLIST=~/Library/LaunchAgents/$LABEL.plist
APP=~/Applications/RadioRelay.app

# --- app bundle wrapper ---
mkdir -p "$APP/Contents/MacOS"
cc -O2 -o "$APP/Contents/MacOS/RadioRelay" macapp/launcher.c
cat > "$APP/Contents/Info.plist" <<'EOR'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>RadioRelay</string>
  <key>CFBundleIdentifier</key><string>dev.radiorelay.server</string>
  <key>CFBundleExecutable</key><string>RadioRelay</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Radio Relay captures the radio's audio through the Digirig.</string>
</dict></plist>
EOR
codesign -s - --force --deep "$APP" >/dev/null 2>&1 || true
tccutil reset Microphone dev.radiorelay.server >/dev/null 2>&1 || true   # forget any stale decision so the prompt shows

# --- launchd ---
mkdir -p ~/Library/LaunchAgents
sed "s#APP_PLACEHOLDER#$APP#g; s#REPO_PLACEHOLDER#$REPO#g" launchd/$LABEL.plist > "$PLIST"
launchctl bootout gui/$UID_/$LABEL 2>/dev/null || true
pkill -f "server/server.py" 2>/dev/null || true
sleep 2
launchctl enable gui/$UID_/$LABEL 2>/dev/null || true
for i in 1 2 3 4 5; do
  launchctl bootstrap gui/$UID_ "$PLIST" 2>/dev/null && break
  echo "launchd busy, retrying ($i)..."; sleep 2
done
launchctl kickstart -k gui/$UID_/$LABEL 2>/dev/null || true
sleep 3
echo "repo: $REPO   app: $APP"
echo "--- status ---"; launchctl print gui/$UID_/$LABEL | grep -E "state|last exit" | head -3
echo "--- log ---"; tail -3 server.log
echo
echo "If macOS asks 'RadioRelay would like to access the microphone' -> Allow."
echo "Check: System Settings > Privacy & Security > Microphone > RadioRelay must be ON."
echo "stop:  launchctl bootout gui/$UID_/$LABEL     start again: bash install-service-mac.sh"
