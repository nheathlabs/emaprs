#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""TEST ONLY: fake gpsd speaking the gpsd JSON protocol over TCP.

Sends VERSION on connect, then a TPV report every second once the client
has sent ?WATCH.

    .venv/bin/python tools/fake_gpsd.py --lat 45.52 --lon -122.68 --walk
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import time
from datetime import UTC, datetime


class Walker:
    """Shared position so every client sees the same track."""

    def __init__(self, lat: float, lon: float, walk: bool) -> None:
        self.lat, self.lon = lat, lon
        self.walk = walk
        self.track = 45.0
        self.speed = 8.0 if walk else 0.0  # m/s

    def step(self, dt: float) -> None:
        if not self.walk:
            return
        self.track = (self.track + 3.0) % 360  # gentle curve
        dist = self.speed * dt
        self.lat += dist * math.cos(math.radians(self.track)) / 111_000
        self.lon += dist * math.sin(math.radians(self.track)) / (
            111_000 * max(math.cos(math.radians(self.lat)), 0.01)
        )


def tpv(w: Walker, nofix: bool) -> dict:
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if nofix:
        return {"class": "TPV", "device": "/dev/fake0", "mode": 1, "time": now}
    return {
        "class": "TPV", "device": "/dev/fake0", "mode": 3, "time": now,
        "lat": round(w.lat, 7), "lon": round(w.lon, 7), "altHAE": 52.3,
        "speed": w.speed, "track": w.track,
    }


async def handle(reader, writer, w: Walker, args) -> None:
    peer = writer.get_extra_info("peername")
    print(f"client connected: {peer}", flush=True)
    try:
        writer.write(b'{"class":"VERSION","release":"3.25-fake","rev":"fake",'
                     b'"proto_major":3,"proto_minor":15}\n')
        await writer.drain()
        line = await reader.readline()
        if not line.startswith(b"?WATCH"):
            print(f"  expected ?WATCH, got {line!r}", flush=True)
            return
        devices = {"class": "DEVICES", "devices": [{"path": "/dev/fake0"}]}
        writer.write(json.dumps(devices).encode() + b"\n")
        while True:
            writer.write(json.dumps(tpv(w, args.nofix)).encode() + b"\n")
            await writer.drain()
            await asyncio.sleep(1.0)
    except (ConnectionError, OSError):
        pass
    finally:
        print(f"client gone: {peer}", flush=True)
        writer.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2947)
    ap.add_argument("--lat", type=float, default=45.52)
    ap.add_argument("--lon", type=float, default=-122.68)
    ap.add_argument("--walk", action="store_true", help="drift slowly so the marker moves")
    ap.add_argument("--nofix", action="store_true", help="report no GPS fix")
    args = ap.parse_args()

    w = Walker(args.lat, args.lon, args.walk)

    async def mover() -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            w.step(now - last)
            last = now

    server = await asyncio.start_server(lambda r, wr: handle(r, wr, w, args), args.host, args.port)
    print(f"fake gpsd on {args.host}:{args.port}", flush=True)
    async with server:
        await asyncio.gather(server.serve_forever(), mover())


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
