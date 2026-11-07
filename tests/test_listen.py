# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Startup listeners for [http] bind: one per address, clear errors."""

import socket

import aiohttp
import pytest
from aiohttp import web

from emaprs import __main__ as main
from emaprs.__main__ import StartupError, _listen
from emaprs.state import Hub
from emaprs.web import make_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def runner():
    r = web.AppRunner(make_app(Hub()))
    await r.setup()
    yield r
    await r.cleanup()


@pytest.mark.skipif(not main._ipv6_available(), reason="IPv6 disabled on this machine")
async def test_listens_on_every_address(runner):
    port = free_port()
    await _listen(runner, ["127.0.0.1", "::1"], port)
    async with aiohttp.ClientSession() as s:
        for host in ("127.0.0.1", "[::1]"):
            async with s.get(f"http://{host}:{port}/api/snapshot") as r:
                assert r.status == 200


async def test_ipv6_disabled_says_to_remove_it(runner, monkeypatch):
    monkeypatch.setattr(main, "_ipv6_available", lambda: False)
    with pytest.raises(StartupError) as e:
        await _listen(runner, ["0.0.0.0", "::"], free_port())
    assert e.value.code == 2
    assert str(e.value) == (
        "[http] bind lists IPv6 address ::, but IPv6 is not enabled on this machine. "
        'Remove the IPv6 address from [http] bind, e.g. bind = ["0.0.0.0"]'
    )


async def test_port_in_use(runner):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(StartupError) as e:
            await _listen(runner, ["127.0.0.1"], port)
    assert e.value.code == 1
    assert "port is already in use" in str(e.value)


async def test_address_not_on_this_machine(runner):
    # TEST-NET-1 (RFC 5737): never assigned to a real interface.
    with pytest.raises(StartupError) as e:
        await _listen(runner, ["192.0.2.1"], free_port())
    assert e.value.code == 1
    assert "no interface on this machine has that address" in str(e.value)
