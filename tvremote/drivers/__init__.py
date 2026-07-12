"""Driver registry. Each brand registers itself on import."""
from __future__ import annotations

from .base import Challenge, DeviceInfo, TVDriver

_REGISTRY: dict[str, type[TVDriver]] = {}


def register(cls: type[TVDriver]) -> type[TVDriver]:
    _REGISTRY[cls.brand] = cls
    return cls


def get(brand: str) -> type[TVDriver] | None:
    return _REGISTRY.get(brand)


def all_classes() -> list[type[TVDriver]]:
    return list(_REGISTRY.values())


# Register built-in drivers (import for side effect).
from . import samsung  # noqa: E402,F401
from . import roku     # noqa: E402,F401
from . import dlna     # noqa: E402,F401
from . import vizio    # noqa: E402,F401
from . import lg       # noqa: E402,F401
from . import androidtv  # noqa: E402,F401
from . import philips  # noqa: E402,F401

__all__ = ["TVDriver", "DeviceInfo", "Challenge", "register", "get", "all_classes"]
