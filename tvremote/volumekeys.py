"""Opt-in Desktop-mode capture of the physical volume rocker (Steam Deck / laptops).

In KDE Plasma / X11 / Wayland desktops the volume keys emit evdev
KEY_VOLUMEUP / KEY_VOLUMEDOWN. When `grab_volume_keys` is enabled we read the
input device directly and EVIOCGRAB it while the app is the active window, so
the rocker drives the *TV* instead of local system volume.

This is best-effort: if python-evdev isn't installed or the device isn't
readable (no udev rule / not in `input` group) it disables itself silently and
the on-screen / keyboard / grip-button volume controls still work. It has no
effect in Steam Game Mode, where gamescope owns the rocker.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("tvremote.volumekeys")


class VolumeKeyGrabber:
    def __init__(self, on_up, on_down):
        self._on_up = on_up
        self._on_down = on_down
        self._thread = None
        self._stop = False
        self._devices = []

    def start(self) -> bool:
        try:
            import evdev  # noqa: F401
        except ImportError:
            log.info("python-evdev not installed; rocker capture disabled")
            return False
        self._thread = threading.Thread(target=self._run, name="volkeys", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop = True
        for d in self._devices:
            try:
                d.ungrab()
            except Exception:
                pass

    def _run(self):
        from evdev import ecodes, InputDevice, list_devices

        found = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_VOLUMEUP in caps and ecodes.KEY_VOLUMEDOWN in caps:
                found.append(dev)
        if not found:
            log.info("no volume-key device found/accessible; rocker capture disabled")
            return
        self._devices = found
        for dev in found:
            try:
                dev.grab()
            except OSError as e:
                log.info("could not grab %s (%s); leaving system volume intact", dev.path, e)

        selectors = {dev.fd: dev for dev in found}
        import select
        while not self._stop:
            r, _, _ = select.select(selectors, [], [], 0.5)
            for fd in r:
                dev = selectors[fd]
                try:
                    for ev in dev.read():
                        if ev.type == ecodes.EV_KEY and ev.value == 1:  # key down
                            if ev.code == ecodes.KEY_VOLUMEUP:
                                self._on_up()
                            elif ev.code == ecodes.KEY_VOLUMEDOWN:
                                self._on_down()
                except OSError:
                    continue
