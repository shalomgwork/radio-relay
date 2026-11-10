#!/usr/bin/env python3
"""
radio-relay server: UDP <-> Kenwood NX-3300 via Digirig Lite. macOS or Linux.

Protocol (UDP, one client, carried over Tailscale so no crypto here):
  byte0  0x00 keepalive   (client -> server every 2 s; server echoes)
         0x01 opus frame  (either direction, 20 ms @ 48 kHz mono, payload follows)
         0x02 PTT on      (client -> server)
         0x03 PTT off     (client -> server)
"""
import logging
import os
import queue
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opus_compat  # noqa: F401,E402
import opuslib  # noqa: E402
import sounddevice as sd  # noqa: E402
from radio_hw import FRAME, RATE, PTT, find_audio_device, rms  # noqa: E402
import recorder  # noqa: E402
import webclient  # noqa: E402

log = logging.getLogger("server")

PORT = int(os.environ.get("PORT", "5000"))
PTT_PREROLL = float(os.environ.get("PTT_PREROLL_MS", "200")) / 1000
PTT_HANG = float(os.environ.get("PTT_HANG_MS", "300")) / 1000
PTT_MAX = float(os.environ.get("PTT_MAX_S", "120"))
RX_GATE = int(os.environ.get("RX_GATE_RMS", "300"))        # 0 = always stream
RX_HANG = float(os.environ.get("RX_HANG_MS", "500")) / 1000
REC_PORT = int(os.environ.get("REC_PORT", str(PORT + 1)))
WEB_PORT = int(os.environ.get("WEB_PORT", str(PORT + 2)))
CLIENT_TIMEOUT = 6.0
SILENCE = b"\x00" * FRAME * 2


def wait_for_hardware():
    """Block until the Digirig is present (plugged in after the server started, or replugged)."""
    waited = False
    while True:
        try:
            find_audio_device("input"); find_audio_device("output")
            if waited: log.info("Digirig detected")
            return
        except RuntimeError:
            if not waited: log.warning("Digirig not found; waiting for it to be plugged in")
            waited = True
            sd._terminate(); sd._initialize()          # PortAudio only rescans devices on re-init
            time.sleep(2)


class Server:
    def __init__(self):
        wait_for_hardware()
        self.ptt = PTT()
        self.in_dev, self.out_dev = find_audio_device("input"), find_audio_device("output")
        self.in_name = sd.query_devices(self.in_dev)["name"]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", PORT))
        self.client = None
        self.client_seen = 0.0
        self.tx_q: queue.Queue[bytes] = queue.Queue()
        self.ptt_req = False
        self.enc = opuslib.Encoder(RATE, 1, opuslib.APPLICATION_VOIP)
        self.dec = opuslib.Decoder(RATE, 1)
        self.rec_rx, self.rec_tx = recorder.Recorder("rx"), recorder.Recorder("tx")
        self.web = None

    # ---- network ----
    def net_loop(self):
        while True:
            data, addr = self.sock.recvfrom(2048)
            if not data:
                continue
            if addr != self.client:
                log.info("client %s", addr)
                self.client = addr
            self.client_seen = time.monotonic()
            t = data[0]
            if t == 0x00:
                self.sock.sendto(b"\x00", addr)
            elif t == 0x01:
                try:
                    self.tx_q.put(self.dec.decode(bytes(data[1:]), FRAME))
                except opuslib.OpusError as e:
                    log.warning("decode: %s", e)
            elif t == 0x02:
                self.ptt_req = True
                self.tx_q.put(b"")                     # wake tx loop immediately
            elif t == 0x03:
                self.ptt_req = False

    # ---- client -> radio ----
    def tx_loop(self):
        out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                 device=self.out_dev, latency="low")
        out.start()
        while True:
            pcm = self.tx_q.get()
            self.ptt.set(True)
            key_time = last = time.monotonic()
            pre = [pcm] if pcm else []
            while time.monotonic() - key_time < PTT_PREROLL:    # let the radio key up before audio
                try:
                    p = self.tx_q.get(timeout=0.01)
                    if p: pre.append(p)
                except queue.Empty:
                    pass
            self.rec_tx.start()
            for p in pre:
                out.write(p); self.rec_tx.write(p)
            while True:
                try:
                    p = self.tx_q.get(timeout=0.02)
                    if p:
                        out.write(p); self.rec_tx.write(p); last = time.monotonic()
                except queue.Empty:
                    out.write(SILENCE)
                now = time.monotonic()
                if not self.ptt_req and now - last > PTT_HANG:
                    break
                if now - key_time > PTT_MAX or now - self.client_seen > CLIENT_TIMEOUT:
                    log.warning("PTT safety unkey")
                    self.ptt_req = False
                    with self.tx_q.mutex: self.tx_q.queue.clear()
                    break
            self.ptt.set(False)
            self.rec_tx.stop()

    # ---- radio -> client ----
    def rx_loop(self):
        inp = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                device=self.in_dev, latency="low")
        inp.start()
        open_until, was_open = 0.0, False
        while True:
            buf, _ = inp.read(FRAME)
            data = bytes(buf)
            now = time.monotonic()
            if self.ptt.keyed:
                open_until = 0.0                      # half duplex: never record/stream our own TX
            else:
                level = rms(data)
                if RX_GATE == 0 or level > RX_GATE:
                    open_until = now + RX_HANG
            is_open = now < open_until
            if is_open != was_open:
                log.info("RX %s", "open" if is_open else "closed")
                was_open = is_open
                if is_open: self.rec_rx.start()
                else: self.rec_rx.stop()
            if not is_open:
                continue
            self.rec_rx.write(data)
            if self.client and now - self.client_seen <= CLIENT_TIMEOUT:
                self.sock.sendto(b"\x01" + self.enc.encode(data, FRAME), self.client)
            if self.web:
                self.web.broadcast(data)

    # ---- browser client (WebSocket) -> radio ----
    def _web_tx(self, pcm):
        self.client_seen = time.monotonic()   # keep tx_loop's safety timer fresh
        self.tx_q.put(pcm)

    def _web_ptt(self, on):
        self.client_seen = time.monotonic()
        if on:
            self.ptt_req = True
            self.tx_q.put(b"")
        else:
            self.ptt_req = False

    def watchdog(self):
        """If the Digirig disappears (USB unplug), exit so launchd restarts us into wait_for_hardware()."""
        import hid
        while True:
            time.sleep(2)
            present = any(d["vendor_id"] == PTT.CM108_VID for d in hid.enumerate())
            if not present:
                log.error("Digirig unplugged; restarting")
                os._exit(3)

    def run(self):
        threading.Thread(target=self.watchdog, daemon=True).start()
        log.info("udp/%d  in=%s  out=%s  preroll=%dms hang=%dms gate=%d", PORT,
                 sd.query_devices(self.in_dev)["name"], sd.query_devices(self.out_dev)["name"],
                 PTT_PREROLL * 1000, PTT_HANG * 1000, RX_GATE)
        recorder.serve(REC_PORT)
        self.web = webclient.serve(WEB_PORT, on_tx=self._web_tx, on_ptt=self._web_ptt)
        threading.Thread(target=self.net_loop, daemon=True).start()
        threading.Thread(target=self.tx_loop, daemon=True).start()
        self.rx_loop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
    s = Server()
    try:
        s.run()
    finally:
        s.ptt.set(False)
        s.rec_tx.stop(); s.rec_rx.stop()
