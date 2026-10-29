"""Hardware layer: Digirig audio device lookup + PTT (CM108 HID)."""
import array
import logging
import math
import os

import sounddevice as sd

log = logging.getLogger("hw")

RATE, FRAME = 48000, 960            # 20 ms frames, CM108 native rate
AUDIO_KEYS = ("usb pnp", "c-media", "digirig", "usb audio")

PTT_MODE = os.environ.get("PTT_MODE", "cm108")      # cm108 (Digirig Lite) | serial (Digirig Mobile) | none
PTT_SERIAL = os.environ.get("PTT_SERIAL", "/dev/ttyUSB0")
PTT_HID_PATH = os.environ.get("PTT_HID_PATH")
PTT_GPIO = int(os.environ.get("PTT_GPIO", "3"))     # CM108 GPIO pin used by the Digirig (3 per their docs)


def rms(pcm: bytes) -> float:
    a = array.array("h", pcm)
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


def find_audio_device(kind: str) -> int:
    """kind = 'input' | 'output'. AUDIO_DEV env (index or name substring) overrides auto-detect."""
    env = os.environ.get("AUDIO_DEV")
    chan = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        name = d["name"].lower()
        if d[chan] <= 0:
            continue
        if env:
            if (env.isdigit() and int(env) == i) or (not env.isdigit() and env.lower() in name):
                return i
        elif any(k in name for k in AUDIO_KEYS):
            return i
    raise RuntimeError(f"Digirig {kind} device not found. Plugged in? Run: python hwtest.py devices")


class PTT:
    """
    cm108: Digirig Lite keys PTT from a GPIO of the CM108 codec via a USB HID output report.
           Report = [report_id=0, 0, gpio_mask, gpio_value, 0]; GPIOn -> bit (1 << (n-1)).
    serial: Digirig Mobile keys PTT from the CP2102 RTS line.
    """
    CM108_VID = 0x0D8C

    def __init__(self):
        self.mode, self._dev, self.keyed = PTT_MODE, None, True
        self.bit = 1 << (PTT_GPIO - 1)
        if self.mode == "cm108":
            import hid
            self._dev = hid.device()
            if PTT_HID_PATH:
                self._dev.open_path(PTT_HID_PATH.encode())
            else:
                devs = [d for d in hid.enumerate() if d["vendor_id"] == self.CM108_VID]
                if not devs:
                    raise RuntimeError("No C-Media HID found. Digirig plugged in? Run: python hwtest.py devices")
                self._dev.open_path(devs[0]["path"])
        elif self.mode == "serial":
            import serial
            self._dev = serial.Serial(PTT_SERIAL)
        self.set(False)
        log.info("PTT mode=%s gpio=%d", self.mode, PTT_GPIO)

    def set(self, on: bool):
        if on == self.keyed:
            return
        if self.mode == "cm108":
            self._dev.write([0x00, 0x00, self.bit, self.bit if on else 0x00, 0x00])
        elif self.mode == "serial":
            self._dev.rts = on
        self.keyed = on
        log.info("PTT %s", "ON" if on else "off")
