# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
import pytest

from emaprs import aprs


def test_position():
    p = aprs.process("N0CALL-9>APRS,WIDE1-1*,WIDE2-1:!4903.50N/07201.75W>088/036 hello")
    assert p["has_position"]
    assert round(p["lat"], 4) == 49.0583
    assert round(p["lon"], 4) == -72.0292
    assert p["symbol"] == ">"
    assert p["comment"] == "hello"
    assert p["path"] == ["WIDE1-1*", "WIDE2-1"]
    assert p["error"] is None
    assert p["name"] == "N0CALL-9"


def test_message_not_position():
    p = aprs.process("N0CALL>APRS::KB1ABC-7 :Hi there{001")
    assert not p["has_position"]
    assert p["format"] == "message"
    assert p["addressee"] == "KB1ABC-7"
    assert p["text"] == "Hi there {001}"


def test_status():
    p = aprs.process("N0CALL>APRS:>Net tonight")
    assert not p["has_position"]
    assert p["format"] == "status"
    assert p["text"] == "Net tonight"


def test_telemetry_is_not_an_error():
    p = aprs.process("N0CALL>APRS:T#005,199,000,255,073,123,01101001")
    assert not p["has_position"]
    assert p["error"] is None
    assert p["format"] == "telemetry report"
    assert p["text"].startswith("T#005")


def test_object_keyed_by_name():
    p = aprs.process("N0CALL>APRS:;LEADER   *092345z4903.50N/07201.75W>088/036")
    assert p["has_position"]
    assert p["name"] == "LEADER"
    assert p["from"] == "N0CALL"


def test_killed_object():
    p = aprs.process("N0CALL>APRS:;LEADER   _092345z4903.50N/07201.75W>")
    assert p["alive"] is False


def test_malformed_goes_through_with_error():
    p = aprs.process("N0CALL>APRS:!garbage")
    assert p["error"]
    assert p["format"] == "error"
    assert not p["has_position"]
    assert p["raw"] == "N0CALL>APRS:!garbage"
    assert p["from"] == "N0CALL"


def test_to_me_matches_with_or_without_ssid():
    pkt = "W7NET>APRS::N0CALL-7 :hello{1"
    assert aprs.process(pkt, mycall="N0CALL")["to_me"]
    assert aprs.process(pkt, mycall="N0CALL-9")["to_me"]
    assert not aprs.process(pkt, mycall="N0OTHER")["to_me"]
    assert not aprs.process(pkt)["to_me"]


def test_from_me():
    p = aprs.process("N0CALL-9>APRS:>hi", mycall="N0CALL")
    assert p["from_me"]
    assert not p["to_me"]


def test_message_ack_and_rej():
    ack = aprs.process("W7A>APRS::N0CALL-7 :ack001", mycall="N0CALL")
    assert (ack["format"], ack["text"], ack["to_me"]) == ("message", "ack 001", True)
    rej = aprs.process("W7A>APRS::N0CALL-7 :rej002")
    assert rej["text"] == "rej 002"


def test_bulletin():
    p = aprs.process("N0CALL>APRS::BLN1     :Net at 8pm")
    assert p["format"] == "bulletin"
    assert p["text"] == "Net at 8pm"


def test_aprslib_crash_is_reported_not_raised(monkeypatch):
    # aprslib has real bugs on garbage input (fuzzing hits a NameError); one
    # bad packet must not take emaprs down.
    def crash(_):
        raise NameError("name 'x' is not defined")

    monkeypatch.setattr(aprs.aprslib, "parse", crash)
    p = aprs.process("N0CALL>APRS:!whatever")
    assert p["format"] == "error"
    assert p["error"] == "NameError: name 'x' is not defined"
    assert p["from"] == "N0CALL" and p["raw"] == "N0CALL>APRS:!whatever"
    assert not p["has_position"]


def test_unsupported_format_without_type_name_is_unknown(monkeypatch):
    # The format name comes from aprslib's message ("... 'T' telemetry
    # report"); a message without one falls back to "unknown", not an error.
    def unsupported(_):
        raise aprs.UnknownFormat("format is not supported")

    monkeypatch.setattr(aprs.aprslib, "parse", unsupported)
    p = aprs.process("N0CALL>APRS:?whatever")
    assert p["format"] == "unknown"
    assert p["error"] is None


@pytest.mark.parametrize(
    "lat, lon",
    [(91, 0), (-91, 0), (0, 181), (0, -181), ("49.0", -72.0), (None, -72.0)],
)
def test_unusable_position_is_dropped(monkeypatch, lat, lon):
    parsed = {"format": "uncompressed", "from": "N0CALL", "latitude": lat, "longitude": lon}
    monkeypatch.setattr(aprs.aprslib, "parse", lambda _: dict(parsed))
    p = aprs.process("N0CALL>APRS:!x")
    assert not p["has_position"]
    assert p["lat"] is None and p["lon"] is None


def test_to_me_only_for_messages():
    # A status that merely mentions mycall isn't addressed to it.
    p = aprs.process("W7A>APRS:>calling N0CALL", mycall="N0CALL")
    assert not p["to_me"] and not p["from_me"]
