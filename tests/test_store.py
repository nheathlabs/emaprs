# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
import contextlib
import sqlite3

import pytest

from emaprs import aprs
from emaprs.state import Hub
from emaprs.store import Store

POS = "N0CALL>APRS:!4903.50N/07201.75W>"


def rows(path):
    # A sqlite3 connection's own `with` only commits; closing() closes it.
    with contextlib.closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT id, name, ts FROM packets ORDER BY id").fetchall()


_stores: list[Store] = []


def make_hub(path, minutes=60):
    store = Store(path)
    _stores.append(store)
    return Hub(station_expire_minutes=minutes, store=store)


@pytest.fixture(autouse=True)
def close_stores():
    """Close every Store make_hub() opened once the test is done."""
    yield
    while _stores:
        _stores.pop().close()


def test_restart_restores_state(tmp_path):
    db = tmp_path / "e.sqlite3"
    hub = make_hub(db)
    hub.add_packet(aprs.process(POS, now=1000))
    hub.add_packet(aprs.process("N0CALL>APRS:>status", now=1010))
    hub.add_packet(aprs.process("W1AW>APRS:!4100.00N/07200.00W-", now=1020))
    hub.store.close()

    hub2 = make_hub(db)
    assert hub2.restore(now=1100) == 3
    assert {n: [p["ts"] for p in q] for n, q in hub2.stations.items()} == {
        "N0CALL": [1000, 1010],
        "W1AW": [1020],
    }
    assert [p["ts"] for p in hub2.raw] == [1000, 1010, 1020]
    assert [p["text"] for p in hub2.messages] == ["status"]
    # New packets continue the id sequence instead of overwriting rows.
    hub2.add_packet(aprs.process(POS, now=1200))
    assert [r[0] for r in rows(db)] == [1, 2, 3, 4]


def test_restore_drops_expired(tmp_path):
    db = tmp_path / "e.sqlite3"
    hub = make_hub(db, minutes=1)
    hub.add_packet(aprs.process(POS, now=0))
    hub.add_packet(aprs.process("W1AW>APRS:!4100.00N/07200.00W-", now=100))
    hub.store.close()

    hub2 = make_hub(db, minutes=1)
    assert hub2.restore(now=150) == 1
    assert list(hub2.stations) == ["W1AW"]
    assert [r[1] for r in rows(db)] == ["W1AW"]

    hub2.store.close()
    hub3 = make_hub(db, minutes=1)
    assert hub3.restore(now=10_000) == 0  # everything past expiry: gone
    assert hub3.stations == {} and rows(db) == []


def test_expiry_cap_and_kill_reach_the_database(tmp_path):
    db = tmp_path / "e.sqlite3"
    hub = make_hub(db, minutes=1)  # cap: 2 packets per station
    for t in (0, 10, 20):
        hub.add_packet(aprs.process(POS, now=t))
    assert [r[2] for r in rows(db)] == [10, 20]  # capped oldest went too

    hub.expire_stations(now=75)  # cutoff 15
    assert [r[2] for r in rows(db)] == [20]

    hub.add_packet(aprs.process("N0CALL>APRS:;OBJ      *092345z4903.50N/07201.75W>", now=30))
    assert {r[1] for r in rows(db)} == {"N0CALL", "OBJ"}
    hub.add_packet(aprs.process("N0CALL>APRS:;OBJ      _092345z4903.50N/07201.75W>", now=31))
    assert {r[1] for r in rows(db)} == {"N0CALL"}


def test_restore_applies_smaller_cap(tmp_path):
    db = tmp_path / "e.sqlite3"
    hub = make_hub(db, minutes=60)
    for t in range(5):
        hub.add_packet(aprs.process(POS, now=t))
    hub.store.close()

    hub2 = make_hub(db, minutes=1)  # cap now 2
    hub2.restore(now=10)
    assert [p["ts"] for p in hub2.stations["N0CALL"]] == [3, 4]
    assert [r[2] for r in rows(db)] == [3, 4]


def test_corrupt_database_moved_aside(tmp_path):
    db = tmp_path / "e.sqlite3"
    db.write_bytes(b"this is not a sqlite database" * 100)
    store = Store(db)
    assert store.load(None) == []
    store.close()
    assert len(list(tmp_path.glob("e.sqlite3.bad-*"))) == 1


def test_creates_parent_directory(tmp_path):
    db = tmp_path / "a" / "b" / "e.sqlite3"
    Store(db).close()
    assert db.exists()
