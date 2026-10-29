#!/usr/bin/env bash
# Mac mini: run the server at login and restart it if it dies.
set -euo pipefail
cd "$(dirname "$0")"
UID_=$(id -u); LABEL=dev.radiorelay.server; PLIST=~/Library/LaunchAgents/$LABEL.plist
mkdir -p ~/Library/LaunchAgents
sed "s#HOME_PLACEHOLDER#$HOME#g" launchd/$LABEL.plist > "$PLIST"
launchctl bootout gui/$UID_/$LABEL 2>/dev/null || true
launchctl bootstrap gui/$UID_ "$PLIST"
launchctl kickstart -k gui/$UID_/$LABEL
sleep 3
echo "--- status ---"; launchctl print gui/$UID_/$LABEL | grep -E "state|last exit" | head -3
echo "--- log ---"; tail -3 server.log
echo; echo "stop:  launchctl bootout gui/$UID_/$LABEL     start again: bash install-service-mac.sh"
