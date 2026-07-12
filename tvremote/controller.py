"""Qt bridge to the active device driver.

Holds the currently-selected `TVDriver`, re-exposes its callbacks as Qt signals,
and forwards logical-key commands. Switching the active device tears down the old
driver and builds the new one. Emitting a PySide6 signal from a driver's worker
thread is safe (queued delivery to GUI-thread slots).
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from . import config, drivers, logical
from .drivers.base import DeviceInfo


class Controller(QObject):
    statusChanged = Signal(str)          # "connected" | "offline" | "pairing" | ...
    appsChanged = Signal(list)           # [{"appId":..., "name":...}, ...]
    gamepadChanged = Signal(bool)        # a controller was (dis)connected
    castResult = Signal(bool, str)       # (ok, message) after a cast attempt
    deviceChanged = Signal()             # active device switched / profiles changed
    gamepadKey = Signal(str)             # a logical key from the gamepad (bg thread)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.driver = None
        self._key_sink = None            # if set, gamepad keys go here (e.g. the wizard)
        # Marshal gamepad keys onto the GUI thread, then route them.
        self.gamepadKey.connect(self._route_gamepad)
        self._build_active()

    # ---- device lifecycle ------------------------------------------------
    def _build_active(self):
        prof = config.active_device(self.cfg)
        if not prof:
            self.driver = None
            return
        cls = drivers.get(prof.get("brand"))
        if cls is None:
            self.driver = None
            return
        dev = DeviceInfo(brand=prof["brand"], name=prof.get("name", ""), host=prof["host"],
                         model=prof.get("model", ""), mac=prof.get("mac", ""))
        # pass the *live* profile dict so pairing/token writes persist into cfg
        self.driver = cls(dev, profile=prof, on_status=self._on_status, on_apps=self._on_apps)

    def start(self):
        if self.driver:
            self.driver.start()

    def stop(self):
        if self.driver:
            self.driver.stop()

    def switch_device(self, dev_id: str):
        self.stop()
        config.set_active(self.cfg, dev_id)
        config.save(self.cfg)
        self._build_active()
        self.start()
        self.deviceChanged.emit()

    def has_device(self) -> bool:
        return self.driver is not None

    def capabilities(self) -> set:
        return self.driver.capabilities() if self.driver else set()

    def active_name(self) -> str:
        prof = config.active_device(self.cfg)
        return prof.get("name", "No device") if prof else "No device"

    # ---- callbacks from driver thread ------------------------------------
    def _on_status(self, s):
        self.statusChanged.emit(s)
        if s == "connected":
            config.save(self.cfg)  # persist any credential the driver just stored

    def _on_apps(self, a):
        self.appsChanged.emit(a)

    def set_gamepad_status(self, ok: bool):
        self.gamepadChanged.emit(bool(ok))

    # ---- gamepad routing -------------------------------------------------
    def feed_gamepad(self, key: str):
        """Gamepad callback (worker thread) → marshal to the GUI thread."""
        self.gamepadKey.emit(key)

    def set_key_sink(self, fn):
        """Redirect gamepad keys to `fn` (e.g. the setup wizard); None → the TV."""
        self._key_sink = fn

    @Slot(str)
    def _route_gamepad(self, key: str):
        if self._key_sink is not None:
            self._key_sink(key)
        else:
            self.send_key(key)

    def reload_active(self):
        """Rebuild the driver for the current active profile (after first pairing)."""
        self.stop()
        self._build_active()

    # ---- control (logical keys) ------------------------------------------
    @Slot(str)
    def send_key(self, key: str):
        if self.driver:
            self.driver.send_key(key)

    @Slot(str)
    def send_text(self, text: str):
        if self.driver and text:
            self.driver.send_text(text)

    @Slot(str)
    def launch_app(self, app_id: str):
        if self.driver:
            self.driver.launch_app(app_id)

    @Slot()
    def power(self):
        if self.driver:
            self.driver.power()

    @Slot()
    def volume_up(self):
        self.send_key(logical.VOLUP)

    @Slot()
    def volume_down(self):
        self.send_key(logical.VOLDOWN)

    @Slot()
    def refresh_apps(self):
        if self.driver:
            self.driver.refresh_apps()

    def cast_media(self, target: str):
        """Cast a local file / media URL via the active driver (off the UI thread)."""
        if not self.driver:
            self.castResult.emit(False, "No device selected.")
            return

        def work():
            ok, msg = self.driver.cast(target)
            self.castResult.emit(ok, msg)
        threading.Thread(target=work, name="cast", daemon=True).start()
