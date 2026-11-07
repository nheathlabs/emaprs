# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""KISS framing and an asyncio KISS-over-TCP client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD

CMD_DATA = 0x00


def escape(data: bytes) -> bytes:
    return data.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(
        bytes([FEND]), bytes([FESC, TFEND])
    )


def unescape(data: bytes) -> bytes:
    out = bytearray()
    it = iter(data)
    for b in it:
        if b == FESC:
            nxt = next(it, None)
            if nxt == TFEND:
                out.append(FEND)
            elif nxt == TFESC:
                out.append(FESC)
            elif nxt is not None:
                # Protocol violation; keep the byte rather than drop data.
                out.append(nxt)
        else:
            out.append(b)
    return bytes(out)


def encode(ax25_frame: bytes, port: int = 0) -> bytes:
    """Wrap an AX.25 frame as a KISS data frame."""
    return bytes([FEND, (port << 4) | CMD_DATA]) + escape(ax25_frame) + bytes([FEND])


class KissDecoder:
    """Incremental KISS stream decoder. Feed bytes, get AX.25 frames back."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        frames: list[bytes] = []
        while True:
            try:
                end = self._buf.index(FEND)
            except ValueError:
                break
            chunk = bytes(self._buf[:end])
            del self._buf[: end + 1]
            if not chunk:
                continue  # leading FEND or back-to-back FENDs
            frame = unescape(chunk)
            if (frame[0] & 0x0F) != CMD_DATA:
                continue  # not a data frame (TXDELAY etc.)
            if len(frame) > 1:
                frames.append(frame[1:])
        # Guard against a stream with no FENDs at all.
        if len(self._buf) > 65536:
            self._buf.clear()
        return frames


FrameHandler = Callable[[bytes], Awaitable[None] | None]
StatusHandler = Callable[[bool], Awaitable[None] | None]


async def _call(fn, *args):
    res = fn(*args)
    if asyncio.iscoroutine(res):
        await res


async def run_client(
    host: str,
    port: int,
    on_frame: FrameHandler,
    on_status: StatusHandler,
    max_backoff: float = 30.0,
) -> None:
    """Connect to a KISS TCP TNC forever, reconnecting with backoff."""
    backoff = 1.0
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as e:
            log.warning("KISS connect to %s:%d failed: %s (retry in %.0fs)", host, port, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue

        log.info("KISS connected to %s:%d", host, port)
        backoff = 1.0
        await _call(on_status, True)
        decoder = KissDecoder()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                for frame in decoder.feed(data):
                    try:
                        await _call(on_frame, frame)
                    except Exception:
                        log.exception("error handling KISS frame %r", frame)
        except OSError as e:
            log.warning("KISS connection error: %s", e)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        log.warning("KISS disconnected from %s:%d", host, port)
        await _call(on_status, False)
        await asyncio.sleep(backoff)
