# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Load and validate the TOML configuration file."""

from __future__ import annotations

import ipaddress
import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "emaprs" / "emaprs.toml"


def _default_database() -> str:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(data_home) / "emaprs" / "emaprs.sqlite3")


class ConfigError(Exception):
    pass


@dataclass
class StationConfig:
    callsign: str = ""
    # Fixed [lat, lon] for this station. Empty = not set. Used as the own
    # position when gpsd is disabled, or until gpsd gets its first fix.
    position: list = field(default_factory=list)


@dataclass
class KissConfig:
    host: str = "127.0.0.1"
    port: int = 8001
    reconnect_max_seconds: int = 30


@dataclass
class GpsdConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 2947


@dataclass
class HttpConfig:
    # Addresses to listen on, one socket each. Default: every IPv4 and IPv6
    # address, so the map is viewable from the local network.
    bind: list = field(default_factory=lambda: ["0.0.0.0", "::"])
    port: int = 8080


@dataclass
class BuffersConfig:
    raw_max: int = 1000
    # Also sets the per-station and Messages caps: 2 x this many packets.
    station_expire_minutes: int = 2880  # 2 days


# Keys that used to exist. Old configs that still set them get a warning,
# not an error, so a deployed config keeps working across an upgrade.
_REMOVED_KEYS = {
    ("buffers", "messages_max"): "the Messages cap is now 2 x station_expire_minutes",
    ("buffers", "station_packets_max"): "the per-station cap is now 2 x station_expire_minutes",
}


@dataclass
class StorageConfig:
    # SQLite file holding station packet history across restarts. "" = off.
    database: str = field(default_factory=_default_database)


@dataclass
class MapConfig:
    center: list = field(default_factory=lambda: [0.0, 0.0])
    zoom: int = 10
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    station: StationConfig = field(default_factory=StationConfig)
    kiss: KissConfig = field(default_factory=KissConfig)
    gpsd: GpsdConfig = field(default_factory=GpsdConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    buffers: BuffersConfig = field(default_factory=BuffersConfig)
    map: MapConfig = field(default_factory=MapConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    # Not a TOML section: problems worth logging once logging is set up.
    warnings: list = field(default_factory=list)


_SECTIONS = [f.name for f in fields(Config) if f.name != "warnings"]

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _check_type(section: str, key: str, value, expected):
    name = f"[{section}] {key}"
    if expected is bool:
        ok = isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        hint = ""
        if expected is list and isinstance(value, str):
            hint = f"; write it as {json.dumps([value])}"
        raise ConfigError(
            f"{name} must be {expected.__name__}, got {type(value).__name__} ({value!r}){hint}"
        )


def _build_section(section: str, cls, data, warnings: list) -> object:
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] must be a table")
    known = {f.name: f for f in fields(cls)}
    data = dict(data)
    for key in list(data):
        why = _REMOVED_KEYS.get((section, key))
        if why:
            warnings.append(f"[{section}] {key} is no longer used and was ignored ({why})")
            del data[key]
        elif key not in known:
            raise ConfigError(
                f"unknown key [{section}] {key} (valid: {', '.join(sorted(known))})"
            )
    obj = cls()
    for key, value in data.items():
        default = getattr(obj, key)
        _check_type(section, key, value, type(default))
        setattr(obj, key, value)
    return obj


def _latlon(name: str, c: list) -> list[float]:
    if (
        len(c) != 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in c)
        or not -90 <= c[0] <= 90
        or not -180 <= c[1] <= 180
    ):
        raise ConfigError(f"{name} must be [lat, lon] in degrees, got {c!r}")
    return [float(c[0]), float(c[1])]


def _bind_addresses(addrs: list) -> list[str]:
    """Check [http] bind and return its addresses in normal form.

    IP addresses only: a wildcard is only recognisable as one by address,
    and the IPv4/IPv6 split decides the startup error when IPv6 is off.
    """
    name = "[http] bind"
    if not addrs:
        raise ConfigError(
            f'{name} must list at least one address, e.g. ["0.0.0.0", "::"] for '
            'every interface or ["127.0.0.1"] for this machine only'
        )
    ips = []
    for a in addrs:
        try:
            if not isinstance(a, str):  # ip_address() would take 8080 as 0.0.31.144
                raise ValueError
            ip = ipaddress.ip_address(a.strip())
        except ValueError:
            raise ConfigError(
                f"{name}: {a!r} is not an IP address (use addresses such as "
                '"0.0.0.0", "::", "127.0.0.1" or "192.168.1.20", not host names)'
            ) from None
        if ip in ips:
            raise ConfigError(f'{name} lists "{ip}" twice')
        ips.append(ip)
    # A wildcard already covers every address of its family, and Linux won't
    # let it and a specific address of that family listen on the same port.
    for family in (4, 6):
        same = [ip for ip in ips if ip.version == family]
        wild = [ip for ip in same if ip.is_unspecified]
        if wild and len(same) > 1:
            others = ", ".join(f'"{ip}"' for ip in same if not ip.is_unspecified)
            raise ConfigError(
                f'{name}: "{wild[0]}" already listens on every IPv{family} address, '
                f"so it can't be combined with {others}; keep one or the other"
            )
    return [str(ip) for ip in ips]


def _validate(cfg: Config) -> None:
    for section, key in (("kiss", "port"), ("gpsd", "port"), ("http", "port")):
        port = getattr(getattr(cfg, section), key)
        if not 1 <= port <= 65535:
            raise ConfigError(f"[{section}] {key} must be 1-65535, got {port}")
    cfg.http.bind = _bind_addresses(cfg.http.bind)
    if cfg.kiss.reconnect_max_seconds < 1:
        raise ConfigError("[kiss] reconnect_max_seconds must be at least 1")
    if cfg.buffers.raw_max < 1:
        raise ConfigError("[buffers] raw_max must be at least 1")
    if cfg.buffers.station_expire_minutes < 1:
        raise ConfigError("[buffers] station_expire_minutes must be at least 1")
    cfg.storage.database = cfg.storage.database.strip()
    if cfg.storage.database:
        cfg.storage.database = str(Path(cfg.storage.database).expanduser())
    cfg.map.center = _latlon("[map] center", cfg.map.center)
    if cfg.station.position:
        cfg.station.position = _latlon("[station] position", cfg.station.position)
    if not 0 <= cfg.map.zoom <= 20:
        raise ConfigError("[map] zoom must be 0-20")
    level = cfg.logging.level.upper()
    if level not in _LOG_LEVELS:
        raise ConfigError(f"[logging] level must be one of {', '.join(sorted(_LOG_LEVELS))}")
    cfg.logging.level = level
    cfg.station.callsign = cfg.station.callsign.strip().upper()


def from_dict(data: dict) -> Config:
    cfg = Config()
    for section, value in data.items():
        if section not in _SECTIONS:
            raise ConfigError(
                f"unknown section [{section}] (valid: {', '.join(sorted(_SECTIONS))})"
            )
        setattr(
            cfg,
            section,
            _build_section(section, type(getattr(cfg, section)), value, cfg.warnings),
        )
    _validate(cfg)
    return cfg


def load(path: Path | None) -> tuple[Config, Path | None]:
    """Load config from `path`, or the default path if None.

    Returns the config and the file actually read (None when running on
    defaults because the default file does not exist).
    """
    if path is None:
        if not DEFAULT_PATH.exists():
            return from_dict({}), None
        path = DEFAULT_PATH
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return from_dict(data), path
