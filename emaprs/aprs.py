# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Turn a TNC2 packet string into the normalized dict the rest of the app uses."""

from __future__ import annotations

import logging
import time

import aprslib
from aprslib.exceptions import ParseError, UnknownFormat

log = logging.getLogger(__name__)


def base_call(call: str) -> str:
    return call.split("-", 1)[0].strip().upper()


def _payload(tnc2: str) -> str:
    return tnc2.split(":", 1)[1] if ":" in tnc2 else ""


def _header(tnc2: str) -> tuple[str, str, list[str]]:
    head = tnc2.split(":", 1)[0]
    src, _, rest = head.partition(">")
    parts = rest.split(",") if rest else [""]
    return src, parts[0], parts[1:]


def _summary(p: dict, payload: str) -> str:
    fmt = p.get("format")
    if fmt == "message":
        resp = p.get("response")
        if resp:
            return f"{resp} {p.get('msgNo', '')}".strip()
        text = p.get("message_text", "")
        if p.get("msgNo"):
            text += f" {{{p['msgNo']}}}"
        return text
    if fmt == "bulletin":
        return p.get("message_text", "")
    if fmt == "status":
        return p.get("status", "")
    if p.get("comment"):
        return p["comment"]
    return payload


def _parse(tnc2: str) -> tuple[dict | None, str | None, str | None]:
    """Run aprslib. Returns (parsed dict or None, format, error)."""
    try:
        p = aprslib.parse(tnc2)
    except UnknownFormat as e:
        # Valid packet that aprslib doesn't decode (telemetry, raw GPS, ...).
        # Its message names the type: "Format is not supported: 'T' telemetry
        # report". Read from the message rather than aprslib's (private)
        # table, so a wording change degrades to "unknown" instead of failing.
        msg = str(e)
        fmt = msg.split("'", 2)[2].strip() if msg.count("'") >= 2 else ""
        return None, fmt or "unknown", None
    except ParseError as e:
        return None, "error", str(e)
    except Exception as e:  # aprslib has real bugs on garbage (e.g. NameError)
        log.debug("aprslib crashed on %r", tnc2, exc_info=True)
        return None, "error", f"{type(e).__name__}: {e}"
    return p, p.get("format"), None


def _position(p: dict) -> tuple[float, float] | None:
    """(lat, lon) if the parsed packet has a usable position, else None."""
    lat, lon = p.get("latitude"), p.get("longitude")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return float(lat), float(lon)


def _mark_mine(pkt: dict, mycall: str) -> None:
    """Set from_me / to_me, ignoring SSIDs (N0CALL-9 is still N0CALL)."""
    me = base_call(mycall)
    pkt["from_me"] = base_call(pkt["from"]) == me
    if pkt["format"] == "message" and pkt["addressee"]:
        pkt["to_me"] = base_call(pkt["addressee"]) == me


def process(tnc2: str, mycall: str = "", now: float | None = None) -> dict:
    """Parse a TNC2 string. Never raises; failures are reported in the dict."""
    ts = time.time() if now is None else now
    src, dest, path = _header(tnc2)
    payload = _payload(tnc2)
    pkt: dict = {
        "ts": ts,
        "raw": tnc2,
        "from": src,
        "to": dest,
        "path": path,
        "format": None,
        "name": src,
        "has_position": False,
        "lat": None,
        "lon": None,
        "symbol": None,
        "symbol_table": None,
        "course": None,
        "speed": None,
        "altitude": None,
        "comment": None,
        "addressee": None,
        "text": payload,
        "alive": True,
        "error": None,
        "to_me": False,
        "from_me": False,
    }

    p, pkt["format"], pkt["error"] = _parse(tnc2)
    if p is not None:
        pkt["from"] = p.get("from", src)
        pkt["to"] = p.get("to", dest)
        pkt["path"] = p.get("path", path)
        name = (p.get("object_name") or "").strip()
        pkt["name"] = name or pkt["from"]
        pkt["alive"] = p.get("alive", True)
        pos = _position(p)
        if pos:
            pkt["has_position"] = True
            pkt["lat"], pkt["lon"] = pos
        for key in ("symbol", "symbol_table", "course", "speed", "altitude", "comment"):
            if p.get(key) not in (None, ""):
                pkt[key] = p[key]
        # "addresse" (sic) is aprslib's spelling of the key; don't "fix" it.
        if p.get("addresse"):
            pkt["addressee"] = p["addresse"].strip()
        pkt["text"] = _summary(p, payload)

    if mycall:
        _mark_mine(pkt, mycall)
    return pkt
