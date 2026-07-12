"""Android TV / Google TV driver (Sony, Nvidia Shield, Chromecast-with-Google-TV,
many TCL/Hisense Google-TV sets, Philips…).

Uses the **Android TV Remote protocol v2** (TLS: pairing on :6467, control on
:6466, protobuf frames) via the `androidtvremote2` library. Pairing shows a
**6-digit code** on the TV which the user types back — this mints a client
certificate; no developer mode needed. The cert/key are stored per-host under the
app config dir and reused on every launch.

Reference implementation: androidtvremote2 (Home Assistant, Apache-2.0).
"""
from __future__ import annotations

import asyncio
import logging
import threading

from androidtvremote2 import (
    AndroidTVRemote,
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
)

from .. import logical
from ..config import CONFIG_DIR
from . import register
from .base import Challenge, DeviceInfo, TVDriver
from .net import local_subnet_hosts, port_open

log = logging.getLogger("tvremote.androidtv")

PAIR_PORT = 6467      # TLS pairing service (distinctive presence signal)
API_PORT = 6466       # TLS remote-control service
CLIENT_NAME = "Demote"

# logical -> Android KeyEvent name (androidtvremote2 prepends "KEYCODE_")
KEYMAP = {
    logical.UP: "DPAD_UP", logical.DOWN: "DPAD_DOWN",
    logical.LEFT: "DPAD_LEFT", logical.RIGHT: "DPAD_RIGHT", logical.OK: "DPAD_CENTER",
    logical.BACK: "BACK", logical.EXIT: "BACK", logical.HOME: "HOME",
    logical.MENU: "MENU", logical.INFO: "INFO",
    logical.VOLUP: "VOLUME_UP", logical.VOLDOWN: "VOLUME_DOWN", logical.MUTE: "VOLUME_MUTE",
    logical.CHUP: "CHANNEL_UP", logical.CHDOWN: "CHANNEL_DOWN",
    logical.PLAY: "MEDIA_PLAY", logical.PAUSE: "MEDIA_PAUSE", logical.STOP: "MEDIA_STOP",
    logical.REWIND: "MEDIA_REWIND", logical.FF: "MEDIA_FAST_FORWARD",
    logical.POWER: "POWER", logical.SOURCE: "TV_INPUT",
}
KEYMAP.update({logical.NUM[n]: str(n) for n in range(10)})

# Android TV can't enumerate installed apps over this protocol, so we offer a
# small set of quick-launch shortcuts (name -> deep link / package that
# send_launch_app_command understands). Missing apps simply no-op on the TV.
QUICK_APPS = [
    {"name": "YouTube", "appId": "https://www.youtube.com"},
    {"name": "Netflix", "appId": "https://www.netflix.com/title"},
    {"name": "Prime Video", "appId": "https://app.primevideo.com"},
    {"name": "Disney+", "appId": "https://www.disneyplus.com"},
    {"name": "Max", "appId": "https://play.max.com"},
    {"name": "Spotify", "appId": "https://open.spotify.com"},
    {"name": "Twitch", "appId": "https://www.twitch.tv"},
    {"name": "Jellyfin", "appId": "org.jellyfin.androidtv"},
    {"name": "Plex", "appId": "com.plexapp.android"},
]


@register
class AndroidTVDriver(TVDriver):
    brand = "androidtv"
    pretty = "Android TV / Google TV"

    def __init__(self, device, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        certdir = CONFIG_DIR / "androidtv"
        certdir.mkdir(parents=True, exist_ok=True)
        self.certfile = str(certdir / f"{self.host}.crt")
        self.keyfile = str(certdir / f"{self.host}.key")
        self._loop = None
        self._thread = None
        self._remote = None
        # pairing session (kept alive between begin_pairing / complete_pairing)
        self._pair_loop = None
        self._pair_thread = None
        self._pair_remote = None

    def capabilities(self):
        return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.VOLUME,
                logical.Cap.CHANNELS, logical.Cap.NUMBERS, logical.Cap.MEDIA,
                logical.Cap.APPS, logical.Cap.TEXT}

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        import concurrent.futures
        hosts = local_subnet_hosts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            live = [ip for ip, ok in
                    zip(hosts, ex.map(lambda h: port_open(h, PAIR_PORT, 0.3), hosts)) if ok]
        # The real name/mac need a paired connection; use a placeholder for now.
        return [DeviceInfo(brand=cls.brand, name=f"Android TV ({ip})", host=ip,
                           port=API_PORT)
                for ip in live]

    def _make_remote(self, loop):
        return AndroidTVRemote(client_name=CLIENT_NAME, certfile=self.certfile,
                               keyfile=self.keyfile, host=self.host,
                               api_port=API_PORT, pair_port=PAIR_PORT, loop=loop)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if self.needs_pairing():
            self._on_status("pairing")
            return
        self._thread = threading.Thread(target=self._run, name="atv-ws", daemon=True)
        self._thread.start()

    def stop(self):
        loop, remote = self._loop, self._remote
        if loop and not loop.is_closed():
            if remote:
                loop.call_soon_threadsafe(remote.disconnect)
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
        remote = self._make_remote(self._loop)
        try:
            await remote.async_generate_cert_if_missing()
            await remote.async_connect()
        except InvalidAuth:
            self.profile["paired"] = False
            self._on_status("pairing")
            return
        except (CannotConnect, ConnectionClosed, OSError) as e:
            log.info("Android TV connect failed: %s", e)
            self._on_status("offline")
            return
        self._remote = remote
        remote.add_is_on_updated_callback(
            lambda on: self._on_status("connected" if on else "standby"))
        remote.add_current_app_updated_callback(lambda app: None)
        remote.keep_reconnecting(invalid_auth_callback=self._on_invalid_auth)
        self._on_status("connected" if remote.is_on else "standby")
        self._on_apps(QUICK_APPS)

    def _on_invalid_auth(self):
        self.profile["paired"] = False
        self._on_status("offline")

    # ---- control ---------------------------------------------------------
    def send_key(self, logical_key: str):
        name = KEYMAP.get(logical_key)
        if name and self._remote and self._loop:
            self._loop.call_soon_threadsafe(self._safe, self._remote.send_key_command, name)

    def send_text(self, text: str):
        if self._remote and self._loop:
            self._loop.call_soon_threadsafe(self._safe, self._remote.send_text, text)

    def launch_app(self, app_id: str):
        if self._remote and self._loop:
            self._loop.call_soon_threadsafe(self._safe, self._remote.send_launch_app_command, app_id)

    def refresh_apps(self):
        self._on_apps(QUICK_APPS)

    def power(self):
        self.send_key(logical.POWER)

    @staticmethod
    def _safe(fn, *args):
        try:
            fn(*args)
        except (ConnectionClosed, OSError) as e:
            log.info("Android TV send failed: %s", e)

    # ---- pairing ---------------------------------------------------------
    def needs_pairing(self):
        return not self.profile.get("paired")

    def begin_pairing(self):
        self._pair_loop = asyncio.new_event_loop()
        self._pair_thread = threading.Thread(
            target=self._pair_loop.run_forever, name="atv-pair", daemon=True)
        self._pair_thread.start()
        fut = asyncio.run_coroutine_threadsafe(self._start_pairing(), self._pair_loop)
        try:
            fut.result(timeout=20)
            return Challenge("pin", "Enter the 6-digit code shown on the Android TV.")
        except Exception as e:
            log.info("Android TV begin pairing failed: %s", e)
            self._tear_down_pairing()
            return Challenge("pin", "Couldn't reach the TV — check it's on, then retry.")

    async def _start_pairing(self):
        self._pair_remote = self._make_remote(self._pair_loop)
        await self._pair_remote.async_generate_cert_if_missing()
        await self._pair_remote.async_start_pairing()

    def complete_pairing(self, response: str | None = None) -> bool:
        if not response or not self._pair_remote or not self._pair_loop:
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self._pair_remote.async_finish_pairing(response.strip()), self._pair_loop)
        try:
            fut.result(timeout=20)
            self.profile["paired"] = True
            self.profile["certfile"] = self.certfile
            self.profile["keyfile"] = self.keyfile
            return True
        except InvalidAuth:
            log.info("Android TV pairing: wrong code")
            return False
        except Exception as e:
            log.info("Android TV finish pairing failed: %s", e)
            return False
        finally:
            self._tear_down_pairing()

    def _tear_down_pairing(self):
        loop = self._pair_loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if self._pair_thread:
            self._pair_thread.join(timeout=2)
        self._pair_remote = None
        self._pair_loop = None
        self._pair_thread = None
