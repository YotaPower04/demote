# Adding a device driver

Every brand Demote controls is a small `TVDriver` subclass under
`tvremote/drivers/`. The UI, gamepad reader and controller talk **only** to the
driver interface using *logical keys* (`tvremote/logical.py`) — they never know
about a brand's wire protocol. To add a brand you implement one class; everything
else (setup wizard, device switcher, capability-gated buttons, gamepad, casting)
works automatically.

## 1. The interface

Subclass `tvremote.drivers.base.TVDriver` and register it:

```python
from .. import logical
from . import register
from .base import Challenge, DeviceInfo, TVDriver

@register
class MyBrandDriver(TVDriver):
    brand = "mybrand"           # unique key, also used for the profile id + icon
    pretty = "My Brand (Model)" # shown in the setup wizard
```

Then import it once for its side effect in `tvremote/drivers/__init__.py`:

```python
from . import mybrand  # noqa: E402,F401
```

## 2. What to implement

| Method | Purpose |
|--------|---------|
| `capabilities() -> set[str]` | Which `logical.Cap.*` flags the device supports. Drives which UI sections appear. **Required.** |
| `send_key(logical_key)` | Translate one logical key (e.g. `logical.UP`) to the device and send it. **Required.** |
| `classmethod discover(timeout) -> list[DeviceInfo]` | Find devices of this brand on the LAN. Return `[]` if none. |
| `start()` / `stop()` | Open/close any background connection. Report state via `self._on_status(...)`. |
| `power()` | Defaults to `send_key(logical.POWER)`; override for WoL / REST power. |
| `send_text(text)` | Only if you advertise `Cap.TEXT`. |
| `launch_app(app_id)` / `refresh_apps()` | Only if you advertise `Cap.APPS`. Push the app list via `self._on_apps([{ "appId":..., "name":... }])`. |
| `cast(target) -> (ok, msg)` | Only if you advertise `Cap.CAST`. |
| `needs_pairing()` / `begin_pairing()` / `complete_pairing(resp)` | The pairing handshake — see below. |

### Capabilities

Advertise only what works, so the UI stays honest:

```python
def capabilities(self):
    return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.VOLUME,
            logical.Cap.MEDIA, logical.Cap.APPS, logical.Cap.TEXT}
```

Full list: `NAV, POWER, POWER_ON, VOLUME, CHANNELS, NUMBERS, MEDIA, COLORS,
SOURCE, APPS, TEXT, CAST`.

### Logical keys

`send_key` receives one of the names in `tvremote/logical.py`
(`UP/DOWN/LEFT/RIGHT/OK/BACK/HOME/MENU/EXIT/INFO/TOOLS`, `POWER/SOURCE`,
`VOLUP/VOLDOWN/MUTE`, `CHUP/CHDOWN/CH_LIST`, `PLAY/PAUSE/STOP/REWIND/FF`,
`NUM0`–`NUM9`, `RED/GREEN/YELLOW/BLUE`). Keep a `KEYMAP` dict from logical → your
protocol code and simply ignore keys you don't map.

## 3. Pairing

Return a `Challenge` describing what the user must do:

- `Challenge("none")` — nothing (e.g. Roku). `complete_pairing` succeeds at once.
- `Challenge("allow", "...")` — user accepts an on-screen prompt (Samsung/LG).
- `Challenge("pin", "...")` — user reads a code off the TV and types it back
  (Vizio/Android TV); `complete_pairing(response)` gets that string.

Persist whatever credential you get by writing it into `self.profile` — it's the
live dict that gets saved to `~/.config/demote/config.json`:

```python
def needs_pairing(self):
    return not self.profile.get("token")

def complete_pairing(self, response=None):
    token = do_the_handshake(response)
    if token:
        self.profile["token"] = token
        return True
    return False
```

## 4. Discovery

`discover()` runs concurrently with every other driver during a scan. Prefer a
fast parallel port probe (see `tvremote/drivers/net.py` helpers `port_open`,
`local_subnet_hosts`) with an mDNS/SSDP path where possible. Return
`DeviceInfo(brand, name, host, model="", mac="", port=None, advanced=False,
extra={})`. Set `advanced=True` for devices that need a manufacturer-specific
walkthrough — the wizard hides those behind its **Advanced** expander.

## 5. Threading

The UI is Qt on the main thread; never block it. Do I/O on a background thread or
asyncio loop and report back through the `self._on_status` / `self._on_apps`
callbacks (Qt delivers them safely). See `roku.py` (thread pool) and `lg.py` /
`androidtv.py` (asyncio loop on a background thread) for the two patterns in use.

## 6. Test it

Add static checks to `tests/test_drivers.py` (keymap coverage, capability set,
pairing flag) — these run headless in CI with no device present. Live-verify
against real hardware before marking the brand *verified* in the README table.
