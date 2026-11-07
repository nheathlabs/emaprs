<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Nicholas Heath -->

# Contributing to emaprs

## Scope

emaprs has a deliberate, narrow purpose: a frontend for working with APRS
through a KISS TNC, with gpsd for your position, served to a browser
(see the top of the [README](README.md)). Before proposing a feature,
weigh it against these; open an issue to discuss anything that isn't a
clear fit.

1. **Does it go through the TNC?** emaprs talks to the air only through
   its KISS TNC: showing, decoding and keeping what arrives, and (planned,
   see "Possible future work" in the README) composing packets to send.
2. **Does it leave the TNC's job to the TNC?** Your own position beacon,
   digipeating and other things a TNC such as Direwolf already does belong
   there, not in emaprs.
3. **Does it run unattended on a small box?** emaprs is meant to sit on a
   station computer as a service: it reconnects forever, doesn't care about
   start order, and keeps its state in one SQLite file. A feature that
   needs a babysitter, a second daemon, or a heavy dependency doesn't fit.
4. **Does the server still work offline?** The server must not require
   internet access: a station may only ever have a local network, or
   none. Running offline is an option, not a rule, so a server feature
   may use the internet when it's there, as long as everything else
   works without it. The browser is more likely to be online (a phone, a
   tethered laptop), so a feature that needs the internet usually
   belongs in the page, fetched by the browser, the way the map tiles
   already are. The page itself, and everything it needs to run, is
   served by emaprs.
5. **Does the page stay plain?** One static HTML/CSS/JS page with no build
   step; Leaflet is the only library, bundled in the package. Display
   settings live in the browser, not on the server.
6. **One feature per pull request** — a guideline, not a hard rule (some
   changes can't be split cleanly). If the description needs an "and
   also", it is probably two features; split them and weigh each against
   1–5.

## Commits

Keep one logical change per commit, and keep mechanical churn out of
behavioural diffs — e.g. a whole-tree reformat or a lint-rule sweep is its
own commit, separate from any feature work.

A change people would notice (a feature, a fix, a changed default or
config key) also gets a line under `## [Unreleased]` in `CHANGELOG.md`,
in the same commit. Releasing is covered in `RELEASING.md`.

## Disclosing AI assistance

If any code, tests, or docs in a change were generated or materially
drafted by an AI tool, say so — both places:

- **In the commit message**, a trailer naming the tool:

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

  One line per tool if more than one was used. Use the model / product
  name, not just "AI".

- **In the pull request description**, a one-line note that the change
  was AI-assisted.

You are still the author: review every line, run the pre-push checks, and
don't submit code you don't understand. The trailer is about
transparency, not a disclaimer of responsibility. Purely human changes
need neither note.

## Setting up

`tools/install_build_deps.sh` installs what's needed to develop, test,
package and screenshot emaprs: git, python3 with venv and pip, ImageMagick
and Chromium via apt (it uses sudo; Chromium comes from snap on Ubuntu and
is skipped if Chrome or Chromium is already installed), then creates
`.venv` with emaprs and the dev tools. It's safe to rerun. Without apt,
`python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` sets up
everything but the screenshot tools.

## Running without a radio or GPS

`tools/` has two test-only fake servers, a fake gpsd and a fake KISS TNC.
They are not part of the installed app.

One command starts both fakes plus emaprs, and Ctrl-C stops all three. On
first run it also creates `.venv` and installs emaprs and the test tools
into it (this needs internet access for pip):

```sh
tools/run_fake.sh                   # uses config.example.toml
tools/run_fake.sh emaprs.toml       # or your own config
LAT=40.0 LON=-105.0 MYCALL=N0CALL INTERVAL=0.5 tools/run_fake.sh
```

The fake servers' output goes to `.run/fake_gpsd.log` and
`.run/fake_kiss.log`. Only emaprs logs to the terminal. Whatever your
config sets for `[storage] database`, the fake packets go to
`.run/emaprs/emaprs.sqlite3`, not your real database. Delete it to start
empty. The test server also always listens on `127.0.0.1` only, whatever
your config's `[http] bind` says. It does this by replacing your config's
`bind` line, so keep that list on one line.

To run the pieces by hand instead:

```sh
.venv/bin/python tools/fake_gpsd.py --lat 45.52 --lon -122.68 --walk   # fake gpsd on :2947
.venv/bin/python tools/fake_kiss.py --center 45.52,-122.68 --mycall N0CALL  # fake TNC on :8001
.venv/bin/emaprs --config config.example.toml
```

`fake_gpsd.py --nofix` reports no GPS fix. `fake_kiss.py --interval` sets
the time between packets.

## Before you push

CI (`.github/workflows/ci.yml`) runs `tools/check.sh` on every push to
`master` and every pull request, on Python 3.11 (the oldest supported) and
3.14, and builds the packages. `master` is protected: changes reach it
only through a pull request, which can merge once those CI jobs pass and
its branch is up to date with `master`. There is no pre-commit hook, so
run the checks yourself first:

```sh
tools/check.sh              # everything
tools/check.sh ruff mypy    # only some
```

It runs the tests with coverage, `ruff`, `mypy`, `vulture` and `bandit`,
and prints a `radon` complexity report; it exits non-zero if any check
fails (see "What the tooling covers" below). Every check must pass before
a change is merged — there is no outstanding finding to work around.
`.venv/bin/pytest` on its own runs just the tests.

After a change that alters the page, regenerate the screenshots (see
"Screenshots" below) and commit the PNGs in the same change.

## Screenshots

`tools/screenshots.py` regenerates the README screenshots in
`docs/screenshots/`. It starts the fake gpsd, fake TNC and emaprs on free
ports (it won't touch a real TNC or gpsd), lets 45 s of traffic build up,
then captures each view in headless Chromium at 1280×800 and shrinks the
PNGs with ImageMagick, so the screenshots stay consistent:

```sh
tools/screenshots.py                    # all screenshots
tools/screenshots.py station-popup      # only some
tools/screenshots.py --settle 20        # shorter warm-up
```

To add a screenshot, add an entry to `SHOTS` in the script (a name and the
JavaScript that sets up the view) and reference the PNG from the README.

## Building packages

```sh
.venv/bin/python -m build           # sdist + wheel into dist/
tools/check_wheel.py dist/*.whl     # the wheel has the page and its files
```

## What the tooling covers

All tool settings live in `pyproject.toml`, each deliberate exception with
the reason next to it. Don't add an exception without one.

- **Tests** (`pytest`, `pytest-asyncio`) must pass, coverage of `emaprs/`
  must stay at or above `fail_under` (80%), and nothing may be left
  unclosed: `check.sh` runs pytest under `-X dev` and fails on any
  `ResourceWarning` (an unclosed socket, file or SQLite connection). Run
  with `-X tracemalloc=20` to see where the leaked object was opened.
  Coverage includes emaprs started as a subprocess by
  `tests/test_shutdown.py`.
- **`ruff`** lints `emaprs/`, `tests/` and `tools/`: pycodestyle,
  pyflakes, import order, bugbear, pyupgrade (Python 3.11+ syntax),
  asyncio pitfalls and more. Line length is 100. `ruff format` isn't used
  yet; follow the style of the surrounding code.
- **Function complexity** is capped by `ruff`'s mccabe check at 15
  (`max-complexity`). Split a function that trips it into helpers that
  each do one step — a small driver over named steps — rather than raising
  the limit or adding a `noqa`. `radon` reports anything graded C or worse
  without failing, so you can see a function heading for the limit.
- **`mypy`** type-checks `emaprs/` (`check_untyped_defs`,
  `warn_unreachable`, `warn_unused_ignores`). New code in `emaprs/` is
  annotated.
- **`vulture`** fails on dead code across `emaprs/`, `tests/` and
  `tools/`. Delete what it reports.
- **`bandit`** fails on medium- or high-severity security patterns in
  `emaprs/`. The one skip (B104, binding to all addresses) is documented in
  `pyproject.toml`.

The dev tools have upper version bounds in `pyproject.toml` because new
releases add rules. Raising one is a deliberate, separate change, with any
new findings fixed in the same change.

Everything below is what the tools *can't* check.

## Conventions

### Every file starts with the licence header

Two lines: `SPDX-License-Identifier: GPL-3.0-or-later` and the copyright
line, in the file's own comment syntax (`#`, `//`, `/* */`, `<!-- -->`;
see any existing file). After a shebang if there is one. Python modules
follow it with a one-line docstring saying what the module is for.

Everything under `emaprs/static/symbols/` and `emaprs/static/leaflet/` is
third-party and keeps its own licence; don't add GPL headers there,
including to the `README.md` in each folder.

### Modules

One module = one responsibility, named for it:

| Module | Responsibility |
|---|---|
| `kiss.py` | KISS framing and the KISS-over-TCP client |
| `ax25.py` | AX.25 UI frame ↔ TNC2 text |
| `aprs.py` | TNC2 text → the normalized packet dict the rest of the app uses |
| `gps.py` | gpsd client |
| `state.py` | the in-memory `Hub` shared by the receivers and web clients, and expiry |
| `store.py` | the SQLite mirror of each station's packet history |
| `web.py` | the aiohttp app: static page, JSON snapshot, push WebSocket |
| `config.py` | loading and validating the TOML config |
| `__main__.py` | the command line and wiring everything together |

When a module gets large, split it at a real seam rather than by size.
Protocol decoding (`kiss`, `ax25`, `aprs`) stays free of I/O and asyncio
so it can be tested with plain values.

### Asyncio

emaprs is one asyncio process. Network I/O is asyncio; nothing blocks the
event loop for long. Each receiver (KISS, gpsd) reconnects forever with a
delay and reports its status to the `Hub`, which the page shows in the
header — a lost TNC or gpsd is a normal state, not an error to exit on.
A new receiver or background task follows the same pattern and shuts down
cleanly on cancellation (`tests/test_shutdown.py`).

### Configuration

Every key is documented, with its default, in `config.example.toml`, and
validated in `config.py`. Unknown keys and bad values stop startup with a
clear error (exit status 2), never a silent fallback. A new setting gets
all three: the validation, the documented example, and a test in
`tests/test_config.py`. Mention it in the README if users need to know
about it.

### Storage

The in-memory `Hub` is the source of truth while running; `store.py`
mirrors it to SQLite as packets arrive and expire, so a restart can
rebuild the map. Every connection is closed (the `ResourceWarning` check
enforces it). The schema is versioned with `PRAGMA user_version`
(`SCHEMA_VERSION` in `store.py`); a schema change bumps it and must open
a database written by the previous version, not refuse it. Keep writes
few: see "Flash wear on embedded devices" in the README.

### Dependencies

Runtime dependencies are `aiohttp` and `aprslib`, nothing else. A new one
needs a strong reason and goes in both `pyproject.toml` and
`requirements.txt`. The page has no build step and no npm: plain
JavaScript and CSS in `emaprs/static/`. Leaflet is bundled, unchanged, in
`emaprs/static/leaflet/`; its `README.md` says where it came from and how
to upgrade it. Don't load scripts or styles from a CDN: the page has to
work in a browser with no internet.

### The web page

Display-only preferences (units, trails, follow-me) are stored in the
browser and change nothing on the server; the server stores and sends
metric values. The page must recover by itself when the WebSocket drops,
and render the current state from the snapshot when it reconnects.

Text from packets (callsigns, object names, comments, messages, raw
packets) is untrusted: any station in range can send anything. Put it in
the page only as text: build elements with `h()`, which sets
`textContent`, never `innerHTML`, and pass Leaflet an element rather than
a string, because `bindTooltip()`, `bindPopup()` and `divIcon({html})`
insert strings as HTML.

### Tests

New behaviour comes with a test in the matching `tests/test_<module>.py`.
Tests needing a TNC or gpsd use local fakes on free ports, never a real
radio or the network. Decoder bugs get a regression test built from the
raw packet that broke it.

A refactor that should change nothing gets proof that it didn't: the
existing tests must pass unchanged, and for a page change, screenshots
from `tools/screenshots.py` before and after should match.
