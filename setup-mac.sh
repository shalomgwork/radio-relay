#!/usr/bin/env bash
# One-time setup on a Mac (MacBook for testing, Mac mini for always-on).
set -euo pipefail
cd "$(dirname "$0")"
brew list --versions opus portaudio hidapi switchaudio-osx >/dev/null 2>&1 || brew install opus portaudio hidapi switchaudio-osx
PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
[ -d venv ] || $PY -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q sounddevice hidapi opuslib
[ -f relay.env ] || cp relay.env.example relay.env
# Digirig input gain: 100% clips, ~50% is right for the NX-3300 at half volume.
if SwitchAudioSource -a -t input | grep -q "USB Audio Device"; then
  cur=$(SwitchAudioSource -t input -c)
  SwitchAudioSource -t input -s "USB Audio Device" >/dev/null && osascript -e 'set volume input volume 50'
  SwitchAudioSource -t input -s "$cur" >/dev/null
  echo "Digirig input gain set to 50%"
fi
echo "ok — next: ./venv/bin/python hwtest.py devices"
