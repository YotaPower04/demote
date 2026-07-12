# Demote — project state

**Demote** (Deck + Remote) — an open-source **universal TV/streamer remote for the
Steam Deck** and Linux desktop, headed for **Flathub**. Started as a copy of the
personal `samsung-tv-remote/` (which is untouched and still works) and generalized
into a multi-brand, driver-based app with no hardcoded device data.

## Status: build complete (M1–M8), pending final on-device verification

| Milestone | State |
|-----------|-------|
| M1 Foundation (logical keys, driver ABC, profiles, discovery, wizard, device switcher, capability-gated UI) | ✅ |
| M2 Roku + DLNA drivers | ✅ |
| M3 Vizio SmartCast driver (PIN) | ✅ |
| M4 LG webOS driver (client-key) | ✅ |
| M5 Android TV / Google TV driver (Remote v2, 6-digit PIN) | ✅ |
| M6 Capability-aware UI polish + Advanced expander + brand icons | ✅ |
| M7 Flatpak packaging files (manifest, deps, metainfo, desktop, icons) | ✅ |
| M8 CI + docs + LICENSE(GPLv3) + tests | ✅ |
| FINAL flatpak-builder build + Deck install + live pair/control | ⏳ pending user go-ahead |

**Verification done so far is offscreen/non-interactive only** — imports, capability
gating, keymap coverage, offscreen renders, packaging validators. Per the user's
standing rule, **no live pairing/control until they say the build is done**, and the
final Samsung/Vizio test must use a **fresh pair, not the old token**.

## Architecture
```
main.py                    QApplication + wizard-on-first-run + window + gamepad focus wiring
tvremote/
  logical.py               brand-agnostic keys + Cap.* capability flags
  drivers/
    base.py                TVDriver ABC, DeviceInfo, Challenge
    __init__.py            registry (register/get/all_classes) + imports each driver
    net.py                 port_open / local_subnet_hosts / broadcast helpers
    samsung.py  roku.py  dlna.py  vizio.py  lg.py  androidtv.py  philips.py
  discovery.py             runs every driver's discover() concurrently, dedups
  config.py                ~/.config/demote/config.json — profiles {active_id, devices[], settings}
  controller.py            QObject: builds active driver from profile, bg callbacks -> Qt signals
  ui.py                    RemoteWindow (capability-gated), device switcher, SettingsDialog, SetupWizard
  gamepad.py               pure-Python evdev reader + EVIOCGRAB focus gate
  cast.py  icons.py  volumekeys.py   (carried from samsung-tv-remote, brand-agnostic)
tests/                     headless: test_drivers.py, test_ui.py, conftest.py (offscreen)
packaging/                 io.github.yotapower04.Demote.{yml,desktop,metainfo.xml}, python3-deps.yaml,
                           gen-python-deps.py, demote.sh, icon.svg, icons/{64,128,256,512}.png
.github/workflows/ci.yml   ruff + headless pytest + (advisory) flatpak build
docs/adding-a-driver.md    driver-authoring guide
README.md CONTRIBUTING.md CHANGELOG.md LICENSE(GPL-3.0) pyproject.toml
```

## Drivers (all register; caps drive the UI)
- **samsung** — Tizen WS :8002, Allow+token, REST app launch, WoL. `needs_pairing` until token.
- **roku** — ECP :8060, no pairing. keys/text/apps.
- **dlna** — SSDP MediaRenderer, AVTransport; CAST+MEDIA only.
- **vizio** — SmartCast REST :7345 (TLS self-signed), PIN pairing → AUTH token. Codes marked "verify on device".
- **lg** — webOS SSAP ws :3000, Allow → client-key; nav via pointer-input socket, apps/power via ssap://.
- **androidtv** — built on `androidtvremote2`; TLS pair :6467 / control :6466; 6-digit PIN → client cert
  stored at `~/.config/demote/androidtv/<host>.{crt,key}`; APPS = curated quick-launch deep links; TEXT ok.
- **philips** — jointSPACE, built on `ha-philipsjs`; HTTP :1925 (api 1/5, no pairing) + HTTPS :1926
  (api 6, PIN pairing → digest username/password). Async bg loop. Note: modern Philips are also Android TVs.

## Live-test fixes (from Deck testing)
- **RemoteWindow rebuilds on device change** — `_build_ui()` re-reads caps and rebuilds the body when the
  active device changes (previously caps were captured once → switching to Samsung still showed DLNA controls).
- **Wizard**: PIN number pad (`_pin_pad`) for the Deck; always-visible **Close** button + `_closing` guard.
- **discovery.merge()**: drops the generic DLNA twin when a brand driver already claims the same host.
- **Gamepad drives the setup wizard**: pad keys route through `Controller.gamepadKey` signal →
  `_route_gamepad` → `set_key_sink` (wizard while open, else TV). Pad started before the wizard so
  first-run pairing is navigable. Device list = D-pad up/down + A; PIN keypad = D-pad up/down/left/right
  + A (grid nav via `_pin_rows`/`_pin_cursor`, Submit is the last navigable row). `_pin_mode` flag (not
  widget visibility, which is unreliable when unshown) gates PIN-grid nav.

## Verify (all green as of last run)
```
./.venv/bin/ruff check .                              # All checks passed
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q       # 12 passed
desktop-file-validate packaging/io.github.yotapower04.Demote.desktop        # VALID
appstreamcli validate --no-net packaging/…metainfo.xml # successful
```

## Open decisions / TODO before Flathub
- **App-id FINALIZED**: `io.github.yotapower04.Demote` (GitHub user YotaPower04, lowercased
  in the reverse-DNS). Manifest/desktop/metainfo/icons all renamed. Still need to pin the
  manifest source to a git tag (currently `type: dir path: ..` for local builds).
- **Screenshots**: metainfo points at `https://raw.githubusercontent.com/YotaPower04/demote/
  main/docs/screenshots/*.png` — those images don't exist yet; capture and commit them under
  `docs/screenshots/` before the Flathub PR.
- **Repo not created/pushed yet** — nothing committed; GitHub repo `YotaPower04/demote` TBD.
- **python3-deps.yaml** pins wheels for **cp312** (KDE 6.11 / freedesktop 24.08). Re-run
  `packaging/gen-python-deps.py --pytag <tag>` if the runtime changes; validate with a
  real `flatpak-builder` run (flatpak-builder + KDE SDK are NOT installed on the dev PC).
- **evdev volume-rocker** feature is unavailable inside the Flatpak (optional C-ext not
  bundled); the gamepad (pure-Python) works fine with `--device=input`.

## FINAL live checklist (only when user says go)
- [ ] `flatpak-builder --user --install` the manifest on a machine with KDE 6.11 SDK.
- [ ] Samsung: discover → **fresh** pair (accept on TV) → Mute/D-pad/launch app/text/Power.
- [ ] Vizio: discover → PIN pair → verify key codes (nav/volume/input); fix KEYMAP if any are off.
- [ ] Install on the Steam Deck (Discover or local bundle), add as Non-Steam Game with a
      Gamepad template, confirm the EVIOCGRAB grab works in the sandbox.
- [ ] Roku/LG/Android-TV/DLNA: live-test if/when a device is available.

## Post-v1
- Fire TV driver (`firetv.py` via `adb-shell`, advanced-gated, enable-ADB walkthrough).
- Apple TV (`pyatv`) if there's demand (weigh Flatpak dependency weight).
