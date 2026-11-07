# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
import pytest

from emaprs import ax25


def test_roundtrip_simple():
    f = ax25.encode("N0CALL-9", "APRS", [], b"!4903.50N/07201.75W>")
    d = ax25.decode(f)
    assert d.source == "N0CALL-9"
    assert d.dest == "APRS"
    assert d.path == []
    assert d.tnc2() == "N0CALL-9>APRS:!4903.50N/07201.75W>"


def test_digipeated_star_on_last_repeated():
    f = ax25.encode("N0CALL", "APRS", ["WIDE1-1", "W7RPT*", "WIDE2-1"], b">hi")
    assert ax25.decode(f).tnc2() == "N0CALL>APRS,WIDE1-1,W7RPT*,WIDE2-1:>hi"


def test_known_bytes():
    # Hand-built: dest APRS, src N0CALL-9, no digis, UI, PID F0, info "x".
    dest = bytes(c << 1 for c in b"APRS  ") + bytes([0x60])
    src = bytes(c << 1 for c in b"N0CALL") + bytes([0x60 | (9 << 1) | 1])
    frame = dest + src + bytes([0x03, 0xF0]) + b"x"
    assert ax25.decode(frame).tnc2() == "N0CALL-9>APRS:x"


def test_latin1_fallback():
    f = ax25.encode("N0CALL", "APRS", [], b">caf\xe9")
    assert ax25.decode(f).tnc2() == "N0CALL>APRS:>café"


@pytest.mark.parametrize(
    "frame",
    [
        b"short",
        ax25.encode("N0CALL", "APRS", [], b"x")[:14],  # no control/PID
        bytes(ax25.encode("N0CALL", "APRS", [], b"x")[:14]) + b"\x3f\xf0x",  # not UI
        # unterminated address field
        bytes(c << 1 for c in b"APRS  ") + b"\x60"
        + bytes(c << 1 for c in b"N0CALL") + b"\x60" + b"\x03\xf0",
    ],
)
def test_bad_frames(frame):
    with pytest.raises(ax25.AX25Error):
        ax25.decode(frame)
