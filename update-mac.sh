#!/usr/bin/env bash
# Mac mini: pull the latest code and restart the service.
cd "$(dirname "$0")" && git pull -q && launchctl kickstart -k gui/$(id -u)/dev.radiorelay.server && sleep 3 && tail -3 server.log
