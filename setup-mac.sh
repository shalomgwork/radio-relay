#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY=$(command -v python3.13 || command -v python3.12 || command -v python3)
$PY -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q sounddevice hidapi
echo "ok — ./venv/bin/python hwtest.py devices"
