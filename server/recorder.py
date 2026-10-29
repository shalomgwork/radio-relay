"""
Per-transmission WAV recording + a tiny HTTP server so the app can list/stream them.

  REC_DIR   where files go           (default: <repo>/recordings)
  REC_DAYS  keep this many days      (default 14; 0 disables recording)
  REC_PORT  HTTP port                (default PORT+1 = 5001)

  GET /recordings            -> JSON [{name, kind, start, dur, size}, ...] newest first
  GET /recordings/<name>     -> audio/wav, supports Range (seeking)
"""
import json
import logging
import os
import re
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from radio_hw import RATE

log = logging.getLogger("rec")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.environ.get("REC_DIR", os.path.join(REPO, "recordings"))
REC_DAYS = float(os.environ.get("REC_DAYS", "14"))
REC_MIN_S = 0.8                                   # drop squelch blips shorter than this
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(rx|tx)\.wav$")


class Recorder:
    """One Recorder per direction. start() opens a file, write() appends PCM, stop() finalizes."""

    def __init__(self, kind):
        self.kind, self.wav, self.path, self.frames = kind, None, None, 0
        if REC_DAYS > 0:
            os.makedirs(REC_DIR, exist_ok=True)

    def start(self):
        if REC_DAYS <= 0 or self.wav:
            return
        self.path = os.path.join(REC_DIR, time.strftime("%Y-%m-%d_%H-%M-%S") + f"_{self.kind}.wav")
        self.wav = wave.open(self.path, "wb")
        self.wav.setnchannels(1); self.wav.setsampwidth(2); self.wav.setframerate(RATE)
        self.frames = 0

    def write(self, pcm):
        if self.wav:
            self.wav.writeframes(pcm)
            self.frames += len(pcm) // 2

    def stop(self):
        if not self.wav:
            return
        self.wav.close(); self.wav = None
        dur = self.frames / RATE
        if dur < REC_MIN_S:
            os.remove(self.path)
        else:
            log.info("saved %s (%.1fs)", os.path.basename(self.path), dur)


def listing():
    out = []
    for n in os.listdir(REC_DIR) if os.path.isdir(REC_DIR) else []:
        m = NAME_RE.match(n)
        if not m:
            continue
        size = os.path.getsize(os.path.join(REC_DIR, n))
        start = time.mktime(time.strptime(n[:19], "%Y-%m-%d_%H-%M-%S"))
        out.append({"name": n, "kind": m.group(1), "start": int(start),
                    "dur": round(max(0, size - 44) / (RATE * 2), 1), "size": size})
    out.sort(key=lambda r: r["name"], reverse=True)
    return out


def sweep():
    """Delete recordings older than REC_DAYS. Called at start and hourly."""
    if REC_DAYS <= 0 or not os.path.isdir(REC_DIR):
        return
    cutoff = time.time() - REC_DAYS * 86400
    for n in os.listdir(REC_DIR):
        p = os.path.join(REC_DIR, n)
        if NAME_RE.match(n) and os.path.getmtime(p) < cutoff:
            os.remove(p)


def _sweeper():
    while True:
        try:
            sweep()
        except OSError as e:
            log.warning("sweep: %s", e)
        time.sleep(3600)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                     # quiet
        pass

    def do_GET(self):
        if self.path == "/recordings":
            body = json.dumps(listing()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        name = self.path[len("/recordings/"):] if self.path.startswith("/recordings/") else ""
        if not NAME_RE.match(name) or not os.path.isfile(os.path.join(REC_DIR, name)):
            self.send_error(404); return
        path = os.path.join(REC_DIR, name)
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = min(int(b), size - 1) if b and a else size - 1
            if start > end:
                self.send_error(416); return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)


def serve(port):
    """Start the HTTP listing/streaming server and the retention sweeper (daemon threads)."""
    if REC_DAYS <= 0:
        log.info("recording disabled (REC_DAYS=0)")
        return
    threading.Thread(target=_sweeper, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("http/%d  dir=%s  keep=%gd", port, REC_DIR, REC_DAYS)
