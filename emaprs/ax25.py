# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""AX.25 UI frame <-> TNC2 text ("SRC>DST,PATH:payload")."""

from __future__ import annotations

from dataclasses import dataclass

CONTROL_UI = 0x03
PID_NO_L3 = 0xF0
MAX_DIGIS = 8


class AX25Error(ValueError):
    pass


@dataclass
class Frame:
    source: str
    dest: str
    path: list[str]  # digipeaters, with '*' on the last one that has repeated
    info: bytes

    def tnc2(self) -> str:
        head = f"{self.source}>{self.dest}"
        if self.path:
            head += "," + ",".join(self.path)
        return head + ":" + decode_info(self.info)


def decode_info(info: bytes) -> str:
    """APRS is nominally ASCII; accept UTF-8 and fall back to Latin-1."""
    try:
        return info.decode("utf-8")
    except UnicodeDecodeError:
        return info.decode("latin-1")


def _decode_addr(raw: bytes) -> tuple[str, int, bool, bool]:
    """Return (callsign-ssid, ssid, h_bit, last_flag) for one 7-byte address."""
    call = "".join(chr(b >> 1) for b in raw[:6]).rstrip()
    if not call or not all(c.isalnum() for c in call):
        raise AX25Error(f"bad callsign in address field: {raw!r}")
    ssid_byte = raw[6]
    ssid = (ssid_byte >> 1) & 0x0F
    h_bit = bool(ssid_byte & 0x80)
    last = bool(ssid_byte & 0x01)
    name = f"{call}-{ssid}" if ssid else call
    return name, ssid, h_bit, last


def decode(frame: bytes) -> Frame:
    """Decode an AX.25 UI frame (no FCS, as delivered by KISS)."""
    if len(frame) < 16:
        raise AX25Error(f"frame too short ({len(frame)} bytes)")
    addrs = []
    pos = 0
    while True:
        if pos + 7 > len(frame):
            raise AX25Error("address field not terminated")
        name, _ssid, h_bit, last = _decode_addr(frame[pos : pos + 7])
        addrs.append((name, h_bit))
        pos += 7
        if last:
            break
        if len(addrs) > 2 + MAX_DIGIS:
            raise AX25Error("too many addresses")
    if len(addrs) < 2:
        raise AX25Error("missing source address")
    if pos + 2 > len(frame):
        raise AX25Error("missing control/PID")
    control, pid = frame[pos], frame[pos + 1]
    if control != CONTROL_UI or pid != PID_NO_L3:
        raise AX25Error(f"not an APRS UI frame (control=0x{control:02x} pid=0x{pid:02x})")

    dest = addrs[0][0]
    source = addrs[1][0]
    digis = addrs[2:]
    # TNC2 convention: '*' after the last digipeater whose H bit is set.
    last_rep = max((i for i, (_, h) in enumerate(digis) if h), default=-1)
    path = [n + ("*" if i == last_rep else "") for i, (n, _) in enumerate(digis)]
    return Frame(source=source, dest=dest, path=path, info=frame[pos + 2 :])


def _encode_addr(name: str, last: bool, h_bit: bool = False) -> bytes:
    call, _, ssid = name.partition("-")
    call = call.upper()
    if not 1 <= len(call) <= 6 or not call.isalnum():
        raise AX25Error(f"bad callsign {name!r}")
    ssid_n = int(ssid) if ssid else 0
    if not 0 <= ssid_n <= 15:
        raise AX25Error(f"bad SSID in {name!r}")
    out = bytes((ord(c) << 1) for c in call.ljust(6))
    sb = 0x60 | (ssid_n << 1) | (0x80 if h_bit else 0) | (0x01 if last else 0)
    return out + bytes([sb])


def encode(source: str, dest: str, path: list[str], info: bytes) -> bytes:
    """Build an AX.25 UI frame. A '*' on a path entry sets H on it and all before it."""
    names = [p.rstrip("*") for p in path]
    starred = max((i for i, p in enumerate(path) if p.endswith("*")), default=-1)
    out = _encode_addr(dest, last=False) + _encode_addr(source, last=not names)
    for i, n in enumerate(names):
        out += _encode_addr(n, last=i == len(names) - 1, h_bit=i <= starred)
    return out + bytes([CONTROL_UI, PID_NO_L3]) + info
