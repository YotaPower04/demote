"""The brand-agnostic driver interface.

Every TV/streamer brand is a `TVDriver` subclass in this package. The rest of the
app (controller, UI, gamepad) talks only to this interface using logical keys
(`tvremote.logical`), never to a brand's protocol directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    """A device found by discovery (or reconstructed from a saved profile)."""
    brand: str
    name: str
    host: str
    model: str = ""
    mac: str = ""
    port: int | None = None
    advanced: bool = False          # hide behind the wizard's "Advanced" expander
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable id for dedup / profile lookup."""
        return f"{self.brand}:{self.host}"


@dataclass
class Challenge:
    """What the user must do to finish pairing.

    kind = "none"  → nothing (e.g. Roku); complete_pairing() succeeds immediately.
    kind = "allow" → accept an on-screen prompt (Samsung/LG); response ignored.
    kind = "pin"   → read a code off the TV and type it back (Vizio/Android TV).
    """
    kind: str = "none"
    prompt: str = ""


class TVDriver(ABC):
    brand: str = "generic"
    pretty: str = "Generic device"

    def __init__(self, device: DeviceInfo, profile: dict | None = None,
                 on_status=None, on_apps=None):
        self.device = device
        self.profile = profile or {}
        self._on_status = on_status or (lambda s: None)
        self._on_apps = on_apps or (lambda a: None)

    # ---- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, timeout: float = 3.0) -> list[DeviceInfo]:
        """Return devices of this brand on the LAN (empty if none/unsupported)."""
        return []

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        """Connect / begin any background connection."""

    def stop(self):
        """Disconnect and release resources."""

    # ---- capabilities ----------------------------------------------------
    @abstractmethod
    def capabilities(self) -> set[str]:
        """Set of `logical.Cap.*` this device supports (drives UI visibility)."""

    # ---- control ---------------------------------------------------------
    @abstractmethod
    def send_key(self, logical: str):
        """Send a logical key (`tvremote.logical` name)."""

    def power(self):
        """Toggle/So power — connected → off; asleep → wake if supported."""
        self.send_key("POWER")

    def send_text(self, text: str):
        """Type into an on-screen keyboard (if Cap.TEXT)."""

    def launch_app(self, app_id: str):
        """Launch an installed app by its id (if Cap.APPS)."""

    def refresh_apps(self):
        """Ask the device for its installed-app list → on_apps callback."""

    def cast(self, target: str) -> tuple[bool, str]:
        """Cast a local file / media URL (if Cap.CAST). Returns (ok, message)."""
        return (False, "Casting isn't supported on this device.")

    # ---- pairing ---------------------------------------------------------
    def needs_pairing(self) -> bool:
        """True if the device has no stored credential yet."""
        return False

    def begin_pairing(self) -> Challenge:
        """Kick off pairing (may show a prompt/PIN on the TV)."""
        return Challenge()

    def complete_pairing(self, response: str | None = None) -> bool:
        """Finish pairing with the user's response (PIN text, or None). Saves creds."""
        return True
