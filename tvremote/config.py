"""Persistent config: saved device **profiles** under the XDG config dir.

No device details are hardcoded — everything comes from discovery + pairing and
is saved here. Shape:

    {
      "active_id": "samsung:192.168.1.50" | null,
      "devices": [ {id, brand, name, host, mac, model, token/clientkey, ...}, ... ],
      "settings": {"grab_volume_keys": false, "cast_port": 8083}
    }

Each device profile is the dict a driver is constructed with; pairing writes
credentials (token/clientkey) back into it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR_NAME = "demote"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_DIR_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_SETTINGS = {
    "grab_volume_keys": False,   # opt-in Desktop-mode evdev rocker capture
    "cast_port": 8083,           # DLNA file-server port
}


def load() -> dict:
    cfg = {"active_id": None, "devices": [], "settings": dict(DEFAULT_SETTINGS)}
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        cfg["active_id"] = data.get("active_id")
        cfg["devices"] = data.get("devices", [])
        cfg["settings"] = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


# ---- profile helpers -----------------------------------------------------
def device_id(profile: dict) -> str:
    return profile.get("id") or f"{profile.get('brand')}:{profile.get('host')}"


def active_device(cfg: dict) -> dict | None:
    aid = cfg.get("active_id")
    return next((d for d in cfg.get("devices", []) if device_id(d) == aid), None)


def upsert_device(cfg: dict, profile: dict, make_active: bool = True) -> dict:
    profile = dict(profile)
    profile["id"] = device_id(profile)
    devices = cfg.setdefault("devices", [])
    for i, d in enumerate(devices):
        if device_id(d) == profile["id"]:
            devices[i] = {**d, **profile}
            break
    else:
        devices.append(profile)
    if make_active:
        cfg["active_id"] = profile["id"]
    return profile


def set_active(cfg: dict, dev_id: str) -> None:
    cfg["active_id"] = dev_id


def remove_device(cfg: dict, dev_id: str) -> None:
    cfg["devices"] = [d for d in cfg.get("devices", []) if device_id(d) != dev_id]
    if cfg.get("active_id") == dev_id:
        cfg["active_id"] = (cfg["devices"][0]["id"] if cfg["devices"] else None)
