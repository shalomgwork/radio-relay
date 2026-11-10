"""
Browser client bridge: a tiny stdlib HTTP + WebSocket server so a web page can listen and talk over
Tailscale, the same way the Android app does over UDP. No external deps.

  GET /            -> the single-page client (HTML/JS below)
  GET /ws          -> WebSocket:
        server -> browser : binary = RX audio, raw int16 LE PCM, 48 kHz mono (960-sample frames)
        browser -> server : binary = TX mic audio, same PCM format (sent only while keyed)
                            text   = JSON control: {"ptt": true|false} or {"ping": 1}

The radio pipeline already works in PCM (opus is only for the UDP link), so the bridge just carries
PCM and hands TX frames to the server's tx queue. Served on WEB_PORT (default PORT+2 = 5002).
"""
import base64
import hashlib
import json
import logging
import queue
import socket
import struct
import threading

log = logging.getLogger("web")

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_TEXT, _OP_BIN, _OP_CLOSE, _OP_PING, _OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA


def _recv_exact(sock, n):
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            return None
        out += chunk
    return bytes(out)


def _read_frame(sock):
    hdr = _recv_exact(sock, 2)
    if not hdr:
        return None, None
    b1, b2 = hdr[0], hdr[1]
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    ln = b2 & 0x7F
    if ln == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None, None
        ln = struct.unpack(">H", ext)[0]
    elif ln == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None, None
        ln = struct.unpack(">Q", ext)[0]
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, ln) if ln else b""
    if payload is None:
        return None, None
    if masked and payload:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return opcode, payload


def _frame(opcode, data):
    b1 = 0x80 | opcode
    n = len(data)
    if n < 126:
        header = bytes([b1, n])
    elif n < 65536:
        header = bytes([b1, 126]) + struct.pack(">H", n)
    else:
        header = bytes([b1, 127]) + struct.pack(">Q", n)
    return header + data


class _Client:
    """One browser connection. Outbound audio goes through a bounded queue + sender thread so a slow
    client never stalls the radio thread (frames are dropped instead)."""

    def __init__(self, sock):
        self.sock = sock
        self.out = queue.Queue(maxsize=120)
        self.alive = True
        threading.Thread(target=self._sender, daemon=True).start()

    def _sender(self):
        while self.alive:
            try:
                frame = self.out.get(timeout=1.0)
            except queue.Empty:
                continue
            if frame is None:
                break
            try:
                self.sock.sendall(frame)
            except OSError:
                self.alive = False
                break

    def send_binary(self, pcm):
        if not self.alive:
            return
        try:
            self.out.put_nowait(_frame(_OP_BIN, pcm))
        except queue.Full:
            pass  # drop realtime audio rather than back up

    def send_raw(self, frame):
        try:
            self.out.put_nowait(frame)
        except queue.Full:
            pass

    def close(self):
        self.alive = False
        try:
            self.out.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class WebHub:
    def __init__(self, on_tx, on_ptt):
        self.on_tx = on_tx
        self.on_ptt = on_ptt
        self.clients = set()
        self.lock = threading.Lock()

    def add(self, c):
        with self.lock:
            self.clients.add(c)

    def remove(self, c):
        with self.lock:
            self.clients.discard(c)
        c.close()

    def broadcast(self, pcm):
        with self.lock:
            targets = list(self.clients)
        for c in targets:
            c.send_binary(pcm)

    @property
    def count(self):
        with self.lock:
            return len(self.clients)


def _handshake(sock, key):
    accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
    sock.sendall(
        ("HTTP/1.1 101 Switching Protocols\r\n"
         "Upgrade: websocket\r\nConnection: Upgrade\r\n"
         f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
    )


def _serve_http(sock, body, content_type="text/html; charset=utf-8"):
    data = body if isinstance(body, bytes) else body.encode()
    sock.sendall(
        (f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n"
         f"Cache-Control: no-store\r\n"
         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data
    )


def _handle(sock, hub):
    try:
        sock.settimeout(10)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(1024)
            if not chunk:
                return
            raw += chunk
            if len(raw) > 65536:
                return
        head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        lines = head.split("\r\n")
        method, path, _ = (lines[0].split(" ") + ["", "", ""])[:3]
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if method == "GET" and path == "/ws" and "websocket" in headers.get("upgrade", "").lower():
            _handshake(sock, headers["sec-websocket-key"])
            sock.settimeout(None)
            client = _Client(sock)
            hub.add(client)
            log.info("web client connected (%d total)", hub.count)
            try:
                while True:
                    opcode, payload = _read_frame(sock)
                    if opcode is None:
                        break
                    if opcode == _OP_BIN:
                        hub.on_tx(payload)
                    elif opcode == _OP_TEXT:
                        try:
                            msg = json.loads(payload.decode())
                        except ValueError:
                            continue
                        if "ptt" in msg:
                            hub.on_ptt(bool(msg["ptt"]))
                    elif opcode == _OP_PING:
                        client.send_raw(_frame(_OP_PONG, payload))
                    elif opcode == _OP_CLOSE:
                        break
            finally:
                hub.on_ptt(False)  # never leave the radio keyed if a browser drops mid-transmission
                hub.remove(client)
                log.info("web client gone (%d left)", hub.count)
            return

        if method == "GET" and path in ("/", "/index.html"):
            _serve_http(sock, PAGE)
            return
        sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except (OSError, ValueError, KeyError):
        pass
    finally:
        try:
            if path != "/ws":  # ws path keeps the socket; others close here
                sock.close()
        except (OSError, NameError):
            pass


def serve(port, on_tx, on_ptt):
    """Start the web client server (daemon). Returns a WebHub whose broadcast(pcm) feeds RX to browsers."""
    hub = WebHub(on_tx, on_ptt)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(8)

    def accept_loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            threading.Thread(target=_handle, args=(conn, hub), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    log.info("web/%d  (browser client)", port)
    return hub


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radio Relay</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; -webkit-user-select: none; user-select: none; }
  body { margin: 0; font: 15px/1.4 -apple-system, system-ui, sans-serif; background: #0E0E10; color: #F2F2F2;
         display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
  header { width: 100%; max-width: 520px; display: flex; align-items: center; gap: 10px; padding: 18px 20px; }
  h1 { font-size: 18px; margin: 0; flex: 1; }
  #dot { width: 10px; height: 10px; border-radius: 50%; background: #777; }
  #status { color: #A0A0A5; font-size: 13px; }
  main { width: 100%; max-width: 520px; padding: 0 20px 24px; flex: 1; display: flex; flex-direction: column; }
  #ptt { flex: 1; min-height: 240px; margin: 8px 0 16px; border: none; border-radius: 20px;
         background: #1C1C1E; color: #fff; font-size: 30px; font-weight: 700; letter-spacing: .5px;
         cursor: pointer; transition: background .08s, transform .08s; touch-action: none; }
  #ptt:disabled { opacity: .5; cursor: default; }
  #ptt.tx { background: #E53935; transform: scale(.99); }
  #ptt small { display: block; font-size: 13px; font-weight: 400; color: #C9C9CE; margin-top: 8px; }
  .hint { color: #7A7A80; font-size: 12px; text-align: center; }
  a { color: #4FC3F7; }
</style></head>
<body>
  <header>
    <h1>Radio Relay</h1>
    <span id="dot"></span><span id="status">connecting…</span>
  </header>
  <main>
    <button id="ptt" disabled>PUSH TO TALK<small>hold the button or the spacebar</small></button>
    <p class="hint">Listening is automatic. Hold to transmit. Recordings at <a id="rec" href="#">port 5001</a>.<br>
      Transmit needs a secure page: open this over Tailscale HTTPS, or trust this origin in Chrome.</p>
  </main>
<script>
const PCM_RATE = 48000, FRAME = 960;
const statusEl = document.getElementById('status'), dot = document.getElementById('dot'), btn = document.getElementById('ptt');
let ctx, ws, keyed = false, ready = false, micOK = false;
let pcmQ = [], cur = null, pos = 0;              // RX playback queue (Int16 chunks)
let txAcc = new Int16Array(FRAME), txN = 0;      // TX accumulator -> 960-sample frames
const BUF = 4096;

function setStatus(text, color) { statusEl.textContent = text; dot.style.background = color; }

function chirp() {
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.frequency.value = 1200; o.connect(g); g.connect(ctx.destination);
  g.gain.setValueAtTime(0.18, ctx.currentTime);
  o.start(); o.stop(ctx.currentTime + 0.07);
}

async function start() {
  ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: PCM_RATE });

  // RX playback via ScriptProcessor. Unlike AudioWorklet it needs no secure context, so plain http
  // over Tailscale can listen. It pulls from pcmQ and outputs silence when there's nothing.
  const play = ctx.createScriptProcessor(BUF, 0, 1);
  play.onaudioprocess = e => {
    const out = e.outputBuffer.getChannelData(0);
    for (let i = 0; i < out.length; i++) {
      if (!cur || pos >= cur.length) { cur = pcmQ.shift() || null; pos = 0; }
      out[i] = cur ? cur[pos++] / 32768 : 0;
    }
  };
  play.connect(ctx.destination);

  // Transmit needs the mic, which browsers only grant in a secure context (HTTPS or localhost).
  try {
    if (!navigator.mediaDevices) throw new Error('insecure context');
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const cap = ctx.createScriptProcessor(BUF, 1, 1);
    cap.onaudioprocess = e => {
      if (!keyed || !ws || ws.readyState !== 1) return;
      const inp = e.inputBuffer.getChannelData(0);
      for (let i = 0; i < inp.length; i++) {
        let s = Math.max(-1, Math.min(1, inp[i]));
        txAcc[txN++] = s < 0 ? s * 32768 : s * 32767;
        if (txN === FRAME) { ws.send(txAcc.buffer.slice(0)); txN = 0; }
      }
    };
    const mute = ctx.createGain(); mute.gain.value = 0;   // keep the node running without echoing the mic
    ctx.createMediaStreamSource(stream).connect(cap);
    cap.connect(mute); mute.connect(ctx.destination);
    micOK = true;
  } catch (err) {
    micOK = false;
    btn.firstChild.textContent = 'LISTEN ONLY';
    btn.querySelector('small').textContent = 'transmit needs HTTPS (see the hint below)';
  }

  connect();
  setInterval(() => { if (ws && ws.readyState === 1) ws.send('{"ping":1}'); }, 2000);
}

function connect() {
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { ready = true; btn.disabled = !micOK; setStatus('listening', '#43A047'); };
  ws.onclose = () => { ready = false; btn.disabled = true; setKeyed(false); setStatus('reconnecting…', '#FFB300'); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
  ws.onmessage = e => { if (typeof e.data !== 'string') { pcmQ.push(new Int16Array(e.data)); if (pcmQ.length > 60) pcmQ.shift(); } };
}

function setKeyed(on) {
  if (on === keyed || !ready || !micOK) return;
  keyed = on;
  btn.classList.toggle('tx', on);
  btn.firstChild.textContent = on ? 'TRANSMITTING' : 'PUSH TO TALK';
  if (on) { ctx.resume(); chirp(); }
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ ptt: on }));
  setStatus(on ? 'transmitting' : 'listening', on ? '#E53935' : '#43A047');
}

btn.addEventListener('pointerdown', e => { e.preventDefault(); setKeyed(true); });
window.addEventListener('pointerup', () => setKeyed(false));
window.addEventListener('blur', () => setKeyed(false));
window.addEventListener('keydown', e => { if (e.code === 'Space' && !e.repeat) { e.preventDefault(); setKeyed(true); } });
window.addEventListener('keyup', e => { if (e.code === 'Space') { e.preventDefault(); setKeyed(false); } });

document.getElementById('rec').href = location.protocol + '//' + location.hostname + ':5001/recordings';

// Audio needs a user gesture to start; first tap/click anywhere boots it.
function boot() { if (ctx) return; start().catch(err => setStatus('audio error: ' + err.message, '#E53935')); }
window.addEventListener('pointerdown', boot, { once: true });
window.addEventListener('keydown', boot, { once: true });
setStatus('tap anywhere to start', '#FFB300');
</script>
</body></html>
"""
