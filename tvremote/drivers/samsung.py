"""Samsung Tizen driver (2016+ Smart TVs).

Control channel: `wss://<host>:8002/api/v2/channels/samsung.remote.control`
(token auth via an on-screen "Allow" prompt). App list/launch via the REST API
on :8001. Wake-on-LAN for power-on. DLNA casting via the TV's MediaRenderer.

A persistent WebSocket runs on an asyncio loop on a background thread; control
methods are thread-safe and enqueue onto it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import ssl
import threading
import urllib.request

import websockets

from .. import logical
from ..cast import MediaCaster
from . import register
from .base import Challenge, DeviceInfo, TVDriver
from .net import broadcast_addr, local_subnet_hosts, port_open

log = logging.getLogger("tvremote.samsung")

WS_PORT = 8002
API_PORT = 8001

# logical -> Tizen KEY_*
KEYMAP = {
    logical.POWER: "KEY_POWER", logical.SOURCE: "KEY_SOURCE",
    logical.UP: "KEY_UP", logical.DOWN: "KEY_DOWN", logical.LEFT: "KEY_LEFT",
    logical.RIGHT: "KEY_RIGHT", logical.OK: "KEY_ENTER", logical.BACK: "KEY_RETURN",
    logical.HOME: "KEY_HOME", logical.MENU: "KEY_MENU", logical.EXIT: "KEY_EXIT",
    logical.INFO: "KEY_INFO", logical.TOOLS: "KEY_TOOLS",
    logical.VOLUP: "KEY_VOLUP", logical.VOLDOWN: "KEY_VOLDOWN", logical.MUTE: "KEY_MUTE",
    logical.CHUP: "KEY_CHUP", logical.CHDOWN: "KEY_CHDOWN", logical.CH_LIST: "KEY_CH_LIST",
    logical.PLAY: "KEY_PLAY", logical.PAUSE: "KEY_PAUSE", logical.STOP: "KEY_STOP",
    logical.REWIND: "KEY_REWIND", logical.FF: "KEY_FF",
    logical.RED: "KEY_RED", logical.GREEN: "KEY_GREEN",
    logical.YELLOW: "KEY_YELLOW", logical.BLUE: "KEY_CYAN",
}
KEYMAP.update({logical.NUM[n]: f"KEY_{n}" for n in range(10)})


@register
class SamsungDriver(TVDriver):
    brand = "samsung"
    pretty = "Samsung (Tizen)"

    def __init__(self, device: DeviceInfo, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        self.mac = device.mac or self.profile.get("mac", "")
        self.app_name = self.profile.get("app_name", "DeckRemote")
        self.token = self.profile.get("token")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._stop = False
        self._state = "starting"
        self._caster = MediaCaster({"ip": self.host, "cast_port": 8083})

    def capabilities(self):
        return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.POWER_ON,
                logical.Cap.VOLUME, logical.Cap.CHANNELS, logical.Cap.NUMBERS,
                logical.Cap.MEDIA, logical.Cap.COLORS, logical.Cap.SOURCE,
                logical.Cap.APPS, logical.Cap.TEXT, logical.Cap.CAST}

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        import concurrent.futures
        hosts = local_subnet_hosts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            open_hosts = [ip for ip, ok in
                          zip(hosts, ex.map(lambda h: port_open(h, API_PORT, 0.3), hosts)) if ok]
        found = []
        for ip in open_hosts:
            try:
                with urllib.request.urlopen(f"http://{ip}:{API_PORT}/api/v2/", timeout=2) as r:
                    dev = json.load(r).get("device", {})
            except Exception:
                continue
            if str(dev.get("OS", "")).lower() == "tizen" or dev.get("type") == "Samsung SmartTV":
                found.append(DeviceInfo(
                    brand=cls.brand, name=dev.get("name", "Samsung TV"), host=ip,
                    model=dev.get("modelName", ""), mac=dev.get("wifiMac", ""),
                    port=WS_PORT, extra={"tokenAuth": dev.get("TokenAuthSupport")}))
        return found

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="samsung-ws", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=2)
        self._caster.stop()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue()
        self._loop.create_task(self._main())
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    # ---- control (thread-safe) ------------------------------------------
    def send_key(self, logical_key: str):
        code = KEYMAP.get(logical_key)
        if code:
            self._submit(("key", code))

    def send_text(self, text: str):
        if text:
            self._submit(("text", text))

    def launch_app(self, app_id: str):
        self._submit(("app", app_id))

    def refresh_apps(self):
        self._submit(("refresh", None))

    def power(self):
        if self._state == "connected":
            self.send_key(logical.POWER)
        else:
            self.wol()

    def cast(self, target: str):
        return self._caster.cast(target)

    def wol(self):
        mac = (self.mac or "").replace(":", "").replace("-", "").strip()
        if len(mac) != 12:
            log.warning("no MAC for Wake-on-LAN")
            return
        packet = bytes.fromhex("ff" * 6 + mac * 16)
        for addr in {"255.255.255.255", broadcast_addr(self.host)}:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                for port in (9, 7):
                    s.sendto(packet, (addr, port))
                s.close()
            except OSError:
                pass

    def _submit(self, cmd):
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._safe_put, cmd)

    def _safe_put(self, cmd):
        try:
            self._queue.put_nowait(cmd)
        except Exception:
            pass

    # ---- state -----------------------------------------------------------
    def _set_state(self, state):
        if state != self._state:
            self._state = state
            try:
                self._on_status(state)
            except Exception:
                log.exception("status callback failed")

    # ---- payloads --------------------------------------------------------
    def _uri(self):
        name_b64 = base64.b64encode(self.app_name.encode()).decode()
        uri = f"wss://{self.host}:{WS_PORT}/api/v2/channels/samsung.remote.control?name={name_b64}"
        if self.token:
            uri += f"&token={self.token}"
        return uri

    @staticmethod
    def _key_payload(code):
        return json.dumps({"method": "ms.remote.control", "params": {
            "Cmd": "Click", "DataOfCmd": code, "Option": "false",
            "TypeOfRemote": "SendRemoteKey"}})

    @staticmethod
    def _text_payload(text):
        b64 = base64.b64encode(text.encode()).decode()
        return json.dumps({"method": "ms.remote.control", "params": {
            "Cmd": b64, "DataOfCmd": "base64", "TypeOfRemote": "SendInputString"}})

    @staticmethod
    def _app_list_req():
        return json.dumps({"method": "ms.channel.emit",
                           "params": {"event": "ed.installedApp.get", "to": "host"}})

    @staticmethod
    def _launch_ws(app_id):
        return json.dumps({"method": "ms.channel.emit", "params": {
            "event": "ed.apps.launch", "to": "host",
            "data": {"action_type": "NATIVE_LAUNCH", "appId": str(app_id)}}})

    async def _launch_rest(self, app_id):
        def do():
            req = urllib.request.Request(
                f"http://{self.host}:{API_PORT}/api/v2/applications/{app_id}", method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                return 200 <= r.status < 300
        try:
            return await self._loop.run_in_executor(None, do)
        except Exception:
            return False

    # ---- main loop -------------------------------------------------------
    async def _main(self):
        ctx = _ssl_ctx()
        while not self._stop:
            if not await self._is_online():
                self._set_state("offline")
                await asyncio.sleep(3)
                continue
            try:
                await self._session(ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info("session ended: %s", e)
                self._set_state("offline")
                await asyncio.sleep(2)

    async def _is_online(self):
        try:
            fut = asyncio.open_connection(self.host, WS_PORT)
            _, w = await asyncio.wait_for(fut, timeout=1.5)
            w.close()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def _session(self, ctx):
        self._set_state("pairing" if not self.token else "connecting")
        connected = asyncio.Event()
        async with websockets.connect(self._uri(), ssl=ctx, open_timeout=8,
                                      max_size=None, ping_interval=None) as ws:
            sender = asyncio.create_task(self._sender(ws, connected))
            try:
                async for raw in ws:
                    if not await self._handle(ws, raw, connected):
                        break
            finally:
                sender.cancel()

    async def _handle(self, ws, raw, connected) -> bool:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return True
        event = data.get("event")
        if event == "ms.channel.connect":
            token = (data.get("data") or {}).get("token")
            if token and str(token) != self.token:
                self.token = str(token)
                self.profile["token"] = self.token  # caller persists profile
            self._set_state("connected")
            connected.set()
            await ws.send(self._app_list_req())
        elif event == "ed.installedApp.get":
            apps = (data.get("data") or {}).get("data") or []
            clean = [{"appId": a.get("appId"), "name": a.get("name")}
                     for a in apps if a.get("appId") and a.get("name")]
            try:
                self._on_apps(clean)
            except Exception:
                log.exception("apps callback failed")
        elif event == "ms.channel.unauthorized":
            self._set_state("denied")
            return False
        return True

    async def _sender(self, ws, connected):
        await connected.wait()
        while True:
            typ, arg = await self._queue.get()
            try:
                if typ == "key":
                    await ws.send(self._key_payload(arg))
                elif typ == "text":
                    await ws.send(self._text_payload(arg))
                elif typ == "app":
                    if not await self._launch_rest(arg):
                        await ws.send(self._launch_ws(arg))
                elif typ == "refresh":
                    await ws.send(self._app_list_req())
            except Exception as e:
                log.info("send failed (%s): %s", typ, e)
                return

    # ---- pairing ---------------------------------------------------------
    def needs_pairing(self):
        return not self.token

    def begin_pairing(self):
        return Challenge("allow", "A prompt will appear on the TV — choose Allow, "
                                  "then finish here.")

    def complete_pairing(self, response: str | None = None) -> bool:
        """One-shot connect (no token) → pops Allow → capture token. Blocking."""
        return asyncio.run(self._pair_once())

    async def _pair_once(self) -> bool:
        ctx = _ssl_ctx()
        try:
            async with websockets.connect(self._uri(), ssl=ctx, open_timeout=10,
                                          max_size=None, ping_interval=None) as ws:
                while True:
                    data = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
                    ev = data.get("event")
                    if ev == "ms.channel.connect":
                        tok = (data.get("data") or {}).get("token")
                        if tok:
                            self.token = str(tok)
                            self.profile["token"] = self.token
                        return True
                    if ev == "ms.channel.unauthorized":
                        return False
        except (asyncio.TimeoutError, OSError, Exception) as e:
            log.info("pairing failed: %s", e)
            return False


# ---- helpers -------------------------------------------------------------
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


