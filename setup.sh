#!/usr/bin/env bash
# Build the local venv, install deps, and install a desktop launcher.
# Run from a NORMAL terminal (needs network for pip). PySide6 6.11.1 has no
# Python 3.14 wheels, so we pick a 3.13/3.12/3.11 base.
set -e
cd "$(dirname "$(readlink -f "$0")")"
PROJ="$PWD"

PYBASE="${PYBASE:-}"
if [ -z "$PYBASE" ]; then
  for c in "$HOME/.miniforge3/bin/python3.13" python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then PYBASE="$c"; break; fi
  done
fi
[ -n "$PYBASE" ] || { echo "No suitable python found; set PYBASE=/path/to/python3.13"; exit 1; }
echo "Using base interpreter: $PYBASE ($("$PYBASE" --version 2>&1))"

"$PYBASE" -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# Desktop launcher with host-correct absolute paths (KDE menu + Deck shortcut target).
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/demote.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Demote
Comment=Universal TV and streamer remote for the Steam Deck and Linux desktop
Exec=$PROJ/run.sh
Icon=$PROJ/packaging/icon.svg
Terminal=false
Categories=AudioVideo;
EOF
chmod +x "$PROJ/run.sh" 2>/dev/null || true

echo
echo "Done."
echo "  Launch:        ./run.sh   (or 'Demote' in your app menu)"
echo "  Launcher file: $APPS/demote.desktop"
