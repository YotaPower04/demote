"""Roku driver — Roku players and every "Roku TV" (TCL, Hisense, Sharp, onn…).

Uses the External Control Protocol (ECP): plain HTTP on :8060, no pairing.
  key    POST /keypress/<RokuKey>
  text   POST /keypress/Lit_<url-encoded char>   (per character)
  apps   GET  /query/apps   ·   launch  POST /launch/<appId>
  info   GET  /query/device-info
"""
from __future__ import annotations

import concurrent.futures
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .. import logical
from . import register
from .base import DeviceInfo, TVDriver
from .net import local_subnet_hosts, port_open

log = logging.getLogger("tvremote.roku")

ECP_PORT = 8060

# logical -> Roku ECP key word
KEYMAP = {
    logical.UP: "Up", logical.DOWN: "Down", logical.LEFT: "Left", logical.RIGHT: "Right",
    logical.OK: "Select", logical.BACK: "Back", logical.HOME: "Home", logical.EXIT: "Home",
    logical.INFO: "Info", logical.MENU: "Info",
    logical.PLAY: "Play", logical.PAUSE: "Play", logical.REWIND: "Rev", logical.FF: "Fwd",
    logical.VOLUP: "VolumeUp", logical.VOLDOWN: "VolumeDown", logical.MUTE: "VolumeMute",
    logical.CHUP: "ChannelUp", logical.CHDOWN: "ChannelDown", logical.POWER: "Power",
}


@register
class RokuDriver(TVDriver):
    brand = "roku"
    pretty = "Roku"

    def __init__(self, device, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        self.base = f"http://{self.host}:{ECP_PORT}"
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def capabilities(self):
        return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.VOLUME,
                logical.Cap.CHANNELS, logical.Cap.MEDIA, logical.Cap.APPS,
                logical.Cap.TEXT}

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        hosts = local_subnet_hosts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            live = [ip for ip, ok in
                    zip(hosts, ex.map(lambda h: port_open(h, ECP_PORT, 0.3), hosts)) if ok]
        found = []
        for ip in live:
            try:
                with urllib.request.urlopen(f"http://{ip}:{ECP_PORT}/query/device-info", timeout=2) as r:
                    root = ET.fromstring(r.read())
            except Exception:
                continue
            name = (root.findtext("user-device-name") or root.findtext("friendly-device-name")
                    or root.findtext("default-device-name") or "Roku")
            found.append(DeviceInfo(brand=cls.brand, name=name, host=ip,
                                    model=root.findtext("model-name") or "", port=ECP_PORT))
        return found

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._pool.submit(self._probe)

    def stop(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _probe(self):
        try:
            urllib.request.urlopen(f"{self.base}/query/device-info", timeout=3).read()
            self._on_status("connected")
            self._load_apps()
        except Exception:
            self._on_status("offline")

    # ---- control ---------------------------------------------------------
    def send_key(self, logical_key: str):
        word = KEYMAP.get(logical_key)
        if word:
            self._pool.submit(self._post, f"/keypress/{word}")

    def send_text(self, text: str):
        for ch in text:
            self._pool.submit(self._post, f"/keypress/Lit_{urllib.parse.quote(ch)}")

    def launch_app(self, app_id: str):
        self._pool.submit(self._post, f"/launch/{app_id}")

    def refresh_apps(self):
        self._pool.submit(self._load_apps)

    def power(self):
        self.send_key(logical.POWER)

    def _post(self, path):
        try:
            req = urllib.request.Request(self.base + path, method="POST")
            urllib.request.urlopen(req, timeout=4).read()
        except Exception as e:
            log.info("roku POST %s failed: %s", path, e)

    def _load_apps(self):
        try:
            with urllib.request.urlopen(f"{self.base}/query/apps", timeout=4) as r:
                root = ET.fromstring(r.read())
        except Exception:
            return
        apps = [{"appId": a.get("id"), "name": (a.text or "").strip()}
                for a in root.findall("app") if a.get("id")]
        try:
            self._on_apps(apps)
        except Exception:
            log.exception("apps callback failed")
