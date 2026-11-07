# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""gpsd client: the connection loop against a fake gpsd (parse_tpv is
covered in test_state_web.py)."""

import asyncio
import json
import socket

from emaprs import gps


def tpv(lat: float, lon: float) -> bytes:
    msg = {"class": "TPV", "mode": 3, "lat": lat, "lon": lon, "altHAE": 10, "speed": 2}
    return json.dumps(msg).encode() + b"\n"


async def test_client_watches_reports_fixes_and_reconnects():
    fixes, statuses, watches = [], [], []
    connections = 0
    two_fixes = asyncio.Event()

    async def fake_gpsd(reader, writer):
        nonlocal connections
        connections += 1
        watches.append(await reader.readline())
        if connections == 1:
            writer.write(b'{"class":"VERSION","release":"3.25"}\n')  # not a TPV
            writer.write(b"not json\n")
            writer.write(b"[1, 2]\n")  # JSON, but not an object
            writer.write(tpv(45.5, -122.6))
        else:
            writer.write(tpv(45.6, -122.7))
        await writer.drain()
        writer.close()  # gpsd going away: the client must reconnect

    async def on_fix(fix):  # async handler: _call must await it
        fixes.append(fix)
        if len(fixes) == 2:
            two_fixes.set()

    server = await asyncio.start_server(fake_gpsd, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    task = asyncio.create_task(
        gps.run_client("127.0.0.1", port, on_fix, statuses.append, max_backoff=1)
    )
    try:
        await asyncio.wait_for(two_fixes.wait(), 5)
    finally:
        task.cancel()
        server.close()
        await server.wait_closed()

    assert watches[:2] == [gps.WATCH, gps.WATCH]
    assert [(f["lat"], f["lon"], f["mode"]) for f in fixes[:2]] == [
        (45.5, -122.6, 3),
        (45.6, -122.7, 3),
    ]
    assert statuses[:3] == [True, False, True]


async def test_client_keeps_retrying_until_gpsd_starts():
    fixes, statuses = [], []
    got_fix = asyncio.Event()

    def on_fix(fix):
        fixes.append(fix)
        got_fix.set()

    with socket.socket() as s:  # a port nothing listens on (yet)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    task = asyncio.create_task(
        gps.run_client("127.0.0.1", port, on_fix, statuses.append, max_backoff=1)
    )
    server = None
    try:
        await asyncio.sleep(0.2)  # first connect has failed by now
        assert statuses == [] and fixes == []

        async def fake_gpsd(reader, writer):
            await reader.readline()
            writer.write(tpv(1.5, 2.5))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(fake_gpsd, "127.0.0.1", port)
        await asyncio.wait_for(got_fix.wait(), 5)  # next retry (after ~1 s) connects
    finally:
        task.cancel()
        if server:
            server.close()
            await server.wait_closed()
    assert statuses[0] is True
    assert (fixes[0]["lat"], fixes[0]["lon"]) == (1.5, 2.5)
