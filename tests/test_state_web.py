# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
import json
import re

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from emaprs import __version__, aprs, gps
from emaprs.state import Hub
from emaprs.web import make_app


def test_hub_routes_packets():
    hub = Hub(raw_max=10)
    hub.add_packet(aprs.process("N0CALL>APRS:!4903.50N/07201.75W>"))
    hub.add_packet(aprs.process("N0CALL>APRS:>status"))
    assert len(hub.raw) == 2
    assert len(hub.messages) == 1
    assert "N0CALL" in hub.stations


def test_hub_expiry_and_killed_object():
    hub = Hub(station_expire_minutes=1)
    hub.add_packet(aprs.process("N0CALL>APRS:!4903.50N/07201.75W>", now=0))
    hub.add_packet(aprs.process("N0CALL>APRS:;OBJ      *092345z4903.50N/07201.75W>", now=100))
    assert set(hub.stations) == {"N0CALL", "OBJ"}
    assert hub.expire_stations(now=100) == ["N0CALL"]
    hub.add_packet(aprs.process("N0CALL>APRS:;OBJ      _092345z4903.50N/07201.75W>", now=101))
    assert hub.stations == {}


def test_hub_caps_are_twice_expiry_minutes():
    hub = Hub(station_expire_minutes=7)
    assert hub.packets_max == 14
    assert hub.messages.maxlen == 14
    assert hub.snapshot()["limits"] == {"raw": 1000, "messages": 14, "station_packets": 14}


def test_hub_station_history_trimmed_oldest_first():
    hub = Hub(station_expire_minutes=1)  # cap: 2 packets
    for i, t in enumerate((0, 30, 50)):
        hub.add_packet(aprs.process(f"N0CALL>APRS:!490{i}.50N/07201.75W>", now=t))
    # Capped at 2: the oldest packet fell off.
    assert [p["ts"] for p in hub.stations["N0CALL"]] == [30, 50]
    hub.add_packet(aprs.process("N0CALL>APRS:>status", now=80))
    assert [p["ts"] for p in hub.stations["N0CALL"]] == [50, 80]
    q = hub.subscribe()
    assert hub.expire_stations(now=115) == []
    assert [p["ts"] for p in hub.stations["N0CALL"]] == [80]
    assert json.loads(q.get_nowait()) == {"type": "expire", "cutoff": 55}
    snap = hub.snapshot()
    assert snap["stations"][0]["name"] == "N0CALL"
    assert [p["ts"] for p in snap["stations"][0]["packets"]] == [80]


def test_hub_messages_expire():
    hub = Hub(station_expire_minutes=1)
    hub.add_packet(aprs.process("N0CALL>APRS:>old", now=0))
    hub.add_packet(aprs.process("W1AW>APRS:>new", now=50))
    hub.expire_stations(now=100)
    assert [p["text"] for p in hub.messages] == ["new"]


def test_gps_tpv_parsing():
    fix = gps.parse_tpv(
        {"class": "TPV", "mode": 3, "lat": 1.5, "lon": 2.5, "altHAE": 10, "speed": 3}
    )
    assert fix["lat"] == 1.5 and fix["alt"] == 10.0 and fix["speed"] == 3.0
    assert gps.parse_tpv({"class": "TPV", "mode": 1})["lat"] is None
    assert gps.parse_tpv({"class": "SKY"}) is None


def test_hub_fix_without_position_updates_mode_only():
    hub = Hub()
    hub.set_gpsd_status(True)
    hub.set_fix(gps.parse_tpv({"class": "TPV", "mode": 3, "lat": 1.0, "lon": 2.0}))
    assert hub.own["lat"] == 1.0 and hub.status["gps_mode"] == 3
    hub.set_fix(gps.parse_tpv({"class": "TPV", "mode": 1}))
    assert hub.status["gps_mode"] == 1
    assert hub.own["lat"] == 1.0  # last known position kept


def test_static_position_until_gps_fix():
    hub = Hub(static_position=[45.0, -122.0])
    assert hub.own["source"] == "static" and hub.own["lat"] == 45.0
    hub.set_fix(gps.parse_tpv({"class": "TPV", "mode": 1}))  # no fix: keep static
    assert hub.own["source"] == "static"
    hub.set_fix(gps.parse_tpv({"class": "TPV", "mode": 3, "lat": 46.0, "lon": -121.0}))
    assert hub.own["source"] == "gpsd" and hub.own["lat"] == 46.0
    hub.set_fix(gps.parse_tpv({"class": "TPV", "mode": 1}))  # lost fix: keep last GPS
    assert hub.own["source"] == "gpsd" and hub.own["lat"] == 46.0


def test_gpsd_disabled_reported_in_status():
    hub = Hub(static_position=[45.0, -122.0], gpsd_enabled=False)
    snap = hub.snapshot()
    assert snap["status"]["gpsd_enabled"] is False
    assert snap["own"]["source"] == "static"


async def test_close_clients_closes_websockets():
    hub = Hub()
    async with TestClient(TestServer(make_app(hub))) as client:
        ws = await client.ws_connect("/ws")
        assert json.loads((await ws.receive()).data)["type"] == "snapshot"
        hub.close_clients()
        msg = await ws.receive(timeout=2)
        assert msg.type == WSMsgType.CLOSE
        assert hub._clients == set()


async def test_websocket_snapshot_then_live():
    hub = Hub(mycall="N0CALL", map_settings={"center": [0, 0], "zoom": 5, "tile_url": "x"})
    hub.add_packet(aprs.process("W7A>APRS:>before"))
    async with TestClient(TestServer(make_app(hub))) as client:
        r = await client.get("/")
        assert r.status == 200 and "emaprs" in (body := await r.text())
        assert f"v{__version__}" in body and "{{" not in body
        assert r.headers["Cache-Control"] == "no-cache"
        r = await client.get("/static/app.js")
        assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
        for t in (0, 1, 2):
            r = await client.get(f"/static/symbols/aprs-symbols-24-{t}@2x.png")
            assert r.status == 200 and r.content_type == "image/png"
        r = await client.get("/api/snapshot")
        assert (await r.json())["mycall"] == "N0CALL"

        ws = await client.ws_connect("/ws")
        snap = json.loads((await ws.receive()).data)
        assert snap["type"] == "snapshot"
        assert [p["text"] for p in snap["messages"]] == ["before"]

        hub.add_packet(aprs.process("W7A>APRS::N0CALL   :hi{1", mycall="N0CALL"))
        msg = json.loads((await ws.receive()).data)
        assert msg["type"] == "packet"
        assert msg["packet"]["to_me"] is True
        await ws.close()


async def test_page_needs_no_internet():
    """Every script and stylesheet the page loads, Leaflet included, is served
    by emaprs itself, so the page works in a browser with no internet."""
    hub = Hub(map_settings={"center": [0, 0], "zoom": 5, "tile_url": "x"})
    async with TestClient(TestServer(make_app(hub))) as client:
        body = await (await client.get("/")).text()
        assets = re.findall(r'<(?:script|link)[^>]* (?:src|href)="([^"]+)"', body)
        assert "/static/leaflet/leaflet.js" in assets
        assert "/static/leaflet/leaflet.css" in assets
        for url in assets:
            assert url.startswith("/static/"), f"{url} is not served by emaprs"
            assert (await client.get(url)).status == 200, url
        css = await (await client.get("/static/leaflet/leaflet.css")).text()
        images = set(re.findall(r'url\((images/[^)]+)\)', css))
        assert images  # the layers control icons, at least
        for image in images:
            r = await client.get(f"/static/leaflet/{image}")
            assert r.status == 200 and r.content_type == "image/png", image
