#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
# DEV ONLY: install what's needed to develop, test, package and screenshot
# emaprs, then create .venv with emaprs (editable) and its dev tools.
#
#   System (apt, needs sudo): git, python3 + venv + pip, ImageMagick,
#                             Chromium (skipped if Chrome/Chromium is present)
#   .venv:                    emaprs -e '.[dev]' (pytest, build, ruff, mypy, ...)
#
# Safe to rerun. On non-apt systems it lists the packages to install by hand
# and still sets up .venv if python3 is available.
#
# Usage: tools/install_build_deps.sh

set -euo pipefail

cd "$(dirname "$0")/.."

have() { command -v "$1" >/dev/null 2>&1; }

have_chrome() {
    have chromium || have chromium-browser || have google-chrome || have google-chrome-stable
}

SUDO=""
if [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
fi

# --- system packages -------------------------------------------------------

pkgs=(git python3 python3-venv python3-pip imagemagick)

if have apt-get; then
    echo "installing system packages: ${pkgs[*]}"
    $SUDO apt-get update -q
    $SUDO apt-get install -y -q "${pkgs[@]}"

    if have_chrome; then
        echo "Chromium/Chrome already installed"
    else
        . /etc/os-release
        if [[ "${ID:-}" == "ubuntu" ]] && have snap; then
            # Ubuntu's apt chromium package is only a wrapper for the snap.
            echo "installing chromium (snap)"
            $SUDO snap install chromium
        else
            echo "installing chromium (apt)"
            $SUDO apt-get install -y -q chromium
        fi
    fi
else
    echo "apt-get not found; install these with your package manager:" >&2
    echo "  ${pkgs[*]} chromium" >&2
    if ! have python3; then
        exit 1
    fi
fi

# --- python venv -----------------------------------------------------------

if [[ ! -x .venv/bin/python ]]; then
    echo "creating virtual environment in .venv"
    python3 -m venv .venv
fi
echo "installing emaprs (editable) and dev tools into .venv"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e '.[dev]'

echo
echo "done. Next:"
echo "  .venv/bin/pytest              run the tests"
echo "  tools/check.sh                tests + code-quality checks (before committing)"
echo "  tools/run_fake.sh             run emaprs against fake TNC + gpsd"
echo "  tools/screenshots.py          regenerate docs/screenshots/"
echo "  .venv/bin/python -m build     build sdist + wheel into dist/"
