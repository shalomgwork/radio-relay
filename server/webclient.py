"""
Browser client bridge: a tiny stdlib HTTP + WebSocket server so a web page can listen and talk over
Tailscale, the same way the Android app does over UDP. No external deps.

  GET /            -> the single-page client (HTML/JS below)
  GET /recordings[/<name>]  -> proxied from the local recorder (REC_PORT) so the page is same-origin
  GET /ws          -> WebSocket:
        server -> browser : binary = RX audio, raw int16 LE PCM, 48 kHz mono (960-sample frames)
        browser -> server : binary = TX mic audio, same PCM format (sent only while keyed)
                            text   = JSON control: {"ptt": true|false} or {"ping": 1}

The radio pipeline already works in PCM (opus is only for the UDP link), so the bridge just carries
PCM and hands TX frames to the server's tx queue. Served on WEB_PORT (default PORT+2 = 5002).
"""
import base64
import hashlib
import http.client
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
    def __init__(self, on_tx, on_ptt, rec_port):
        self.on_tx = on_tx
        self.on_ptt = on_ptt
        self.rec_port = rec_port
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


def _proxy_recordings(sock, path, req_headers, rec_port):
    """Relay GET /recordings[...] from the local recorder so the page stays same-origin (avoids CORS
    and, under Tailscale HTTPS, mixed-content blocking). Forwards Range so audio seeking works."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", rec_port, timeout=10)
        fwd = {}
        if "range" in req_headers:
            fwd["Range"] = req_headers["range"]
        conn.request("GET", path, headers=fwd)
        resp = conn.getresponse()
        headers = [("Cache-Control", "no-store"), ("Connection", "close")]
        for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            v = resp.getheader(h)
            if v:
                headers.append((h, v))
        head = (f"HTTP/1.1 {resp.status} {resp.reason}\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in headers) + "\r\n")
        sock.sendall(head.encode())
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            sock.sendall(chunk)
        conn.close()
    except OSError:
        try:
            sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass


def _handle(sock, hub):
    path = ""
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

        if method == "GET" and (path == "/recordings" or path.startswith("/recordings/")):
            _proxy_recordings(sock, path, headers, hub.rec_port)
            return

        if method == "GET" and path in ("/", "/index.html"):
            _serve_http(sock, PAGE)
            return
        sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except (OSError, ValueError, KeyError):
        pass
    finally:
        if path != "/ws":  # ws keeps the socket; everything else closes here
            try:
                sock.close()
            except OSError:
                pass


def serve(port, on_tx, on_ptt, rec_port):
    """Start the web client server (daemon). Returns a WebHub whose broadcast(pcm) feeds RX to browsers."""
    hub = WebHub(on_tx, on_ptt, rec_port)
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
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.4 -apple-system, system-ui, sans-serif; background: #0E0E10; color: #F2F2F2; }
  button, a.btn { -webkit-user-select: none; user-select: none; }
  header { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid #1C1C1E; }
  h1 { font-size: 17px; margin: 0; flex: 1; }
  #dot { width: 10px; height: 10px; border-radius: 50%; background: #777; }
  #status { color: #A0A0A5; font-size: 13px; }
  #wrap { display: flex; max-width: 920px; margin: 0 auto; }
  main { flex: 1; padding: 22px 18px; display: flex; flex-direction: column; align-items: center; }
  #ptt { width: 170px; height: 70px; border: none; border-radius: 14px; background: #1C1C1E; color: #fff;
         font-size: 17px; font-weight: 700; letter-spacing: .4px; cursor: pointer; touch-action: none;
         transition: background .08s, transform .06s; }
  #ptt:disabled { opacity: .5; cursor: default; }
  #ptt.tx { background: #E53935; transform: scale(.98); }
  #ptt small { display: block; font-size: 11px; font-weight: 400; color: #C9C9CE; margin-top: 4px; }
  #pip { margin-top: 12px; background: none; border: 1px solid #2C2C30; color: #4FC3F7; border-radius: 10px;
         padding: 8px 14px; font-size: 13px; cursor: pointer; }
  #pip[hidden] { display: none; }
  .hint { color: #7A7A80; font-size: 12px; text-align: center; margin-top: 18px; }
  aside { width: 300px; border-left: 1px solid #1C1C1E; display: flex; flex-direction: column; height: calc(100vh - 51px); }
  .side-head { display: flex; align-items: center; padding: 12px 16px; font-weight: 600; }
  .side-head span { flex: 1; }
  #refresh { background: none; border: none; color: #4FC3F7; font-size: 16px; cursor: pointer; }
  #recs { flex: 1; overflow-y: auto; }
  .day { color: #7A7A80; font-size: 11px; letter-spacing: .08em; padding: 12px 16px 2px; }
  .rec { display: flex; align-items: center; gap: 8px; padding: 9px 16px; cursor: pointer; }
  .rec:hover { background: #151518; }
  .rec.sel { background: #202027; }
  .rec .k { font-size: 11px; font-weight: 700; width: 30px; }
  .rec .t { flex: 1; }
  .rec .d { color: #A0A0A5; font-size: 13px; }
  .rec .s { color: #4FC3F7; width: 16px; text-align: right; font-size: 12px; }
  #empty { color: #7A7A80; font-size: 13px; padding: 16px; }
  #nowbar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-top: 1px solid #1C1C1E; }
  #nowbar[hidden] { display: none; }
  #np-play { width: 36px; height: 36px; border: none; border-radius: 9px; background: #1C1C1E; color: #fff; font-size: 14px; cursor: pointer; }
  .np-info { flex: 1; min-width: 0; }
  #np-title { display: block; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #np-dl { color: #4FC3F7; font-size: 20px; text-decoration: none; }
  @media (max-width: 760px) {
    #wrap { flex-direction: column; }
    aside { width: auto; border-left: none; border-top: 1px solid #1C1C1E; height: auto; }
    #recs { max-height: 50vh; }
  }
</style></head>
<body>
  <header>
    <h1>Radio Relay</h1>
    <span id="dot"></span><span id="status">connecting…</span>
  </header>
  <div id="wrap">
    <main>
      <button id="ptt" disabled>PTT<small>hold, or spacebar</small></button>
      <button id="pip" hidden>Pop out PTT</button>
      <p class="hint">Listening is automatic. Hold PTT or the spacebar to talk.</p>
    </main>
    <aside>
      <div class="side-head"><span>Recordings</span><button id="refresh" title="Refresh">&#x21bb;</button></div>
      <div id="recs"><div id="empty">Loading…</div></div>
      <div id="nowbar" hidden>
        <button id="np-play">&#x275a;&#x275a;</button>
        <div class="np-info"><span id="np-title"></span></div>
        <a id="np-dl" title="Download" download>&#x2913;</a>
      </div>
    </aside>
  </div>
  <audio id="audio"></audio>
<script>
const PCM_RATE = 48000, FRAME = 960, BUF = 4096, GAP = 15 * 60;
const statusEl = document.getElementById('status'), dot = document.getElementById('dot'), btn = document.getElementById('ptt');
let ctx, ws, keyed = false, ready = false, micOK = false, pipBtn = null;
let pcmQ = [], cur = null, pos = 0;
let txAcc = new Int16Array(FRAME), txN = 0;

function setStatus(t, c) { statusEl.textContent = t; dot.style.background = c; }

function chirp() {
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.frequency.value = 1200; o.connect(g); g.connect(ctx.destination);
  g.gain.setValueAtTime(0.18, ctx.currentTime); o.start(); o.stop(ctx.currentTime + 0.07);
}

async function start() {
  ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: PCM_RATE });
  const play = ctx.createScriptProcessor(BUF, 0, 1);
  play.onaudioprocess = e => {
    const out = e.outputBuffer.getChannelData(0);
    for (let i = 0; i < out.length; i++) {
      if (!cur || pos >= cur.length) { cur = pcmQ.shift() || null; pos = 0; }
      out[i] = cur ? cur[pos++] / 32768 : 0;
    }
  };
  play.connect(ctx.destination);

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
    const mute = ctx.createGain(); mute.gain.value = 0;
    ctx.createMediaStreamSource(stream).connect(cap);
    cap.connect(mute); mute.connect(ctx.destination);
    micOK = true;
    if (window.documentPictureInPicture) document.getElementById('pip').hidden = false;
  } catch (err) {
    micOK = false;
    btn.firstChild.textContent = 'LISTEN ONLY';
    btn.querySelector('small').textContent = 'transmit needs HTTPS';
  }

  connect();
  loadRecs();
  ctx.resume().catch(() => {});
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
  btn.firstChild.textContent = on ? 'ON AIR' : 'PTT';
  if (pipBtn) { pipBtn.classList.toggle('tx', on); pipBtn.textContent = on ? 'ON AIR' : 'PTT'; }
  if (on) { ctx.resume(); chirp(); }
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ ptt: on }));
  setStatus(on ? 'transmitting' : 'listening', on ? '#E53935' : '#43A047');
}

btn.addEventListener('pointerdown', e => { e.preventDefault(); setKeyed(true); });
btn.addEventListener('pointerup', () => setKeyed(false));
btn.addEventListener('pointerleave', () => setKeyed(false));
window.addEventListener('blur', () => setKeyed(false));
window.addEventListener('keydown', e => { if (e.code === 'Space' && !e.repeat) { e.preventDefault(); setKeyed(true); } });
window.addEventListener('keyup', e => { if (e.code === 'Space') { e.preventDefault(); setKeyed(false); } });

// ---- Picture-in-Picture pop-out PTT ----
document.getElementById('pip').addEventListener('click', async () => {
  if (!window.documentPictureInPicture) { setStatus('pop-out needs Chrome 116+', '#FFB300'); return; }
  try {
    const w = await documentPictureInPicture.requestWindow({ width: 200, height: 150 });
    const st = w.document.createElement('style');
    st.textContent = 'body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;' +
      'background:#0E0E10;font-family:system-ui}button{width:88%;height:74%;border:none;border-radius:16px;' +
      'background:#1C1C1E;color:#fff;font-size:24px;font-weight:700;cursor:pointer;touch-action:none}button.tx{background:#E53935}';
    w.document.head.append(st);
    const b = w.document.createElement('button');
    b.textContent = keyed ? 'ON AIR' : 'PTT';
    b.addEventListener('pointerdown', e => { e.preventDefault(); if (ctx) ctx.resume().catch(() => {}); setKeyed(true); });
    b.addEventListener('pointerup', () => setKeyed(false));
    b.addEventListener('pointerleave', () => setKeyed(false));
    w.document.addEventListener('keydown', e => { if (e.code === 'Space' && !e.repeat) { e.preventDefault(); setKeyed(true); } });
    w.document.addEventListener('keyup', e => { if (e.code === 'Space') { e.preventDefault(); setKeyed(false); } });
    w.document.body.append(b);
    pipBtn = b;
    w.addEventListener('pagehide', () => { pipBtn = null; });
  } catch (e) { setStatus('pop-out failed: ' + e.message, '#E53935'); }
});

// ---- Recordings sidebar (fetch, group, autoplay, download) ----
const audio = document.getElementById('audio');
const recsEl = document.getElementById('recs'), nowbar = document.getElementById('nowbar');
const npTitle = document.getElementById('np-title'), npPlay = document.getElementById('np-play'), npDl = document.getElementById('np-dl');
let recs = [], playingIdx = -1;

function fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
function dayOf(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  const yest = new Date(now); yest.setDate(now.getDate() - 1);
  if (same(d, now)) return 'Today';
  if (same(d, yest)) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

async function loadRecs() {
  try {
    const r = await fetch('/recordings', { cache: 'no-store' });
    recs = await r.json();
  } catch (e) { recsEl.innerHTML = '<div id="empty">can\'t reach recordings</div>'; return; }
  render();
}

function render() {
  if (!recs.length) { recsEl.innerHTML = '<div id="empty">No recordings yet</div>'; return; }
  let html = '', lastDay = null;
  for (let i = 0; i < recs.length; i++) {
    const r = recs[i];
    // conversation header: first (newest) row of each group separated by a > GAP gap
    const isHead = i === 0 || (recs[i - 1].start - r.start) > GAP;
    if (isHead) {
      let j = i; while (j + 1 < recs.length && recs[j].start - recs[j + 1].start <= GAP) j++;
      const from = fmtTime(recs[j].start), to = fmtTime(r.start);
      html += '<div class="day">' + dayOf(r.start) + ' &middot; ' + (i === j ? to : from + ' – ' + to) + '</div>';
    }
    const tx = r.kind === 'tx';
    html += '<div class="rec' + (i === playingIdx ? ' sel' : '') + '" data-i="' + i + '">' +
      '<span class="k" style="color:' + (tx ? '#E53935' : '#43A047') + '">' + (tx ? 'YOU' : 'RX') + '</span>' +
      '<span class="t">' + fmtTime(r.start) + '</span>' +
      '<span class="d">' + Math.round(r.dur) + 's</span>' +
      '<span class="s">' + (i === playingIdx && !audio.paused ? '❚❚' : (i === playingIdx ? '▶' : '')) + '</span></div>';
  }
  recsEl.innerHTML = html;
  recsEl.querySelectorAll('.rec').forEach(el =>
    el.addEventListener('click', () => { const i = +el.dataset.i; i === playingIdx ? togglePause() : playRec(i); }));
}

function playRec(i) {
  playingIdx = i;
  const r = recs[i];
  audio.src = '/recordings/' + r.name;
  audio.play().catch(() => {});
  npTitle.textContent = (r.kind === 'tx' ? 'You' : 'Radio') + ' · ' + dayOf(r.start) + ' ' + fmtTime(r.start);
  npDl.href = '/recordings/' + r.name; npDl.setAttribute('download', r.name);
  nowbar.hidden = false;
  render();
}
function togglePause() { if (audio.paused) audio.play(); else audio.pause(); render(); }
npPlay.addEventListener('click', togglePause);
audio.addEventListener('play', () => { npPlay.innerHTML = '&#x275a;&#x275a;'; render(); });
audio.addEventListener('pause', () => { npPlay.innerHTML = '&#x25b6;'; render(); });
audio.addEventListener('ended', () => { if (playingIdx > 0) playRec(playingIdx - 1); else { nowbar.hidden = true; playingIdx = -1; render(); } });
document.getElementById('refresh').addEventListener('click', loadRecs);
setInterval(() => { if (document.visibilityState === 'visible' && playingIdx < 0) loadRecs(); }, 15000);

// Start listening automatically on load. The AudioContext may begin suspended under the browser's
// autoplay policy; we resume it opportunistically here and on the first interaction, so there's no
// explicit "tap to start".
setStatus('connecting…', '#FFB300');
start().catch(err => setStatus('audio error: ' + err.message, '#E53935'));
function resumeAudio() { if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {}); }
window.addEventListener('pointerdown', resumeAudio);
window.addEventListener('keydown', resumeAudio);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') resumeAudio(); });
</script>
</body></html>
"""
