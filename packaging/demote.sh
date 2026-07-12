#!/bin/sh
# Flatpak launcher: run the app from its install prefix.
exec python3 /app/share/demote/main.py "$@"
