<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Nicholas Heath -->

# Changelog

Notable changes to emaprs, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-07

The first release. Earlier version numbers (0.0.x) were used during
development only and were never released.

### Added

- A map of heard stations, objects and items in the browser, drawn with
  the standard APRS symbol set, with station trails and your own position
  from gpsd or a fixed location in the config.
- Station popups with the latest details and the station's stored
  packets; a Raw tab with every packet, including ones that failed to
  decode; a Messages tab for packets without a position, highlighting
  messages to your callsign.
- Display options, remembered by the browser: station trails, imperial or
  metric units, and following your position.
- Reads packets from any KISS-over-TCP TNC (e.g. Direwolf) and your
  position from gpsd, and reconnects to both on its own.
- Packet history kept in SQLite, so a restart picks up where it left off.
- Listens on every IPv4 and IPv6 address by default, or only on the
  addresses in `[http] bind`.
- Works without internet access: Leaflet is bundled, and only the map
  tiles come from the internet, fetched by the browser.
- A reference systemd unit in `contrib/systemd/`.

### Known limitations

- emaprs doesn't transmit yet. Composing and sending messages and other
  packets is planned.
- There is no login, by design. emaprs is not meant to be reachable from
  the internet; see `SECURITY.md`.
- Without internet in the browser the map has no background; distance
  rings are planned for that case.
- The database is written on every received packet, which wears out SD
  cards and cheap flash; see "Flash wear on embedded devices" in the
  README.
- Tested on Linux, with Python 3.11 and 3.14.
