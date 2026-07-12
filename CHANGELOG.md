# Changelog

All notable changes to Demote are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions use semver.

## [Unreleased]

### Added
- Multi-brand driver architecture: brand-agnostic logical keys + capability flags,
  a `TVDriver` interface, a driver registry, and per-device profiles.
- Drivers: **Samsung (Tizen)**, **Roku**, **Vizio SmartCast**, **LG webOS**,
  **Android TV / Google TV** (Remote v2, 6-digit pairing), and generic
  **DLNA/UPnP** casting.
- First-run **setup wizard**: LAN scan → device list with brand icons → pairing
  (Allow / PIN / none), plus an **Advanced** expander for devices that need extra
  setup (Fire TV scaffold).
- **Device switcher** in the header; add/remove/switch saved devices.
- **Capability-aware UI**: sections appear only for what the selected device
  supports.
- **Steam Deck**: fullscreen landscape layout, gamepad/stick/trigger mapping with
  on-screen input hints, and exclusive controller capture (`EVIOCGRAB`) while
  focused.
- Drag-and-drop **casting** of local video files and media URLs (DLNA).
- **Flatpak** packaging (KDE runtime, pinned offline wheels, `--device=input`),
  AppStream metainfo, desktop entry, and hicolor icons.
- CI (ruff + headless tests), docs (README, driver-authoring guide, contributing),
  and a GPL-3.0-or-later license.

### Notes
- The reverse-DNS app-id org segment is finalized at first release
  (`io.github.yotapower04.Demote`).
- On-device verification of each brand is ongoing; see the README status table.
