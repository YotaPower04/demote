"""Shared LAN helpers for drivers (port probing, local subnet enumeration)."""
from __future__ import annotations

import socket


def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        try:
            s.close()
        except OSError:
            pass
        return False


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_subnet_hosts() -> list[str]:
    ip = local_ip()
    try:
        a, b, c, _ = ip.split(".")
    except ValueError:
        return []
    return [f"{a}.{b}.{c}.{i}" for i in range(1, 255)]


def broadcast_addr(host: str) -> str:
    try:
        a, b, c, _ = host.split(".")
        return f"{a}.{b}.{c}.255"
    except ValueError:
        return "255.255.255.255"
