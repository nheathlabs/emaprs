# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""aiohttp web app: static page, JSON snapshot, and a push WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from aiohttp import WSMsgType, web

from . import __version__
from .state import Hub

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
HUB_KEY = web.AppKey("hub", Hub)


def _git_hash() -> str:
    """Short commit hash this code came from.

    Running from a git checkout: ask git (reflects the current HEAD).
    Installed copy: use the hash setup.py recorded at build time.
    """
    pkg_dir = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--short", "HEAD"],
            cwd=pkg_dir,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        toplevel, short = out.stdout.split()
        # Only trust git if this package *is* the checkout, not merely
        # installed somewhere inside an unrelated repository.
        if Path(toplevel).resolve() == pkg_dir.parent:
            return short
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        from ._build_info import GIT_HASH as built_hash
    except ImportError:
        return "unknown"
    return built_hash


GIT_HASH = _git_hash()


async def index(request: web.Request) -> web.Response:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(
        text=html.replace("{{VERSION}}", __version__).replace("{{GIT_HASH}}", GIT_HASH),
        content_type="text/html",
    )


async def snapshot(request: web.Request) -> web.Response:
    return web.json_response(request.app[HUB_KEY].snapshot())


async def websocket(request: web.Request) -> web.WebSocketResponse:
    hub = request.app[HUB_KEY]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    # Subscribe before taking the snapshot so no event falls in between.
    q = hub.subscribe()
    try:
        await ws.send_str(json.dumps(hub.snapshot()))

        async def pump() -> None:
            while True:
                data = await q.get()
                if data is None:
                    await ws.close()
                    return
                await ws.send_str(data)

        pump_task = asyncio.create_task(pump())
        try:
            async for msg in ws:  # the page sends nothing yet; just drain incoming frames
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            pump_task.cancel()
    except ConnectionResetError:
        pass
    finally:
        hub.unsubscribe(q)
    return ws


async def _no_stale_cache(request: web.Request, response: web.StreamResponse) -> None:
    # Make browsers revalidate the page and static files (cheap 304s via
    # ETag/Last-Modified) so an upgraded server never pairs with stale CSS/JS.
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"


def make_app(hub: Hub) -> web.Application:
    app = web.Application()
    app.on_response_prepare.append(_no_stale_cache)
    app[HUB_KEY] = hub
    app.router.add_get("/", index)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/static/", STATIC_DIR)
    return app
