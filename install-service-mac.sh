#!/usr/bin/env bash
# Mac mini: run the server at login and restart it if it dies.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ~/Library/LaunchAgents
sed "s#HOME_PLACEHOLDER#$HOME#g" launchd/dev.radiorelay.server.plist > ~/Library/LaunchAgents/dev.radiorelay.server.plist
launchctl unload ~/Library/LaunchAgents/dev.radiorelay.server.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/dev.radiorelay.server.plist
echo "loaded. logs: tail -f ~/radio-relay/server.log"
