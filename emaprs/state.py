# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""In-memory state shared by the receivers and the web clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

from .store import Store

log = logging.getLogger(__name__)

CLIENT_QUEUE_MAX = 1000


def packets_max(station_expire_minutes: int) -> int:
    """Cap on each station's history and on the Messages list.

    Twice the expiry time in minutes, i.e. an average of two packets a
    minute sustained over the whole expiry window.
    """
    return 2 * station_expire_minutes


class Hub:
    def __init__(
        self,
        raw_max: int = 1000,
        station_expire_minutes: int = 2880,
        mycall: str = "",
        map_settings: dict | None = None,
        static_position: list[float] | None = None,
        gpsd_enabled: bool = True,
        store: Store | None = None,
    ) -> None:
        if station_expire_minutes < 1:
            raise ValueError("station_expire_minutes must be at least 1")
        self.packets_max = packets_max(station_expire_minutes)
        self.raw: deque[dict] = deque(maxlen=raw_max)
        self.messages: deque[dict] = deque(maxlen=self.packets_max)
        # Per-station packet history, keyed by station name (callsign-SSID,
        # or object/item name), oldest first. The map marker sits at the
        # newest positioned packet; earlier positions form the trail.
        # Mirrored to `store` (if any) so it survives a restart.
        self.stations: dict[str, deque[dict]] = {}
        self.station_expire = station_expire_minutes * 60
        self.store = store
        self.mycall = mycall
        self.map_settings = map_settings or {}
        # Own position. Starts at the configured fixed position (if any);
        # the first real gpsd fix replaces it and gpsd is followed from then on.
        self.own: dict | None = None
        if static_position:
            self.own = {
                "source": "static",
                "mode": 0,
                "lat": static_position[0],
                "lon": static_position[1],
                "alt": None,
                "speed": None,
                "track": None,
                "time": None,
            }
        self.status = {"kiss": False, "gpsd": False, "gps_mode": 0, "gpsd_enabled": gpsd_enabled}
        self._next_id = 1
        self._clients: set[asyncio.Queue] = set()

    # --- subscribers -------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def broadcast(self, msg: dict) -> None:
        data = json.dumps(msg)
        for q in list(self._clients):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                log.warning("dropping slow web client")
                self._close_client(q)

    def close_clients(self) -> None:
        """Close every web client's websocket (called at shutdown).

        A websocket handler only returns when the browser disconnects or its
        queue yields None, so without this, open map pages keep the web
        server's graceful shutdown waiting (aiohttp: up to shutdown_timeout).
        """
        for q in list(self._clients):
            self._close_client(q)

    def _close_client(self, q: asyncio.Queue) -> None:
        # Drop queued data so None fits even in a full queue; the websocket
        # handler sees None, closes the socket and returns.
        self._clients.discard(q)
        while not q.empty():
            q.get_nowait()
        q.put_nowait(None)

    # --- updates -----------------------------------------------------------

    def add_packet(self, pkt: dict) -> None:
        pkt["id"] = self._next_id
        self._next_id += 1
        self.raw.append(pkt)
        name = pkt["name"]
        if pkt["has_position"] and not pkt["alive"]:
            # Killed object/item: forget it and its history.
            if self.stations.pop(name, None) is not None:
                if self.store:
                    self.store.delete_station(name)
                self.broadcast({"type": "remove", "names": [name]})
        else:
            q = self.stations.get(name)
            if q is None:
                q = self.stations[name] = deque(maxlen=self.packets_max)
            if self.store:
                if len(q) == q.maxlen:
                    self.store.delete_ids([q[0]["id"]])  # about to fall off
                self.store.add(pkt)
            q.append(pkt)
        if not pkt["has_position"]:
            self.messages.append(pkt)
        self.broadcast({"type": "packet", "packet": pkt})

    def set_kiss_status(self, connected: bool) -> None:
        self.status["kiss"] = connected
        self.broadcast({"type": "status", "status": self.status})

    def set_gpsd_status(self, connected: bool) -> None:
        self.status["gpsd"] = connected
        if not connected:
            self.status["gps_mode"] = 0
        self.broadcast({"type": "status", "status": self.status})

    def set_fix(self, fix: dict) -> None:
        if fix["mode"] != self.status["gps_mode"]:
            self.status["gps_mode"] = fix["mode"]
            self.broadcast({"type": "status", "status": self.status})
        if fix["lat"] is None:
            return  # keep the last known (or fixed) position
        self.own = {**fix, "source": "gpsd"}
        self.broadcast({"type": "gps", "fix": self.own})

    def restore(self, now: float | None = None) -> int:
        """Reload station histories from the store after a restart.

        Packets past expiry are dropped. Raw and Messages are rebuilt from
        what's left. Returns the number of packets restored.
        """
        if not self.store:
            return 0
        cutoff = (time.time() if now is None else now) - self.station_expire
        packets = self.store.load(cutoff)
        overflow = []
        for pkt in packets:
            q = self.stations.get(pkt["name"])
            if q is None:
                q = self.stations[pkt["name"]] = deque(maxlen=self.packets_max)
            if len(q) == q.maxlen:
                overflow.append(q[0]["id"])  # cap shrank since it was saved
            q.append(pkt)
            self.raw.append(pkt)
            if not pkt["has_position"]:
                self.messages.append(pkt)
        self.store.delete_ids(overflow)
        if packets:
            self._next_id = packets[-1]["id"] + 1
        return len(packets)

    def expire_stations(self, now: float | None = None) -> list[str]:
        """Drop packets older than the expiry from every station's history
        and from Messages.

        Stations left with no packets are removed. Returns their names.
        """
        cutoff = (time.time() if now is None else now) - self.station_expire
        trimmed = False
        while self.messages and self.messages[0]["ts"] < cutoff:
            self.messages.popleft()
            trimmed = True
        gone = []
        for name, q in self.stations.items():
            while q and q[0]["ts"] < cutoff:
                q.popleft()
                trimmed = True
            if not q:
                gone.append(name)
        for n in gone:
            del self.stations[n]
        if trimmed and self.store:
            self.store.expire(cutoff)
        if trimmed:
            # Clients trim their own copies with the same cutoff.
            self.broadcast({"type": "expire", "cutoff": cutoff})
        return gone

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "mycall": self.mycall,
            "map": self.map_settings,
            "limits": {
                "raw": self.raw.maxlen,
                "messages": self.packets_max,
                "station_packets": self.packets_max,
            },
            "status": self.status,
            "own": self.own,
            "stations": [{"name": n, "packets": list(q)} for n, q in self.stations.items()],
            "raw": list(self.raw),
            "messages": list(self.messages),
        }


async def run_expiry(hub: Hub, interval: float = 60.0) -> None:
    while True:
        await asyncio.sleep(interval)
        hub.expire_stations()
