# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
from pathlib import Path

import pytest

from emaprs import config


def test_defaults():
    cfg = config.from_dict({})
    assert cfg.kiss.port == 8001
    assert cfg.gpsd.port == 2947
    assert cfg.http.bind == ["0.0.0.0", "::"]
    assert cfg.station.callsign == ""


def test_example_file_loads():
    from pathlib import Path

    cfg, path = config.load(Path(__file__).parent.parent / "config.example.toml")
    assert cfg.buffers.raw_max == 1000
    assert path is not None


def test_overrides_and_normalizing():
    cfg = config.from_dict(
        {
            "station": {"callsign": " n0call-9 "},
            "map": {"center": [45, -122]},
            "logging": {"level": "debug"},
        }
    )
    assert cfg.station.callsign == "N0CALL-9"
    assert cfg.station.position == []
    assert cfg.map.center == [45.0, -122.0]
    assert cfg.logging.level == "DEBUG"


def test_station_position():
    cfg = config.from_dict({"station": {"position": [45, -122.5]}})
    assert cfg.station.position == [45.0, -122.5]


@pytest.mark.parametrize(
    "data, msg",
    [
        ({"kiss": {"prot": 1}}, "unknown key"),
        ({"bogus": {}}, "unknown section"),
        ({"kiss": {"port": "8001"}}, "must be int"),
        ({"kiss": {"port": 70000}}, "1-65535"),
        ({"gpsd": {"enabled": 1}}, "must be bool"),
        ({"map": {"center": [100, 0]}}, "center"),
        ({"station": {"position": [45.0]}}, "position"),
        ({"station": {"position": [45.0, 200]}}, "position"),
        ({"station": {"position": "45,-122"}}, "must be list"),
        ({"logging": {"level": "LOUD"}}, "level"),
        ({"buffers": {"station_expire_minutes": 0}}, "at least 1"),
        ({"storage": {"database": 1}}, "must be str"),
        ({"kiss": 8001}, r"\[kiss\] must be a table"),
        ({"kiss": {"reconnect_max_seconds": 0}}, "reconnect_max_seconds must be at least 1"),
        ({"buffers": {"raw_max": 0}}, "raw_max must be at least 1"),
        ({"map": {"zoom": 21}}, "zoom must be 0-20"),
        ({"map": {"zoom": -1}}, "zoom must be 0-20"),
    ],
)
def test_errors(data, msg):
    with pytest.raises(config.ConfigError, match=msg):
        config.from_dict(data)


def test_missing_explicit_file(tmp_path):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")


def test_default_path_absent_uses_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_PATH", tmp_path / "missing.toml")
    cfg, path = config.load(None)
    assert path is None
    assert cfg.kiss.port == 8001


def test_default_path_present_is_read(monkeypatch, tmp_path):
    f = tmp_path / "emaprs.toml"
    f.write_text("[kiss]\nport = 9001\n")
    monkeypatch.setattr(config, "DEFAULT_PATH", f)
    cfg, path = config.load(None)
    assert path == f
    assert cfg.kiss.port == 9001


def test_config_error_exits_2_with_message(tmp_path, capsys):
    from emaprs.__main__ import main

    f = tmp_path / "emaprs.toml"
    f.write_text('[http]\nbind = "0.0.0.0"\n')
    assert main(["--config", str(f)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("emaprs: config error: [http] bind must be list")


def test_bad_toml(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("[kiss\n")
    with pytest.raises(config.ConfigError):
        config.load(f)


def test_removed_buffer_keys_warn_not_fail():
    cfg = config.from_dict(
        {"buffers": {"messages_max": 500, "station_packets_max": 500, "raw_max": 20}}
    )
    assert cfg.buffers.raw_max == 20
    assert len(cfg.warnings) == 2
    assert all("no longer used" in w for w in cfg.warnings)
    assert config.from_dict({}).warnings == []


def test_storage_database(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert config.from_dict({}).storage.database == str(tmp_path / "emaprs" / "emaprs.sqlite3")
    assert config.from_dict({"storage": {"database": ""}}).storage.database == ""
    cfg = config.from_dict({"storage": {"database": "~/x.sqlite3"}})
    assert cfg.storage.database == str(Path.home() / "x.sqlite3")


@pytest.mark.parametrize(
    "bind",
    [
        ["0.0.0.0", "::"],
        ["0.0.0.0"],
        ["127.0.0.1", "192.168.1.20"],
        ["127.0.0.1", "::1"],
        ["192.168.1.20", "fd00::20"],
    ],
)
def test_http_bind_lists(bind):
    assert config.from_dict({"http": {"bind": bind}}).http.bind == bind


def test_http_bind_normalized():
    cfg = config.from_dict({"http": {"bind": [" 127.0.0.1 ", "FD00:0::20"]}})
    assert cfg.http.bind == ["127.0.0.1", "fd00::20"]


@pytest.mark.parametrize(
    "bind, message",
    [
        ("0.0.0.0", 'must be list, got str (\'0.0.0.0\'); write it as ["0.0.0.0"]'),
        ([], "must list at least one address"),
        (["localhost"], "'localhost' is not an IP address"),
        ([""], "'' is not an IP address"),
        ([8080], "8080 is not an IP address"),
        (["127.0.0.1", "127.0.0.1"], 'lists "127.0.0.1" twice'),
        (["0.0.0.0", "192.168.1.20"], '"0.0.0.0" already listens on every IPv4 address'),
        (["::", "::1"], '"::" already listens on every IPv6 address'),
    ],
)
def test_http_bind_rejected(bind, message):
    with pytest.raises(config.ConfigError) as e:
        config.from_dict({"http": {"bind": bind}})
    assert message in str(e.value)


def test_http_bind_wildcards_of_different_families_combine():
    # Each wildcard covers only its own family, so these don't overlap.
    assert config.from_dict({"http": {"bind": ["0.0.0.0", "::1"]}}).http.bind == ["0.0.0.0", "::1"]
