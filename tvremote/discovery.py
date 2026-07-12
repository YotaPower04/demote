"""Aggregate device discovery across all registered drivers.

Runs every driver's `discover()` concurrently and merges the results, de-duped by
`DeviceInfo.key`. Drivers that find nothing (or don't support discovery) just
return an empty list.
"""
from __future__ import annotations

import concurrent.futures
import logging

from . import drivers

log = logging.getLogger("tvremote.discovery")


def merge(results: list) -> list:
    """De-dupe and order discovery results (pure; unit-tested)."""
    seen, deduped = set(), []
    for d in results:
        if d.key not in seen:
            seen.add(d.key)
            deduped.append(d)

    # A smart TV also answers generic DLNA/UPnP discovery, so it shows up twice
    # (e.g. once as Samsung, once as a DLNA renderer). Prefer the brand-specific
    # driver — drop the generic DLNA twin when another driver already claims the
    # same host. Pure DLNA renderers (no brand entry) are kept.
    specific_hosts = {d.host for d in deduped if d.brand != "dlna"}
    deduped = [d for d in deduped if not (d.brand == "dlna" and d.host in specific_hosts)]

    # Normal devices first, "advanced" (e.g. Fire TV) last.
    deduped.sort(key=lambda d: (d.advanced, d.brand, d.name))
    return deduped


def scan(timeout: float = 3.0) -> list:
    """Return a de-duplicated list of DeviceInfo found on the LAN."""
    results: list = []
    classes = drivers.all_classes()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(classes))) as ex:
        futures = {ex.submit(cls.discover, timeout): cls for cls in classes}
        for fut in concurrent.futures.as_completed(futures, timeout=timeout + 10):
            cls = futures[fut]
            try:
                results.extend(fut.result() or [])
            except Exception as e:
                log.info("%s.discover failed: %s", cls.__name__, e)
    return merge(results)
