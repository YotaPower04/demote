# Demote

**A universal TV & streamer remote for the Steam Deck and Linux desktop.**

*Demote = **De**ck + Re**mote**.*

Demote discovers the TVs and streaming boxes on your network, pairs with them, and
gives you a big, touch-friendly remote you can also drive with the Steam Deck's
gamepad. The on-screen buttons adapt to whatever the selected device actually
supports, so you only ever see controls that work.

> Status: feature-complete for v1, packaged for Flathub, and in final on-device
> verification. See [supported devices](#supported-devices) for what's been tested
> vs. implemented-but-untested.

## Features

- **Multi-brand** — one app, many TVs (see below). Switch between saved devices
  from the header.
- **First-run setup wizard** — scans the LAN, lists what it finds with brand
  icons, and walks you through pairing (on-screen *Allow*, or a PIN/6-digit code).
- **Capability-aware UI** — a cast-only DLNA renderer shows just a drop target; a
  full smart TV shows power, nav, volume, channels, a numpad, apps and text entry.
- **Steam Deck native** — a fullscreen landscape layout with large targets; the
  gamepad, D-pad, sticks and triggers map to remote keys, each shown with a small
  input hint. While focused, Demote grabs the controller **exclusively** so a game
  in the background never sees your button presses.
- **Cast** — drag a video file or paste a media URL onto the window to play it on a
  DLNA-capable device.
- **No hardcoded anything** — every device detail comes from discovery + pairing
  and is saved under `~/.config/demote/`.

## Supported devices

| Brand | Protocol | Pairing | Status |
|-------|----------|---------|--------|
| Samsung (Tizen) | WebSocket `:8002` | on-screen Allow + token | implemented |
| Roku / Roku TV | ECP `:8060` | none | implemented |
| Vizio SmartCast | REST `:7345` (TLS) | on-screen PIN | implemented |
| LG webOS | SSAP `:3000` | on-screen Allow + client-key | implemented |
| Android TV / Google TV | Remote v2 (TLS `:6467`/`:6466`) | 6-digit code | implemented |
| DLNA / UPnP renderer | AVTransport (SSDP) | none | implemented |

**Experimental / planned:** Fire TV (via ADB, behind the wizard's *Advanced*
option) and Apple TV are on the roadmap — see [ROADMAP / Post-v1](#roadmap).

## Install

### Flathub
Coming soon. Once published:

```sh
flatpak install flathub io.github.yotapower04.Demote   # app-id finalized at release
```

### From source (development)
Needs a Python 3.11–3.13 base (PySide6 has no 3.14 wheels yet).

```sh
git clone https://github.com/YotaPower04/demote.git
cd demote
./setup.sh          # builds .venv, installs deps, adds a desktop launcher
./run.sh            # or launch "Demote" from your app menu
```

### Build the Flatpak locally
Needs `flatpak-builder` and the KDE 6.11 runtime:

```sh
flatpak install flathub org.kde.Platform//6.11 org.kde.Sdk//6.11
flatpak-builder --user --install --force-clean build packaging/io.github.yotapower04.Demote.yml
```

For a quick iteration build, switch the app module's source in the manifest to
`type: dir  path: ..`.

## Steam Deck

1. Install Demote (Discover in Desktop Mode, or the local Flatpak build).
2. Add it to Steam as a **Non-Steam Game** so it shows in Game Mode.
3. Give it a **Gamepad** controller template — this lets Demote read and grab the
   controller. (Demote auto-detects the Deck and opens fullscreen in landscape.)

Controller mapping (while Demote is focused):

| Input | Action | Input | Action |
|-------|--------|-------|--------|
| D-pad / left stick | navigate | A | OK |
| B | Back | X | Home |
| Y | Menu | L3 | Mute |
| R3 | Info | View / Select | Source |
| Start | Exit | L1 / R1 | Channel − / + |
| L2 / R2 | Volume − / + | right stick | volume / channel |

## Privacy & networking

Demote only talks to devices on your local network. Pairing credentials are stored
locally under `~/.config/demote/`. The DLNA cast feature runs a short-lived local
file server (default port 8083); on a restrictive host firewall you may need to
allow that port on the LAN. Only DLNA/HTTP media is supported for casting — see
`docs/` for the `+faststart` MP4 caveat.

## Contributing

Adding a brand is meant to be easy — see **[docs/adding-a-driver.md](docs/adding-a-driver.md)**
and **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## License

[GPL-3.0-or-later](LICENSE). Protocol implementations were written from public
documentation and MIT/Apache-licensed references (pyvizio, pywebostv,
androidtvremote2, Roku ECP, UPnP AVTransport); no manufacturer logos or code are
bundled. "Steam Deck" is a trademark of Valve; Samsung, Roku, Vizio, LG, Android
TV and Google TV are trademarks of their respective owners — Demote is an
independent project and is not endorsed by any of them.
