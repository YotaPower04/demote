#!/usr/bin/env python3
"""Demote — a universal TV/streamer remote for the Steam Deck & Linux desktop.

("Demote" = Deck + Remote.)

Launches the Qt UI, the active device driver, the focus-gated controller reader,
and (optionally) the Desktop-mode volume-rocker grabber. On first run (no saved
device) it shows the setup wizard to discover and pair one.

Flags:
  --fullscreen / --deck   force the big-touch landscape layout, fullscreen
  --portrait              force the windowed portrait layout (old desktop mode)
By default it auto-detects: fullscreen landscape on the Steam Deck (or any small
landscape screen), windowed landscape on desktop.
"""
from __future__ import annotations

import logging
import os
import signal
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from tvremote import config
from tvremote.controller import Controller
from tvremote.gamepad import GamePad
from tvremote.ui import RemoteWindow, SetupWizard
from tvremote.volumekeys import VolumeKeyGrabber


def decide_mode(app, argv):
    """Return (landscape, fullscreen)."""
    if "--portrait" in argv:
        return False, False
    if "--fullscreen" in argv or "--deck" in argv:
        return True, True
    if os.environ.get("SteamDeck") or os.environ.get("SteamGamepadUI"):
        return True, True
    return True, False  # default: landscape windowed


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config.load()

    app = QApplication(sys.argv)
    app.setApplicationName("Demote")
    app.setDesktopFileName("io.github.yotapower04.Demote")  # Wayland app_id / icon assoc

    controller = Controller(cfg)

    # Gamepad reads go through the controller, which routes them to the setup
    # wizard while it's open (so the Deck can drive pairing) and to the TV
    # otherwise. Started before the wizard so first-run pairing is navigable.
    pad = GamePad(on_key=controller.feed_gamepad, on_status=controller.set_gamepad_status)
    pad.start()

    # First run (or all devices removed): pair one before showing the remote.
    if not config.active_device(cfg):
        pad.set_active(True)
        wiz = SetupWizard(cfg)
        controller.set_key_sink(wiz.gamepad_key)
        accepted = wiz.exec() == QDialog.Accepted
        controller.set_key_sink(None)
        if not accepted:
            pad.stop()
            sys.exit(0)
        controller.reload_active()

    landscape, fullscreen = decide_mode(app, sys.argv[1:])
    window = RemoteWindow(controller, landscape=landscape)
    if fullscreen:
        window.showFullScreen()
    else:
        size = (1200, 680) if landscape else (470, 900)
        window.resize(*size)
        window.show()

    controller.start()

    grabber = None
    if cfg.get("settings", {}).get("grab_volume_keys"):
        grabber = VolumeKeyGrabber(controller.volume_up, controller.volume_down)
        grabber.start()

    # Only translate/capture controller input while the remote is the focused app,
    # so a backgrounded remote never fires keys and a focused one grabs exclusively.
    app.applicationStateChanged.connect(
        lambda s: pad.set_active(s == Qt.ApplicationActive))
    pad.set_active(app.applicationState() == Qt.ApplicationActive)

    signal.signal(signal.SIGINT, signal.SIG_DFL)  # let Ctrl-C quit cleanly
    rc = app.exec()

    pad.stop()
    if grabber:
        grabber.stop()
    controller.stop()
    sys.exit(rc)


if __name__ == "__main__":
    main()
