# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Receive path end to end: a fake KISS TNC's frames, through run()'s
on_frame (AX.25 decode -> APRS parse -> hub), to the web snapshot."""

import asyncio
import contextlib
import socket

import aiohttp

from emaprs import ax25, config, kiss
from emaprs.__main__ import run

GOOD = ax25.encode("N0CALL-9", "APRS", ["WIDE1-1"], b"!4903.50N/07201.75W>hello")
BAD_APRS = ax25.encode("W7BAD", "APRS", [], b"!garbage")  # valid AX.25, bad APRS
NOT_AX25 = b"\x01\x02\x03"  # too short to be an AX.25 frame


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_frames_reach_the_hub():
    async def fake_tnc(reader, writer):
        for frame in (GOOD, BAD_APRS, NOT_AX25):
            writer.write(kiss.encode(frame))
        await writer.drain()
        await reader.read()  # stay connected until emaprs goes away
        writer.close()

    tnc = await asyncio.start_server(fake_tnc, "127.0.0.1", 0)
    http_port = free_port()
    cfg = config.from_dict(
        {
            "station": {"callsign": "N0CALL"},
            "kiss": {"host": "127.0.0.1", "port": tnc.sockets[0].getsockname()[1]},
            "gpsd": {"enabled": False},
            "http": {"bind": ["127.0.0.1"], "port": http_port},
            "storage": {"database": ""},
        }
    )
    task = asyncio.create_task(run(cfg))
    try:
        async with aiohttp.ClientSession() as s, asyncio.timeout(10):
            while True:
                with contextlib.suppress(aiohttp.ClientConnectionError):
                    async with s.get(f"http://127.0.0.1:{http_port}/api/snapshot") as r:
                        snap = await r.json()
                        if len(snap["raw"]) == 3:
                            break
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        tnc.close()
        await tnc.wait_closed()

    good, bad_aprs, not_ax25 = snap["raw"]  # in arrival order

    assert good["raw"] == "N0CALL-9>APRS,WIDE1-1:!4903.50N/07201.75W>hello"
    assert good["error"] is None and good["has_position"]
    assert good["from_me"]  # mycall N0CALL matches N0CALL-9
    # Every sender gets a packet history; only N0CALL-9's has a position, so
    # it's the only one the map draws.
    stations = {st["name"]: st["packets"] for st in snap["stations"]}
    assert set(stations) == {"N0CALL-9", "W7BAD", "?"}
    assert [p["has_position"] for p in stations["N0CALL-9"]] == [True]

    # Decodes as AX.25 but not as APRS: kept, marked as an error.
    assert bad_aprs["from"] == "W7BAD" and bad_aprs["format"] == "error"
    assert bad_aprs["error"] and not bad_aprs["error"].startswith("AX.25")

    # Not AX.25 at all: still shown in Raw, as hex, so nothing vanishes.
    assert not_ax25["raw"] == "01 02 03"
    assert not_ax25["from"] == "?" and not_ax25["format"] == "error"
    assert not_ax25["error"].startswith("AX.25: ")
    assert not not_ax25["has_position"]
