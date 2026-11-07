#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""DEV ONLY: regenerate the README screenshots in docs/screenshots/.

Starts the fake gpsd + fake KISS TNC and emaprs on free ports (so a real
Direwolf/gpsd on the default ports is never touched), lets packets build up,
then drives headless Chromium over the DevTools protocol to capture each
view in SHOTS at a fixed window size. Finally shrinks the PNGs to 256 colours
with ImageMagick, if installed.

    tools/screenshots.py                  # all shots
    tools/screenshots.py station-popup    # just some
    tools/screenshots.py --settle 20      # shorter warm-up

Needs Chromium/Chrome and the .venv (tools/install_build_deps.sh sets up both).
To add a screenshot, add an entry to SHOTS and reference it in README.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"

# aiohttp lives in the project venv; re-run under it if started elsewhere.
try:
    import aiohttp
except ImportError:
    # (Compare prefixes, not executables: venv python is a symlink to the system one.)
    if VENV_PY.exists() and Path(sys.prefix).resolve() != VENV_PY.parent.parent.resolve():
        os.execv(VENV_PY, [str(VENV_PY), __file__, *sys.argv[1:]])
    sys.exit("aiohttp not found: run tools/install_build_deps.sh first")

import argparse
import asyncio
import base64
import contextlib
import json
import shutil
import socket
import subprocess
import tempfile

OUT_DIR = ROOT / "docs" / "screenshots"
WIDTH, HEIGHT = 1280, 800
LAT, LON = 45.52, -122.68
MYCALL = "N0CALL"
ZOOM = 13
PACKET_INTERVAL = 0.5

# Shared JS helpers, evaluated on the page before each shot's script.
JS_HELPERS = """
window.__sleep = (ms) => new Promise((r) => setTimeout(r, ms));
window.__openStation = async (name) => {
  for (const m of document.querySelectorAll('.leaflet-marker-icon')) {
    m.click();
    await __sleep(200);
    const p = document.querySelector('.popup');
    if (p && p.innerText.split('\\n').includes(name)) return true;
  }
  throw new Error('station not on map: ' + name);
};
window.__reset = () => {
  document.querySelector('.leaflet-popup-close-button')?.click();
  document.getElementById('tab-raw').click();
};
"""

# name -> JS run (awaited) after resetting to the Raw tab with no popup open.
SHOTS: dict[str, str] = {
    "overview": "",
    "station-popup": """
        await __openStation('K7CAR-9');
        document.querySelector('.popup details').open = true;
    """,
    "messages": "document.getElementById('tab-msg').click();",
    "display-options": "document.getElementById('tab-opts').click();",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_chrome() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    sys.exit("Chromium/Chrome not found: run tools/install_build_deps.sh first")


async def wait_http(url: str) -> None:
    """Wait (up to 20 s) until url answers 200."""
    try:
        async with asyncio.timeout(20), aiohttp.ClientSession() as s:
            while True:
                try:
                    async with s.get(url) as r:
                        if r.status == 200:
                            return
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.3)
    except TimeoutError:
        raise RuntimeError(f"timed out waiting for {url}") from None


class Tab:
    """Minimal Chrome DevTools Protocol client for one page."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self.next_id = 0
        self.errors: list[str] = []

    async def call(self, method: str, **params) -> dict:
        self.next_id += 1
        mid = self.next_id
        await self.ws.send_json({"id": mid, "method": method, "params": params})
        while True:
            msg = json.loads((await self.ws.receive()).data)
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.errors.append(d.get("exception", {}).get("description") or d.get("text", ""))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    async def js(self, body: str) -> None:
        r = await self.call(
            "Runtime.evaluate",
            expression=f"(async () => {{ {body} }})()",
            awaitPromise=True,
        )
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(d.get("exception", {}).get("description") or d.get("text"))

    async def screenshot(self, path: Path) -> None:
        r = await self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(r["data"]))


async def capture(devtools_port: int, url: str, names: list[str]) -> list[str]:
    async with aiohttp.ClientSession() as s:
        async with s.put(f"http://127.0.0.1:{devtools_port}/json/new?about:blank") as r:
            target = await r.json()
        async with s.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            tab = Tab(ws)
            await tab.call("Runtime.enable")
            await tab.call("Page.enable")
            await tab.call(
                "Emulation.setDeviceMetricsOverride",
                width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False,
            )
            await tab.call("Page.navigate", url=url)
            await asyncio.sleep(4)  # snapshot, map tiles
            await tab.js(JS_HELPERS)
            for name in names:
                await tab.js("__reset();")
                await asyncio.sleep(0.3)
                await tab.js(SHOTS[name])
                await asyncio.sleep(1.5)  # tiles, popup pan
                path = OUT_DIR / f"{name}.png"
                await tab.screenshot(path)
                print(f"  {path.relative_to(ROOT)}")
            return tab.errors


async def close_chrome(devtools_port: int) -> None:
    # Ask the browser to quit: a snap-confined Chromium may not accept signals.
    with contextlib.suppress(Exception):
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{devtools_port}/json/version") as r:
                url = (await r.json())["webSocketDebuggerUrl"]
            async with s.ws_connect(url) as ws:
                await ws.send_json({"id": 1, "method": "Browser.close"})
                await asyncio.sleep(1)


def shrink(path: Path) -> None:
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return
    subprocess.run(
        [magick, str(path), "-dither", "FloydSteinberg", "-colors", "256",
         "-define", "png:compression-level=9", f"PNG8:{path}"],
        check=True,
    )


async def run(names: list[str], settle: float) -> int:
    kiss_port, gpsd_port, http_port, devtools_port = (free_port() for _ in range(4))
    with tempfile.TemporaryDirectory(prefix="emaprs-shots-") as tmp:
        cfg = Path(tmp) / "emaprs.toml"
        cfg.write_text(
            f'[station]\ncallsign = "{MYCALL}"\n'
            f'[kiss]\nhost = "127.0.0.1"\nport = {kiss_port}\n'
            f'[gpsd]\nhost = "127.0.0.1"\nport = {gpsd_port}\n'
            f'[http]\nbind = ["127.0.0.1"]\nport = {http_port}\n'
            f"[map]\ncenter = [{LAT}, {LON}]\nzoom = {ZOOM}\n"
            '[storage]\ndatabase = ""\n'  # start empty, never touch the real database
            '[logging]\nlevel = "WARNING"\n'
        )
        py = sys.executable
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": ROOT}
        procs = [
            # No --walk: keep "me" in the same spot every run.
            subprocess.Popen([py, "tools/fake_gpsd.py", "--port", str(gpsd_port),
                              "--lat", str(LAT), "--lon", str(LON)], **quiet),
            subprocess.Popen([py, "tools/fake_kiss.py", "--port", str(kiss_port),
                              "--center", f"{LAT},{LON}", "--mycall", MYCALL,
                              "--interval", str(PACKET_INTERVAL)], **quiet),
            subprocess.Popen([py, "-m", "emaprs", "--config", str(cfg)], cwd=ROOT),
            subprocess.Popen([find_chrome(), "--headless=new", "--hide-scrollbars",
                              f"--remote-debugging-port={devtools_port}",
                              f"--user-data-dir={tmp}/chrome", "about:blank"], **quiet),
        ]
        try:
            await wait_http(f"http://127.0.0.1:{http_port}/api/snapshot")
            await wait_http(f"http://127.0.0.1:{devtools_port}/json/version")
            print(f"letting packets build up for {settle:.0f}s ...")
            await asyncio.sleep(settle)
            print("capturing:")
            errors = await capture(devtools_port, f"http://127.0.0.1:{http_port}/", names)
        finally:
            await close_chrome(devtools_port)
            for p in reversed(procs):
                with contextlib.suppress(OSError):
                    p.terminate()
            for p in procs:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        p.kill()
    for name in names:
        shrink(OUT_DIR / f"{name}.png")
    if not (shutil.which("magick") or shutil.which("convert")):
        print("note: ImageMagick not installed, PNGs left full size")
    if errors:
        print("JavaScript errors on the page:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="*", metavar="NAME",
                    help=f"screenshots to take (default: all of {', '.join(SHOTS)})")
    ap.add_argument("--settle", type=float, default=45,
                    help="seconds of fake traffic before capturing (default: 45)")
    args = ap.parse_args()
    unknown = [n for n in args.names if n not in SHOTS]
    if unknown:
        ap.error(f"unknown screenshot(s): {', '.join(unknown)}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args.names or list(SHOTS), args.settle))


if __name__ == "__main__":
    sys.exit(main())
