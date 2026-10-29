#!/usr/bin/env python3
"""
Local radio console, no network. Radio RX -> this computer's speakers. Enter = key up (mic -> radio), Enter = unkey.

Tunables (env):  PTT_PREROLL_MS=200  PTT_HANG_MS=300  RX_GATE_RMS=300  RX_HANG_MS=500  TX_GAIN=1.0  RX_GAIN=1.0
  e.g.  PTT_PREROLL_MS=300 ./venv/bin/python local.py
"""
import array
import logging
import os
import queue
import sys
import threading
import time

import sounddevice as sd

sys.path.insert(0, "server")
from radio_hw import FRAME, RATE, PTT, find_audio_device, rms  # noqa: E402

PREROLL = float(os.environ.get("PTT_PREROLL_MS", "200")) / 1000
HANG = float(os.environ.get("PTT_HANG_MS", "300")) / 1000
RX_GATE = int(os.environ.get("RX_GATE_RMS", "300"))
RX_HANG = float(os.environ.get("RX_HANG_MS", "500")) / 1000
TX_GAIN = float(os.environ.get("TX_GAIN", "1.0"))
RX_GAIN = float(os.environ.get("RX_GAIN", "1.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("local")


def gain(pcm: bytes, g: float) -> bytes:
    if g == 1.0:
        return pcm
    a = array.array("h", pcm)
    for i, v in enumerate(a):
        a[i] = max(-32768, min(32767, int(v * g)))
    return a.tobytes()


class Console:
    def __init__(self):
        self.ptt = PTT()
        self.want_tx = False
        self.radio_in = find_audio_device("input")
        self.radio_out = find_audio_device("output")

    # mic -> radio, gated by Enter
    def tx_loop(self):
        mic = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, latency="low")  # default mic
        out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                 device=self.radio_out, latency="low")
        mic.start(); out.start()
        silence = b"\x00" * FRAME * 2
        while True:
            buf, _ = mic.read(FRAME)
            if self.want_tx:
                if not self.ptt.keyed:
                    self.ptt.set(True)
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < PREROLL:      # let the radio key up before audio
                        mic.read(FRAME)
                    self.unkey_at = None
                out.write(gain(bytes(buf), TX_GAIN))
            elif self.ptt.keyed:
                if self.unkey_at is None:
                    self.unkey_at = time.monotonic() + HANG
                out.write(silence)
                if time.monotonic() >= self.unkey_at:
                    self.ptt.set(False)

    # radio -> speakers, muted while keyed, gated on level
    def rx_loop(self):
        inp = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                device=self.radio_in, latency="low")
        spk = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, latency="high")  # default spk, buffered
        inp.start(); spk.start()
        open_until, was_open = 0.0, False
        while True:
            buf, _ = inp.read(FRAME)
            data = bytes(buf)
            if self.ptt.keyed:
                continue
            now = time.monotonic()
            level = rms(data)
            if RX_GATE == 0 or level > RX_GATE:
                open_until = now + RX_HANG
            is_open = now < open_until
            if is_open != was_open:
                log.info("RX %s (rms %.0f)", "open" if is_open else "closed", level)
                was_open = is_open
            spk.write(gain(data, RX_GAIN) if is_open else b"\x00" * FRAME * 2)

    def run(self):
        self.unkey_at = None
        threading.Thread(target=self.tx_loop, daemon=True).start()
        threading.Thread(target=self.rx_loop, daemon=True).start()
        log.info("ready: preroll=%dms hang=%dms gate=%d — press Enter to key, Enter to unkey, Ctrl-C to quit",
                 PREROLL * 1000, HANG * 1000, RX_GATE)
        try:
            while True:
                input()
                self.want_tx = not self.want_tx
                log.info("TX %s", "ON (talk)" if self.want_tx else "released")
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self.want_tx = False
            self.ptt.set(False)


if __name__ == "__main__":
    Console().run()
