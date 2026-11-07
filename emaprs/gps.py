# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Minimal asyncio gpsd client (JSON protocol over TCP)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

WATCH = b'?WATCH={"enable":true,"json":true}\n'

FixHandler = Callable[[dict], Awaitable[None] | None]
StatusHandler = Callable[[bool], Awaitable[None] | None]


async def _call(fn, *args):
    res = fn(*args)
    if asyncio.iscoroutine(res):
        await res


def parse_tpv(msg: dict) -> dict | None:
    """Convert a gpsd TPV report to our fix dict, or None if not a TPV."""
    if msg.get("class") != "TPV":
        return None
    mode = int(msg.get("mode", 0) or 0)
    fix = {
        "mode": mode,  # 0/1 = no fix, 2 = 2D, 3 = 3D
        "lat": None,
        "lon": None,
        "alt": None,
        "speed": None,  # m/s
        "track": None,  # degrees true
        "time": msg.get("time"),
    }
    if mode >= 2 and "lat" in msg and "lon" in msg:
        fix["lat"] = float(msg["lat"])
        fix["lon"] = float(msg["lon"])
        alt = msg.get("altHAE", msg.get("alt"))
        fix["alt"] = float(alt) if alt is not None else None
        for key in ("speed", "track"):
            if msg.get(key) is not None:
                fix[key] = float(msg[key])
    else:
        fix["mode"] = min(mode, 1)
    return fix


async def run_client(
    host: str,
    port: int,
    on_fix: FixHandler,
    on_status: StatusHandler,
    max_backoff: float = 30.0,
) -> None:
    """Stay connected to gpsd forever, reconnecting with backoff."""
    backoff = 1.0
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as e:
            log.warning("gpsd connect to %s:%d failed: %s (retry in %.0fs)", host, port, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue

        log.info("gpsd connected at %s:%d", host, port)
        backoff = 1.0
        await _call(on_status, True)
        try:
            writer.write(WATCH)
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.debug("gpsd: ignoring non-JSON line %r", line)
                    continue
                fix = parse_tpv(msg) if isinstance(msg, dict) else None
                if fix is not None:
                    await _call(on_fix, fix)
        except (OSError, ValueError) as e:
            log.warning("gpsd connection error: %s", e)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        log.warning("gpsd disconnected from %s:%d", host, port)
        await _call(on_status, False)
        await asyncio.sleep(backoff)
