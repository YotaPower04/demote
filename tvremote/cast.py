"""Cast media to the TV over DLNA (UPnP AVTransport).

Two cases:
  * a **URL** the TV can reach → sent straight to AVTransport SetAVTransportURI.
  * a **local file** → served by a tiny built-in HTTP server (with byte-range
    support so the TV can seek) and that URL is sent to the TV.

The AVTransport control endpoint is discovered from the renderer description at
http://<ip>:9197/dmr (cached in config), falling back to the known Samsung path.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

log = logging.getLogger("tvremote.cast")

DMR_PORT = 9197
FALLBACK_CTRL = "/upnp/control/AVTransport1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"

MEDIA_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".ts": "video/mp2t", ".m3u8": "application/vnd.apple.mpegurl",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".wav": "audio/wav", ".aac": "audio/aac", ".ogg": "audio/ogg",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
}


def guess_mime(name: str) -> str:
    _, ext = os.path.splitext(name.split("?")[0].lower())
    return MEDIA_EXT.get(ext, "video/mp4")


def is_probably_media(url: str) -> bool:
    _, ext = os.path.splitext(urllib.parse.urlparse(url).path.lower())
    return ext in MEDIA_EXT


# ---- local file server ---------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self._serve(body=False)

    def do_GET(self):
        self._serve(body=True)

    def _serve(self, body):
        token = self.path.lstrip("/").split("/")[0]
        entry = self.server.registry.get(token)
        if not entry:
            self.send_error(404)
            return
        path, mime = entry
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404)
            return
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[6:].partition("-")
                start = int(s) if s else 0
                end = int(e) if e else size - 1
                end = min(end, size - 1)
                status = 206
            except ValueError:
                pass
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not body:
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def log_message(self, *args):
        pass


class MediaCaster:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._server = None
        self._port = None
        self._registry = {}
        self._lock = threading.Lock()

    # ---- public ----------------------------------------------------------
    def cast(self, target: str) -> tuple[bool, str]:
        """target = a local path or a URL. Returns (ok, message)."""
        try:
            if os.path.exists(target):
                url, mime, title = self._serve_file(target)
            else:
                url = target
                mime = guess_mime(target)
                title = os.path.basename(urllib.parse.urlparse(target).path) or "Media"
                if not is_probably_media(target):
                    return (False, "That doesn't look like a direct media file/URL "
                                   "(DLNA can't cast web pages like a YouTube link).")
            self._set_uri(url, mime, title)
            self._play()
            return (True, f"Casting {title}")
        except Exception as e:
            log.exception("cast failed")
            return (False, f"Cast failed: {e}")

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None

    # ---- AVTransport (SOAP) ----------------------------------------------
    def _control_url(self) -> str:
        ip = self.cfg.get("ip")
        cached = self.cfg.get("dlna_ctrl")
        if cached:
            return cached
        ctrl_path = FALLBACK_CTRL
        try:
            with urllib.request.urlopen(f"http://{ip}:{DMR_PORT}/dmr", timeout=4) as r:
                root = ET.fromstring(r.read())
            ns = "{urn:schemas-upnp-org:device-1-0}"
            for svc in root.iter(f"{ns}service"):
                st = svc.findtext(f"{ns}serviceType") or ""
                if "AVTransport" in st:
                    ctrl_path = svc.findtext(f"{ns}controlURL") or FALLBACK_CTRL
                    break
        except Exception as e:
            log.info("DMR description fetch failed (%s); using fallback path", e)
        url = urllib.parse.urljoin(f"http://{ip}:{DMR_PORT}/", ctrl_path)
        self.cfg["dlna_ctrl"] = url
        return url

    def _soap(self, action: str, body: str):
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body>{body}</s:Body></s:Envelope>'
        ).encode()
        req = urllib.request.Request(
            self._control_url(), data=envelope, method="POST",
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": f'"{AVT}#{action}"',
            })
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status

    def _set_uri(self, url, mime, title):
        didl = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0" parentID="-1" restricted="1">'
            f'<dc:title>{escape(title)}</dc:title>'
            f'<upnp:class>{self._upnp_class(mime)}</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res>'
            '</item></DIDL-Lite>'
        )
        body = (
            f'<u:SetAVTransportURI xmlns:u="{AVT}">'
            '<InstanceID>0</InstanceID>'
            f'<CurrentURI>{escape(url)}</CurrentURI>'
            f'<CurrentURIMetaData>{escape(didl)}</CurrentURIMetaData>'
            '</u:SetAVTransportURI>'
        )
        self._soap("SetAVTransportURI", body)

    def _play(self):
        body = (f'<u:Play xmlns:u="{AVT}"><InstanceID>0</InstanceID>'
                '<Speed>1</Speed></u:Play>')
        self._soap("Play", body)

    @staticmethod
    def _upnp_class(mime):
        if mime.startswith("audio"):
            return "object.item.audioItem.musicTrack"
        if mime.startswith("image"):
            return "object.item.imageItem.photo"
        return "object.item.videoItem"

    # ---- file server -----------------------------------------------------
    def _ensure_server(self):
        with self._lock:
            if self._server is None:
                # Prefer the fixed port (so a single LAN firewall rule suffices);
                # fall back to an ephemeral port if it is busy.
                want = int(self.cfg.get("cast_port", 8083))
                try:
                    srv = ThreadingHTTPServer(("0.0.0.0", want), _Handler)
                except OSError:
                    srv = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
                srv.daemon_threads = True
                srv.registry = self._registry
                threading.Thread(target=srv.serve_forever, name="cast-http",
                                 daemon=True).start()
                self._server = srv
                self._port = srv.server_address[1]
                log.info("cast file server on :%d", self._port)
            return self._port

    def _serve_file(self, path):
        port = self._ensure_server()
        token = uuid.uuid4().hex[:8]
        mime = guess_mime(path)
        self._registry[token] = (os.path.abspath(path), mime)
        name = os.path.basename(path)
        url = f"http://{self._lan_ip()}:{port}/{token}/{urllib.parse.quote(name)}"
        return url, mime, name

    def _lan_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.cfg.get("ip", "8.8.8.8"), DMR_PORT))
            return s.getsockname()[0]
        finally:
            s.close()
