#!/usr/bin/env bash
# Launch Demote — the universal TV/streamer remote.
cd "$(dirname "$(readlink -f "$0")")"
exec .venv/bin/python main.py "$@"
