"""Make opuslib find Homebrew's libopus on macOS (ctypes.util.find_library doesn't look in /opt/homebrew/lib)."""
import ctypes.util
import os

_orig = ctypes.util.find_library


def _find(name):
    if name == "opus":
        for p in ("/opt/homebrew/lib/libopus.dylib", "/usr/local/lib/libopus.dylib"):
            if os.path.exists(p):
                return p
    return _orig(name)


ctypes.util.find_library = _find
