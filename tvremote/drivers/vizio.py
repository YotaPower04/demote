"""Vizio SmartCast driver (2016+ Vizio TVs).

SmartCast REST API on :7345 (HTTPS, self-signed). Pairing shows a 4-digit PIN on
the TV: PUT /pairing/start → read PIN → PUT /pairing/pair → AUTH token. Control is
PUT /key_command/ with (CODESET, CODE) tuples and an `AUTH` header.

Key codes are the community/pyvizio values; verify against a real set — the user
has a Vizio D32h-G9 on the LAN for that.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import ssl
import urllib.request
import uuid

from .. import logical
from . import register
from .base import Challenge, DeviceInfo, TVDriver
from .net import local_subnet_hosts, port_open

log = logging.getLogger("tvremote.vizio")

PORT = 7345

# logical -> (CODESET, CODE)
KEYMAP = {
    logical.POWER: (11, 2),                    # toggle  (on=1, off=0)
    logical.VOLUP: (5, 1), logical.VOLDOWN: (5, 0), logical.MUTE: (5, 4),
    logical.CHUP: (8, 1), logical.CHDOWN: (8, 0), logical.CH_LIST: (8, 2),
    logical.SOURCE: (7, 1),                    # cycle input
    logical.UP: (3, 8), logical.DOWN: (3, 0), logical.LEFT: (3, 1),
    logical.RIGHT: (3, 7), logical.OK: (3, 2),
    logical.BACK: (4, 0), logical.HOME: (4, 3), logical.MENU: (4, 8), logical.INFO: (4, 6),
    logical.EXIT: (9, 0),
    logical.PLAY: (2, 3), logical.PAUSE: (2, 4), logical.REWIND: (2, 1), logical.FF: (2, 0),
}


@register
class VizioDriver(TVDriver):
    brand = "vizio"
    pretty = "Vizio (SmartCast)"

    def __init__(self, device, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        self.token = self.profile.get("token")
        self.device_id = self.profile.setdefault("device_id", f"demote-{uuid.uuid4().hex[:12]}")
        self._pair_token = None
        self._challenge = 1
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def capabilities(self):
        return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.VOLUME,
                logical.Cap.CHANNELS, logical.Cap.SOURCE, logical.Cap.MEDIA}

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        hosts = local_subnet_hosts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            live = [ip for ip, ok in
                    zip(hosts, ex.map(lambda h: port_open(h, PORT, 0.3), hosts)) if ok]
        found = []
        for ip in live:
            info = cls._get(ip, "/state/device/deviceinfo")
            if not info:
                continue
            val = (info.get("ITEMS") or [{}])[0].get("VALUE", {})
            if val:
                found.append(DeviceInfo(brand=cls.brand,
                                        name=val.get("CAST_NAME", "Vizio TV"), host=ip,
                                        model=val.get("MODEL_NAME", ""), port=PORT))
        return found

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._pool.submit(self._probe)

    def stop(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _probe(self):
        d = self._get(self.host, "/state/device/power_mode")
        self._on_status("connected" if d and d.get("STATUS", {}).get("RESULT") == "SUCCESS"
                        else "offline")

    # ---- control ---------------------------------------------------------
    def send_key(self, logical_key: str):
        pair = KEYMAP.get(logical_key)
        if pair:
            self._pool.submit(self._key, *pair)

    def power(self):
        self.send_key(logical.POWER)

    def _key(self, codeset, code):
        body = {"KEYLIST": [{"CODESET": codeset, "CODE": code, "ACTION": "KEYPRESS"}]}
        self._put("/key_command/", body, auth=True)

    # ---- pairing ---------------------------------------------------------
    def needs_pairing(self):
        return not self.token

    def begin_pairing(self):
        resp = self._put("/pairing/start",
                         {"DEVICE_ID": self.device_id, "DEVICE_NAME": "Demote"})
        item = self._item(resp)
        self._pair_token = item.get("PAIRING_REQ_TOKEN")
        self._challenge = item.get("CHALLENGE_TYPE", 1)
        return Challenge("pin", "Enter the code shown on the Vizio TV.")

    def complete_pairing(self, response: str | None = None) -> bool:
        if not response:
            return False
        resp = self._put("/pairing/pair", {
            "DEVICE_ID": self.device_id, "CHALLENGE_TYPE": self._challenge,
            "RESPONSE_VALUE": str(response), "PAIRING_REQ_TOKEN": self._pair_token})
        if not resp or resp.get("STATUS", {}).get("RESULT") != "SUCCESS":
            return False
        token = self._item(resp).get("AUTH_TOKEN")
        if token:
            self.token = token
            self.profile["token"] = token
            return True
        return False

    # ---- HTTP ------------------------------------------------------------
    @staticmethod
    def _ctx():
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c

    @staticmethod
    def _item(resp):
        if not resp:
            return {}
        return resp.get("ITEM") or (resp.get("ITEMS") or [{}])[0] or {}

    @classmethod
    def _get(cls, host, path):
        try:
            req = urllib.request.Request(f"https://{host}:{PORT}{path}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3, context=cls._ctx()) as r:
                return json.load(r)
        except Exception:
            return None

    def _put(self, path, body, auth=False):
        headers = {"Content-Type": "application/json"}
        if auth and self.token:
            headers["AUTH"] = self.token
        data = json.dumps(body).encode()
        try:
            req = urllib.request.Request(f"https://{self.host}:{PORT}{path}",
                                         data=data, method="PUT", headers=headers)
            with urllib.request.urlopen(req, timeout=6, context=self._ctx()) as r:
                return json.load(r)
        except Exception as e:
            log.info("vizio PUT %s failed: %s", path, e)
            return None
