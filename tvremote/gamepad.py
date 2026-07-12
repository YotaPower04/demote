"""Read a game controller via the evdev interface (/dev/input/event*).

Pure-Python (struct/fcntl only) so it needs NO extra wheels — important on
SteamOS. Runs on a background thread and turns the Steam Deck's sticks / D-pad /
buttons / triggers into TV remote key presses.

Focus behaviour:
  * While the remote window is focused we **EVIOCGRAB** the controller, so input
    is captured exclusively and a game in the background does NOT also receive it.
  * When the remote loses focus we ungrab and stop translating, so the game gets
    the controller back untouched.
Both are driven by `set_active()` (wired to window focus in main.py).

We read evdev (not the older joystick /dev/input/js* API) specifically because
EVIOCGRAB — the exclusive-capture ioctl — only exists on evdev devices, and a
grab there also silences the jsN interface, so evdev must be the read path too.
"""
from __future__ import annotations

import fcntl
import glob
import logging
import os
import select
import struct
import threading
import time

from . import logical

log = logging.getLogger("tvremote.gamepad")

# struct input_event (64-bit): time(2x long), type u16, code u16, value s32
_EV = struct.Struct("llHHi")
_SIZE = _EV.size  # 24
EV_KEY = 0x01
EV_ABS = 0x03

# ioctl numbers
def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


EVIOCGRAB = _IOC(1, "E", 0x90, 4)          # _IOW('E', 0x90, int)
def _EVIOCGABS(code):
    return _IOC(2, "E", 0x40 + code, 24)   # _IOR('E', 0x40+abs, struct input_absinfo)

# evdev button codes (Linux gamepad, Xbox layout) -> TV key
BTN_KEYS = {
    0x130: logical.OK,      # BTN_SOUTH / A -> OK
    0x131: logical.BACK,    # BTN_EAST  / B -> Back
    0x133: logical.HOME,    # BTN_X        -> Home
    0x134: logical.MENU,    # BTN_Y        -> Menu
    0x136: logical.CHDOWN,  # BTN_TL / LB  -> Channel down
    0x137: logical.CHUP,    # BTN_TR / RB  -> Channel up
    0x13a: logical.SOURCE,  # BTN_SELECT   -> Source
    0x13b: logical.EXIT,    # BTN_START    -> Exit
    0x13d: logical.MUTE,    # BTN_THUMBL / L3 -> Mute
    0x13e: logical.INFO,    # BTN_THUMBR / R3 -> Info
    # 0x13c BTN_MODE (guide): owned by Steam, ignored.
}

ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0, 1, 2, 3, 4, 5
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
# directional axis -> (negative-key, positive-key). up/left = negative.
AXIS_DIRS = {
    ABS_X: (logical.LEFT, logical.RIGHT),
    ABS_Y: (logical.UP, logical.DOWN),
    ABS_HAT0X: (logical.LEFT, logical.RIGHT),
    ABS_HAT0Y: (logical.UP, logical.DOWN),
    ABS_RX: (logical.CHDOWN, logical.CHUP),
    ABS_RY: (logical.VOLUP, logical.VOLDOWN),
}
TRIGGER_KEYS = {ABS_Z: logical.VOLDOWN, ABS_RZ: logical.VOLUP}
HATS = {ABS_HAT0X, ABS_HAT0Y}

REPEAT_FIRST = 0.45
REPEAT_NEXT = 0.20


class GamePad:
    def __init__(self, on_key, on_status=None):
        self._on_key = on_key
        self._on_status = on_status or (lambda ok: None)
        self._thread = None
        self._stop = False
        self._active = True          # only act on / grab input while focused
        self._held = {}
        self._stick_thr = {}         # abs code -> stick direction threshold
        self._trig_thr = {}          # abs code -> trigger "pressed" threshold

    def set_active(self, active: bool):
        """Enable/disable exclusive controller capture (call on focus changes)."""
        self._active = bool(active)
        if not active:
            self._held.clear()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    # ---- device handling -------------------------------------------------
    def _find_event_device(self):
        """Return an evdev path for the controller (mapped from js* via sysfs)."""
        for js in sorted(glob.glob("/dev/input/js*")):
            sysdir = f"/sys/class/input/{os.path.basename(js)}/device"
            try:
                for entry in os.listdir(sysdir):
                    if entry.startswith("event"):
                        return f"/dev/input/{entry}"
            except OSError:
                continue
        # fallback: first event device that we can open
        for ev in sorted(glob.glob("/dev/input/event*")):
            return ev
        return None

    def _open(self):
        path = self._find_event_device()
        if not path:
            return None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.debug("cannot open %s: %s", path, e)
            return None
        log.info("gamepad: reading %s (exclusive grab while focused)", path)
        self._load_axis_ranges(fd)
        return fd

    def _load_axis_ranges(self, fd):
        self._stick_thr.clear()
        self._trig_thr.clear()
        for code in (ABS_X, ABS_Y, ABS_RX, ABS_RY):
            rng = self._absinfo(fd, code)
            if rng:
                lo, hi = rng
                self._stick_thr[code] = int(max(abs(lo), abs(hi)) * 0.6) or 20000
        for code in (ABS_Z, ABS_RZ):
            rng = self._absinfo(fd, code)
            if rng:
                lo, hi = rng
                self._trig_thr[code] = int(lo + (hi - lo) * 0.3)

    def _absinfo(self, fd, code):
        try:
            buf = fcntl.ioctl(fd, _EVIOCGABS(code), b"\x00" * 24)
            _val, lo, hi, _f, _flat, _res = struct.unpack("iiiiii", buf)
            return (lo, hi) if hi > lo else None
        except OSError:
            return None

    def _run(self):
        connected = False
        fd = None
        while not self._stop:
            if fd is None:
                fd = self._open()
                if fd is None:
                    if connected:
                        connected = False
                        self._on_status(False)
                    time.sleep(1.5)
                    continue
                connected = True
                self._on_status(True)
            try:
                self._pump(fd)
            except OSError:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 0)
                except OSError:
                    pass
                os.close(fd)
                fd = None
                self._held.clear()
        if fd is not None:
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            os.close(fd)

    def _pump(self, fd):
        applied_grab = None
        while not self._stop:
            if self._active != applied_grab:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 1 if self._active else 0)
                    applied_grab = self._active
                except OSError as e:
                    log.info("EVIOCGRAB failed: %s", e)
                    applied_grab = self._active  # avoid hammering
            r, _, _ = select.select([fd], [], [], self._next_timeout())
            now = time.monotonic()
            if r:
                data = os.read(fd, _SIZE * 64)  # may raise OSError on unplug
                for i in range(0, len(data) - _SIZE + 1, _SIZE):
                    _s, _u, etype, code, value = _EV.unpack(data[i:i + _SIZE])
                    self._dispatch(etype, code, value, now)
            self._fire_repeats(now)

    # ---- event handling (pure, unit-testable) ----------------------------
    def _dispatch(self, etype, code, value, now):
        if not self._active:
            return  # still drained in _pump; we just don't act on it
        if etype == EV_KEY:
            if value == 1 and code in BTN_KEYS:
                self._emit(BTN_KEYS[code])
        elif etype == EV_ABS:
            if code in TRIGGER_KEYS:
                thr = self._trig_thr.get(code, 8000)
                self._axis_hold(("t", code), value >= thr, TRIGGER_KEYS[code], now)
            elif code in AXIS_DIRS:
                neg, pos = AXIS_DIRS[code]
                if code in HATS:
                    self._axis_hold(("n", code), value < 0, neg, now)
                    self._axis_hold(("p", code), value > 0, pos, now)
                else:
                    thr = self._stick_thr.get(code, 20000)
                    self._axis_hold(("n", code), value <= -thr, neg, now)
                    self._axis_hold(("p", code), value >= thr, pos, now)

    def _axis_hold(self, bid, active, key, now):
        if active:
            if bid not in self._held:
                self._emit(key)
                self._held[bid] = (key, now + REPEAT_FIRST)
        else:
            self._held.pop(bid, None)

    def _fire_repeats(self, now):
        for bid, (key, nxt) in list(self._held.items()):
            if now >= nxt:
                self._emit(key)
                self._held[bid] = (key, now + REPEAT_NEXT)

    def _next_timeout(self):
        if not self._held:
            return 0.2
        soonest = min(nxt for _, nxt in self._held.values())
        return max(0.0, min(0.1, soonest - time.monotonic()))

    def _emit(self, key):
        try:
            self._on_key(key)
        except Exception:
            log.exception("gamepad key callback failed")
