#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""DEV ONLY: check a built wheel has the page and the files it bundles.

Used by CI and the release workflow after `python -m build`, so a packaging
change can't quietly ship a wheel with no page or no Leaflet.

    tools/check_wheel.py dist/emaprs-*.whl
"""

from __future__ import annotations

import sys
import zipfile

NEEDED = [
    "emaprs/static/index.html",
    "emaprs/static/app.js",
    "emaprs/static/style.css",
    "emaprs/static/leaflet/leaflet.js",
    "emaprs/static/leaflet/leaflet.css",
    "emaprs/static/leaflet/LICENSE",
    "emaprs/static/leaflet/images/layers.png",
    "emaprs/static/symbols/aprs-symbols-24-0@2x.png",
    "emaprs/static/symbols/COPYRIGHT.md",
]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: tools/check_wheel.py WHEEL", file=sys.stderr)
        return 2
    with zipfile.ZipFile(argv[0]) as whl:
        names = set(whl.namelist())
    missing = [n for n in NEEDED if n not in names]
    if missing:
        print(f"{argv[0]} is missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"{argv[0]}: OK ({len(names)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
