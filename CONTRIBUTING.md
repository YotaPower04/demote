# Contributing to Demote

Thanks for helping! Demote aims to be the friendliest way to control any TV from a
Steam Deck or Linux desktop.

## Ways to help

- **Add or fix a device driver** — start with
  [docs/adding-a-driver.md](docs/adding-a-driver.md).
- **Test on real hardware** — the hardest part of a multi-brand remote is device
  coverage. If you own a TV/streamer, verify discovery → pairing → keys/apps and
  report what worked in an issue (brand, model, firmware).
- **Report bugs** with your device model, firmware, and the log output
  (`./run.sh` prints to the terminal).

## Dev setup

```sh
./setup.sh                     # .venv + deps + desktop launcher
./run.sh                       # run it
```

## Before you open a PR

```sh
./.venv/bin/ruff check .                        # lint
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q # headless tests
```

Both run in CI on every PR. Please:

- Keep the UI/gamepad/controller layers **brand-agnostic** — new device behaviour
  belongs in a driver, expressed through logical keys and capability flags.
- Match the surrounding style (small, documented modules; no bundled logos or
  manufacturer code; protocols reimplemented from public docs / permissively
  licensed references).
- Add static tests for new drivers (keymap coverage, capabilities, pairing flag).
- Don't commit personal data (tokens, IPs, MACs) — those live in
  `~/.config/demote/`, never in the repo.

## Commit / PR

- One logical change per PR; describe what device(s) you tested on.
- By contributing you agree your work is licensed under
  [GPL-3.0-or-later](LICENSE).
