#!/usr/bin/env bash
# Run the network server in the foreground. Ctrl-C to stop.
cd "$(dirname "$0")"
[ -f relay.env ] || cp relay.env.example relay.env
set -a; . ./relay.env; set +a
exec ./venv/bin/python server/server.py
