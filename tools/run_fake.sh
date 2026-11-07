#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
# TEST ONLY: start fake gpsd + fake KISS TNC, then emaprs in the foreground.
# Ctrl-C (or emaprs exiting for any reason) stops all three.
#
# Usage: tools/run_fake.sh [CONFIG]          (default: config.example.toml)
#
# Environment overrides:
#   LAT, LON   where the fake GPS and fake stations are   (45.52, -122.68)
#   MYCALL     callsign the fake TNC sends messages to    (N0CALL)
#   INTERVAL   seconds between fake packets               (2)
#
# Creates .venv and installs emaprs into it on first run.
#
# Fake server output goes to .run/fake_gpsd.log and .run/fake_kiss.log.
# The packet database is .run/emaprs/emaprs.sqlite3 (delete it to start empty).

set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="${1:-config.example.toml}"
LAT="${LAT:-45.52}"
LON="${LON:--122.68}"
MYCALL="${MYCALL:-N0CALL}"
INTERVAL="${INTERVAL:-2}"
PY=.venv/bin/python

# Create the venv and install emaprs (+ test tools) on first run, or if a
# previous install didn't finish.
if [[ ! -x "$PY" ]]; then
    echo "creating virtual environment in .venv ..."
    if ! python3 -m venv .venv; then
        echo "failed to create .venv (on Debian/Ubuntu: sudo apt install python3-venv)" >&2
        exit 1
    fi
fi
if ! "$PY" -c "import emaprs, aiohttp, aprslib" 2>/dev/null; then
    echo "installing emaprs and its dependencies into .venv ..."
    if ! "$PY" -m pip install -q -e '.[dev]'; then
        echo "pip install failed; fix the error above and rerun" >&2
        exit 1
    fi
fi
if [[ ! -f "$CONFIG" ]]; then
    echo "config not found: $CONFIG" >&2
    exit 1
fi

mkdir -p .run
# Run emaprs on a copy of the config, adjusted for testing:
#   - [storage] database points into .run/, keeping fake packets out of the
#     real database (XDG_DATA_HOME covers configs that leave the key out);
#   - [http] bind is forced to ["127.0.0.1"], so the test server is reachable
#     only from this machine, not the network (bind's default is every
#     address). The bind line is replaced, so keep it on one line.
FAKE_DB="$PWD/.run/emaprs/emaprs.sqlite3"
sed -E -e "s|^[[:space:]]*database[[:space:]]*=.*|database = \"$FAKE_DB\"|" \
    -e '/^[[:space:]]*bind[[:space:]]*=/d' "$CONFIG" >.run/config.toml
if grep -q '^[[:space:]]*\[http\]' .run/config.toml; then
    sed -i -E 's|^([[:space:]]*\[http\].*)$|\1\nbind = ["127.0.0.1"]|' .run/config.toml
else
    printf '\n[http]\nbind = ["127.0.0.1"]\n' >>.run/config.toml
fi
export XDG_DATA_HOME="$PWD/.run"
pids=()

cleanup() {
    trap - EXIT INT TERM
    echo
    echo "stopping fake servers..."
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PY" tools/fake_gpsd.py --lat "$LAT" --lon "$LON" --walk >.run/fake_gpsd.log 2>&1 &
pids+=($!)
"$PY" tools/fake_kiss.py --center "$LAT,$LON" --mycall "$MYCALL" --interval "$INTERVAL" \
    >.run/fake_kiss.log 2>&1 &
pids+=($!)

echo "fake gpsd + fake KISS running (logs in .run/); starting emaprs with $CONFIG"
echo "(packet database: .run/emaprs/emaprs.sqlite3)"
echo "Ctrl-C stops everything."

# Run emaprs in the background too, so this script's INT trap fires right away.
"$PY" -m emaprs --config .run/config.toml &
app=$!
pids+=($app)
wait "$app"
