#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""TEST ONLY: fake KISS-over-TCP TNC that feeds sample APRS packets.

Every connected client gets the same rotating mix of packets:
moving stations, a fixed station, an object, a weather station, messages,
status, telemetry, a malformed APRS packet and an undecodable AX.25 frame.

    .venv/bin/python tools/fake_kiss.py --center 45.52,-122.68 --mycall N0CALL
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from emaprs import ax25, kiss


def fmt_lat(lat: float) -> str:
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    deg = int(lat)
    return f"{deg:02d}{(lat - deg) * 60:05.2f}{hemi}"


def fmt_lon(lon: float) -> str:
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    deg = int(lon)
    return f"{deg:03d}{(lon - deg) * 60:05.2f}{hemi}"


def offset(lat: float, lon: float, north_km: float, east_km: float) -> tuple[float, float]:
    return (
        lat + north_km / 111.0,
        lon + east_km / (111.0 * max(math.cos(math.radians(lat)), 0.01)),
    )


def zulu() -> str:
    return time.strftime("%d%H%Mz", time.gmtime())


class Scenario:
    def __init__(self, lat: float, lon: float, mycall: str) -> None:
        self.lat, self.lon = lat, lon
        self.mycall = mycall.upper()
        self.t0 = time.time()
        self.msgno = itertools.count(1)

    def _circle(self, radius_km: float, period_s: float, phase: float):
        a = 2 * math.pi * (time.time() - self.t0) / period_s + phase
        la, lo = offset(self.lat, self.lon, radius_km * math.sin(a), radius_km * math.cos(a))
        course = int(math.degrees(-a)) % 360 or 360
        speed_knots = int(2 * math.pi * radius_km / period_s * 3600 / 1.852)
        return la, lo, course, speed_knots

    def packets(self):
        """Yield (description, frame bytes) forever."""
        while True:
            la, lo, crs, spd = self._circle(3.0, 600, 0)
            yield "moving car", ax25.encode(
                "K7CAR-9", "APRS", ["WIDE1-1*", "WIDE2-1"],
                f"!{fmt_lat(la)}/{fmt_lon(lo)}>{crs:03d}/{spd:03d}Mobile test".encode(),
            )
            yield "status", ax25.encode(
                "W7NET", "APRS", ["WIDE2-2"], b">Net tonight 1900 local on 146.840"
            )
            la, lo = offset(self.lat, self.lon, -2.0, 4.0)
            yield "fixed home", ax25.encode(
                "W7HOME", "APRS", ["WIDE2-1"],
                f"={fmt_lat(la)}/{fmt_lon(lo)}-Home station".encode(),
            )
            la, lo = offset(self.lat, self.lon, 3.5, 1.0)
            yield "overlay digi (S#)", ax25.encode(
                "W7RPT-10", "APRS", [],
                f"!{fmt_lat(la)}S{fmt_lon(lo)}#Overlay digipeater".encode(),
            )
            la, lo = offset(self.lat, self.lon, -3.0, -2.5)
            yield "alt table (\\k SUV)", ax25.encode(
                "K7SUV", "APRS", ["WIDE1-1"],
                f"!{fmt_lat(la)}\\{fmt_lon(lo)}kAlternate table symbol".encode(),
            )
            yield "message", ax25.encode(
                "K7CAR-9", "APRS", ["WIDE1-1", "WIDE2-1"],
                f":{'W7HOME':<9}:On my way, ETA 10 min{{{next(self.msgno)}".encode(),
            )
            la, lo, crs, spd = self._circle(5.0, 900, math.pi)
            yield "moving bike", ax25.encode(
                "KD7BIK-7", "APRS", ["WIDE1-1"],
                f"!{fmt_lat(la)}/{fmt_lon(lo)}b{crs:03d}/{spd:03d}".encode(),
            )
            yield "telemetry", ax25.encode(
                "W7WX", "APRS", ["WIDE2-1"], b"T#005,199,000,255,073,123,01101001"
            )
            la, lo = offset(self.lat, self.lon, 1.5, -3.0)
            yield "weather", ax25.encode(
                "W7WX", "APRS", ["WIDE2-1"],
                f"@{zulu()}{fmt_lat(la)}/{fmt_lon(lo)}_220/004g005t077r000p000P000h50b10132".encode(),
            )
            if self.mycall:
                yield "message to mycall", ax25.encode(
                    "W7NET", "APRS", ["WIDE2-1"],
                    f":{self.mycall:<9}:Are you checking in tonight?{{{next(self.msgno)}".encode(),
                )
            la, lo = offset(self.lat, self.lon, -1.0, -1.0)
            yield "object", ax25.encode(
                "W7NET", "APRS", ["WIDE2-1"],
                f";{'FIELDDAY':<9}*{zulu()}{fmt_lat(la)}/{fmt_lon(lo)}[Field day site".encode(),
            )
            yield "bulletin", ax25.encode(
                "W7NET", "APRS", ["WIDE2-2"], f":{'BLN1':<9}:Repeater maintenance Sat".encode()
            )
            yield "malformed APRS", ax25.encode(
                "N0BAD", "APRS", [], b"!this is not a position"
            )
            # Bad control byte: decoded as an AX.25 error by the app.
            bad = bytearray(ax25.encode("N0BAD", "APRS", [], b"junk"))
            bad[14] = 0x3F
            yield "bad AX.25", bytes(bad)


async def handle(reader, writer, args) -> None:
    peer = writer.get_extra_info("peername")
    print(f"client connected: {peer}", flush=True)
    scen = Scenario(args.lat, args.lon, args.mycall)
    try:
        for desc, frame in scen.packets():
            writer.write(kiss.encode(frame))
            await writer.drain()
            print(f"  sent {desc}", flush=True)
            await asyncio.sleep(args.interval)
    except (ConnectionError, OSError):
        pass
    finally:
        print(f"client gone: {peer}", flush=True)
        writer.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--center", default="45.5200,-122.6800",
                    help="lat,lon to place stations around (default: %(default)s)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between packets")
    ap.add_argument("--mycall", default="", help="also send messages addressed to this call")
    args = ap.parse_args()
    args.lat, args.lon = (float(v) for v in args.center.split(","))

    server = await asyncio.start_server(lambda r, w: handle(r, w, args), args.host, args.port)
    print(f"fake KISS TNC on {args.host}:{args.port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
