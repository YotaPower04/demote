"""Qt UI: the on-screen remote, plus the first-run setup/pairing wizard.

Two layouts share the same buttons:
  * portrait  — a tall single column, good in a window on the desktop PC.
  * landscape — a 3-pane layout with large touch targets that fills the Steam
                Deck's 1280x800 screen (shown fullscreen). Controls a gamepad
                maps to show a small Deck-input badge.

Sections are shown/hidden based on the active driver's `capabilities()`, so a
cast-only DLNA renderer shows just what it can do, while a Samsung shows the lot.
"""
from __future__ import annotations

import threading
from functools import partial

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from . import config, discovery, drivers, icons, logical
from .controller import Controller
from .drivers.base import DeviceInfo
from .logical import Cap

STATUS_TEXT = {
    "starting": ("#c8a02a", "Starting…"),
    "connecting": ("#c8a02a", "Connecting…"),
    "pairing": ("#c8a02a", "Accept the prompt on the device…"),
    "connected": ("#3fa34d", "Connected"),
    "offline": ("#b23a34", "Device asleep — press Power to wake"),
    "denied": ("#b23a34", "Pairing denied — re-pair the device"),
}


def _style(landscape: bool) -> str:
    fs = 19 if landscape else 15
    mh = 62 if landscape else 46
    rad = 12 if landscape else 10
    return f"""
    QWidget {{ background: #1b1d23; color: #e7e9ee; font-size: {fs}px; }}
    QPushButton {{
        background: #2a2d36; border: 1px solid #333844; border-radius: {rad}px;
        padding: 8px; min-height: {mh}px;
    }}
    QPushButton:hover {{ background: #353a46; }}
    QPushButton:pressed {{ background: #454b5a; }}
    QPushButton[kind="power"] {{ background: #7a2620; border-color: #a4352c; }}
    QPushButton[kind="power"]:hover {{ background: #98332a; }}
    QPushButton[kind="ok"] {{ background: #24507a; border-color: #2f6ba4; font-weight: bold; }}
    QPushButton[kind="ok"]:hover {{ background: #2c6092; }}
    QPushButton[kind="chip"] {{ min-height: 34px; padding: 4px 10px; }}
    QPushButton[pinkey="true"] {{ min-height: 40px; padding: 4px; font-size: {fs + 2}px; }}
    QPushButton[padfocus="true"] {{ border: 2px solid #2f6ba4; background: #2c3a4f; }}
    QToolButton {{
        background: #23262e; border: 1px solid #333844; border-radius: 12px; padding: 6px;
    }}
    QToolButton:hover {{ background: #2f333d; }}
    QLabel#status {{ color: #9aa2b1; font-size: {fs - 2}px; }}
    QLabel#section {{ color: #8b93a3; font-size: {fs - 3}px; }}
    QLabel#dropzone {{ border: 2px dashed #3a4150; border-radius: 12px;
                       color: #8b93a3; padding: 12px; }}
    QLabel#dropzone[hot="true"] {{ border-color: #2f6ba4; color: #cfe0f2; background: #1f2530; }}
    QLabel#dropzone[flash="ok"] {{ border-color: #3fa34d; color: #bfe6c6; }}
    QLabel#dropzone[flash="err"] {{ border-color: #b23a34; color: #eab5b1; }}
    QLineEdit {{ background: #24262e; border: 1px solid #333844; border-radius: 8px;
                 padding: 10px; min-height: {mh - 20}px; }}
    QListWidget {{ background: #23262e; border: 1px solid #333844; border-radius: 8px; }}
    QScrollArea {{ border: none; }}
    """


class HintButton(QPushButton):
    """A button that can paint a small controller-hint badge in its corner."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._badge = None

    def set_badge(self, pm):
        self._badge = pm

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._badge is not None:
            p = QPainter(self)
            p.drawPixmap(self.width() - self._badge.width() - 6, 6, self._badge)
            p.end()


class DropZone(QLabel):
    """Accepts dropped video files / media URLs and casts them to the device."""

    IDLE = "⤓  Drop a video file or media URL here to cast"

    def __init__(self, on_drop, parent=None):
        super().__init__(self.IDLE, parent)
        self._on_drop = on_drop
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(72)

    def _flag(self, name, value):
        self.setProperty(name, value)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
            self._flag("hot", "true")

    def dragLeaveEvent(self, e):
        self._flag("hot", "false")

    def dropEvent(self, e):
        md = e.mimeData()
        target = None
        if md.hasUrls() and md.urls():
            u = md.urls()[0]
            target = u.toLocalFile() if u.isLocalFile() else u.toString()
        elif md.hasText():
            target = md.text().strip()
        self._flag("hot", "false")
        if target:
            self.setText(f"Casting…\n{target[:80]}")
            self._on_drop(target)


class RemoteWindow(QWidget):
    def __init__(self, controller: Controller, landscape: bool = False):
        super().__init__()
        self.ctrl = controller
        self.landscape = landscape
        self.setWindowTitle("Demote")
        self.setStyleSheet(_style(landscape))
        self._icon_px = 34 if landscape else 22
        self._tile_w, self._tile_h, self._tile_icon = (
            (116, 104, 54) if landscape else (94, 88, 46))
        if not landscape:
            self.setMinimumWidth(360)

        # The body is rebuilt whenever the active device (and thus its
        # capabilities) changes, so switching devices re-gates the controls.
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._body = None
        self._build_ui()

        controller.statusChanged.connect(self.on_status)
        controller.appsChanged.connect(self.on_apps)
        controller.gamepadChanged.connect(self.on_gamepad)
        controller.castResult.connect(self.on_cast)
        controller.deviceChanged.connect(self.on_device_changed)

    def _has(self, cap):
        return cap in self.caps

    # ---- assemblers ------------------------------------------------------
    def _build_ui(self):
        """(Re)build the whole remote for the active device's capabilities."""
        self.caps = self.ctrl.capabilities()
        self.drop = None
        self.text_edit = None
        if self._body is not None:
            self._root.removeWidget(self._body)
            self._body.deleteLater()
        self._body = QWidget()
        if self.landscape:
            self._build_landscape(self._body)
        else:
            self._build_portrait(self._body)
        self._root.addWidget(self._body)

    def _build_portrait(self, host):
        root = QVBoxLayout(host)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addLayout(self._header())
        root.addLayout(self._power_source_row())
        root.addWidget(self._divider())
        if self._has(Cap.NAV):
            root.addWidget(self._nav_pad())
        if self._has(Cap.VOLUME) or self._has(Cap.CHANNELS):
            row = QHBoxLayout()
            if self._has(Cap.VOLUME):
                row.addWidget(self._volume_block())
            if self._has(Cap.CHANNELS):
                row.addWidget(self._channel_block())
            root.addLayout(row)
        if self._has(Cap.MEDIA):
            root.addLayout(self._media_row())
        if self._has(Cap.NUMBERS):
            root.addWidget(self._numpad())
        if self._has(Cap.APPS):
            root.addWidget(self._apps_section())
        if self._has(Cap.TEXT):
            root.addLayout(self._text_row())
        if self._has(Cap.CAST):
            self.drop = DropZone(self.ctrl.cast_media)
            root.addWidget(self.drop)
        root.addStretch(0)

    def _build_landscape(self, host):
        root = QVBoxLayout(host)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)
        root.addLayout(self._header())

        cols = QHBoxLayout()
        cols.setSpacing(16)

        left = QVBoxLayout()
        left.addLayout(self._power_source_row())
        if self._has(Cap.NAV):
            nav = self._nav_pad()
            nav.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            left.addWidget(nav, 1)

        mid = QVBoxLayout()
        if self._has(Cap.VOLUME) or self._has(Cap.CHANNELS):
            vc = QHBoxLayout()
            if self._has(Cap.VOLUME):
                vc.addWidget(self._volume_block())
            if self._has(Cap.CHANNELS):
                vc.addWidget(self._channel_block())
            mid.addLayout(vc, 1)
        if self._has(Cap.MEDIA):
            mid.addLayout(self._media_row())
        if self._has(Cap.NUMBERS):
            mid.addWidget(self._numpad())
        mid.addStretch(0)

        right = QVBoxLayout()
        if self._has(Cap.APPS):
            right.addWidget(self._apps_section(), 1)
        if self._has(Cap.TEXT):
            right.addLayout(self._text_row())
        if self._has(Cap.CAST):
            self.drop = DropZone(self.ctrl.cast_media)
            right.addWidget(self.drop)

        cols.addLayout(left, 4)
        cols.addLayout(mid, 4)
        cols.addLayout(right, 3)
        root.addLayout(cols, 1)

    # ---- sections --------------------------------------------------------
    def _header(self):
        row = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color:#c8a02a; font-size:18px;")
        self.dev_btn = QPushButton("▾ " + self.ctrl.active_name())
        self.dev_btn.setProperty("kind", "chip")
        self.dev_btn.setFocusPolicy(Qt.NoFocus)
        self.dev_btn.setIconSize(QSize(20, 20))
        self.dev_btn.clicked.connect(self._open_device_menu)
        self._update_dev_icon()
        self.pad_icon = QLabel("🎮")
        self.pad_icon.setToolTip("Controller connected")
        self.pad_icon.setVisible(False)
        self.status = QLabel("Starting…")
        self.status.setObjectName("status")
        gear = self._chip("⚙", self.open_settings)
        quit_btn = self._chip("✕", self.close)
        row.addWidget(self.dot)
        row.addWidget(self.dev_btn)
        row.addWidget(self.pad_icon)
        row.addStretch(1)
        row.addWidget(self.status)
        row.addWidget(gear)
        row.addWidget(quit_btn)
        return row

    def _power_source_row(self):
        row = QHBoxLayout()
        if self._has(Cap.POWER):
            row.addWidget(self._btn("Power", self.ctrl.power, "power", icon="power"))
        if self._has(Cap.SOURCE):
            row.addWidget(self._key_btn("Source", logical.SOURCE, icon="source", hint="select"))
        if self._has(Cap.NAV):
            row.addWidget(self._key_btn("Home", logical.HOME, icon="home", hint="X"))
            row.addWidget(self._key_btn("Back", logical.BACK, icon="back", hint="B"))
            row.addWidget(self._key_btn("Exit", logical.EXIT, icon="exit", hint="start"))
        return row

    def _nav_pad(self):
        wrap = QWidget()
        g = QGridLayout(wrap)
        g.setSpacing(8)
        g.addWidget(self._key_btn("Info", logical.INFO, icon="info", hint="R3"), 0, 0)
        g.addWidget(self._key_btn("", logical.UP, icon="up", hint="dpad"), 0, 1)
        g.addWidget(self._key_btn("Menu", logical.MENU, icon="menu", hint="Y"), 0, 2)
        g.addWidget(self._key_btn("", logical.LEFT, icon="left", hint="dpad"), 1, 0)
        g.addWidget(self._btn("OK", partial(self.ctrl.send_key, logical.OK), "ok", hint="A"), 1, 1)
        g.addWidget(self._key_btn("", logical.RIGHT, icon="right", hint="dpad"), 1, 2)
        g.addWidget(self._key_btn("Tools", logical.TOOLS, icon="tools"), 2, 0)
        g.addWidget(self._key_btn("", logical.DOWN, icon="down", hint="dpad"), 2, 1)
        g.addWidget(self._key_btn("Guide", logical.MENU, icon="menu"), 2, 2)
        for c in range(3):
            g.setColumnStretch(c, 1)
        for r in range(3):
            g.setRowStretch(r, 1)
        return wrap

    def _volume_block(self):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._label("Volume"))
        v.addWidget(self._key_btn("", logical.VOLUP, icon="volup", hint="R2"))
        v.addWidget(self._key_btn("Mute", logical.MUTE, icon="mute", hint="L3"))
        v.addWidget(self._key_btn("", logical.VOLDOWN, icon="voldown", hint="L2"))
        return wrap

    def _channel_block(self):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._label("Channel"))
        v.addWidget(self._key_btn("", logical.CHUP, icon="chup", hint="R1"))
        v.addWidget(self._key_btn("List", logical.CH_LIST, icon="list"))
        v.addWidget(self._key_btn("", logical.CHDOWN, icon="chdown", hint="L1"))
        return wrap

    def _media_row(self):
        row = QHBoxLayout()
        row.addWidget(self._key_btn("", logical.REWIND, icon="rewind"))
        row.addWidget(self._key_btn("", logical.PLAY, icon="play"))
        row.addWidget(self._key_btn("", logical.PAUSE, icon="pause"))
        row.addWidget(self._key_btn("", logical.STOP, icon="stop"))
        row.addWidget(self._key_btn("", logical.FF, icon="ff"))
        return row

    def _numpad(self):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        toggle = QPushButton("123  ▾")
        toggle.setCheckable(True)
        toggle.setFocusPolicy(Qt.NoFocus)
        v.addWidget(toggle)
        pad = QWidget()
        g = QGridLayout(pad)
        g.setContentsMargins(0, 0, 0, 0)
        for idx, n in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9]):
            g.addWidget(self._key_btn(str(n), logical.NUM[n]), idx // 3, idx % 3)
        g.addWidget(self._key_btn("0", logical.NUM[0]), 3, 1)
        pad.setVisible(False)
        v.addWidget(pad)

        def _toggle(checked):
            pad.setVisible(checked)
            toggle.setText("123  ▴" if checked else "123  ▾")
        toggle.toggled.connect(_toggle)
        return wrap

    def _apps_section(self):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._label("Apps"))
        self.apps_area = QScrollArea()
        self.apps_area.setWidgetResizable(True)
        self.apps_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if not self.landscape:
            self.apps_area.setFixedHeight(2 * self._tile_h + 30)
        inner = QWidget()
        self.apps_layout = QGridLayout(inner)
        self.apps_layout.setContentsMargins(0, 0, 0, 0)
        self.apps_layout.setSpacing(8)
        self.apps_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.apps_area.setWidget(inner)
        v.addWidget(self.apps_area, 1)
        self._render_apps([])
        return wrap

    def _text_row(self):
        row = QHBoxLayout()
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Type into the device, then Enter…")
        self.text_edit.returnPressed.connect(self._send_text)
        send = QPushButton("Send")
        send.setFocusPolicy(Qt.NoFocus)
        send.clicked.connect(self._send_text)
        row.addWidget(self.text_edit)
        row.addWidget(send)
        return row

    # ---- helpers ---------------------------------------------------------
    def _btn(self, text, slot, kind="", icon=None, hint=None):
        b = HintButton(text)
        if kind:
            b.setProperty("kind", kind)
        b.setFocusPolicy(Qt.NoFocus)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if icon:
            b.setIcon(icons.get(icon))
            b.setIconSize(QSize(self._icon_px, self._icon_px))
        if hint and self.landscape:
            b.set_badge(icons.pad_badge(hint, 26))
        b.clicked.connect(slot)
        return b

    def _key_btn(self, text, key, kind="", icon=None, hint=None):
        return self._btn(text, partial(self.ctrl.send_key, key), kind, icon, hint)

    def _chip(self, text, slot):
        b = QPushButton(text)
        b.setProperty("kind", "chip")
        b.setFocusPolicy(Qt.NoFocus)
        b.setFixedWidth(46)
        b.clicked.connect(slot)
        return b

    def _label(self, text):
        lab = QLabel(text)
        lab.setObjectName("section")
        return lab

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#2a2d36;")
        return line

    def _send_text(self):
        txt = self.text_edit.text()
        if txt:
            self.ctrl.send_text(txt)
            self.text_edit.clear()

    def _app_tile(self, app):
        name = app["name"]
        t = QToolButton()
        t.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        t.setFocusPolicy(Qt.NoFocus)
        t.setIcon(icons.app_tile(name))
        t.setIconSize(QSize(self._tile_icon, self._tile_icon))
        t.setFixedSize(self._tile_w, self._tile_h)
        fm = QFontMetrics(t.font())
        t.setText(fm.elidedText(name, Qt.ElideRight, self._tile_w - 12))
        t.setToolTip(name)
        t.clicked.connect(partial(self.ctrl.launch_app, str(app["appId"])))
        return t

    def _render_apps(self, apps):
        while self.apps_layout.count():
            item = self.apps_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cols = 2 if self.landscape else 4
        for i, app in enumerate(apps):
            self.apps_layout.addWidget(self._app_tile(app), i // cols, i % cols)

    # ---- device switcher / settings --------------------------------------
    def _open_device_menu(self):
        menu = QMenu(self)
        active = config.active_device(self.ctrl.cfg)
        active_id = config.device_id(active) if active else None
        for d in self.ctrl.cfg.get("devices", []):
            act = menu.addAction(("● " if config.device_id(d) == active_id else "   ") + d.get("name", "?"))
            act.triggered.connect(partial(self._switch_to, config.device_id(d)))
        menu.addSeparator()
        menu.addAction("Add device…", self._add_device)
        if active:
            menu.addAction(f"Remove “{active.get('name')}”", self._remove_active)
        menu.exec(self.dev_btn.mapToGlobal(self.dev_btn.rect().bottomLeft()))

    def _switch_to(self, dev_id):
        if dev_id != config.device_id(config.active_device(self.ctrl.cfg) or {}):
            self.ctrl.switch_device(dev_id)

    def _add_device(self):
        wiz = SetupWizard(self.ctrl.cfg, self)
        self.ctrl.set_key_sink(wiz.gamepad_key)   # let the gamepad drive the wizard
        accepted = wiz.exec() == QDialog.Accepted
        self.ctrl.set_key_sink(None)
        if accepted and wiz.result_id:
            self.ctrl.switch_device(wiz.result_id)

    def _remove_active(self):
        active = config.active_device(self.ctrl.cfg)
        if not active:
            return
        self.ctrl.stop()
        config.remove_device(self.ctrl.cfg, config.device_id(active))
        config.save(self.ctrl.cfg)
        new = self.ctrl.cfg.get("active_id")
        if new:
            self.ctrl.switch_device(new)
        else:
            self.dev_btn.setText("▾ No device")

    def open_settings(self):
        dlg = SettingsDialog(self.ctrl.cfg, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()

    # ---- slots -----------------------------------------------------------
    @Slot(str)
    def on_status(self, state):
        color, text = STATUS_TEXT.get(state, ("#9aa2b1", state))
        self.dot.setStyleSheet(f"color:{color}; font-size:18px;")
        self.status.setText(text)

    @Slot(list)
    def on_apps(self, apps):
        if apps and self._has(Cap.APPS):
            self._render_apps(apps)

    @Slot(bool)
    def on_gamepad(self, ok):
        self.pad_icon.setVisible(ok)

    @Slot()
    def on_device_changed(self):
        # Rebuild the whole remote so the controls match the new device's
        # capabilities (the header/text/icon are recreated by _build_ui too).
        self._build_ui()

    def _update_dev_icon(self):
        active = config.active_device(self.ctrl.cfg)
        if active and active.get("brand"):
            self.dev_btn.setIcon(icons.brand_icon(active["brand"]))
        else:
            self.dev_btn.setIcon(QIcon())

    @Slot(bool, str)
    def on_cast(self, ok, msg):
        zone = self.drop
        if zone is None:
            return
        zone.setText(("✓  " if ok else "✕  ") + msg)
        zone._flag("flash", "ok" if ok else "err")

        def reset():
            zone.setText(DropZone.IDLE)
            zone._flag("flash", "")
        QTimer.singleShot(5000, reset)

    # ---- keyboard + grip-button control ---------------------------------
    def keyPressEvent(self, event):
        if self.text_edit is not None and self.text_edit.hasFocus():
            return super().keyPressEvent(event)
        k = event.key()
        if k == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            return
        m = {
            Qt.Key_Up: logical.UP, Qt.Key_Down: logical.DOWN,
            Qt.Key_Left: logical.LEFT, Qt.Key_Right: logical.RIGHT,
            Qt.Key_Return: logical.OK, Qt.Key_Enter: logical.OK,
            Qt.Key_Backspace: logical.BACK, Qt.Key_Escape: logical.EXIT,
            Qt.Key_Plus: logical.VOLUP, Qt.Key_Equal: logical.VOLUP, Qt.Key_Minus: logical.VOLDOWN,
            Qt.Key_M: logical.MUTE, Qt.Key_H: logical.HOME, Qt.Key_Space: logical.PAUSE,
            Qt.Key_PageUp: logical.CHUP, Qt.Key_PageDown: logical.CHDOWN,
            # grip buttons via Steam Input (map L4/R4/L5 to these keysyms):
            Qt.Key_F13: logical.VOLUP, Qt.Key_F14: logical.VOLDOWN, Qt.Key_F15: logical.MUTE,
        }
        for i in range(10):
            m[getattr(Qt, f"Key_{i}")] = logical.NUM[i]
        if k in m:
            self.ctrl.send_key(m[k])
            event.accept()
        else:
            super().keyPressEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        v = QVBoxLayout(self)
        self.grab = QCheckBox("Capture the physical volume rocker (Desktop mode)")
        self.grab.setChecked(bool(cfg.get("settings", {}).get("grab_volume_keys")))
        v.addWidget(self.grab)
        note = QLabel("Rocker capture takes effect on next launch (needs the `evdev` package).")
        note.setObjectName("status")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def apply(self):
        self.cfg.setdefault("settings", {})["grab_volume_keys"] = self.grab.isChecked()
        config.save(self.cfg)


class SetupWizard(QDialog):
    """Scan → pick a device → pair → save a profile. Sets `result_id` on success."""

    _scanDone = Signal(list)
    _pairDone = Signal(bool, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.result_id = None
        self._devices = []
        self._driver = None
        self._closing = False
        self._pin_mode = False
        self.setWindowTitle("Add a device")
        self.setMinimumSize(440, 500)   # tall enough for the PIN keypad page
        self.setStyleSheet(_style(False))

        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)

        self.stack.addWidget(self._scan_page())   # 0
        self.stack.addWidget(self._list_page())    # 1
        self.stack.addWidget(self._pair_page())    # 2

        # Always-visible Close, so the wizard (incl. the scanning page) can be
        # dismissed on the Steam Deck, where there's no window chrome.
        footer = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)
        footer.addStretch(1)
        root.addLayout(footer)

        self._scanDone.connect(self._on_scan_done)
        self._pairDone.connect(self._on_pair_done)
        QTimer.singleShot(0, self._start_scan)

    def reject(self):
        self._closing = True          # ignore any late background scan result
        super().reject()

    # -- pages --
    def _scan_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        lab = QLabel("Scanning your network for TVs and streamers…")
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
        bar = QProgressBar()
        bar.setRange(0, 0)
        v.addWidget(lab)
        v.addWidget(bar)
        v.addStretch(1)
        return w

    def _list_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Pick your device:"))
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        self.adv_box = QGroupBox("Advanced (needs extra setup)")
        self.adv_box.setCheckable(True)
        self.adv_box.setChecked(False)
        av = QVBoxLayout(self.adv_box)
        adv_note = QLabel("These devices need a manufacturer-specific walkthrough "
                          "(e.g. Fire TV: enable ADB debugging + accept the on-screen "
                          "prompt). Tick the box to select one.")
        adv_note.setObjectName("status")
        adv_note.setWordWrap(True)
        av.addWidget(adv_note)
        self.adv_list = QListWidget()
        av.addWidget(self.adv_list)
        self.adv_box.setVisible(False)
        v.addWidget(self.adv_box)
        btns = QHBoxLayout()
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self._start_scan)
        nxt = QPushButton("Next →")
        nxt.setProperty("kind", "ok")
        nxt.clicked.connect(self._chose_device)
        btns.addWidget(rescan)
        btns.addStretch(1)
        btns.addWidget(nxt)
        v.addLayout(btns)
        return w

    def _pin_pad(self):
        """A touch-friendly 0-9 keypad that types into the PIN field (for the
        Steam Deck, where the pairing dialog has no easy keyboard). The buttons
        are stored in `self._pin_rows` so the gamepad can navigate the grid."""
        pad = QWidget()
        g = QGridLayout(pad)
        g.setContentsMargins(0, 8, 0, 0)
        g.setSpacing(6)

        def digit(n):
            return lambda: self.pin_edit.setText(self.pin_edit.text() + str(n))

        def button(text, slot):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.NoFocus)
            b.setProperty("pinkey", True)   # compact style so 4 rows fit / never overlap
            b.clicked.connect(slot)
            return b

        self._pin_rows = []
        for r, row_vals in enumerate([[1, 2, 3], [4, 5, 6], [7, 8, 9]]):
            row = [button(str(n), digit(n)) for n in row_vals]
            for c, b in enumerate(row):
                g.addWidget(b, r, c)
            self._pin_rows.append(row)
        last = [button("Clear", self.pin_edit.clear),
                button("0", digit(0)),
                button("⌫", lambda: self.pin_edit.setText(self.pin_edit.text()[:-1]))]
        for c, b in enumerate(last):
            g.addWidget(b, 3, c)
        self._pin_rows.append(last)
        return pad

    # ---- gamepad navigation of the PIN grid ------------------------------
    def _pin_move(self, dr, dc):
        rows = getattr(self, "_pin_rows", None)
        if not rows:
            return
        r, c = self._pin_cursor
        r = max(0, min(len(rows) - 1, r + dr))
        c = max(0, min(len(rows[r]) - 1, c + dc))
        self._pin_cursor = (r, c)
        self._pin_highlight()

    def _pin_highlight(self):
        for row in self._pin_rows:
            for b in row:
                if b.property("padfocus"):
                    b.setProperty("padfocus", False)
                    b.style().unpolish(b)
                    b.style().polish(b)
        r, c = self._pin_cursor
        b = self._pin_rows[r][c]
        b.setProperty("padfocus", True)
        b.style().unpolish(b)
        b.style().polish(b)

    def _pin_press(self):
        r, c = self._pin_cursor
        self._pin_rows[r][c].click()

    def _pair_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.pair_msg = QLabel()
        self.pair_msg.setWordWrap(True)
        v.addWidget(self.pair_msg)
        self.pin_edit = QLineEdit()
        self.pin_edit.setPlaceholderText("Enter the code shown on the TV")
        self.pin_edit.setVisible(False)
        v.addWidget(self.pin_edit)
        self.pin_pad = self._pin_pad()
        self.pin_pad.setVisible(False)
        v.addWidget(self.pin_pad)
        self.pair_status = QLabel()
        self.pair_status.setObjectName("status")
        self.pair_status.setWordWrap(True)
        v.addWidget(self.pair_status)
        v.addStretch(1)
        btns = QHBoxLayout()
        back = QPushButton("← Back")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        self.pair_btn = QPushButton("Pair")
        self.pair_btn.setProperty("kind", "ok")
        self.pair_btn.clicked.connect(self._do_pair)
        btns.addWidget(back)
        btns.addStretch(1)
        btns.addWidget(self.pair_btn)
        v.addLayout(btns)
        # Submit is the last gamepad-navigable "row" below the keypad.
        self._pin_rows.append([self.pair_btn])
        self._pin_cursor = (0, 0)
        return w

    # -- scanning --
    def _start_scan(self):
        self.stack.setCurrentIndex(0)
        threading.Thread(target=lambda: self._scanDone.emit(discovery.scan(3.0)),
                         daemon=True).start()

    @Slot(list)
    def _on_scan_done(self, devices):
        if self._closing:
            return
        self._devices = devices
        self.list.clear()
        self.adv_list.clear()
        has_adv = False
        for d in devices:
            label = f"{d.name}  —  {drivers.get(d.brand).pretty if drivers.get(d.brand) else d.brand}"
            item = QListWidgetItem(icons.brand_icon(d.brand), label)
            item.setData(Qt.UserRole, d)
            if d.advanced:
                has_adv = True
                self.adv_list.addItem(item)
            else:
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.adv_box.setVisible(has_adv)
        if not devices:
            self.list.addItem("No devices found — check the network and Rescan.")
        self.stack.setCurrentIndex(1)

    def _chose_device(self):
        # Prefer the Advanced list only when it's expanded and has a selection.
        if self.adv_box.isVisible() and self.adv_box.isChecked() and self.adv_list.currentItem():
            item = self.adv_list.currentItem()
        else:
            item = self.list.currentItem()
        dev = item.data(Qt.UserRole) if item else None
        if not isinstance(dev, DeviceInfo):
            return
        cls = drivers.get(dev.brand)
        self._driver = cls(dev, profile={"app_name": "DeckRemote"})
        ch = self._driver.begin_pairing() if self._driver.needs_pairing() else None
        self.stack.setCurrentIndex(2)
        if ch is None:
            self.pair_msg.setText(f"{dev.name} needs no pairing. Click Connect.")
            self.pin_edit.setVisible(False)
            self.pin_pad.setVisible(False)
            self.pair_btn.setText("Connect")
            self._pin_mode = False
        else:
            self.pair_msg.setText(ch.prompt)
            is_pin = ch.kind == "pin"
            self._pin_mode = is_pin
            self.pin_edit.clear()
            self.pin_edit.setVisible(is_pin)
            self.pin_pad.setVisible(is_pin)
            self.pair_btn.setText("Submit" if is_pin else "Pair")
            if is_pin:
                self._pin_cursor = (0, 0)
                self._pin_highlight()
                self.adjustSize()   # grow to fit the now-visible keypad

    # -- gamepad navigation --
    def _active_list(self):
        if self.adv_box.isVisible() and self.adv_box.isChecked() and self.adv_list.count():
            return self.adv_list
        return self.list

    def gamepad_key(self, key: str):
        """Drive the wizard from the gamepad (routed here while it's open)."""
        page = self.stack.currentIndex()
        if page == 1:                       # device list: up/down + OK to choose
            lst = self._active_list()
            if key in (logical.UP, logical.DOWN) and lst.count():
                step = -1 if key == logical.UP else 1
                row = lst.currentRow()
                row = 0 if row < 0 else max(0, min(lst.count() - 1, row + step))
                lst.setCurrentRow(row)
            elif key == logical.OK:
                self._chose_device()
            elif key == logical.BACK:
                self.reject()
        elif page == 2:                     # pair page
            if self._pin_mode:              # navigate the PIN grid
                moves = {logical.UP: (-1, 0), logical.DOWN: (1, 0),
                         logical.LEFT: (0, -1), logical.RIGHT: (0, 1)}
                if key in moves:
                    self._pin_move(*moves[key])
                elif key == logical.OK:
                    self._pin_press()
                elif key == logical.BACK:
                    self.stack.setCurrentIndex(1)
            else:
                if key == logical.OK:
                    self._do_pair()
                elif key == logical.BACK:
                    self.stack.setCurrentIndex(1)
        elif page == 0 and key == logical.BACK:
            self.reject()

    # -- pairing --
    def _do_pair(self):
        self.pair_btn.setEnabled(False)
        self.pair_status.setText("Pairing…")
        resp = self.pin_edit.text().strip() or None

        def work():
            try:
                ok = self._driver.complete_pairing(resp)
            except Exception:
                ok = False
            self._pairDone.emit(bool(ok), "")
        threading.Thread(target=work, daemon=True).start()

    @Slot(bool, str)
    def _on_pair_done(self, ok, _msg):
        self.pair_btn.setEnabled(True)
        if not ok:
            self.pair_status.setText("Pairing failed — try again.")
            return
        dev = self._driver.device
        profile = {"brand": dev.brand, "name": dev.name, "host": dev.host,
                   "model": dev.model, "mac": dev.mac}
        profile.update(dev.extra)              # e.g. DLNA control_url
        profile.update(self._driver.profile)   # token/clientkey/app_name
        saved = config.upsert_device(self.cfg, profile, make_active=True)
        config.save(self.cfg)
        self.result_id = saved["id"]
        self.accept()
