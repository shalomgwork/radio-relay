#!/usr/bin/env python3
"""
Hardware check, no phone needed. Digirig + radio plugged in, then:

  python hwtest.py devices   list audio + HID devices (is the Digirig seen?)
  python hwtest.py ptt       key the radio for 1 s        -> TX LED should light
  python hwtest.py tone      key + send 800 Hz for 2 s    -> another radio hears a tone
  python hwtest.py rx        record 5 s from the radio    -> level meter + rx.wav, then plays it back
  python hwtest.py all
"""
import logging
import math
import struct
import sys
import time
import wave

import sounddevice as sd

sys.path.insert(0, "server")
from radio_hw import FRAME, RATE, PTT, find_audio_device, rms  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")


def devices():
    print("== audio devices ==")
    for i, d in enumerate(sd.query_devices()):
        tag = "  <-- Digirig?" if any(k in d["name"].lower() for k in ("usb pnp", "c-media", "digirig", "usb audio")) else ""
        print(f"[{i}] {d['name']}  in={d['max_input_channels']} out={d['max_output_channels']}{tag}")
    print("== HID devices (vendor 0x0d8c = C-Media) ==")
    import hid
    found = [d for d in hid.enumerate() if d["vendor_id"] == 0x0D8C]
    for d in found:
        print(f"  {d['path'].decode()}  product={d.get('product_string')}")
    if not found:
        print("  none — Digirig not seen as HID (not plugged in, or OS permission)")


def ptt():
    p = PTT()
    print("keying for 1 s — watch the radio's TX LED")
    p.set(True); time.sleep(1.0); p.set(False)


def tone():
    p = PTT()
    out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                             device=find_audio_device("output"), latency="low")
    out.start()
    amp = 0.3 * 32767
    frames = [struct.pack(f"<{FRAME}h", *[int(amp * math.sin(2 * math.pi * 800 * (n * FRAME + i) / RATE))
                                           for i in range(FRAME)]) for n in range(100)]  # 2 s
    print("keying + 800 Hz tone for 2 s — listen on another radio")
    p.set(True); time.sleep(0.2)
    for f in frames: out.write(f)
    time.sleep(0.2); p.set(False)
    out.stop()


def rx():
    inp = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                            device=find_audio_device("input"), latency="low")
    inp.start()
    print("recording 5 s from the radio — have someone transmit, or open the squelch")
    chunks = []
    for n in range(250):
        buf, _ = inp.read(FRAME)
        chunks.append(bytes(buf))
        if n % 25 == 24:
            level = rms(b"".join(chunks[-25:]))
            print(f"  {(n + 1) / 50:>4.1f}s  rms={level:6.0f}  {'#' * min(60, int(level / 100))}")
    inp.stop()
    pcm = b"".join(chunks)
    with wave.open("rx.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
    print("saved rx.wav — playing back on this computer's speakers")
    out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, latency="low")
    out.start(); out.write(pcm); out.stop()
    print("RX_GATE_RMS should sit above the silent rms and below the speech rms")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "devices"
    steps = {"devices": devices, "ptt": ptt, "tone": tone, "rx": rx}
    for name in (steps if cmd == "all" else [cmd]):
        print(f"\n### {name}")
        steps[name]()
