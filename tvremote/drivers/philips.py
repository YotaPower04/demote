"""Philips TV driver (jointSPACE / JointSpace API).

Two generations, both handled by the `ha-philipsjs` library:
  * **2014-2015 (api 1/5)** — plain HTTP on :1925, **no pairing**.
  * **2016+ (api 6, incl. Android/Saphi)** — HTTPS on :1926 with **PIN pairing**
    (digest auth); the TV shows a code you type back.

Note: recent Philips sets are Android TVs and are *also* controllable via the
Android TV driver; this native driver adds Philips-specific behaviour and covers
the non-Android models. Reference: ha-philipsjs (Apache-2.0). Untested locally —
verify on a Philips set.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import urllib.request

from haphilipsjs import PhilipsTV

from .. import logical
from . import register
from .base import Challenge, DeviceInfo, TVDriver
from .net import local_subnet_hosts, port_open

log = logging.getLogger("tvremote.philips")

HTTP_PORT = 1925
HTTPS_PORT = 1926

# logical -> Philips jointSPACE key name
KEYMAP = {
    logical.UP: "CursorUp", logical.DOWN: "CursorDown",
    logical.LEFT: "CursorLeft", logical.RIGHT: "CursorRight", logical.OK: "Confirm",
    logical.BACK: "Back", logical.EXIT: "Back", logical.HOME: "Home",
    logical.MENU: "Options", logical.INFO: "Info", logical.TOOLS: "Adjust",
    logical.POWER: "Standby", logical.SOURCE: "Source",
    logical.VOLUP: "VolumeUp", logical.VOLDOWN: "VolumeDown", logical.MUTE: "Mute",
    logical.CHUP: "ChannelStepUp", logical.CHDOWN: "ChannelStepDown",
    logical.PLAY: "Play", logical.PAUSE: "Pause", logical.STOP: "Stop",
    logical.REWIND: "Rewind", logical.FF: "FastForward",
    logical.RED: "RedColour", logical.GREEN: "GreenColour",
    logical.YELLOW: "YellowColour", logical.BLUE: "BlueColour",
}
KEYMAP.update({logical.NUM[n]: f"Digit{n}" for n in range(10)})


@register
class PhilipsDriver(TVDriver):
    brand = "philips"
    pretty = "Philips (jointSPACE)"

    def __init__(self, device, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        ex = device.extra
        self.secured = self.profile.get("secured", ex.get("secured", False))
        self.api_version = self.profile.get("api_version", ex.get("api_version",
                                            6 if self.secured else 5))
        self.username = self.profile.get("username")
        self.password = self.profile.get("password")
        self._tv = None
        self._loop = None
        self._thread = None
        self._app_intents = {}
        # pairing session kept alive between begin_pairing / complete_pairing
        self._pair_tv = None
        self._pair_loop = None
        self._pair_thread = None
        self._pair_state = None

    def capabilities(self):
        return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.VOLUME,
                logical.Cap.CHANNELS, logical.Cap.NUMBERS, logical.Cap.MEDIA,
                logical.Cap.COLORS, logical.Cap.SOURCE, logical.Cap.APPS}

    def _make_tv(self):
        return PhilipsTV(host=self.host, api_version=self.api_version,
                         secured_transport=self.secured, username=self.username,
                         password=self.password, verify=False)

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        import concurrent.futures
        hosts = local_subnet_hosts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            sec = {ip for ip, ok in
                   zip(hosts, ex.map(lambda h: port_open(h, HTTPS_PORT, 0.3), hosts)) if ok}
            uns = {ip for ip, ok in
                   zip(hosts, ex.map(lambda h: port_open(h, HTTP_PORT, 0.3), hosts)) if ok}
        found = []
        for ip in sorted(sec | uns):
            secured = ip in sec
            name, model, api = cls._system(ip, secured)
            found.append(DeviceInfo(
                brand=cls.brand, name=name or f"Philips TV ({ip})", host=ip, model=model,
                port=HTTPS_PORT if secured else HTTP_PORT,
                extra={"secured": secured, "api_version": api}))
        return found

    @staticmethod
    def _system(ip, secured):
        """Unauthenticated GET /<v>/system → (name, model, api_version)."""
        proto, port = ("https", HTTPS_PORT) if secured else ("http", HTTP_PORT)
        ctx = ssl._create_unverified_context() if secured else None
        for api in (6, 5, 1):
            try:
                with urllib.request.urlopen(f"{proto}://{ip}:{port}/{api}/system",
                                            timeout=2, context=ctx) as r:
                    d = json.load(r)
            except Exception:
                continue
            av = d.get("api_version")
            ver = av.get("Major", api) if isinstance(av, dict) else api
            return d.get("name", ""), d.get("model", ""), ver
        return "", "", (6 if secured else 5)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if self.needs_pairing():
            self._on_status("pairing")
            return
        self._thread = threading.Thread(target=self._run, name="philips", daemon=True)
        self._thread.start()

    def stop(self):
        loop, tv = self._loop, self._tv
        if loop and not loop.is_closed():
            if tv:
                asyncio.run_coroutine_threadsafe(tv.aclose(), loop)
            loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._connect())
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _connect(self):
        self._on_status("connecting")
        self._tv = self._make_tv()
        try:
            await self._tv.update()
        except Exception as e:
            log.info("Philips connect failed: %s", e)
            self._on_status("offline")
            return
        self._on_status("connected")
        await self._load_apps()

    def _spawn(self, coro):
        loop = self._loop
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, loop)

    # ---- control ---------------------------------------------------------
    def send_key(self, logical_key: str):
        key = KEYMAP.get(logical_key)
        if key and self._tv:
            self._spawn(self._safe(self._tv.sendKey(key)))

    def power(self):
        self.send_key(logical.POWER)

    def launch_app(self, app_id: str):
        intent = self._app_intents.get(app_id)
        if intent and self._tv:
            self._spawn(self._safe(self._tv.setApplication(intent)))

    def refresh_apps(self):
        if self._tv:
            self._spawn(self._load_apps())

    async def _load_apps(self):
        try:
            await self._tv.getApplications()
        except Exception as e:
            log.info("Philips getApplications failed: %s", e)
            return
        apps, intents = [], {}
        for app_id, app in (self._tv.applications or {}).items():
            label = app.get("label") or app_id
            apps.append({"appId": app_id, "name": label})
            if "intent" in app:
                intents[app_id] = app["intent"]
        self._app_intents = intents
        self._on_apps(apps)

    @staticmethod
    async def _safe(coro):
        try:
            await coro
        except Exception as e:
            log.info("Philips request failed: %s", e)

    # ---- pairing ---------------------------------------------------------
    def needs_pairing(self):
        return self.secured and not (self.username and self.password)

    def begin_pairing(self):
        if not self.secured:
            return Challenge("none")
        self._pair_loop = asyncio.new_event_loop()
        self._pair_thread = threading.Thread(
            target=self._pair_loop.run_forever, name="philips-pair", daemon=True)
        self._pair_thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._start_pairing(), self._pair_loop)
        try:
            fut.result(timeout=20)
            return Challenge("pin", "Enter the PIN shown on the Philips TV.")
        except Exception as e:
            log.info("Philips begin pairing failed: %s", e)
            self._teardown_pairing()
            return Challenge("pin", "Couldn't reach the TV — check it's on, then retry.")

    async def _start_pairing(self):
        self._pair_tv = self._make_tv()
        self._pair_state = await self._pair_tv.pairRequest(
            "app.id", "Demote", "Demote", "Android", "native")

    def complete_pairing(self, response: str | None = None) -> bool:
        if not self.secured:
            return True
        if not response or not self._pair_tv or not self._pair_state:
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self._pair_tv.pairGrant(self._pair_state, response.strip()), self._pair_loop)
        try:
            fut.result(timeout=20)
            self.username = self._pair_state["device"]["id"]
            self.password = self._pair_state["auth_key"]
            self.profile.update({"username": self.username, "password": self.password,
                                 "secured": True, "api_version": self.api_version})
            return True
        except Exception as e:
            log.info("Philips finish pairing failed: %s", e)
            return False
        finally:
            self._teardown_pairing()

    def _teardown_pairing(self):
        loop, tv = self._pair_loop, self._pair_tv
        if loop and not loop.is_closed():
            if tv:
                asyncio.run_coroutine_threadsafe(tv.aclose(), loop)
            loop.call_soon_threadsafe(loop.stop)
        if self._pair_thread:
            self._pair_thread.join(timeout=2)
        self._pair_tv = self._pair_loop = self._pair_thread = self._pair_state = None
