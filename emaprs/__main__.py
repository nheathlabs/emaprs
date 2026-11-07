# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Entry point: `emaprs [--config PATH]` or `python -m emaprs [--config PATH]`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import ipaddress
import logging
import signal
import socket
import sqlite3
import sys
from pathlib import Path

from aiohttp import web

from . import __version__, aprs, ax25, config, gps, kiss
from .state import Hub, run_expiry
from .store import Store
from .web import make_app

log = logging.getLogger("emaprs")


class StartupError(Exception):
    """Can't start; the message says why and what to change. Exit with `code`."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _ipv6_available() -> bool:
    try:
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM).close()
    except OSError:
        return False
    return True


async def _listen(runner: web.AppRunner, addrs: list[str], port: int) -> None:
    """Start one web listener per [http] bind address, or raise StartupError."""
    v6 = [a for a in addrs if ipaddress.ip_address(a).version == 6]
    # With IPv6 off in the kernel, asyncio fails an IPv6 address with the same
    # generic "could not bind on any address" error as an address that isn't
    # up, so check first to give the right advice.
    if v6 and not _ipv6_available():
        v4 = [a for a in addrs if a not in v6] or ["0.0.0.0"]
        listed = ", ".join(f'"{a}"' for a in v4)
        what = "address" if len(v6) == 1 else "addresses"
        raise StartupError(
            f"[http] bind lists IPv6 {what} {', '.join(v6)}, but IPv6 is not "
            f"enabled on this machine. Remove the IPv6 {what} from [http] bind, "
            f"e.g. bind = [{listed}]",
            code=2,  # a config change, like other config errors: retrying won't help
        )
    for addr in addrs:
        try:
            await web.TCPSite(runner, addr, port).start()
        except OSError as e:
            raise StartupError(_listen_error(addr, port, e)) from None
        shown = f"[{addr}]" if addr in v6 else addr
        log.info("web page at http://%s:%d/", shown, port)


def _listen_error(addr: str, port: int, e: OSError) -> str:
    where = f"can't listen on {addr} port {port}"
    if e.errno == errno.EADDRINUSE:
        return (f"{where}: the port is already in use (another emaprs, or another "
                "program?). Stop it or change [http] port.")
    # asyncio drops an address that isn't up (EADDRNOTAVAIL) and, with no
    # socket left, raises "could not bind on any address" with no errno.
    if e.errno in (errno.EADDRNOTAVAIL, None):
        extra = ", with IPv6 enabled on it" if ipaddress.ip_address(addr).version == 6 else ""
        return (f"{where}: no interface on this machine has that address (yet). "
                f"Check the address in [http] bind and that its interface is up{extra}.")
    if e.errno == errno.EACCES:
        return (f"{where}: ports below 1024 need root or CAP_NET_BIND_SERVICE; "
                "use a higher [http] port.")
    return f"{where}: {e.strerror or e}"


async def run(cfg: config.Config) -> None:
    store = None
    if cfg.storage.database:
        store = Store(cfg.storage.database)
        log.info("database: %s", store.path)
    else:
        log.info("database disabled; packet history is lost on restart")
    hub = Hub(
        raw_max=cfg.buffers.raw_max,
        station_expire_minutes=cfg.buffers.station_expire_minutes,
        store=store,
        mycall=cfg.station.callsign,
        map_settings={
            "center": cfg.map.center,
            "zoom": cfg.map.zoom,
            "tile_url": cfg.map.tile_url,
        },
        static_position=cfg.station.position or None,
        gpsd_enabled=cfg.gpsd.enabled,
    )
    if store:
        n = hub.restore()
        log.info("restored %d packets for %d stations", n, len(hub.stations))

    def on_frame(frame: bytes) -> None:
        try:
            tnc2 = ax25.decode(frame).tnc2()
        except ax25.AX25Error as e:
            log.info("undecodable AX.25 frame (shown in Raw): %s", e)
            hub.add_packet(
                {
                    **aprs.process("", cfg.station.callsign),
                    "raw": frame.hex(" "),
                    "from": "?",
                    "name": "?",
                    "format": "error",
                    "error": f"AX.25: {e}",
                    "text": "",
                }
            )
            return
        pkt = aprs.process(tnc2, cfg.station.callsign)
        if pkt["error"]:
            log.info("APRS parse error (%s): %s", pkt["error"], tnc2)
        else:
            log.debug("rx %s", tnc2)
        hub.add_packet(pkt)

    tasks = [
        asyncio.create_task(
            kiss.run_client(
                cfg.kiss.host,
                cfg.kiss.port,
                on_frame,
                hub.set_kiss_status,
                max_backoff=cfg.kiss.reconnect_max_seconds,
            ),
            name="kiss",
        ),
        asyncio.create_task(run_expiry(hub), name="expiry"),
    ]
    if cfg.gpsd.enabled:
        tasks.append(
            asyncio.create_task(
                gps.run_client(cfg.gpsd.host, cfg.gpsd.port, hub.set_fix, hub.set_gpsd_status),
                name="gpsd",
            )
        )

    # When stopping, aiohttp waits up to shutdown_timeout (default 60 s) for
    # open requests to finish, then cancels them and can wait that long again;
    # an open map page used to delay exit by ~45 s. Websockets are now closed
    # first (hub.close_clients() below), so this only caps how long a stuck
    # connection can hold up exit (~2 x 5 s).
    runner = web.AppRunner(make_app(hub), access_log=None, shutdown_timeout=5)
    await runner.setup()

    try:
        await _listen(runner, cfg.http.bind, cfg.http.port)
        # Background tasks loop forever; stop on SIGTERM, or surface an error if one dies.
        stop = asyncio.create_task(_wait_for_sigterm(), name="sigterm")
        done, _ = await asyncio.wait([*tasks, stop], return_when=asyncio.FIRST_COMPLETED)
        stop.cancel()
        for t in done:
            if t is not stop:
                t.result()
        log.info("shutting down")
    finally:
        for t in tasks:
            t.cancel()
        # Close browser websockets before stopping the web server: they never
        # end on their own, and an open map page would otherwise hold up
        # shutdown for the whole shutdown_timeout.
        hub.close_clients()
        await runner.cleanup()
        if store:
            store.close()


async def _wait_for_sigterm() -> None:
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Not supported on every platform; Ctrl-C still works there.
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGTERM, event.set)
    await event.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="emaprs", description="Embeddable APRS: a frontend for working with APRS."
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=f"TOML config file (default: {config.DEFAULT_PATH}, if it exists)",
    )
    args = parser.parse_args(argv)

    try:
        cfg, path = config.load(args.config)
    except config.ConfigError as e:
        print(f"emaprs: config error: {e}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=cfg.logging.level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log.info("config: %s", path if path else "defaults (no config file)")
    for w in cfg.warnings:
        log.warning("config: %s", w)
    if cfg.station.position:
        lat, lon = cfg.station.position
        if cfg.gpsd.enabled:
            log.info("fixed position %.5f, %.5f used until gpsd has a fix", lat, lon)
        else:
            log.info("gpsd disabled; using fixed position %.5f, %.5f", lat, lon)

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    except StartupError as e:
        log.error("%s", e)
        return e.code
    except OSError as e:
        log.error("%s", e)
        return 1
    except sqlite3.Error as e:
        log.error("database %s: %s", cfg.storage.database, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
