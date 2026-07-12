"""LG webOS driver (2014+ LG smart TVs).

SSAP over WebSocket (ws://host:3000). First connection sends a registration
handshake; the TV shows an "Allow" prompt and returns a persistent *client-key*.
Most keys are sent as buttons over the secondary pointer-input socket; apps and
power-off go over the main socket as SSAP requests.

Protocol references: pywebostv / bscpylgtv (MIT). Untested locally — verify on an
LG set.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

import websockets

from .. import logical
from . import register
from .base import Challenge, DeviceInfo, TVDriver
from .net import local_subnet_hosts, port_open

log = logging.getLogger("tvremote.lg")

PORT = 3000

# logical -> LG pointer-input button name
BUTTON = {
    logical.UP: "UP", logical.DOWN: "DOWN", logical.LEFT: "LEFT", logical.RIGHT: "RIGHT",
    logical.OK: "ENTER", logical.BACK: "BACK", logical.EXIT: "EXIT", logical.HOME: "HOME",
    logical.MENU: "MENU", logical.INFO: "INFO",
    logical.VOLUP: "VOLUMEUP", logical.VOLDOWN: "VOLUMEDOWN", logical.MUTE: "MUTE",
    logical.CHUP: "CHANNELUP", logical.CHDOWN: "CHANNELDOWN",
    logical.PLAY: "PLAY", logical.PAUSE: "PAUSE", logical.STOP: "STOP",
    logical.REWIND: "REWIND", logical.FF: "FASTFORWARD",
    logical.RED: "RED", logical.GREEN: "GREEN", logical.YELLOW: "YELLOW", logical.BLUE: "BLUE",
}
BUTTON.update({logical.NUM[n]: str(n) for n in range(10)})

REGISTER_MANIFEST = {
    "manifestVersion": 1,
    "appVersion": "1.1",
    "signed": {"created": "20140509", "appId": "com.lge.test", "vendorId": "com.lge",
               "localizedAppNames": {"": "Demote"}, "localizedVendorNames": {"": "LG"},
               "permissions": ["TEST_SECURE", "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD",
                               "READ_INSTALLED_APPS", "CONTROL_AUDIO", "CONTROL_POWER",
                               "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV",
                               "READ_TV_CHANNEL_LIST", "CONTROL_INPUT_JOYSTICK", "READ_CURRENT_CHANNEL"],
               "serial": "2f930e2d2cfe083771f68e4fe7bb07"},
    "permissions": ["LAUNCH", "CONTROL_AUDIO", "CONTROL_POWER", "READ_INSTALLED_APPS",
                    "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV",
                    "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD"],
}


@register
class LGDriver(TVDriver):
    brand = "lg"
    pretty = "LG (webOS)"

    def __init__(self, device, profile=None, on_status=None, on_apps=None):
        super().__init__(device, profile, on_status, on_apps)
        self.host = device.host
        self.client_key = self.profile.get("client_key")
        self._loop = None
        self._queue = None
        self._thread = None
        self._stop = False
        self._msg_id = 0

    def capabilities(self):
        return {logical.Cap.NAV, logical.Cap.POWER, logical.Cap.VOLUME,
                logical.Cap.CHANNELS, logical.Cap.NUMBERS, logical.Cap.MEDIA,
                logical.Cap.COLORS, logical.Cap.APPS}

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0):
        import concurrent.futures
        hosts = local_subnet_hosts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            live = [ip for ip, ok in
                    zip(hosts, ex.map(lambda h: port_open(h, PORT, 0.3), hosts)) if ok]
        # :3000 open is a strong webOS signal; name is filled in after pairing.
        return [DeviceInfo(brand=cls.brand, name=f"LG TV ({ip})", host=ip, port=PORT)
                for ip in live]

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="lg-ws", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue()
        self._loop.create_task(self._main())
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    # ---- control ---------------------------------------------------------
    def send_key(self, logical_key: str):
        if logical_key in BUTTON:
            self._submit(("button", BUTTON[logical_key]))

    def power(self):
        self._submit(("ssap", "ssap://system/turnOff", {}))

    def launch_app(self, app_id):
        self._submit(("ssap", "ssap://system.launcher/launch", {"id": app_id}))

    def refresh_apps(self):
        self._submit(("apps", None, None))

    def _submit(self, cmd):
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: self._queue.put_nowait(cmd))

    # ---- pairing ---------------------------------------------------------
    def needs_pairing(self):
        return not self.client_key

    def begin_pairing(self):
        return Challenge("allow", "Accept the pairing prompt on the LG TV, then finish here.")

    def complete_pairing(self, response: str | None = None) -> bool:
        return asyncio.run(self._register_once())

    async def _register_once(self) -> bool:
        try:
            async with websockets.connect(f"ws://{self.host}:{PORT}", open_timeout=10,
                                          max_size=None, ping_interval=None) as ws:
                await ws.send(self._register_msg())
                while True:
                    data = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
                    if data.get("type") == "registered":
                        key = data.get("payload", {}).get("client-key")
                        if key:
                            self.client_key = key
                            self.profile["client_key"] = key
                            return True
                    elif data.get("type") == "error":
                        return False
        except Exception as e:
            log.info("LG pairing failed: %s", e)
            return False

    # ---- main loop -------------------------------------------------------
    async def _main(self):
        while not self._stop:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info("LG session ended: %s", e)
                self._on_status("offline")
                await asyncio.sleep(3)

    async def _session(self):
        self._on_status("pairing" if not self.client_key else "connecting")
        async with websockets.connect(f"ws://{self.host}:{PORT}", open_timeout=8,
                                      max_size=None, ping_interval=None) as ws:
            await ws.send(self._register_msg())
            registered = asyncio.Event()
            input_ws = [None]
            sender = asyncio.create_task(self._sender(ws, registered, input_ws))
            try:
                async for raw in ws:
                    await self._handle(ws, raw, registered, input_ws)
            finally:
                sender.cancel()
                if input_ws[0]:
                    await input_ws[0].close()

    async def _handle(self, ws, raw, registered, input_ws):
        data = json.loads(raw)
        typ = data.get("type")
        if typ == "registered":
            key = data.get("payload", {}).get("client-key")
            if key and key != self.client_key:
                self.client_key = key
                self.profile["client_key"] = key
            self._on_status("connected")
            registered.set()
            await ws.send(self._req("ssap://com.webos.service.networkinput/getPointerInputSocket"))
            await ws.send(self._req("ssap://com.webos.applicationManager/listLaunchPoints"))
        elif "socketPath" in (data.get("payload") or {}):
            path = data["payload"]["socketPath"]
            input_ws[0] = await websockets.connect(path, ping_interval=None, max_size=None)
        elif "launchPoints" in (data.get("payload") or {}):
            apps = [{"appId": lp.get("id"), "name": lp.get("title")}
                    for lp in data["payload"]["launchPoints"] if lp.get("id")]
            self._on_apps(apps)

    async def _sender(self, ws, registered, input_ws):
        await registered.wait()
        while True:
            kind, a, b = await self._queue.get()
            try:
                if kind == "button":
                    if input_ws[0]:
                        await input_ws[0].send(f"type:button\nname:{a}\n\n")
                elif kind == "ssap":
                    await ws.send(self._req(a, b or {}))
                elif kind == "apps":
                    await ws.send(self._req("ssap://com.webos.applicationManager/listLaunchPoints"))
            except Exception as e:
                log.info("LG send failed: %s", e)
                return

    # ---- message builders ------------------------------------------------
    def _register_msg(self):
        payload = dict(REGISTER_MANIFEST)
        if self.client_key:
            payload = {"client-key": self.client_key, **payload}
        return json.dumps({"type": "register", "id": "register_0", "payload": payload})

    def _req(self, uri, payload=None):
        self._msg_id += 1
        msg = {"type": "request", "id": f"req_{self._msg_id}", "uri": uri}
        if payload is not None:
            msg["payload"] = payload
        return json.dumps(msg)
