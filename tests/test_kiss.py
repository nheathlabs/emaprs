# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
import asyncio

from emaprs import kiss
from emaprs.kiss import FEND, FESC, TFEND, TFESC, KissDecoder


def test_escape_roundtrip():
    data = bytes([0x01, FEND, 0x02, FESC, 0x03])
    esc = kiss.escape(data)
    assert FEND not in esc
    assert esc == bytes([0x01, FESC, TFEND, 0x02, FESC, TFESC, 0x03])
    assert kiss.unescape(esc) == data


def test_decoder_split_across_reads():
    payload = b"hello\xc0world\xdb!"
    stream = kiss.encode(payload) + kiss.encode(b"second")
    d = KissDecoder()
    frames = []
    for i in range(len(stream)):  # one byte at a time
        frames += d.feed(stream[i : i + 1])
    assert frames == [payload, b"second"]


def test_decoder_ignores_empty_and_non_data_frames():
    d = KissDecoder()
    stream = bytes([FEND, FEND]) + bytes([FEND, 0x01, 0x30, FEND]) + kiss.encode(b"x")
    assert d.feed(stream) == [b"x"]


def test_decoder_accepts_other_ports():
    d = KissDecoder()
    assert d.feed(kiss.encode(b"abc", port=2)) == [b"abc"]


async def test_client_receives_and_reconnects():
    frames, statuses = [], []
    connections = 0

    async def server_handler(reader, writer):
        nonlocal connections
        connections += 1
        writer.write(kiss.encode(f"frame{connections}".encode()))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    task = asyncio.create_task(
        kiss.run_client("127.0.0.1", port, frames.append, statuses.append, max_backoff=1)
    )
    try:
        for _ in range(100):
            if len(frames) >= 2:
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        server.close()
    assert frames[:2] == [b"frame1", b"frame2"]
    assert statuses[:3] == [True, False, True]
