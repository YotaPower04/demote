"""Driver-layer checks — all static / offline (no sockets are opened)."""
from tvremote import drivers, logical
from tvremote.drivers.base import DeviceInfo

# brand -> expected needs_pairing()
EXPECTED = {
    "samsung": True, "vizio": True, "lg": True, "androidtv": True,
    "roku": False, "dlna": False,
}


def _make(brand, **profile):
    dev = DeviceInfo(brand=brand, name=f"t-{brand}", host="192.168.0.9")
    return drivers.get(brand)(dev, dict(profile))


def test_all_brands_registered():
    assert set(drivers._REGISTRY) >= set(EXPECTED)


def test_pretty_names_present():
    for cls in drivers.all_classes():
        assert cls.brand and cls.pretty


def test_capabilities_are_known_flags():
    known = {getattr(logical.Cap, n) for n in dir(logical.Cap) if not n.startswith("_")}
    for brand in EXPECTED:
        d = _make(brand, control_url="http://x/ctrl")  # dlna wants a control url
        caps = d.capabilities()
        assert caps, f"{brand} advertises no capabilities"
        assert caps <= known, f"{brand} has unknown caps {caps - known}"


def test_pairing_flags():
    for brand, needs in EXPECTED.items():
        d = _make(brand, control_url="http://x/ctrl")
        assert d.needs_pairing() is needs, brand


def test_nav_keymaps_cover_dpad_and_ok():
    from tvremote.drivers import androidtv, lg, roku, samsung, vizio
    maps = {
        "samsung": samsung.KEYMAP, "roku": roku.KEYMAP, "vizio": vizio.KEYMAP,
        "lg": lg.BUTTON, "androidtv": androidtv.KEYMAP,
    }
    for name, m in maps.items():
        for k in (logical.UP, logical.DOWN, logical.LEFT, logical.RIGHT, logical.OK):
            assert k in m, f"{name} keymap missing {k}"


def test_androidtv_keymap_completeness():
    from tvremote.drivers.androidtv import KEYMAP
    need = [logical.UP, logical.DOWN, logical.LEFT, logical.RIGHT, logical.OK,
            logical.BACK, logical.HOME, logical.VOLUP, logical.VOLDOWN, logical.MUTE,
            logical.PLAY, logical.PAUSE, logical.POWER, *logical.NUM]
    missing = [k for k in need if k not in KEYMAP]
    assert not missing, missing


def test_dlna_is_cast_and_media_only():
    d = _make("dlna", control_url="http://x/ctrl")
    assert d.capabilities() == {logical.Cap.CAST, logical.Cap.MEDIA}
