#!/usr/bin/env python3
"""
Desktop test client for server.py (same protocol as the Android app).

  ./venv/bin/python client.py <host>          interactive: Enter = key/unkey, RX plays on default output
  ./venv/bin/python client.py <host> --test   scripted smoke test: keepalive, 1 s PTT with tone, count RX
"""
import logging
import math
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, "server")
import opus_compat  # noqa: F401,E402
import opuslib  # noqa: E402
import sounddevice as sd  # noqa: E402
from radio_hw import FRAME, RATE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("client")
HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(os.environ.get("PORT", "5000"))


class Client:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.addr = (HOST, PORT)
        self.enc = opuslib.Encoder(RATE, 1, opuslib.APPLICATION_VOIP)
        self.dec = opuslib.Decoder(RATE, 1)
        self.ptt = False
        self.rx_frames = 0
        self.last_rx = 0.0

    def send(self, t, payload=b""):
        self.sock.sendto(bytes([t]) + payload, self.addr)

    def keepalive_loop(self):
        while True:
            self.send(0x00); time.sleep(2)

    def rx_loop(self, play=True):
        spk = None
        if play:
            spk = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, latency="high")
            spk.start()
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            self.last_rx = time.monotonic()
            if data and data[0] == 0x01:
                self.rx_frames += 1
                if spk and not self.ptt:
                    spk.write(self.dec.decode(bytes(data[1:]), FRAME))

    def tx_loop(self):
        mic = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, latency="low")
        mic.start()
        while True:
            buf, _ = mic.read(FRAME)
            if self.ptt:
                self.send(0x01, self.enc.encode(bytes(buf), FRAME))

    def interactive(self):
        threading.Thread(target=self.keepalive_loop, daemon=True).start()
        threading.Thread(target=self.rx_loop, daemon=True).start()
        threading.Thread(target=self.tx_loop, daemon=True).start()
        log.info("connected to %s:%d — Enter = key, Enter = unkey, Ctrl-C quits", HOST, PORT)
        try:
            while True:
                input()
                self.ptt = not self.ptt
                self.send(0x02 if self.ptt else 0x03)
                log.info("TX %s", "ON" if self.ptt else "released")
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self.send(0x03)

    def test(self):
        threading.Thread(target=self.rx_loop, kwargs={"play": False}, daemon=True).start()
        self.send(0x00); time.sleep(0.5)
        assert self.last_rx > 0, "no keepalive echo from server"
        log.info("keepalive echo OK")
        self.send(0x02)
        amp = 0.0  # silence: keys the radio without sending a tone
        for n in range(50):   # 1 s of 800 Hz
            pcm = struct.pack(f"<{FRAME}h", *[int(amp * math.sin(2 * math.pi * 800 * (n * FRAME + i) / RATE)) for i in range(FRAME)])
            self.send(0x01, self.enc.encode(pcm, FRAME)); time.sleep(0.02)
        self.send(0x03)
        log.info("sent PTT on + 1 s audio + PTT off")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3:
            self.send(0x00); time.sleep(0.5)
        log.info("RX frames received in 3 s: %d (0 is fine if the channel was quiet)", self.rx_frames)


if __name__ == "__main__":
    c = Client()
    c.test() if "--test" in sys.argv else c.interactive()
