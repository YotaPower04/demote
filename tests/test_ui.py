"""Offscreen UI checks — render both layouts, brand icons, wizard population.

The driver is never `start()`ed, so no connection is made. The wizard's live
scan is monkeypatched out; we feed it canned devices.
"""
from tvremote import config, discovery, icons
from tvremote.controller import Controller
from tvremote.drivers.base import DeviceInfo
from tvremote.ui import RemoteWindow, SetupWizard


def _cfg(brand, **extra):
    dev_id = f"{brand}:1.2.3.4"
    return {
        "active_id": dev_id,
        "devices": [{"id": dev_id, "brand": brand, "name": f"My {brand}",
                     "host": "1.2.3.4", **extra}],
        "settings": dict(config.DEFAULT_SETTINGS),
    }


def _render(qapp, brand, landscape, **extra):
    ctrl = Controller(_cfg(brand, **extra))
    win = RemoteWindow(ctrl, landscape=landscape)
    win.resize(1280, 800) if landscape else win.resize(380, 900)
    win.show()
    qapp.processEvents()
    try:
        assert not win.dev_btn.icon().isNull()   # header brand icon
        return set(ctrl.capabilities())
    finally:
        ctrl.stop()
        win.close()


def test_full_capability_render(qapp):
    caps = _render(qapp, "samsung", landscape=True, token="x", app_name="D")
    assert {"nav", "power", "volume", "apps", "media"} <= caps


def test_minimal_capability_render(qapp):
    caps = _render(qapp, "dlna", landscape=False, control_url="http://x/ctrl")
    assert caps == {"cast", "media"}


def test_portrait_and_landscape(qapp):
    _render(qapp, "roku", landscape=False)
    _render(qapp, "androidtv", landscape=True, paired=True)


def test_brand_icons_render(qapp):
    for brand in ["samsung", "roku", "vizio", "lg", "androidtv", "firetv", "dlna", "??"]:
        assert not icons.brand_icon(brand).isNull(), brand


def test_controller_routes_gamepad_to_sink(qapp):
    from tvremote.controller import Controller
    got = []
    c = Controller(_cfg("roku"))
    c.set_key_sink(got.append)
    c.feed_gamepad("OK")
    qapp.processEvents()          # queued (worker-thread) delivery
    assert got == ["OK"]
    c.set_key_sink(None)
    c.stop()


def test_gamepad_drives_device_list(qapp, monkeypatch):
    from tvremote import discovery, logical
    monkeypatch.setattr(discovery, "scan", lambda timeout=3.0: [])
    cfg = {"active_id": None, "devices": [], "settings": dict(config.DEFAULT_SETTINGS)}
    wiz = SetupWizard(cfg)
    wiz._on_scan_done([
        DeviceInfo(brand="roku", name="A", host="1.1.1.1"),
        DeviceInfo(brand="samsung", name="B", host="1.1.1.2"),
    ])
    wiz.stack.setCurrentIndex(1)
    wiz.gamepad_key(logical.DOWN)
    assert wiz.list.currentRow() == 1
    wiz.gamepad_key(logical.DOWN)          # clamps at last row
    assert wiz.list.currentRow() == 1
    wiz.gamepad_key(logical.UP)
    assert wiz.list.currentRow() == 0
    wiz.close()


def test_gamepad_drives_pin_grid(qapp):
    from tvremote import logical
    cfg = {"active_id": None, "devices": [], "settings": dict(config.DEFAULT_SETTINGS)}
    wiz = SetupWizard(cfg)
    wiz.stack.setCurrentIndex(2)
    wiz._pin_mode = True
    wiz._pin_cursor = (0, 0)
    wiz._pin_highlight()
    for k in (logical.RIGHT, logical.OK, logical.DOWN, logical.OK):  # 2, then 5
        wiz.gamepad_key(k)
    assert wiz.pin_edit.text() == "25"
    # last navigable row is the Submit button
    for _ in range(len(wiz._pin_rows)):
        wiz.gamepad_key(logical.DOWN)
    r, c = wiz._pin_cursor
    assert wiz._pin_rows[r][c] is wiz.pair_btn
    wiz.close()


def test_pin_pad_rows_do_not_overlap(qapp):
    cfg = {"active_id": None, "devices": [], "settings": dict(config.DEFAULT_SETTINGS)}
    wiz = SetupWizard(cfg)
    wiz.show()
    qapp.processEvents()
    wiz.stack.setCurrentIndex(2)
    wiz.pin_edit.setVisible(True)
    wiz.pin_pad.setVisible(True)
    wiz._pin_mode = True
    wiz.adjustSize()
    qapp.processEvents()
    rows = [r[0].geometry() for r in wiz._pin_rows[:4]]
    gaps = [rows[i + 1].y() - (rows[i].y() + rows[i].height()) for i in range(3)]
    assert all(g >= 0 for g in gaps), f"keypad rows overlap: gaps={gaps}"
    wiz.close()


def test_wizard_lists_devices_with_icons(qapp, monkeypatch):
    canned = [
        DeviceInfo(brand="samsung", name="Living Room", host="192.168.1.50"),
        DeviceInfo(brand="firetv", name="Fire TV", host="192.168.1.70", advanced=True),
    ]
    monkeypatch.setattr(discovery, "scan", lambda timeout=3.0: canned)
    cfg = {"active_id": None, "devices": [], "settings": dict(config.DEFAULT_SETTINGS)}
    wiz = SetupWizard(cfg)
    wiz._on_scan_done(canned)   # deterministic; don't rely on the timer thread
    qapp.processEvents()
    assert wiz.list.count() == 1
    assert not wiz.list.item(0).icon().isNull()
    assert wiz.adv_list.count() == 1   # the Fire TV routed to Advanced
    wiz.close()
