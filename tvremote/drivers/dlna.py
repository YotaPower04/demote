"""Generic DLNA / UPnP MediaRenderer driver — media casting + transport only.

No remote keys: a DLNA renderer only understands AVTransport. So this driver
advertises just CAST + MEDIA (play/pause/stop). Discovery is SSDP for
`MediaRenderer`; the AVTransport control URL is parsed from the device
description and stored in the profile so it survives restarts.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .. import logical
from ..cast import MediaCaster
from . import register
from .base import DeviceInfo, TVDriver

log = logging.getLogger("tvremote.dlna")

AVT = "urn:schemas-upnp-org:service:AVTransport:1"
_ACTIONS = {logical.PLAY: "Play", logical.PAUSE: "Pause", logical.STOP: "Stop"}


@register
class DlnaDriver(TVDriver):
    brand = "dlna"
    pretty = "DLNA / UPnP renderer"

    def __init__(self, device, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        self.control_url = device.extra.get("control_url") or self.profile.get("control_url")
        self._caster = MediaCaster({"ip": self.host, "cast_port": 8083,
                                    "dlna_ctrl": self.control_url})

    def capabilities(self):
        return {logical.Cap.MEDIA, logical.Cap.CAST}

    # ---- discovery (SSDP) ------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        found = {}
        msg = ("M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\n"
               'MAN:"ssdp:discover"\r\nMX:2\r\n'
               "ST:urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(min(2.5, timeout))
        try:
            s.sendto(msg, ("239.255.255.250", 1900))
            while True:
                try:
                    data, addr = s.recvfrom(2048)
                except socket.timeout:
                    break
                m = re.search(rb"LOCATION:\s*(\S+)", data, re.I)
                if m:
                    found.setdefault(addr[0], m.group(1).decode())
        except OSError:
            pass
        finally:
            s.close()

        devices = []
        for host, location in found.items():
            info = cls._parse_description(location)
            if info:
                devices.append(DeviceInfo(
                    brand=cls.brand, name=info["name"], host=host,
                    extra={"control_url": info["control_url"]}))
        return devices

    @staticmethod
    def _parse_description(location):
        try:
            with urllib.request.urlopen(location, timeout=3) as r:
                root = ET.fromstring(r.read())
        except Exception:
            return None
        ns = "{urn:schemas-upnp-org:device-1-0}"
        name = (root.findtext(f".//{ns}friendlyName") or "DLNA renderer")
        ctrl = None
        for svc in root.iter(f"{ns}service"):
            if "AVTransport" in (svc.findtext(f"{ns}serviceType") or ""):
                ctrl = svc.findtext(f"{ns}controlURL")
                break
        if not ctrl:
            return None
        return {"name": name, "control_url": urllib.parse.urljoin(location, ctrl)}

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._on_status("connected" if self.control_url else "offline")

    def stop(self):
        self._caster.stop()

    # ---- control ---------------------------------------------------------
    def send_key(self, logical_key: str):
        action = _ACTIONS.get(logical_key)
        if action:
            threading.Thread(target=self._transport, args=(action,), daemon=True).start()

    def cast(self, target: str):
        return self._caster.cast(target)

    def power(self):
        pass  # DLNA renderers have no power control

    def _transport(self, action):
        if not self.control_url:
            return
        speed = "<Speed>1</Speed>" if action == "Play" else ""
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{AVT}"><InstanceID>0</InstanceID>{speed}</u:{action}>'
            "</s:Body></s:Envelope>"
        ).encode()
        req = urllib.request.Request(self.control_url, data=body, method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{AVT}#{action}"'})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            log.info("DLNA %s failed: %s", action, e)
