# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Shutdown must not wait on open map pages.

Browser websockets never end on their own, so before the fix aiohttp's
graceful shutdown waited ~45 s for them whenever a page was open. Both ways
of stopping emaprs are covered: cancelling run() (what Ctrl-C does) and
SIGTERM (what systemd does).
"""

import asyncio
import contextlib
import signal
import socket
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType

from emaprs import config
from emaprs.__main__ import run

ROOT = Path(__file__).resolve().parent.parent
# Well under aiohttp's ~10 s shutdown_timeout backstop, so this fails if
# websockets are no longer closed up front and the backstop is doing the work.
MAX_EXIT_SECONDS = 3


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def settings(http_port: int) -> dict:
    # No database, no gpsd; nothing listens on the KISS port, so the KISS
    # client just keeps retrying in the background.
    return {
        "kiss": {"host": "127.0.0.1", "port": free_port()},
        "gpsd": {"enabled": False},
        "http": {"bind": ["127.0.0.1"], "port": http_port},
        "storage": {"database": ""},
    }


async def connect_ws(session: aiohttp.ClientSession, port: int):
    """Connect to /ws once the server is up (within 15 s); return it after the snapshot."""
    async with asyncio.timeout(15):
        while True:
            try:
                ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws")
                break
            except aiohttp.ClientConnectionError:
                await asyncio.sleep(0.1)
    msg = await ws.receive(timeout=5)
    assert msg.type == WSMsgType.TEXT and '"snapshot"' in msg.data
    return ws


async def assert_closed_by_server(ws) -> None:
    msg = await ws.receive(timeout=MAX_EXIT_SECONDS)
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED)


async def test_cancel_with_open_page_exits_promptly():
    port = free_port()
    task = asyncio.create_task(run(config.from_dict(settings(port))))
    async with aiohttp.ClientSession() as session:
        ws = await connect_ws(session, port)
        start = time.monotonic()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, MAX_EXIT_SECONDS)
        assert time.monotonic() - start < MAX_EXIT_SECONDS
        await assert_closed_by_server(ws)


async def test_sigterm_with_open_page_exits_promptly(tmp_path):
    port = free_port()
    s = settings(port)
    cfg = tmp_path / "emaprs.toml"
    cfg.write_text(
        f'[kiss]\nhost = "127.0.0.1"\nport = {s["kiss"]["port"]}\n'
        "[gpsd]\nenabled = false\n"
        f'[http]\nbind = ["127.0.0.1"]\nport = {port}\n'
        '[storage]\ndatabase = ""\n'
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "emaprs", "--config", str(cfg),
        cwd=ROOT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with aiohttp.ClientSession() as session:
            ws = await connect_ws(session, port)
            start = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            await assert_closed_by_server(ws)
            rc = await asyncio.wait_for(proc.wait(), MAX_EXIT_SECONDS)
            assert time.monotonic() - start < MAX_EXIT_SECONDS
            assert rc == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
